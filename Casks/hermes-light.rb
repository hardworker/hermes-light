cask "hermes-light" do
  version "0.22.5,2"
  sha256 "74476adfffd1baf6275da3ab9062487fe3a79fd0a7b46ff9f871320ac5c97de0"

  url "https://github.com/hardworker/hermes-light/releases/download/hermes-light-v0.22.5-r2/HermesLight-0.22.5-r2-macos-arm64.zip"
  name "Hermes Light"
  desc "Remote Hermes client without a local agent runtime"
  homepage "https://github.com/hardworker/hermes-light"

  depends_on arch: :arm64
  depends_on macos: :ventura

  app "Hermes Light bf0203a.app", target: "Hermes Light.app"

  uninstall quit: "com.nousresearch.hermes-light-bf0203a"

  caveats "Independent Light build. Updates are distributed through this Homebrew tap."
end
