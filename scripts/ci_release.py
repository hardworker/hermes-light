#!/usr/bin/env python3
"""GitHub Actions orchestration; credentials are supplied only to the steps that need them."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import zipfile

import light_client_release as release


def output(name, value):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as target:
        target.write(f'{name}={value}\n')


def api(path, method=None, data=None):
    args = ['gh', 'api', path]
    if method:
        args += ['--method', method]
    if data is not None:
        args += ['--input', '-']
    result = release.run(*args, input=json.dumps(data) if data is not None else None, capture=True).stdout
    return json.loads(result) if result.strip() else None


def artifacts(repository):
    pages = json.loads(release.run('gh', 'api', '--paginate', '--slurp',
                                  f'repos/{repository}/actions/artifacts?per_page=100', capture=True).stdout)
    return [artifact for page in pages for artifact in page['artifacts']]


def trusted_artifacts(config):
    repository = config['distribution_repository']
    prefix = f"hermes-checkpoint-{release.release_tag(config)}-"
    checked, results = {}, []
    for artifact in artifacts(repository):
        if artifact['expired'] or not artifact['name'].startswith(prefix):
            continue
        run_id = artifact['workflow_run']['id']
        if run_id not in checked:
            run = api(f'repos/{repository}/actions/runs/{run_id}')
            checked[run_id] = (run['path'] == '.github/workflows/release.yml'
                               and run['head_branch'] == 'main'
                               and run['event'] in ('push', 'workflow_dispatch')
                               and run['head_repository']['full_name'] == repository)
        if checked[run_id]:
            results.append(artifact)
    return sorted(results, key=lambda x: ('-final-' in x['name'], x['id']), reverse=True)


def restore(config, work):
    matches = trusted_artifacts(config)
    if not matches:
        return False
    artifact = matches[0]
    with tempfile.TemporaryDirectory(prefix='hermes-checkpoint-') as folder:
        folder = Path(folder)
        download = folder / 'artifact.zip'
        with download.open('wb') as target:
            subprocess.run(['gh', 'api', f"repos/{config['distribution_repository']}/actions/artifacts/{artifact['id']}/zip"],
                           check=True, stdout=target)
        extracted = folder / 'checkpoint'
        extracted.mkdir()
        with zipfile.ZipFile(download) as archive:
            allowed = {'checkpoint.json', 'notary-upload.zip', release.archive_name(config)}
            if any(info.filename not in allowed or info.is_dir() for info in archive.infolist()):
                raise ValueError('Unexpected file in checkpoint artifact')
            archive.extractall(extracted)
        release.restore_checkpoint(config, work, extracted)
    return True


def prepare(config, work):
    release.assert_current_pin(config)
    output('tag', release.release_tag(config))
    output('work', work)
    published = release.get_release(config)
    if published and not published['isDraft']:
        release.sync_cask(config)
        output('published', 'true')
        output('candidate', 'false')
        output('final', 'false')
        return
    restored = restore(config, work)
    if published and published['isDraft'] and not restored:
        raise ValueError('A partial draft exists but its exact checkpoint is unavailable; do not rebuild and overwrite its assets')
    output('published', 'false')
    output('candidate', str((work / 'candidate.json').exists()).lower())
    output('final', str((work / 'release.json').exists()).lower())


def secure(*args, input=None):
    result = subprocess.run(list(map(str, args)), input=input, text=True, capture_output=True)
    if result.returncode:
        raise ValueError(f'{Path(str(args[0])).name} failed; signing arguments and output withheld')
    return result.stdout


def signing_setup():
    names = ('MACOS_SIGNING_P12_BASE64', 'MACOS_SIGNING_P12_PASSWORD', 'APPLE_NOTARY_APPLE_ID',
             'APPLE_NOTARY_TEAM_ID', 'APPLE_NOTARY_PASSWORD')
    if any(not os.environ.get(name) for name in names):
        raise ValueError('Release environment secrets are incomplete; run the local secure CI setup')
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
        output('keychain', keychain)
        output('identity', identity)
    finally:
        p12.unlink(missing_ok=True)


def signing_cleanup():
    directory = Path(os.environ['RUNNER_TEMP']) / 'hermes-signing'
    keychain = directory / 'release.keychain-db'
    if keychain.exists():
        secure('security', 'delete-keychain', keychain)
    if directory.exists():
        shutil.rmtree(directory)


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


def finish(config):
    for artifact in trusted_artifacts(config):
        api(f"repos/{config['distribution_repository']}/actions/artifacts/{artifact['id']}", method='DELETE')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as out:
            out.write(f"Released {release.release_tag(config)}. Signature, notarization, archive digest and Homebrew checks passed.\n")


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'signing-setup', 'signing-cleanup', 'commit-cask',
                                         'smoke', 'smoke-cleanup', 'finish', 'clean'])
    args = parser.parse_args()
    config = release.read_config()
    work = release.DEFAULT_WORK / release.release_tag(config)
    if args.action == 'prepare': prepare(config, work)
    elif args.action == 'signing-setup': signing_setup()
    elif args.action == 'signing-cleanup': signing_cleanup()
    elif args.action == 'commit-cask': commit_cask(config)
    elif args.action == 'smoke': smoke(config)
    elif args.action == 'smoke-cleanup': smoke_cleanup(config)
    elif args.action == 'clean':
        if release.DEFAULT_WORK.exists(): shutil.rmtree(release.DEFAULT_WORK)
    else: finish(config)


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
