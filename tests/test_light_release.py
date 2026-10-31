"""Release boundary checks for the independently packaged Light client."""
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

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
        self.app = self.root / f"Hermes Light {self.config['commit'][:7]}.app"
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

    def test_candidate_records_the_built_app_and_includes_the_upstream_license(self):
        source = self.root / "source"
        source.mkdir()
        (source / "LICENSE").write_text("upstream license")
        work = self.root / "work"
        with patch.object(release, "run") as command:
            command.return_value.stdout = "b" * 40
            release.record_candidate(self.config, work, source, self.app)
        manifest = json.loads((work / "candidate.json").read_text())
        self.assertEqual(manifest["app"], str(self.app.resolve()))
        self.assertEqual(manifest["packaging_commit"], "b" * 40)
        resources = self.app / "Contents/Resources"
        self.assertEqual((resources / "Hermes-UPSTREAM-LICENSE").read_text(), "upstream license")
        self.assertIn(self.config["commit"], (resources / "INDEPENDENT-DISTRIBUTION.txt").read_text())

    def manifest(self):
        archive = self.root / release.archive_name(self.config)
        archive.write_bytes(b"signed release fixture")
        return {"config": self.config, "notarization_status": "Accepted", "archive": str(archive),
                "sha256": release.checksum(archive), "app": str(self.app),
                "bundle_identifier": "com.nousresearch.hermes-light-" + self.config["commit"][:7]}

    def test_cask_requires_apple_acceptance_and_exact_archive_bytes(self):
        manifest = self.manifest()
        cask = release.cask_text(manifest, self.config)
        self.assertIn(manifest["sha256"], cask)
        self.assertIn(f"https://github.com/{self.config['distribution_repository']}/releases/download/{release.release_tag(self.config)}/", cask)
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
            release.validate_release(manifest, {**self.config, "revision": self.config["revision"] + 1})

    def test_update_peels_annotated_tag_and_resets_packaging_revision(self):
        destination = self.root / "source.json"
        commit = "a" * 40
        refs = f"{'b' * 40}\trefs/tags/v99.0.0\n{commit}\trefs/tags/v99.0.0^{{}}\n"
        with patch.object(release, "run") as command:
            command.return_value.stdout = refs
            updated = release.update_source(self.config, "v99.0.0", destination=destination)
        self.assertEqual((updated["commit"], updated["version"], updated["revision"]), (commit, "99.0.0", 1))
        self.assertEqual(json.loads(destination.read_text()), updated)
        self.assertNotEqual(release.release_tag(updated), release.release_tag(self.config))

    def test_missing_or_moved_upstream_tag_does_not_change_pin(self):
        destination = self.root / "source.json"
        destination.write_text(json.dumps(self.config))
        original = destination.read_bytes()
        for refs in ("", f"{'f' * 40}\trefs/tags/{self.config['tag']}\n"):
            with self.subTest(refs=refs), patch.object(release, "run") as command:
                command.return_value.stdout = refs
                with self.assertRaises(ValueError):
                    release.update_source(self.config, self.config["tag"], destination=destination)
            self.assertEqual(destination.read_bytes(), original)

    def test_resume_candidate_does_not_rebuild_or_overwrite_it(self):
        work = self.root / "work"
        work.mkdir()
        manifest = {"config": self.config, "app": str(self.app), "notarization_status": "In Progress"}
        (work / "candidate.json").write_text(json.dumps(manifest))
        with patch.object(release, "require_mac"), patch.object(release, "run") as command:
            release.build(self.config, work, self.root / "not-downloaded")
            command.assert_not_called()
        self.assertEqual(json.loads((work / "candidate.json").read_text()), manifest)

    def test_cask_can_be_recovered_after_cleanup_and_rejects_wrong_remote_digest(self):
        manifest = self.manifest()
        public = {key: manifest[key] for key in ("config", "notarization_status", "sha256", "bundle_identifier")}
        (self.root / "Casks").mkdir()
        Path(manifest["archive"]).unlink()
        published = {"isDraft": False, "assets": [{"name": release.archive_name(self.config),
                                                   "digest": "sha256:" + manifest["sha256"]}]}
        from types import SimpleNamespace
        with patch.object(release, "ROOT", self.root), patch.object(release, "run") as command:
            command.side_effect = [SimpleNamespace(stdout=json.dumps(published)),
                                   SimpleNamespace(stdout=json.dumps(public))]
            release.sync_cask(self.config)
            destination = self.root / "Casks/hermes-light.rb"
            original = destination.read_bytes()
            published["assets"][0]["digest"] = "sha256:" + "0" * 64
            command.side_effect = [SimpleNamespace(stdout=json.dumps(published)),
                                   SimpleNamespace(stdout=json.dumps(public))]
            with self.assertRaisesRegex(ValueError, "digest"):
                release.sync_cask(self.config)
            self.assertEqual(destination.read_bytes(), original)

    def test_cleanup_keeps_release_and_credentials_and_does_not_follow_source_symlinks(self):
        work = self.root / "work"
        work.mkdir()
        external = self.root / "external"
        external.mkdir()
        key = external / "signing.key"
        key.write_bytes(b"fixture")
        (work / "source").symlink_to(external, target_is_directory=True)
        archive = work / release.archive_name(self.config)
        archive.write_bytes(b"release fixture")
        (work / "notary-upload.zip").write_bytes(b"temporary fixture")
        release.clean_work(work)
        self.assertTrue(archive.exists())
        self.assertTrue(key.exists())
        self.assertFalse((work / "source").is_symlink())
        self.assertFalse((work / "notary-upload.zip").exists())


if __name__ == "__main__":
    unittest.main()
