"""Release publication, signing cleanup, and upstream-update boundaries."""
import base64
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import light_client_release as release
import ci_release as ci


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = release.read_config()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.app = self.work / 'candidate' / f"Hermes Light {self.config['commit'][:7]}.app"
        resources = self.app / 'Contents/Resources'
        resources.mkdir(parents=True)
        (resources / 'install-stamp.json').write_text(json.dumps({
            'payload': 'light', 'commit': self.config['commit'], 'updateMechanism': 'external'}))
        import plistlib
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.nousresearch.hermes-light-' + self.config['commit'][:7],
            'CFBundleShortVersionString': self.config['version'], 'CFBundleExecutable': 'Hermes Light'}))
        binary = self.app / 'Contents/MacOS/Hermes Light'
        binary.parent.mkdir()
        binary.write_bytes(b'fixture')
        self.manifest = {'config': self.config, 'app': str(self.app), 'source': str(self.work / 'source'),
                         'bundle_identifier': 'com.nousresearch.hermes-light-' + self.config['commit'][:7],
                         'app_version': self.config['version'], 'notarization_status': 'not_submitted',
                         'packaging_commit': 'b' * 40}

    def test_notarization_waits_and_rejects_unaccepted_submissions(self):
        release.write_json(self.work / 'candidate.json', self.manifest)
        prepared = self.work / 'source/.build/desktop-job/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text(json.dumps({'node': 'node'}))
        for status in ('Invalid', 'Accepted'):
            calls = []
            def command(*args, **kwargs):
                calls.append(args)
                if args[0] == 'ditto': Path(args[-1]).write_bytes(b'signed archive')
                return SimpleNamespace(stdout=json.dumps({'id': 'submission-id', 'status': status}))
            with self.subTest(status=status), patch.object(release, 'require_mac'), \
                    patch.object(release, 'select_identity', return_value='b'*40), \
                    patch.object(release, 'run', side_effect=command):
                if status == 'Invalid':
                    with self.assertRaisesRegex(ValueError, 'Invalid'):
                        release.notarize(self.config, self.work, 'fixture', None, Path('ci.keychain'))
                    self.assertFalse((self.work / 'release.json').exists())
                    self.assertFalse(any(args[:2] == ('xcrun', 'stapler') for args in calls))
                else:
                    release.notarize(self.config, self.work, 'fixture', None, Path('ci.keychain'))
                    manifest = json.loads((self.work / 'release.json').read_text())
                    release.validate_release(manifest, self.config)
                submit = next(args for args in calls if args[:3] == ('xcrun', 'notarytool', 'submit'))
                self.assertIn('--wait', submit)
                self.assertIn('ci.keychain', submit)

    def test_existing_release_or_draft_stops_before_build_or_upload(self):
        for draft in (False, True):
            with self.subTest(draft=draft), patch.object(release, 'require_mac'), \
                    patch.object(release, 'assert_current_pin'), \
                    patch.object(release, 'get_release', return_value={'isDraft': draft}), \
                    patch.object(release, 'run') as command, patch.object(release, 'build') as build:
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    release.release_all(self.config, self.work, self.work/'source', 'fixture', None)
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    release.publish(self.config, self.work)
                build.assert_not_called()
                command.assert_not_called()

    def test_existing_tag_without_a_release_also_stops(self):
        with patch.object(release, 'assert_current_pin'), patch.object(release, 'get_release', return_value=None), \
                patch.object(release, 'run', return_value=SimpleNamespace(stdout='b'*40+'\trefs/tags/fixture\n')):
            with self.assertRaisesRegex(ValueError, 'tag already exists'):
                release.assert_new_release(self.config)

    def test_api_failure_is_not_treated_as_a_missing_release(self):
        with patch.object(release.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='connection failed')):
            with self.assertRaisesRegex(ValueError, 'API failure'):
                release.get_release(self.config)

    def test_draft_is_published_only_after_all_uploaded_digests_match(self):
        archive = self.work / release.archive_name(self.config)
        archive.write_bytes(b'signed archive')
        manifest = dict(self.manifest, notarization_status='Accepted', notarization_id='submission-id',
                        archive=str(archive), sha256=release.checksum(archive))
        release.write_json(self.work / 'release.json', manifest)
        for tampered in (True, False):
            calls, assets = [], []
            created = False
            def existing(config):
                return {'isDraft': True, 'assets': assets} if created else None
            def command(*args, **kwargs):
                nonlocal created
                calls.append(args)
                if args[:3] == ('gh', 'repo', 'view'):
                    return SimpleNamespace(stdout='{"isPrivate":false}')
                if args[:3] == ('gh', 'release', 'create'): created = True
                if args[:3] == ('gh', 'release', 'upload'):
                    for path in map(Path, args[4:6]):
                        digest = '0'*64 if tampered and path == archive else release.checksum(path)
                        assets.append({'name': path.name, 'digest': 'sha256:'+digest})
                return SimpleNamespace(stdout='')
            with self.subTest(tampered=tampered), patch.object(release, 'ROOT', self.root), \
                    patch.object(release, 'require_mac'), patch.object(release, 'assert_current_pin'), \
                    patch.object(release, 'get_release', side_effect=existing), \
                    patch.object(release, 'run', side_effect=command):
                if tampered:
                    with self.assertRaisesRegex(ValueError, 'different digest'):
                        release.publish(self.config, self.work)
                    self.assertFalse((self.root/'Casks/hermes-light.rb').exists())
                else:
                    release.publish(self.config, self.work)
                    self.assertIn(manifest['sha256'], (self.root/'Casks/hermes-light.rb').read_text())
            published = [args for args in calls if args[:3] == ('gh', 'release', 'edit')]
            self.assertEqual(bool(published), not tampered)
            create = next(args for args in calls if args[:3] == ('gh', 'release', 'create'))
            self.assertEqual(create[create.index('--target')+1], manifest['packaging_commit'])
            self.assertIn('--draft', create)
            self.assertFalse(any('--clobber' in args for args in calls))

    def test_failed_import_or_notarization_still_cleans_signing_credentials(self):
        for stage in ('setup', 'notarize'):
            with self.subTest(stage=stage), \
                    patch.object(ci, 'signing_setup', side_effect=ValueError('setup failed') if stage=='setup' else None,
                                 return_value=(Path('ci.keychain'), 'identity')), \
                    patch.object(release, 'notarize', side_effect=ValueError('notarize failed')), \
                    patch.object(ci, 'signing_cleanup') as cleanup:
                with self.assertRaises(ValueError):
                    ci.sign(self.config, self.work)
                cleanup.assert_called_once()

    def test_signing_files_are_removed_even_if_keychain_deletion_fails(self):
        directory = self.root/'hermes-signing'
        directory.mkdir()
        (directory/'release.keychain-db').write_bytes(b'fixture')
        (directory/'identity.p12').write_bytes(b'fixture')
        with patch.dict('os.environ', RUNNER_TEMP=str(self.root)), \
                patch.object(ci, 'secure', side_effect=ValueError('delete failed')):
            with self.assertRaises(ValueError): ci.signing_cleanup()
        self.assertFalse(directory.exists())

    def test_cleanup_attempts_every_stage_after_an_earlier_failure(self):
        for stage in ('signing', 'smoke'):
            self.work.mkdir(exist_ok=True)
            with self.subTest(stage=stage), patch.object(release, 'DEFAULT_WORK', self.work), \
                    patch.object(ci, 'signing_cleanup', side_effect=ValueError('failed') if stage=='signing' else None), \
                    patch.object(ci, 'smoke_cleanup', side_effect=ValueError('failed') if stage=='smoke' else None) as smoke:
                with self.assertRaises(ValueError): ci.clean(self.config)
                smoke.assert_called_once()
            self.assertFalse(self.work.exists())

    def test_failed_homebrew_verification_removes_only_its_app_and_cache(self):
        tap = self.root / 'tap'
        (tap / 'Casks').mkdir(parents=True)
        (self.root / 'Casks').mkdir()
        (self.root / 'Casks/hermes-light.rb').write_text('fixture')
        unrelated = self.root / 'unrelated-cache'
        unrelated.mkdir()
        calls = []

        def command(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('brew', '--repository'):
                return SimpleNamespace(stdout=str(tap))
            if args[:2] == ('brew', 'install'):
                cache = Path(kwargs['env']['HOMEBREW_CACHE'])
                cache.mkdir(parents=True)
                (cache / 'download.zip').write_bytes(b'fixture')
                appdir = Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--appdir=')))
                (appdir / 'Hermes Light.app').mkdir(parents=True)
            return SimpleNamespace(stdout='')

        with (
            patch.dict('os.environ', RUNNER_TEMP=str(self.root)),
            patch.object(release, 'ROOT', self.root),
            patch.object(release, 'DEFAULT_WORK', self.work),
            patch.object(release, 'run', side_effect=command),
            patch.object(release, 'verify_app', side_effect=ValueError('verification failed')),
            patch.object(ci.subprocess, 'run', return_value=SimpleNamespace(returncode=0)),
        ):
            with self.assertRaisesRegex(ValueError, 'verification failed'):
                ci.smoke(self.config)
            ci.clean(self.config)

        self.assertFalse((self.root / 'hermes-homebrew').exists())
        self.assertFalse(self.work.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(any(args[:2] == ('brew', 'uninstall') for args in calls))

    def test_homebrew_cleanup_removes_files_even_if_uninstall_fails(self):
        directory = self.root / 'hermes-homebrew'
        (directory / 'cache').mkdir(parents=True)
        (directory / 'cache/download.zip').write_bytes(b'fixture')
        with (
            patch.dict('os.environ', RUNNER_TEMP=str(self.root)),
            patch.object(ci.subprocess, 'run', return_value=SimpleNamespace(returncode=0)),
            patch.object(release, 'run', side_effect=subprocess.CalledProcessError(1, 'brew')),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                ci.smoke_cleanup(self.config)
        self.assertFalse(directory.exists())

    def test_main_pin_advance_prevents_stale_publication(self):
        newer = dict(self.config, revision=self.config['revision']+1)
        response = {'content': base64.b64encode(json.dumps(newer).encode()).decode()}
        with patch.object(release, 'run', return_value=SimpleNamespace(stdout=json.dumps(response))):
            with self.assertRaisesRegex(ValueError, 'stale'):
                release.assert_current_pin(self.config)

    def test_downgrade_is_rejected_before_network_or_pin_write(self):
        with patch.object(release, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'downgrade'):
                release.update_source(self.config, 'v0.0.0', destination=self.root/'config.json')
            command.assert_not_called()



if __name__ == '__main__': unittest.main()
