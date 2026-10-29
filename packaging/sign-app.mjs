// Use the exact osx-sign dependency and entitlements prepared by upstream.
import { createRequire } from 'node:module'
import path from 'node:path'

const [source, app, identity] = process.argv.slice(2)
if (!source || !app || !identity) throw new Error('source, app and signing identity are required')
const require = createRequire(path.join(source, 'apps/desktop/package.json'))
const { sign } = require('@electron/osx-sign')
const entitlements = path.join(source, 'apps/desktop/electron')
await sign({
  app,
  identity,
  platform: 'darwin',
  type: 'distribution',
  preAutoEntitlements: false,
  optionsForFile: file => ({
    hardenedRuntime: true,
    entitlements: path.join(entitlements, file === app ? 'entitlements.mac.plist' : 'entitlements.mac.inherit.plist')
  })
})
