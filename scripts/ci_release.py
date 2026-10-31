#!/usr/bin/env python3
"""GitHub Actions orchestration; credentials are supplied only to the steps that need them."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

import light_client_release as release


def api(path, method=None, data=None):
    args = ['gh', 'api', path]
    if method:
        args += ['--method', method]
    if data is not None:
        args += ['--input', '-']
    result = release.run(*args, input=json.dumps(data) if data is not None else None, capture=True).stdout
    return json.loads(result) if result.strip() else None


def check(config):
    if os.environ.get('CONFIGURED') != 'true':
        raise ValueError('Configure the release environment secrets manually using docs/RELEASING.md')
    release.assert_new_release(config)


def secure(*args):
    result = subprocess.run(list(map(str, args)), text=True, capture_output=True)
    if result.returncode:
        raise ValueError(f'{Path(str(args[0])).name} failed; signing arguments and output withheld')
    return result.stdout


def signing_setup():
    names = ('MACOS_SIGNING_P12_BASE64', 'MACOS_SIGNING_P12_PASSWORD', 'APPLE_NOTARY_APPLE_ID',
             'APPLE_NOTARY_TEAM_ID', 'APPLE_NOTARY_PASSWORD')
    if any(not os.environ.get(name) for name in names):
        raise ValueError('Release environment secrets are incomplete; configure them manually using docs/RELEASING.md')
    directory = Path(os.environ['RUNNER_TEMP']) / 'hermes-signing'
    directory.mkdir(mode=0o700)
    keychain = directory / 'release.keychain-db'
    password = secrets.token_urlsafe(32)
    print(f'::add-mask::{password}')
    # Only this ephemeral runner's files contain the decoded certificate.
    p12 = directory / 'identity.p12'
    p12.write_bytes(base64.b64decode(os.environ['MACOS_SIGNING_P12_BASE64'], validate=True))
    p12.chmod(0o600)
    try:
        secure('security', 'create-keychain', '-p', password, keychain)
        secure('security', 'set-keychain-settings', '-lut', '7200', keychain)
        secure('security', 'unlock-keychain', '-p', password, keychain)
        secure('security', 'import', p12, '-P', os.environ['MACOS_SIGNING_P12_PASSWORD'],
               '-k', keychain, '-T', '/usr/bin/codesign')
        intermediate = directory / 'intermediate.cer'
        secure('curl', '--fail', '--silent', '--show-error', '--proto', '=https', '--output', intermediate,
               'https://www.apple.com/certificateauthority/DeveloperIDG2CA.cer')
        secure('security', 'verify-cert', '-c', intermediate)
        secure('security', 'import', intermediate, '-k', keychain)
        secure('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:,codesign:',
               '-s', '-k', password, keychain)
        identity = release.select_identity(keychain=keychain)
        secure('xcrun', 'notarytool', 'store-credentials', 'hermes-light-ci', '--keychain', keychain,
               '--apple-id', os.environ['APPLE_NOTARY_APPLE_ID'], '--team-id', os.environ['APPLE_NOTARY_TEAM_ID'],
               '--password', os.environ['APPLE_NOTARY_PASSWORD'])
        return keychain, identity
    finally:
        p12.unlink(missing_ok=True)


def signing_cleanup():
    directory = Path(os.environ['RUNNER_TEMP']) / 'hermes-signing'
    keychain = directory / 'release.keychain-db'
    try:
        if keychain.exists():
            secure('security', 'delete-keychain', keychain)
    finally:
        if directory.exists():
            shutil.rmtree(directory)


def sign(config, work):
    try:
        keychain, identity = signing_setup()
        release.notarize(config, work, 'hermes-light-ci', identity, keychain)
    finally:
        signing_cleanup()


def commit_cask(config):
    release.assert_current_pin(config)
    content = (release.ROOT / 'Casks/hermes-light.rb').read_text()
    release.run('git', 'fetch', 'origin', 'main')
    main_config = json.loads(release.run('git', 'show', 'origin/main:packaging/source.json', capture=True).stdout)
    if main_config != config:
        raise ValueError('Main advanced to another version; refusing to roll the cask backward')
    release.run('git', 'restore', '--worktree', '--staged', 'Casks/hermes-light.rb')
    release.run('git', 'checkout', '--detach', 'origin/main')
    (release.ROOT / 'Casks/hermes-light.rb').write_text(content)
    release.run('ruby', '-c', 'Casks/hermes-light.rb')
    release.run('python3', 'scripts/check_repository.py', '--history')
    unchanged = subprocess.run(['git', 'diff', '--quiet', '--', 'Casks/hermes-light.rb']).returncode == 0
    if not unchanged:
        release.run('git', 'config', 'user.name', 'github-actions[bot]')
        release.run('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
        release.run('git', 'add', 'Casks/hermes-light.rb')
        release.run('git', 'commit', '-m', f"Release Hermes Light {config['version']} revision {config['revision']}")
        release.run('gh', 'auth', 'setup-git')
        release.run('git', 'push', 'origin', 'HEAD:main')


def smoke(config):
    (Path(os.environ['RUNNER_TEMP']) / '.hermes-smoke-started').touch()
    token = config['distribution_repository'] + '/hermes-light'
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1')
    def brew(*args, capture=False):
        return release.run('brew', *args, env=env, capture=capture)
    brew('tap', config['distribution_repository'], f"https://github.com/{config['distribution_repository']}")
    tap = Path(brew('--repository', config['distribution_repository'], capture=True).stdout.strip())
    shutil.copyfile(release.ROOT / 'Casks/hermes-light.rb', tap / 'Casks/hermes-light.rb')
    if subprocess.run(['brew', 'help', 'trust'], capture_output=True).returncode == 0:
        brew('trust', '--cask', token)
    appdir = Path(os.environ['RUNNER_TEMP']) / 'hermes-install'
    brew('install', '--cask', '--appdir=' + str(appdir), token)
    app = appdir / 'Hermes Light.app'
    release.inspect_app(app, config)
    release.run('codesign', '--verify', '--deep', '--strict', app)
    release.run('xcrun', 'stapler', 'validate', app)
    release.run('spctl', '--assess', '--type', 'execute', '--verbose', app)


def smoke_cleanup(config):
    marker = Path(os.environ['RUNNER_TEMP']) / '.hermes-smoke-started'
    if not marker.exists():
        return
    token = config['distribution_repository'] + '/hermes-light'
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1')
    cached = subprocess.run(['brew', '--cache', '--cask', token], capture_output=True, text=True, env=env)
    if cached.returncode == 0:
        path = Path(cached.stdout.strip())
        if path.name.endswith('--' + release.archive_name(config)) and 'Homebrew' in path.parts:
            path.unlink(missing_ok=True)
    installed = subprocess.run(['brew', 'list', '--cask', token], capture_output=True, env=env)
    if installed.returncode == 0:
        release.run('brew', 'uninstall', '--cask', token, env=env)
    appdir = Path(os.environ['RUNNER_TEMP']) / 'hermes-install'
    if appdir.exists():
        shutil.rmtree(appdir)
    marker.unlink(missing_ok=True)


def clean(config):
    try:
        signing_cleanup()
    finally:
        try:
            smoke_cleanup(config)
        finally:
            if release.DEFAULT_WORK.exists():
                shutil.rmtree(release.DEFAULT_WORK)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'sign', 'smoke', 'commit-cask', 'clean'])
    args = parser.parse_args()
    config = release.read_config()
    work = release.DEFAULT_WORK / release.release_tag(config)
    if args.action == 'check': check(config)
    elif args.action == 'sign': sign(config, work)
    elif args.action == 'smoke': smoke(config)
    elif args.action == 'commit-cask': commit_cask(config)
    else: clean(config)


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
