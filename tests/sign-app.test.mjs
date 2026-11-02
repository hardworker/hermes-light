import assert from 'node:assert/strict'
import path from 'node:path'
import { setImmediate } from 'node:timers/promises'
import test from 'node:test'

import { signAndNotarize } from '../packaging/sign-app.mjs'

const source = path.resolve('fixture-upstream')
const app = path.join(source, 'Hermes Light.app')
const options = { source, app, identity: 'fixture-identity', keychain: 'fixture.keychain' }
const env = {
  APPLE_NOTARY_APPLE_ID: 'fixture-account',
  APPLE_NOTARY_PASSWORD: 'fixture-password',
  APPLE_NOTARY_TEAM_ID: 'fixture-team'
}

test('CI uses the existing credentials and preserves upstream signing policy', async () => {
  const calls = []
  await signAndNotarize(options, {
    env,
    sign: async signing => {
      calls.push('sign')
      assert.equal(signing.identity, options.identity)
      assert.equal(signing.keychain, options.keychain)
      assert.equal(signing.preAutoEntitlements, false)
      const entitlements = path.join(source, 'apps/desktop/electron')
      assert.deepEqual(signing.optionsForFile(app), {
        hardenedRuntime: true,
        entitlements: path.join(entitlements, 'entitlements.mac.plist')
      })
      assert.deepEqual(signing.optionsForFile(path.join(app, 'nested-helper')), {
        hardenedRuntime: true,
        entitlements: path.join(entitlements, 'entitlements.mac.inherit.plist')
      })
    },
    notarizeApp: async credentials => {
      calls.push('notarize')
      assert.deepEqual(credentials, {
        appPath: app,
        appleId: env.APPLE_NOTARY_APPLE_ID,
        appleIdPassword: env.APPLE_NOTARY_PASSWORD,
        teamId: env.APPLE_NOTARY_TEAM_ID
      })
    }
  })
  assert.deepEqual(calls, ['sign', 'notarize'])
})

test('manual releases retain the saved profile and explicit Keychain', async () => {
  await signAndNotarize({ ...options, profile: 'fixture-profile' }, {
    env: {},
    sign: async () => {},
    notarizeApp: async credentials => {
      assert.deepEqual(credentials, {
        appPath: app, keychainProfile: 'fixture-profile', keychain: options.keychain
      })
    }
  })
})

test('incomplete CI credentials fail before signing without disclosing values', async () => {
  for (const name of Object.keys(env)) {
    let called = false
    await assert.rejects(signAndNotarize(options, {
      env: { ...env, [name]: '' },
      sign: async () => { called = true },
      notarizeApp: async () => { called = true }
    }), error => {
      assert.match(error.message, /APPLE_NOTARY_/)
      for (const value of Object.values(env)) {
        assert.ok(!error.message.includes(value))
      }
      return true
    })
    assert.equal(called, false)
  }
})

test('a signing failure prevents submission to Apple', async () => {
  let submitted = false
  await assert.rejects(signAndNotarize(options, {
    env,
    sign: async () => { throw new Error('signing failed') },
    notarizeApp: async () => { submitted = true }
  }), /signing failed/)
  assert.equal(submitted, false)
})

test('Apple rejection or a stapling failure propagates to the release gate', async () => {
  await assert.rejects(signAndNotarize(options, {
    env,
    sign: async () => {},
    notarizeApp: async () => { throw new Error('notarization failed') }
  }), /notarization failed/)
})

test('the helper waits for notarization and stapling to complete', async () => {
  let complete
  const acceptance = new Promise(resolve => { complete = resolve })
  let finished = false
  const pending = signAndNotarize(options, {
    env,
    sign: async () => {},
    notarizeApp: async () => acceptance
  }).then(() => { finished = true })
  await setImmediate()
  assert.equal(finished, false)
  complete()
  await pending
  assert.equal(finished, true)
})
