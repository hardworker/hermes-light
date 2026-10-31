"""Exercise updater and cask publication against a real local Git remote."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import ci_release as ci
import light_client_release as release
import upstream_update as upstream


class WorkflowGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / 'origin.git'
        self.checkout = self.root / 'checkout'
        self.config = release.read_config()
        self.updated = dict(
            self.config, version='99.0.0', tag='v99.0.0', commit='c' * 40, revision=1
        )
        self.pulls = []
        self.changed_files = ['packaging/source.json']
        self.pr_writes = []
        self.before_push = None

        self.git('init', '--bare', '--initial-branch=main', str(self.remote), cwd=self.root)
        self.git('init', '--initial-branch=main', str(self.checkout), cwd=self.root)
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.com')
        self.git('config', 'commit.gpgsign', 'false')
        release.write_json(self.checkout / 'packaging/source.json', self.config)
        (self.checkout / 'Casks').mkdir()
        (self.checkout / 'Casks/hermes-light.rb').write_text('original cask\n')
        (self.checkout / 'README.md').write_text('original readme\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Base')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '--set-upstream', 'origin', 'main')

        self.original_run = release.run
        for target, value in (
            ('ROOT', self.checkout),
            ('CONFIG', self.checkout / 'packaging/source.json'),
            ('run', self.command),
        ):
            patcher = patch.object(release, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        resolver = patch.object(release, 'resolve_source', side_effect=self.resolve_source)
        resolver.start()
        self.addCleanup(resolver.stop)

    def git(self, *args, cwd=None):
        return subprocess.check_output(
            ['git', *args], cwd=cwd or self.checkout, text=True, stderr=subprocess.STDOUT
        ).strip()

    def resolve_source(self, config, tag):
        return config if tag == config['tag'] else self.updated

    def command(self, *args, **kwargs):
        if args[0] != 'gh':
            if args[:2] == ('git', 'push') and self.before_push:
                self.before_push()
            return self.original_run(*args, **dict(kwargs, capture=True))
        if args[1:3] == ('api', 'repos/NousResearch/hermes-agent/releases/latest'):
            result = {'draft': False, 'prerelease': False, 'tag_name': self.updated['tag']}
            return SimpleNamespace(stdout=json.dumps(result))
        if args[1:3] == ('pr', 'list'):
            return SimpleNamespace(stdout=json.dumps(self.pulls))
        if args[1:3] == ('pr', 'diff'):
            return SimpleNamespace(stdout='\n'.join(self.changed_files) + '\n')
        if args[1:3] in (('pr', 'create'), ('pr', 'edit')):
            self.pr_writes.append(args)
        elif args[1:3] != ('auth', 'setup-git'):
            raise AssertionError(f'Unexpected GitHub command: {args[:3]}')
        return SimpleNamespace(stdout='')

    def remote_head(self, branch):
        return self.git('--git-dir', str(self.remote), 'rev-parse', branch)

    def remote_file(self, branch, path):
        return self.git('--git-dir', str(self.remote), 'show', f'{branch}:{path}')

    def seed_update(self, config=None):
        self.git('switch', '-c', upstream.BRANCH)
        release.write_json(self.checkout / 'packaging/source.json', config or self.updated)
        self.git('commit', '-am', 'Previous update')
        self.git('push', 'origin', upstream.BRANCH)
        self.git('switch', 'main')

    def test_create_and_update_pr_change_only_the_pin(self):
        upstream.update()
        self.assertEqual(json.loads(self.remote_file(upstream.BRANCH, 'packaging/source.json')), self.updated)
        self.assertEqual(self.git('diff', self.base, 'HEAD', '--name-only'), 'packaging/source.json')
        self.assertEqual(self.pr_writes[-1][1:3], ('pr', 'create'))

        self.pulls = [{'number': 3, 'state': 'OPEN', 'mergedAt': None}]
        self.updated = dict(self.updated, version='100.0.0', tag='v100.0.0', commit='d' * 40)
        upstream.update()
        self.assertEqual(json.loads(self.remote_file(upstream.BRANCH, 'packaging/source.json')), self.updated)
        self.assertEqual(self.git('diff', self.base, 'HEAD', '--name-only'), 'packaging/source.json')
        self.assertEqual(self.pr_writes[-1][1:4], ('pr', 'edit', '3'))

    def test_current_or_declined_update_is_not_rewritten(self):
        self.seed_update()
        original = self.remote_head(upstream.BRANCH)
        for state in ('OPEN', 'CLOSED'):
            with self.subTest(state=state):
                self.pulls = [{'number': 3, 'state': state, 'mergedAt': None}]
                upstream.update()
                self.assertEqual(self.remote_head(upstream.BRANCH), original)
                self.assertFalse(self.pr_writes)
                self.assertEqual(self.git('symbolic-ref', '--short', 'HEAD'), 'main')

    def test_dry_run_preserves_checkout_and_remote(self):
        upstream.update(dry_run=True)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)
        self.assertFalse(self.git('status', '--porcelain'))
        self.assertFalse(self.git('ls-remote', '--heads', 'origin', upstream.BRANCH))
        self.assertFalse(self.pr_writes)

    def test_unrelated_pr_changes_are_left_for_human_review(self):
        older = dict(self.updated, version='98.0.0', tag='v98.0.0')
        self.seed_update(older)
        original = self.remote_head(upstream.BRANCH)
        self.pulls = [{'number': 3, 'state': 'OPEN', 'mergedAt': None}]
        self.changed_files.append('README.md')
        with self.assertRaisesRegex(ValueError, 'other changes'):
            upstream.update()
        self.assertEqual(self.remote_head(upstream.BRANCH), original)
        self.assertFalse(self.pr_writes)

    def test_concurrent_bot_branch_change_is_not_overwritten(self):
        older = dict(self.updated, version='98.0.0', tag='v98.0.0')
        self.seed_update(older)
        self.pulls = [{'number': 3, 'state': 'OPEN', 'mergedAt': None}]
        self.before_push = lambda: self.git(
            '--git-dir', str(self.remote), 'update-ref', f'refs/heads/{upstream.BRANCH}', self.base
        )
        with self.assertRaises(subprocess.CalledProcessError):
            upstream.update()
        self.assertEqual(self.remote_head(upstream.BRANCH), self.base)
        self.assertFalse(self.pr_writes)

    def test_cask_commit_keeps_unrelated_main_changes(self):
        (self.checkout / 'README.md').write_text('new main documentation\n')
        self.git('commit', '-am', 'Update documentation')
        self.git('push', 'origin', 'main')
        self.git('switch', '--detach', self.base)
        (self.checkout / 'Casks/hermes-light.rb').write_text('updated cask\n')
        ci.commit_cask(self.config)
        self.assertEqual(self.remote_file('main', 'README.md'), 'new main documentation')
        self.assertEqual(self.remote_file('main', 'Casks/hermes-light.rb'), 'updated cask')
        original = self.remote_head('main')
        ci.commit_cask(self.config)
        self.assertEqual(self.remote_head('main'), original)

    def test_cask_commit_refuses_a_newer_main_pin(self):
        release.write_json(self.checkout / 'packaging/source.json', self.updated)
        self.git('commit', '-am', 'Upgrade main')
        self.git('push', 'origin', 'main')
        self.git('switch', '--detach', self.base)
        (self.checkout / 'Casks/hermes-light.rb').write_text('stale cask\n')
        with self.assertRaisesRegex(ValueError, 'another version'):
            ci.commit_cask(self.config)
        self.assertEqual(self.remote_file('main', 'Casks/hermes-light.rb'), 'original cask')


if __name__ == '__main__':
    unittest.main()
