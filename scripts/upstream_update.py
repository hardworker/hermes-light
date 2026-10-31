#!/usr/bin/env python3
"""Maintain one source-pin PR using Git and the workflow's built-in token."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

import light_client_release as release

BRANCH = 'automation/upstream-update'


def version(tag):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Only stable upstream version tags are accepted')
    return tuple(map(int, tag[1:].split('.')))


def config_at(commit):
    content = release.run(
        'git', 'show', f'{commit}:packaging/source.json', capture=True
    ).stdout
    return release.validate_config(json.loads(content))


def update(dry_run=False):
    repository = release.read_config()['distribution_repository']
    release.run('git', 'fetch', 'origin', 'main')
    main_commit = release.run('git', 'rev-parse', 'FETCH_HEAD', capture=True).stdout.strip()
    config = config_at(main_commit)
    latest = json.loads(release.run(
        'gh', 'api', 'repos/NousResearch/hermes-agent/releases/latest', capture=True
    ).stdout)
    if latest['draft'] or latest['prerelease']:
        raise ValueError('Upstream latest must be a published stable release')

    # Check that the current upstream tag has not moved, even without an upgrade.
    release.resolve_source(config, config['tag'])
    tag = latest['tag_name']
    if version(tag) <= version(config['tag']):
        print('No newer stable upstream release.')
        return
    updated = release.resolve_source(config, tag)

    pulls = json.loads(release.run(
        'gh', 'pr', 'list', '--repo', repository, '--base', 'main', '--head', BRANCH,
        '--state', 'all', '--limit', '10', '--json', 'number,state,mergedAt', capture=True
    ).stdout)
    opened = [pull for pull in pulls if pull['state'] == 'OPEN']
    if len(opened) > 1:
        raise ValueError('Multiple updater PRs found; review them before continuing')
    pull = opened[0] if opened else None

    branch_ref = f'refs/heads/{BRANCH}'
    refs = release.run('git', 'ls-remote', '--heads', 'origin', branch_ref, capture=True).stdout
    branch_commit = refs.split()[0] if refs.strip() else ''
    branch_config = None
    if branch_commit:
        release.run('git', 'fetch', 'origin', branch_commit)
        branch_config = config_at(branch_commit)

    if pull:
        changed_files = release.run(
            'gh', 'pr', 'diff', str(pull['number']), '--repo', repository,
            '--name-only', capture=True
        ).stdout.splitlines()
        if changed_files != ['packaging/source.json']:
            raise ValueError('The updater PR contains other changes; leaving it for review')
        if branch_config == updated:
            print(f"Update PR #{pull['number']} is already current.")
            return
    elif branch_config == updated:
        declined = any(item['state'] == 'CLOSED' and not item['mergedAt'] for item in pulls)
        if declined:
            print('The same update was declined; leaving it closed.')
            return

    if dry_run:
        print(f'Would create or update the upstream PR for {tag}.')
        return
    if release.run('git', 'status', '--porcelain', capture=True).stdout.strip():
        raise ValueError('The updater needs a clean checkout')

    release.run('git', 'switch', '--detach', main_commit)
    release.write_json(release.CONFIG, updated)
    release.run('git', 'add', 'packaging/source.json')
    release.run('git', 'config', 'user.name', 'github-actions[bot]')
    release.run(
        'git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'
    )
    release.run('git', 'commit', '-m', f'Update upstream Hermes to {tag}')
    release.run('gh', 'auth', 'setup-git')
    # Rewrite only the bot branch, and refuse to overwrite a concurrent change.
    release.run(
        'git', 'push', f'--force-with-lease={branch_ref}:{branch_commit}',
        'origin', f'HEAD:{branch_ref}'
    )

    with tempfile.TemporaryDirectory() as folder:
        notes = Path(folder) / 'body.md'
        notes.write_text(
            f"Update Hermes Light to upstream {tag} ({updated['commit']}).\n\n"
            'Approve validation if GitHub requests it, then review and merge. '
            'Merging runs the signed release and updates the Homebrew cask.\n'
        )
        options = [
            '--repo', repository, '--title', f'Update Hermes Light to {tag}',
            '--body-file', notes,
        ]
        if pull:
            release.run('gh', 'pr', 'edit', str(pull['number']), *options)
        else:
            release.run('gh', 'pr', 'create', '--base', 'main', '--head', BRANCH, *options)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    try:
        update(parser.parse_args().dry_run)
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
