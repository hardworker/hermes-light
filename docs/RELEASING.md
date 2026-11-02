# Maintaining releases

## Updates

**Check upstream release** runs daily at 06:17 UTC and can be started manually.
It maintains one source-pin PR on `automation/upstream-update`, checking stable
upstream tags and rejecting moved tags or downgrades. Current or declined
updates are left alone. The bot branch contains only `packaging/source.json`.

Enable **Allow GitHub Actions to create and approve pull requests** in the
repository's Actions settings. The workflows use `GITHUB_TOKEN` and never
approve or merge PRs. Select **Approve workflows to run** if GitHub requests it,
then review and merge normally.

A source-pin change on `main` starts **Release Hermes Light** on the ARM64
`xcode-27` runner: build, sign, notarize, verify the draft's uploaded digests,
publish, test Homebrew installation, and open a cask PR on
`automation/cask-update`. Review and merge that PR to update the Homebrew tap;
the release job does not push to protected `main`. Releases run one at a time
and refuse to replace existing tags or assets. Cask commits do not start
another release.

The cask PR uses `peter-evans/create-pull-request` and changes only
`Casks/hermes-light.rb`. The release job explicitly starts **Validate client
release tooling** on its branch because `GITHUB_TOKEN`-created PRs do not
automatically trigger pull-request workflows. It never approves or merges
the cask PR.

## Signing setup

Create a `release` GitHub environment restricted to `main` and add its secrets
through **Settings → Environments → release**:

1. In **Keychain Access → login → My Certificates**, select the Developer ID
   Application certificate and its private key. Export only this identity to
   `/tmp/hermes-light-signing.p12`, choosing a password to encrypt it.
2. Copy its Base64 representation:

   ```sh
   base64 -i /tmp/hermes-light-signing.p12 | pbcopy
   ```

3. Add these environment secrets:

   | Secret | Value |
   |---|---|
   | `MACOS_SIGNING_P12_BASE64` | The copied Base64 text |
   | `MACOS_SIGNING_P12_PASSWORD` | The export password |
   | `APPLE_NOTARY_APPLE_ID` | Your Developer Program Apple ID |
   | `APPLE_NOTARY_TEAM_ID` | Your Team ID |
   | `APPLE_NOTARY_PASSWORD` | A new CI-only app-specific password from [Apple Account](https://account.apple.com) |

Keep secret values out of source files, PRs, and chat. CI imports credentials
into a temporary Keychain after building, removes them after signing, and
cleans its test app, dedicated Homebrew cache, and build files on failure too.
After the first successful release, delete the temporary export:

```sh
rm /tmp/hermes-light-signing.p12
```

Keep the local Keychain identity and `hermes-light-notary` profile for manual
releases. To renew the certificate, update both signing secrets while releases
are idle. The CI app-specific password can be replaced independently.

## Failed releases

Before a draft or tag is created, retry with **Run workflow → main**. Each run
builds and submits to Apple again; there are no saved build checkpoints.

If a draft or tag already exists, inspect the failure and increase the
packaging revision in `packaging/source.json`. Merging it starts a new release
without changing previous assets.

If publication succeeded but the cask update failed, fix the reported problem
and recover the cask from the published metadata:

```sh
git switch -c update-hermes-light-cask origin/main
make cask
git add Casks/hermes-light.rb
git commit -m "Update Hermes Light cask"
git push --set-upstream origin HEAD
gh pr create --base main --fill
```

Review and merge the cask PR; verify installation after merging. An existing
published release does not need a rebuild or a packaging revision bump just
to recover its cask. Do not rerun the full release workflow for that case.
GitHub's normal workflow failure notifications report problems.

## Local commands

```sh
make update TAG=vX.Y.Z       # resolve and review a stable upstream pin
make test
git add packaging/source.json
git commit -m "Update upstream Hermes version"
git push                    # release starts after this reaches main
```

Use `REVISION=N` with the current tag for a packaging-only rebuild. The first
cloud release uses revision 2 of the existing upstream version.

`make release` builds, notarizes, and publishes using the existing Keychain
identity and `hermes-light-notary` profile. `make build`, `make notarize`, and
`make publish` run those stages separately. Pass `--keychain` to the release
CLI when selecting a different Keychain.

`make cask` regenerates the cask from published metadata without downloading
the app. `make clean` removes managed build files but preserves the final ZIP,
public manifest, and external source checkouts. The upstream app identity
changes with its source commit; verify gateway reconnection after upgrading.
