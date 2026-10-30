# Maintaining releases

## Normal upgrades

The **Check upstream release** workflow runs daily at 06:17 UTC and can also
be run manually. It maintains one PR on `automation/upstream-update`, pins an
exact official stable commit, and rejects moved tags and downgrades. Only
`packaging/source.json` is changed. An unchanged or declined update is not
reopened each day.

The updater uses the built-in `GITHUB_TOKEN`. Repository Actions settings must
allow Actions to create PRs. GitHub combines that permission with review
approval capability; these workflows never approve or merge PRs. If GitHub
shows **Approve workflows to run**, approve validation, review, and merge.

Changing the source pin on `main` triggers **Release Hermes Light**, using the
standard ARM64 `xcode-27` runner. It validates packaging, builds without signing
credentials, then uses a temporary Keychain to sign and submit to Apple.
Only an accepted, stapled, Gatekeeper-verified archive is published. Uploaded
asset digests are verified while the release is a draft; the cask is then
installed and checked through Homebrew before its commit is pushed to `main`.
Cask-only commits do not trigger another release.

## One-time signing setup

Create a `release` GitHub environment restricted to the `main` branch. On the
signing Mac, with Python 3.11+ and an authenticated GitHub CLI, run:

```sh
make ci-setup
```

All inputs are hidden. Create a new CI-only app-specific password in your
[Apple Account](https://account.apple.com). The helper exports an encrypted
CI copy of the existing Developer ID certificate/private key with a generated
password, proves it can be restored and used for signing, and validates the
Apple credentials before uploading these environment secrets through GitHub
CLI. CI setup does not request a recovery password:

- `MACOS_SIGNING_P12_BASE64`
- `MACOS_SIGNING_P12_PASSWORD`
- `APPLE_NOTARY_APPLE_ID`
- `APPLE_NOTARY_TEAM_ID`
- `APPLE_NOTARY_PASSWORD`

When the identity exists only in Keychain, setup exports exactly the selected
Developer ID identity into an encrypted temporary file. macOS may request
permission in its native Keychain dialog. No export is started until you run
the setup command. Temporary files are removed afterward. With multiple
identities, `HERMES_LIGHT_SIGNING_IDENTITY` selects one fingerprint.

## Optional portable recovery backup

The existing local Keychain identity and `hermes-light-notary` profile remain
usable for emergency signing. A separate exported backup is optional and can
be created independently of CI:

```sh
python3 scripts/setup_ci.py --backup-only
```

The optional encrypted backup lives in
`secrets/recovery/hermes-light-signing.p12` inside the ignored project directory.
Retain its password in your password manager. The recovery password stays
local. CI receives a separately encrypted PKCS#12 copy with a generated password,
stored as its own GitHub environment secret. Loose PEM,
CSR, and certificate files can be removed after a successful cloud release and
backup verification. New Macs can import the encrypted PKCS#12 backup; creating
a fresh identity instead uses `make signing-request` and
`make import-certificate`. Explicit file inputs default to the ignored project
directory `secrets/apple-signing`, or `HERMES_LIGHT_SIGNING_DIR` when supplied.
Normal CI setup can use Keychain directly. Local state stays inside the
project or temporary storage; no Application Support directory or launcher is
created.

Renew the Developer ID certificate before its expiry. Retain the old encrypted
backup separately, replace the local certificate/key inputs, and rerun the
secure setup to upload a verified new backup. Revoke and replace the dedicated
CI app-specific password separately from local recovery credentials.

## Retries and recovery

Use **Run workflow** on **Release Hermes Light**, selecting `main`. It retries
the approved current pin; it accepts no arbitrary source or branch input.

Signed submission and final archive checkpoints contain only the signed app,
release pin, checksums, and submission metadata. They have no credentials or
local paths. A retry verifies checkpoint provenance, configuration and archive
hash, and resumes the recorded Apple submission. Final checkpoints preserve the
exact bytes needed to resume partial draft uploads. Existing assets are never
overwritten. Successful checkpoints are deleted; failed ones expire after
seven days. If a partial draft has lost its matching checkpoint, stop and
investigate rather than replace assets.

Releases are serialized. The pin is checked again before publication and cask
updates so stale runs cannot roll back a newer version. Failed jobs remove
temporary Keychains, signing files, test installs and downloads, and build
files. GitHub's workflow summary and failure notifications are the status source.

## Local maintenance

```sh
make update TAG=vX.Y.Z       # resolve the official stable tag
make test
git add packaging/source.json
git commit -m "Update upstream Hermes version"
git push                    # triggers the cloud release
```

Use `REVISION=N` with the current tag for a packaging-only rebuild. The first
cloud rollout uses revision 2 of the existing upstream pin.

`make release` remains available locally. The CLI supports `--keychain` for an
explicit signing/notarization Keychain and `--target-commit` for an exact
packaging commit. `notarize --submit-only`, `checkpoint`, and `restore` provide
the staged CI interface. `make cask` recovers the cask from the published
manifest and GitHub's archive digest without downloading the app.

`make clean` removes managed temporary build files while preserving the final
ZIP and public manifest. User-supplied source checkouts are preserved. The app
retains upstream's commit-specific identity, so check gateway reconnection when
testing an upgrade on another Mac.
