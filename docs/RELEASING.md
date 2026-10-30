# Maintaining releases

Use Python 3.11+, Git, GitHub CLI, and Xcode on an Apple Silicon Mac.
Keep a Developer ID Application identity and the `hermes-light-notary`
notarization profile in the Mac's Keychain. No signing credentials belong
in the checkout, GitHub repository, or CI.

## Update upstream

```sh
make update TAG=vX.Y.Z
make test
git add packaging/source.json
git commit -m "Update upstream Hermes version"
git push
```

Replace `vX.Y.Z` with an official stable tag. `update` resolves lightweight or
annotated tags to an exact upstream commit and writes the pin. New versions
start at packaging revision 1; a packaging-only rebuild can specify
`REVISION=2` (or the next revision) with the existing tag. Moved upstream tags
are rejected. `update` fetches tag metadata; it downloads no source checkout,
dependencies, or app.

## Release

```sh
make release
```

This builds the Light variant, signs it, submits it to Apple, staples and
validates the ticket, verifies Gatekeeper, publishes the ZIP and public
manifest, and generates `Casks/hermes-light.rb` with the exact archive checksum.
Build state is isolated by version and packaging revision. Rerunning resumes
an existing candidate or Apple submission; an already published release
regenerates the cask without rebuilding or replacing its assets.

Test onboarding and connection to a gateway on another Mac, then publish the
tap update:

```sh
git add Casks/hermes-light.rb
git commit -m "Update Hermes Light cask"
git push
make clean
```

`clean` removes the current release's managed source/dependency cache,
candidate and notarization upload. It preserves the final ZIP and public
release manifest. A custom `--source` checkout is never removed. To recover
the cask after local cleanup, run `make cask`; it checks the published
manifest against the pin and GitHub's archive digest without downloading
the app.

The app retains upstream's commit-specific identity. Homebrew consistently
installs it as `Hermes Light.app`; check gateway reconnection when testing
an upgrade. No changes are made to previously published app bundles.

## Signing on a new Mac

`make signing-request` prints the CSR path. Create a **Developer ID
Application** certificate using that CSR and select **G2 Sub-CA** when asked
in [Apple Developer](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/).
Save the returned certificate as `developerID_application.cer` beside the CSR.

```sh
make import-certificate
xcrun notarytool store-credentials hermes-light-notary
```

Signing files default to `~/Library/Application Support/Hermes Light/signing`,
outside the repository. `HERMES_LIGHT_SIGNING_DIR` can select another private
directory. Authentication is entered locally and stored in Keychain.

Run `make help` for individual build/signing/publication commands. Additional
CLI options, including a custom Keychain profile or signing identity, are
listed by `python3 scripts/light_client_release.py --help`.
