import { notarize } from '@electron/notarize'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

export async function signAndNotarize(
  { source, app, identity, keychain, profile },
  { sign, notarizeApp = notarize, env = process.env } = {}
) {
  if (!source || !app || !identity) {
    throw new Error('source, app and signing identity are required')
  }
  let credentials
  if (profile) {
    credentials = { keychainProfile: profile, keychain: keychain || undefined }
  } else {
    const names = ['APPLE_NOTARY_APPLE_ID', 'APPLE_NOTARY_PASSWORD', 'APPLE_NOTARY_TEAM_ID']
    if (names.some(name => !env[name])) {
      throw new Error('All three APPLE_NOTARY_* secrets are required for CI notarization')
    }
    credentials = {
      appleId: env.APPLE_NOTARY_APPLE_ID,
      appleIdPassword: env.APPLE_NOTARY_PASSWORD,
      teamId: env.APPLE_NOTARY_TEAM_ID
    }
  }

  // Preserve the exact signing dependency and entitlements prepared by upstream.
  const require = createRequire(path.join(source, 'apps/desktop/package.json'))
  const signApp = sign || require('@electron/osx-sign').sign
  const entitlements = path.join(source, 'apps/desktop/electron')
  await signApp({
    app,
    identity,
    keychain: keychain || undefined,
    platform: 'darwin',
    type: 'distribution',
    preAutoEntitlements: false,
    optionsForFile: file => {
      const entitlementsFile = file === app
        ? 'entitlements.mac.plist'
        : 'entitlements.mac.inherit.plist'
      return {
        hardenedRuntime: true,
        entitlements: path.join(entitlements, entitlementsFile)
      }
    }
  })
  // Resolves only after Apple accepts the submission and the ticket is stapled.
  await notarizeApp({ appPath: app, ...credentials })
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [source, app, identity, keychain, profile] = process.argv.slice(2)
  await signAndNotarize({ source, app, identity, keychain, profile })
}
