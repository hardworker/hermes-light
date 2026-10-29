"""Release boundary checks for the independently packaged Light client."""
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("light_release", ROOT / "scripts/light_client_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class LightReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = release.read_config()
        self.app = self.root / "Hermes Light bf0203a.app"
        resources = self.app / "Contents/Resources"
        resources.mkdir(parents=True)
        self.stamp = resources / "install-stamp.json"
        self.stamp.write_text(json.dumps({"payload": "light", "commit": self.config["commit"],
                                          "updateMechanism": "external"}))
        binary = self.app / "Contents/MacOS/Hermes Light"
        binary.parent.mkdir()
        binary.write_bytes(b"fixture")
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.nousresearch.hermes-light-" + self.config["commit"][:7],
            "CFBundleExecutable": binary.name, "CFBundleShortVersionString": self.config["version"]}))

    def test_only_pinned_light_app_with_external_updates_is_accepted(self):
        release.inspect_app(self.app, self.config)
        for changed in [{"payload": "bundled"}, {"payload": "bootstrap"},
                        {"commit": "a" * 40}, {"updateMechanism": "self"}]:
            data = {"payload": "light", "commit": self.config["commit"], "updateMechanism": "external"}
            self.stamp.write_text(json.dumps({**data, **changed}))
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                release.inspect_app(self.app, self.config)

    def test_agent_payload_is_rejected_even_with_a_light_stamp(self):
        (self.app / "Contents/Resources/agent-payload").mkdir()
        with self.assertRaisesRegex(ValueError, "local agent payload"):
            release.inspect_app(self.app, self.config)

    def manifest(self):
        archive = self.root / "HermesLight-0.22.5-r1-macos-arm64.zip"
        archive.write_bytes(b"signed release fixture")
        return {"config": self.config, "notarization_status": "Accepted", "archive": str(archive),
                "sha256": release.checksum(archive), "app": str(self.app),
                "bundle_identifier": "com.nousresearch.hermes-light-bf0203a"}

    def test_cask_requires_apple_acceptance_and_exact_archive_bytes(self):
        manifest = self.manifest()
        cask = release.cask_text(manifest, self.config)
        self.assertIn(manifest["sha256"], cask)
        self.assertIn('https://github.com/hardworker/homebrew-hermes-light/releases/download/hermes-light-v0.22.5-r1/', cask)
        self.assertIn('target: "Hermes Light.app"', cask)
        self.assertNotIn("no_check", cask)
        self.assertNotIn("DownloadStrategy", cask)
        self.assertNotIn("Bearer", cask)
        self.assertNotIn("HOMEBREW_GITHUB_API_TOKEN", cask)
        for status in ["not_submitted", "In Progress", "Invalid", "Rejected"]:
            with self.subTest(status=status), self.assertRaises(ValueError):
                release.cask_text({**manifest, "notarization_status": status}, self.config)
        Path(manifest["archive"]).write_bytes(b"modified after approval")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            release.cask_text(manifest, self.config)

    def test_wrong_release_cannot_reuse_an_accepted_manifest(self):
        manifest = self.manifest()
        with self.assertRaises(ValueError):
            release.validate_release(manifest, {**self.config, "revision": 2})


if __name__ == "__main__":
    unittest.main()
