"""Exercise retry boundaries, publication immutability, and workflow isolation."""
import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import light_client_release as release
import ci_release as ci
import upstream_update as upstream
import setup_ci as setup


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
                         'app_version': self.config['version'], 'notarization_status': 'In Progress',
                         'notarization_id': str(uuid.uuid4()), 'packaging_commit': 'b' * 40}

    def zip_app(self, name):
        path = self.work / name
        with zipfile.ZipFile(path, 'w') as archive:
            for p in self.app.rglob('*'):
                if p.is_file(): archive.write(p, p.relative_to(self.app.parent))
        return path

    def test_checkpoint_is_portable_and_restores_the_same_submission(self):
        upload = self.zip_app('notary-upload.zip')
        self.manifest['upload_sha256'] = release.checksum(upload)
        release.write_json(self.work / 'candidate.json', self.manifest)
        checkpoint = self.root / 'checkpoint'
        release.export_checkpoint(self.config, self.work, checkpoint)
        text = (checkpoint / 'checkpoint.json').read_text()
        self.assertNotIn(str(self.root), text)
        def command(*args, **kwargs):
            if args[0] == 'ditto':
                with zipfile.ZipFile(args[3]) as archive: archive.extractall(args[4])
            return SimpleNamespace(stdout='')
        restored = self.root / 'restored'
        with patch.object(release, 'run', side_effect=command):
            release.restore_checkpoint(self.config, restored, checkpoint)
        actual = json.loads((restored / 'candidate.json').read_text())
        self.assertEqual(actual['notarization_id'], self.manifest['notarization_id'])
        self.assertEqual(actual['upload_sha256'], self.manifest['upload_sha256'])
        (checkpoint / 'notary-upload.zip').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            release.restore_checkpoint(self.config, self.root / 'wrong', checkpoint)

    def test_checkpoint_rejects_path_and_symlink_escape(self):
        app = self.app.name
        for name, link in [('../escape', None), (app + '/escape', '/etc/passwd')]:
            archive = self.root / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as zipped:
                if link:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = 0o120777 << 16
                    zipped.writestr(info, link)
                else: zipped.writestr(name, 'fixture')
            with self.subTest(name=name), self.assertRaises(ValueError):
                release.validate_zip(archive, app)

    def test_final_checkpoint_keeps_exact_published_archive_bytes(self):
        archive = self.zip_app(release.archive_name(self.config))
        manifest = dict(self.manifest, notarization_status='Accepted', archive=str(archive), sha256=release.checksum(archive))
        release.write_json(self.work/'release.json', manifest)
        checkpoint = self.root/'final-checkpoint'
        release.export_checkpoint(self.config, self.work, checkpoint)
        def command(*args, **kwargs):
            if args[0] == 'ditto':
                with zipfile.ZipFile(args[3]) as zipped: zipped.extractall(args[4])
            return SimpleNamespace(stdout='')
        destination=self.root/'final-restored'
        with patch.object(release,'run',side_effect=command):
            release.restore_checkpoint(self.config,destination,checkpoint)
        restored=json.loads((destination/'release.json').read_text())
        self.assertEqual(release.checksum(Path(restored['archive'])),manifest['sha256'])
        with patch.object(release,'get_release',return_value={'isDraft':True}), \
                patch.object(release,'publish') as publish, patch.object(release,'build') as build, \
                patch.object(release,'notarize') as notarize:
            release.release_all(self.config,destination,destination/'source','fixture',None)
            publish.assert_called_once()
            build.assert_not_called()
            notarize.assert_not_called()

    def test_submit_only_persists_id_without_waiting(self):
        self.manifest['notarization_status'] = 'not_submitted'
        release.write_json(self.work / 'candidate.json', self.manifest)
        prepared = self.work / 'source/.build/desktop-job/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text(json.dumps({'node': 'node'}))
        def command(*args, **kwargs):
            if args[0] == 'ditto': Path(args[-1]).write_bytes(b'signed upload')
            result = {'id': self.manifest['notarization_id'], 'status': 'In Progress'}
            return SimpleNamespace(stdout=json.dumps(result))
        with patch.object(release, 'require_mac'), patch.object(release, 'select_identity', return_value='b'*40), \
                patch.object(release, 'run', side_effect=command), patch.object(release.subprocess, 'run') as waiting:
            release.notarize(self.config, self.work, 'fixture', None, Path('ci.keychain'), submit_only=True)
            waiting.assert_not_called()
        saved = json.loads((self.work / 'candidate.json').read_text())
        self.assertEqual(saved['notarization_id'], self.manifest['notarization_id'])
        self.assertIn('upload_sha256', saved)

    def test_wait_timeout_keeps_saved_submission_and_needs_no_source_cache(self):
        release.write_json(self.work / 'candidate.json', self.manifest)
        with patch.object(release, 'require_mac'), patch.object(release.subprocess, 'run',
                return_value=SimpleNamespace(stdout='', returncode=1)):
            with self.assertRaisesRegex(ValueError, 'submission is saved'):
                release.notarize(self.config, self.work, 'fixture', None)
        self.assertEqual(json.loads((self.work / 'candidate.json').read_text())['notarization_id'],
                         self.manifest['notarization_id'])

    def test_partial_draft_uploads_only_missing_matching_assets(self):
        archive = self.zip_app(release.archive_name(self.config))
        manifest = dict(self.manifest, notarization_status='Accepted', archive=str(archive), sha256=release.checksum(archive))
        release.write_json(self.work / 'release.json', manifest)
        (self.root / 'Casks').mkdir()
        assets = {archive.name: {'name': archive.name, 'digest': 'sha256:' + manifest['sha256']}}
        calls = []
        def existing(config):
            return {'isDraft': True, 'targetCommitish': 'b'*40, 'assets': list(assets.values())}
        def command(*args, **kwargs):
            calls.append(args)
            if args[:3] == ('gh', 'repo', 'view'): return SimpleNamespace(stdout='{"isPrivate":false}')
            if args[:3] == ('gh', 'release', 'upload'):
                path = Path(args[4]); assets[path.name] = {'name': path.name, 'digest': 'sha256:'+release.checksum(path)}
            return SimpleNamespace(stdout='')
        with patch.object(release, 'ROOT', self.root), patch.object(release, 'require_mac'), \
                patch.object(release, 'assert_current_pin'), patch.object(release, 'get_release', side_effect=existing), \
                patch.object(release, 'run', side_effect=command):
            release.publish(self.config, self.work, 'b'*40)
        uploaded = [args for args in calls if args[:3] == ('gh','release','upload')]
        self.assertEqual(len(uploaded), 1)
        self.assertEqual(Path(uploaded[0][4]).name, 'release-manifest.json')
        self.assertTrue(any(args[:3] == ('gh','release','edit') and '--draft=false' in args for args in calls))
        self.assertFalse(any('--clobber' in args for args in calls))

    def test_bad_existing_asset_cannot_be_published(self):
        archive = self.root / 'release.zip'; archive.write_bytes(b'fixture')
        wrong = {'assets': [{'name': archive.name, 'digest': 'sha256:'+'0'*64}]}
        with self.assertRaisesRegex(ValueError, 'will not be replaced'):
            release.verify_assets(wrong, [archive])

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

    def test_update_pr_deduplicates_without_writing(self):
        newer = dict(self.config, version='99.0.0', tag='v99.0.0', commit='c'*40, revision=1)
        def github(path, **kwargs):
            if path.endswith('git/ref/heads/main'): return {'object':{'sha':'b'*40}}
            if path.endswith('releases/latest'): return {'draft':False,'prerelease':False,'tag_name':'v99.0.0'}
            raise AssertionError('Unexpected write or API call')
        def command(*args, **kwargs):
            if args[:3] == ('gh','pr','list'): return SimpleNamespace(stdout='[{"number":3,"state":"OPEN"}]')
            return SimpleNamespace(stdout='packaging/source.json\n')
        with patch.object(upstream,'api',side_effect=github), patch.object(upstream,'config_at',side_effect=[self.config,newer]), \
                patch.object(upstream,'optional_api',return_value={'object':{'sha':'d'*40}}), \
                patch.object(release,'update_source',side_effect=[self.config,newer]), patch.object(release,'run',side_effect=command):
            upstream.update()

    def test_release_workflow_is_main_only_serial_and_always_cleans(self):
        text = (ROOT/'.github/workflows/release.yml').read_text()
        self.assertIn("if: github.ref == 'refs/heads/main'", text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertIn('environment: release', text)
        self.assertIn('runs-on: xcode-27', text)
        self.assertNotIn('pull_request_target', text)
        self.assertIn('if: always()', text)
        self.assertIn('retention-days: 7', text)
        self.assertLess(text.index('Build unsigned'), text.index('Import signing credentials'))

    def test_artifact_recovery_excludes_fork_and_nonrelease_workflow_runs(self):
        tag=release.release_tag(self.config)
        records=[{'id':i,'name':f'hermes-checkpoint-{tag}-final-{i}-1','expired':False,
                  'workflow_run':{'id':i}} for i in (1,2,3)]
        def github(path, **kwargs):
            run_id=int(path.rsplit('/',1)[-1])
            return {'path':'.github/workflows/release.yml' if run_id!=2 else '.github/workflows/validate.yml',
                    'head_branch':'main','event':'push',
                    'head_repository':{'full_name':self.config['distribution_repository'] if run_id!=3 else 'example/fork'}}
        with patch.object(ci,'artifacts',return_value=records),patch.object(ci,'api',side_effect=github):
            self.assertEqual([a['id'] for a in ci.trusted_artifacts(self.config)],[1])

    def test_recovery_password_is_never_uploaded_to_github(self):
        private=self.root/'private'
        (private/'apple-signing').mkdir(parents=True)
        for name in ('developerID_application.cer','developer-id-private.pem'):
            (private/'apple-signing'/name).write_bytes(b'fixture')
        password='recovery_'+'z'*24
        app_password='x'*4+('-'+'y'*4)*3
        uploaded={}
        exports=[]
        def command(*args,**kwargs):
            if '-subject' in args: return 'subject=CN=Fixture,OU='+'A'*10+',O=Example'
            if '-fingerprint' in args: return 'SHA1 Fingerprint='+'B'*40
            if args[:3]==('gh','secret','set'): uploaded[args[3]]=kwargs['input']
            return ''
        def export(destination,chosen_password,*args):
            exports.append(chosen_password)
            destination.write_bytes(b'encrypted certificate fixture')
        with patch.object(setup,'PRIVATE',private),patch.object(setup,'command',side_effect=command), \
                patch.object(setup,'export_identity',side_effect=export),patch.object(setup,'verify_backup'):
            setup.provision(password,'fixture@example.com',app_password)
        self.assertEqual(exports[0],password)
        self.assertNotEqual(uploaded['MACOS_SIGNING_P12_PASSWORD'],password)
        self.assertEqual(exports[1],uploaded['MACOS_SIGNING_P12_PASSWORD'])
        self.assertNotIn(password,uploaded.values())

    def test_ci_setup_does_not_require_a_recovery_password(self):
        private=self.root/'ci-only'
        (private/'apple-signing').mkdir(parents=True)
        for name in ('developerID_application.cer','developer-id-private.pem'):
            (private/'apple-signing'/name).write_bytes(b'fixture')
        uploaded={}
        def command(*args,**kwargs):
            if '-subject' in args: return 'subject=CN=Fixture,OU='+'A'*10+',O=Example'
            if '-fingerprint' in args: return 'SHA1 Fingerprint='+'B'*40
            if args[:3]==('gh','secret','set'): uploaded[args[3]]=kwargs['input']
            return ''
        def export(destination,*args): destination.write_bytes(b'encrypted CI fixture')
        with patch.object(setup,'PRIVATE',private),patch.object(setup,'command',side_effect=command), \
                patch.object(setup,'export_identity',side_effect=export),patch.object(setup,'verify_backup'), \
                patch.object(setup,'backup_identity') as backup:
            setup.provision(None,'fixture@example.com','x'*4+('-'+'y'*4)*3)
            backup.assert_not_called()
        self.assertEqual(len(uploaded),5)
        self.assertFalse((private/'recovery/hermes-light-signing.p12').exists())

    def test_keychain_export_selects_one_identity_and_keeps_password_off_arguments(self):
        calls=[]
        def command(*args,**kwargs):
            calls.append((args,kwargs))
            return ''
        password='fixture_'+'z'*24
        with patch.object(setup,'keychain_identity',return_value=('B'*40,'Developer ID Application: Fixture')), \
                patch.object(setup,'command',side_effect=command):
            setup.export_identity(self.root/'identity.p12',password,self.root,None)
        request=json.loads(calls[-1][1]['input'])
        self.assertEqual(request['fingerprint'],'B'*40)
        self.assertEqual(request['password'],password)
        self.assertTrue(all(password not in str(args) for args,kwargs in calls))

    def test_default_signing_paths_stay_inside_the_project(self):
        self.assertTrue(release.SIGNING.is_relative_to(release.ROOT))
        self.assertTrue(setup.PRIVATE.is_relative_to(setup.ROOT))


if __name__ == '__main__': unittest.main()
