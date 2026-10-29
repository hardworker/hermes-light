cask "hermes-light" do
  version "0.22.5,1"
  sha256 "6356a805cc710304d380ee6e10def1607a44c104c09ccd5f7edf31fe3bf110a5"

  url "https://github.com/hardworker/hermes-light/releases/download/hermes-light-v0.22.5-r1/HermesLight-0.22.5-r1-macos-arm64.zip"
  name "Hermes Light"
  desc "Remote Hermes client without a local agent runtime"
  homepage "https://github.com/hardworker/hermes-light"

  depends_on arch: :arm64
  depends_on macos: ">= :ventura"

  app "Hermes Light bf0203a.app", target: "Hermes Light.app"

  uninstall quit: "com.nousresearch.hermes-light-bf0203a"

  caveats "Independent Light build. Updates are distributed through this Homebrew tap."
end
