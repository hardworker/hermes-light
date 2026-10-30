#!/usr/bin/env python3
"""Maintain one config-only update PR using the workflow's built-in token."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import light_client_release as release
from ci_release import api

BRANCH = 'automation/upstream-update'


def version(tag):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Only stable upstream version tags are accepted')
    return tuple(map(int, tag[1:].split('.')))


def optional_api(path):
    try:
        return api(path)
    except subprocess.CalledProcessError as exc:
        if 'HTTP 404' in (exc.stderr or ''):
            return None
        raise ValueError('GitHub API request failed; refusing to assume the branch is missing') from None


def config_at(repository, ref):
    blob = api(f'repos/{repository}/contents/packaging/source.json?ref={ref}')
    return release.validate_config(json.loads(base64.b64decode(blob['content'])))


def ensure_config_only(repository, pull):
    names = release.run('gh', 'pr', 'diff', str(pull['number']), '--repo', repository,
                        '--name-only', capture=True).stdout.splitlines()
    if names != ['packaging/source.json']:
        raise ValueError('The updater PR contains other changes; leaving it for a human to review')


def update(dry_run=False):
    local = release.read_config()
    repository = local['distribution_repository']
    main = api(f'repos/{repository}/git/ref/heads/main')['object']['sha']
    config = config_at(repository, main)
    latest = api('repos/NousResearch/hermes-agent/releases/latest')
    if latest['draft'] or latest['prerelease']:
        raise ValueError('Upstream latest must be a published stable release')
    tag = latest['tag_name']
    with tempfile.TemporaryDirectory() as folder:
        destination = Path(folder) / 'source.json'
        # Check the existing immutable pin even when no new version exists.
        release.update_source(config, config['tag'], destination=destination)
        if version(tag) <= version(config['tag']):
            print('No newer stable upstream release.')
            return
        updated = release.update_source(config, tag, destination=destination)
    pulls = json.loads(release.run('gh', 'pr', 'list', '--repo', repository, '--head', BRANCH,
                                  '--state', 'all', '--limit', '10', '--json',
                                  'number,state,mergedAt,title', capture=True).stdout)
    opened = [pull for pull in pulls if pull['state'] == 'OPEN']
    if len(opened) > 1:
        raise ValueError('Multiple updater PRs found; refusing an ambiguous update')
    pull = opened[0] if opened else None
    branch = optional_api(f'repos/{repository}/git/ref/heads/{BRANCH}')
    current_branch_config = config_at(repository, branch['object']['sha']) if branch else None
    if pull:
        ensure_config_only(repository, pull)
        if current_branch_config == updated:
            print(f"Update PR #{pull['number']} is already current.")
            return
    elif current_branch_config == updated and any(p['state'] == 'CLOSED' and not p['mergedAt'] for p in pulls):
        print('The same update was closed without merging; leaving it closed.')
        return
    if dry_run:
        print(f'Would create or update the single upstream PR for {tag}.')
        return
    main_commit = api(f'repos/{repository}/git/commits/{main}')
    content = json.dumps(updated, indent=2) + '\n'
    blob = api(f'repos/{repository}/git/blobs', method='POST',
               data={'content': content, 'encoding': 'utf-8'})
    tree = api(f'repos/{repository}/git/trees', method='POST', data={
        'base_tree': main_commit['tree']['sha'],
        'tree': [{'path': 'packaging/source.json', 'mode': '100644', 'type': 'blob', 'sha': blob['sha']}],
    })
    # A merge parent preserves the automation branch; no force-push is needed.
    parents = [main]
    if branch and branch['object']['sha'] != main:
        parents.append(branch['object']['sha'])
    commit = api(f'repos/{repository}/git/commits', method='POST', data={
        'message': f'Update upstream Hermes to {tag}', 'tree': tree['sha'], 'parents': parents,
    })
    if branch:
        api(f'repos/{repository}/git/refs/heads/{BRANCH}', method='PATCH',
            data={'sha': commit['sha'], 'force': False})
    else:
        api(f'repos/{repository}/git/refs', method='POST',
            data={'ref': f'refs/heads/{BRANCH}', 'sha': commit['sha']})
    with tempfile.TemporaryDirectory() as folder:
        notes = Path(folder) / 'body.md'
        notes.write_text(f"Update the pinned Light client source to upstream {tag} ({updated['commit']}).\n\n"
                         'Review and approve the validation workflow if GitHub requests it. Merging runs the signed release workflow and updates the Homebrew cask.\n')
        if pull:
            release.run('gh', 'pr', 'edit', str(pull['number']), '--repo', repository,
                        '--title', f'Update Hermes Light to {tag}', '--body-file', notes)
        else:
            release.run('gh', 'pr', 'create', '--repo', repository, '--base', 'main', '--head', BRANCH,
                        '--title', f'Update Hermes Light to {tag}', '--body-file', notes)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    try:
        update(parser.parse_args().dry_run)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
