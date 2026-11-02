#!/usr/bin/env python3
"""Release preflight, Homebrew verification, and cask publication."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

import light_client_release as release


def check(config):
    if os.environ.get('RELEASE_SECRETS_CONFIGURED') != 'true':
        raise ValueError('Configure release environment secrets using docs/RELEASING.md')
    release.assert_new_release(config)


def commit_cask(config):
    cask = release.ROOT / 'Casks/hermes-light.rb'
    content = cask.read_text()
    release.run('git', 'fetch', 'origin', 'main')
    main_config = json.loads(release.run(
        'git', 'show', 'origin/main:packaging/source.json', capture=True
    ).stdout)
    if main_config != config:
        raise ValueError('Main pins another version; refusing to roll the cask backward')
    current_cask = release.run(
        'git', 'show', 'origin/main:Casks/hermes-light.rb', capture=True
    ).stdout
    if current_cask == content:
        return

    release.run('git', 'restore', '--worktree', '--staged', 'Casks/hermes-light.rb')
    release.run('git', 'switch', '--detach', 'origin/main')
    cask.write_text(content)
    release.run('git', 'config', 'user.name', 'github-actions[bot]')
    release.run(
        'git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'
    )
    release.run('git', 'add', 'Casks/hermes-light.rb')
    release.run(
        'git', 'commit', '-m',
        f"Release Hermes Light {config['version']} revision {config['revision']}"
    )
    release.run('git', 'push', 'origin', 'HEAD:main')


def smoke(config):
    directory = Path(os.environ['RUNNER_TEMP']) / 'hermes-homebrew'
    directory.mkdir()
    token = config['distribution_repository'] + '/hermes-light'
    env = dict(
        os.environ,
        HOMEBREW_NO_AUTO_UPDATE='1',
        HOMEBREW_CACHE=str(directory / 'cache'),
    )

    def brew(*args, capture=False):
        return release.run('brew', *args, env=env, capture=capture)

    brew(
        'tap', config['distribution_repository'],
        f"https://github.com/{config['distribution_repository']}"
    )
    tap = Path(brew('--repository', config['distribution_repository'], capture=True).stdout.strip())
    shutil.copyfile(release.ROOT / 'Casks/hermes-light.rb', tap / 'Casks/hermes-light.rb')
    if subprocess.run(['brew', 'help', 'trust'], capture_output=True).returncode == 0:
        brew('trust', '--cask', token)
    appdir = directory / 'apps'
    brew('install', '--cask', '--appdir=' + str(appdir), token)
    release.verify_app(appdir / 'Hermes Light.app', config)


def smoke_cleanup(config):
    directory = Path(os.environ['RUNNER_TEMP']) / 'hermes-homebrew'
    if not directory.exists():
        return
    env = dict(
        os.environ,
        HOMEBREW_NO_AUTO_UPDATE='1',
        HOMEBREW_CACHE=str(directory / 'cache'),
    )
    token = config['distribution_repository'] + '/hermes-light'
    try:
        installed = subprocess.run(['brew', 'list', '--cask', token], capture_output=True, env=env)
        if installed.returncode == 0:
            release.run('brew', 'uninstall', '--cask', token, env=env)
    finally:
        shutil.rmtree(directory)


def clean(config):
    try:
        smoke_cleanup(config)
    finally:
        if release.DEFAULT_WORK.exists():
            shutil.rmtree(release.DEFAULT_WORK)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'smoke', 'commit-cask', 'clean'])
    args = parser.parse_args()
    config = release.read_config()
    if args.action == 'check':
        check(config)
    elif args.action == 'smoke':
        smoke(config)
    elif args.action == 'commit-cask':
        commit_cask(config)
    else:
        clean(config)


if __name__ == '__main__':
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
