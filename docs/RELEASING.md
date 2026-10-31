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

## One-time manual credential setup

The `release` GitHub environment is restricted to `main`. Configure its
secrets manually in the repository's **Settings → Environments → release**.
No local setup script or credential-export helper is needed.

1. Open **Keychain Access → login → My Certificates**. Locate the Developer ID
   Application identity used for Hermes Light. Expand it and select its
   certificate and private key together, then choose **File → Export Items**.
   Export only this identity as `/tmp/hermes-light-signing.p12`.
2. macOS may ask you to authorize the private-key export. Choose a password
   to encrypt the exported PKCS#12 file and retain it for the GitHub secret
   below. This is the CI certificate password; it is separate from your Apple
   Account password and notarization app-specific password.
3. Copy the encrypted file's Base64 representation to the clipboard:

   ```sh
   base64 -i /tmp/hermes-light-signing.p12 | pbcopy
   ```

4. Add the following **environment secrets** using GitHub's UI:

   | Secret | Value |
   |---|---|
   | `MACOS_SIGNING_P12_BASE64` | The Base64 text copied above |
   | `MACOS_SIGNING_P12_PASSWORD` | The password chosen for the exported PKCS#12 file |
   | `APPLE_NOTARY_APPLE_ID` | The Apple ID for the Developer Program membership |
   | `APPLE_NOTARY_TEAM_ID` | The membership's Team ID |
   | `APPLE_NOTARY_PASSWORD` | A new CI-only app-specific password from [Apple Account](https://account.apple.com) |

Keep values out of source files, pull requests, and chat. The local Keychain
identity and `hermes-light-notary` profile remain available for emergency
signing. GitHub's runner imports its encrypted copy into a temporary Keychain
only after the unsigned build, and deletes that Keychain afterward.

After the first cloud release and Homebrew checks pass, delete the temporary
export:

```sh
rm /tmp/hermes-light-signing.p12
```

For certificate renewal, export the replacement identity and update the two
certificate secrets together while releases are idle. Renew before the old
certificate expires. The dedicated CI app-specific password can be replaced
independently of the working local notarization profile.

## Failed releases

There are no saved build checkpoints. If a run fails before creating a GitHub
release, use **Run workflow** on **Release Hermes Light**, selecting `main`.
It rebuilds, signs, and submits to Apple again.

If a draft release or tag already exists, the workflow stops rather than
replacing it. Inspect the failed run, then increase the packaging revision in
`packaging/source.json` and merge that change to try again with a new tag.
Existing releases and assets are left untouched.

If publication succeeded but the Homebrew check or cask commit failed, fix the
reported problem and recover the cask from the published metadata:

```sh
make cask
git add Casks/hermes-light.rb
git commit -m "Update Hermes Light cask"
git push
```

Review the generated cask and verify installation after pushing it. There is
no need to rebuild or replace the published app.

Releases run one at a time and check the current source pin before publishing
or committing the cask. Each run removes its temporary signing Keychain,
private files, test app, downloads, and build tree, including on failure.
GitHub's normal workflow failure notifications report problems.

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

`make release` remains available locally, using the existing
`hermes-light-notary` Keychain profile. Use `--keychain` to select a different
signing/notarization Keychain. `make cask` regenerates the cask from a published
release's metadata and archive digest without downloading the app.

`make clean` removes managed temporary build files while preserving the final
ZIP and public manifest. User-supplied source checkouts are preserved. The app
retains upstream's commit-specific identity, so check gateway reconnection when
testing an upgrade on another Mac.
