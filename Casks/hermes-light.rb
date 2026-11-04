cask "hermes-light" do
  version "0.22.7,1"
  sha256 "c6443a57d8d867b5e8ecf397a29e26938fe942392ccb284e94f81c0728c51e67"

  url "https://github.com/hardworker/hermes-light/releases/download/hermes-light-v0.22.7-r1/HermesLight-0.22.7-r1-macos-arm64.zip"
  name "Hermes Light"
  desc "Remote Hermes client without a local agent runtime"
  homepage "https://github.com/hardworker/hermes-light"

  depends_on arch: :arm64
  depends_on macos: :ventura

  app "Hermes Light b200f5b.app", target: "Hermes Light.app"

  uninstall quit: "com.nousresearch.hermes-light-b200f5b"

  caveats "Independent Light build. Updates are distributed through this Homebrew tap."
end
