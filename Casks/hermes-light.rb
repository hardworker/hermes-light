cask "hermes-light" do
  version "0.23.1,1"
  sha256 "0dbfd3cb10ed1fe2c6b05e1b3e26a53c4e76f7a65109b992a8bcafc82bfa77a7"

  url "https://github.com/hardworker/hermes-light/releases/download/hermes-light-v0.23.1-r1/HermesLight-0.23.1-r1-macos-arm64.zip"
  name "Hermes Light"
  desc "Remote Hermes client without a local agent runtime"
  homepage "https://github.com/hardworker/hermes-light"

  depends_on arch: :arm64
  depends_on macos: :ventura

  app "Hermes Light 3b42157.app", target: "Hermes Light.app"

  uninstall quit: "com.nousresearch.hermes-light-3b42157"

  caveats "Independent Light build. Updates are distributed through this Homebrew tap."
end
