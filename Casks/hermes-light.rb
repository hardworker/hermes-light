cask "hermes-light" do
  version "0.22.5,3"
  sha256 "719e06197a931a324a094617af7852c3f56824440553dddaca406e09df0626c3"

  url "https://github.com/hardworker/hermes-light/releases/download/hermes-light-v0.22.5-r3/HermesLight-0.22.5-r3-macos-arm64.zip"
  name "Hermes Light"
  desc "Remote Hermes client without a local agent runtime"
  homepage "https://github.com/hardworker/hermes-light"

  depends_on arch: :arm64
  depends_on macos: :ventura

  app "Hermes Light bf0203a.app", target: "Hermes Light.app"

  uninstall quit: "com.nousresearch.hermes-light-bf0203a"

  caveats "Independent Light build. Updates are distributed through this Homebrew tap."
end
