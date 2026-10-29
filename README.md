# Hermes Light client and Homebrew tap

An independently packaged, remote-only [Hermes Desktop](https://github.com/NousResearch/hermes-agent)
client. This repository contains the client build/release tooling **and** its
Homebrew tap. It contains no server infrastructure or configuration.

The Light app ships no Python or local agent payload. It connects to an existing
Hermes gateway and delegates app updates to Homebrew. This distribution is
independently maintained; Nous Research does not publish or sign this package.
Apple Silicon and macOS 13 or newer are supported.

**Release status:** the native client candidate has been built. Developer ID
certificate setup and Apple notarization are pending. No installable cask or
notarized release has been published yet.

## Install after the first notarized release

```sh
brew tap hardworker/hermes-light https://github.com/hardworker/hermes-light
brew install --cask hardworker/hermes-light/hermes-light
```

Both commands use this public repository, `hardworker/hermes-light`.
Client installation requires no GitHub login. The cask is generated only after
Apple accepts the package and its ticket and Gatekeeper checks pass.

Open **Hermes Light**, choose **Connect to existing Hermes**, and configure your
remote gateway. Agent execution runs on that gateway. Its desktop and takeover
features must be enabled on the gateway separately.

## Source and layout

[packaging/source.json](packaging/source.json) pins the official upstream tag
and exact source commit. The upstream `light` builder prepares dependencies in
isolated build state without installing an agent in the build user's home.

| Path | Purpose |
|---|---|
| `packaging/` | Upstream source pin and Electron signing adapter |
| `scripts/light_client_release.py` | Build, signing setup, notarization, release and cask generation |
| `Casks/` | Generated Homebrew cask after publication |
| `tests/` | Release boundary checks |
| `secrets/` | Local signing material; ignored by Git |
| `.build/` | Local source, build tools and release candidates; ignored by Git |

The app retains upstream's commit-specific identity. Homebrew installs the
bundle as `Hermes Light.app`. The candidate includes upstream's MIT license
and an independent distribution notice before signing.

## Signing setup

Use Python 3.11+, Git and a selected Xcode installation on an Apple Silicon Mac.

```sh
python3 scripts/light_client_release.py request
```

This creates a private RSA key (0600) and public certificate request in
`secrets/apple-signing/` (0700). Keep the private key; the returned certificate
must match it. Never upload the private key to Apple, GitHub or chat.

In [Apple Developer Certificates](https://developer.apple.com/account/resources/certificates/add),
create **Developer ID Application** using
`secrets/apple-signing/hermes-light.certSigningRequest`. Save the returned
certificate as `secrets/apple-signing/developerID_application.cer`, then run:

```sh
python3 scripts/light_client_release.py import-certificate
xcrun notarytool store-credentials hermes-light-notary
```

The second command prompts locally for Apple ID, team ID and an app-specific
password, and stores them in Keychain. A notarization API-key Keychain profile
also works. These credentials never belong in this repository or chat.

## Build and notarize

```sh
python3 scripts/light_client_release.py build
python3 scripts/light_client_release.py notarize
```

The build creates `.build/hermes-light/candidate.json` and the Light app. It
rejects bootstrap/full apps, unexpected source or app identity, local agent
payloads, and automatic-update ownership. `--source PATH` can reuse a clean
upstream checkout at the pinned commit.

Notarization selects a valid **Developer ID Application** identity. If several
exist, pass `--identity 'Developer ID Application: …'`. It uses upstream's
prepared signing dependency, hardened runtime and Electron entitlements.
Submissions are saved before waiting so reruns can resume a recorded submission.
Apple acceptance, stapled-ticket validation and Gatekeeper approval are required
before the final ZIP and `release.json` are written. The ZIP is made after
stapling; its checksum covers the final distributed bytes.

Test the signed app on a clean Mac: remote onboarding must work without a local
runtime install. Connect to a configured gateway and verify chat, **Screen**,
**Take over**, and **Hand back** before publishing.

## Publish

```sh
python3 scripts/light_client_release.py publish
```

The maintainer needs GitHub CLI write access to this public repository.
Publication rechecks the signature, ticket, Gatekeeper, archive checksum and
repository visibility, then publishes an immutable versioned GitHub release
and generates `Casks/hermes-light.rb` with its exact SHA-256. Commit that cask
to `main` to make the installation commands usable. Existing releases are never
overwritten. Credentials and local filesystem paths are excluded from release
metadata. No quarantine or Gatekeeper bypass is used.

## Validate

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/light_client_release.py
node --check packaging/sign-app.mjs
bash scripts/check-secrets.sh
```
