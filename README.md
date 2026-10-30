# Hermes Light

A remote-only [Hermes Desktop](https://github.com/NousResearch/hermes-agent)
client and Homebrew tap for **Apple Silicon, macOS 13+**. Connect it to an
existing Hermes gateway; no local agent or Python runtime is bundled.

Independent distribution. Release apps are Developer ID signed, notarized by
Apple, and include a stapled ticket. [Download the latest release](https://github.com/hardworker/hermes-light/releases/latest).

## Install

```sh
brew tap hardworker/hermes-light https://github.com/hardworker/hermes-light
brew trust --cask hardworker/hermes-light/hermes-light
brew install --cask hardworker/hermes-light/hermes-light
```

Older Homebrew versions without `brew trust` can skip that line. Open
**Hermes Light**, choose **Connect to existing Hermes**, and enter your gateway.

## Upgrade

Quit Hermes Light, then run:

```sh
brew update
brew upgrade --cask hardworker/hermes-light/hermes-light
```

Homebrew owns app updates. The gateway is upgraded separately.

## Maintain

The upstream tag and exact commit are pinned in
[packaging/source.json](packaging/source.json). See
[Maintaining releases](docs/RELEASING.md) to pin a new version, build, notarize,
publish, or remove temporary build files.
