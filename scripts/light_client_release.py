#!/usr/bin/env python3
"""Build, notarize and publish the pinned remote-only Hermes Desktop client.

No agent runtime is installed on the build host or included in the client.
Apple authentication stays in a local notarytool Keychain profile.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "packaging/source.json"
DEFAULT_WORK = ROOT / ".build/hermes-light"


def run(*args, cwd=None, env=None, capture=False):
    return subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd or ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )


def validate_config(config):
    if not re.fullmatch(r"[a-f0-9]{40}", config["commit"]):
        raise ValueError("An exact upstream source commit is required")
    if config["repository"] != "https://github.com/NousResearch/hermes-agent.git":
        raise ValueError("Build source must be the official Hermes repository")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", config["distribution_repository"]):
        raise ValueError("Invalid distribution repository")
    if (not re.fullmatch(r"\d+\.\d+\.\d+", config["version"])
            or config["tag"] != "v" + config["version"]
            or type(config["revision"]) is not int or config["revision"] < 1
            or config["target"] != "darwin-arm64"):
        raise ValueError("Release must identify a versioned Apple Silicon distribution")
    return config


def read_config():
    return validate_config(json.loads(CONFIG.read_text()))


def resolve_source(config, tag, revision=None):
    if not tag or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Specify a stable upstream tag, for example --tag vX.Y.Z")
    if tuple(map(int, tag[1:].split('.'))) < tuple(map(int, config['version'].split('.'))):
        raise ValueError("Upstream version downgrades are not allowed")
    refs = run("git", "ls-remote", "--tags", config["repository"],
               f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}", capture=True).stdout
    hashes = {}
    for line in refs.splitlines():
        digest, name = line.split()
        if re.fullmatch(r"[a-f0-9]{40}", digest):
            hashes[name] = digest
    commit = hashes.get(f"refs/tags/{tag}^{{}}", hashes.get(f"refs/tags/{tag}"))
    if not commit:
        raise ValueError("The requested tag was not found in the official upstream repository")
    same_version = tag == config["tag"]
    if same_version and commit != config["commit"]:
        raise ValueError("Upstream moved an already pinned tag; review it before changing the pin")
    if revision is None:
        revision = config["revision"] if same_version else 1
    updated = validate_config(dict(
        config, tag=tag, commit=commit, version=tag[1:], revision=revision
    ))
    if same_version and updated != config and revision <= config["revision"]:
        raise ValueError("A packaging revision must increase")
    return updated


def update_source(config, tag, revision=None, destination=CONFIG):
    updated = resolve_source(config, tag, revision)
    if updated == config:
        print(f"Already pinned to {tag}, revision {updated['revision']}")
    else:
        write_json(destination, updated)
        print(f"Pinned {tag} to {updated['commit']}; review and commit packaging/source.json")
    return updated


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_mac():
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("This release targets Apple Silicon and must be built on an arm64 Mac")


def inspect_app(app, config):
    """Reject a bootstrap/full app, a different revision, or an unexpected identity."""
    resources = app / "Contents/Resources"
    stamp = json.loads((resources / "install-stamp.json").read_text())
    if stamp.get("payload") != "light" or stamp.get("commit") != config["commit"]:
        raise ValueError("App must be the Light client built from the pinned source commit")
    if stamp.get("updateMechanism") != "external":
        raise ValueError("The Homebrew client must use external updates")
    if (resources / "agent-payload").exists():
        raise ValueError("A local agent payload must not be present")
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    expected = "com.nousresearch.hermes-light-" + config["commit"][:7]
    if info.get("CFBundleIdentifier") != expected:
        raise ValueError("Unexpected app bundle identifier")
    if info.get("CFBundleShortVersionString") != config["version"]:
        raise ValueError("App version must match the independent release version")
    if not (app / "Contents/MacOS" / info["CFBundleExecutable"]).is_file():
        raise ValueError("App executable missing")
    return info


def build(config, work, source):
    require_mac()
    candidate = work / "candidate.json"
    if candidate.exists():
        manifest = json.loads(candidate.read_text())
        if manifest["config"] != config:
            raise ValueError("Existing candidate belongs to another release; use another work directory")
        inspect_app(Path(manifest["app"]), config)
        print(f"Reusing candidate: {manifest['app']}")
        return
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--depth", "1", "--branch", config["tag"], config["repository"], source)
    actual = run("git", "rev-parse", "HEAD", cwd=source, capture=True).stdout.strip()
    origin = run("git", "remote", "get-url", "origin", cwd=source, capture=True).stdout.strip()
    dirty = run("git", "status", "--porcelain", cwd=source, capture=True).stdout.strip()
    if actual != config["commit"] or origin != config["repository"] or dirty:
        raise ValueError("Build checkout must be clean and match the official pinned commit")
    env = dict(os.environ, CSC_IDENTITY_AUTO_DISCOVERY="false",
               GITHUB_REPOSITORY=config["distribution_repository"])
    # Unsigned preparation must never accidentally use signing credentials.
    for name in ("APPLE_API_KEY", "APPLE_API_KEY_ID", "APPLE_API_ISSUER", "APPLE_NOTARY_PROFILE",
                 "APPLE_SIGNING_IDENTITY", "CSC_LINK", "CSC_KEY_PASSWORD", "CSC_NAME", "CSC_KEYCHAIN"):
        env.pop(name, None)
    run(sys.executable, "scripts/bundles/desktop.py", "--commit", config["commit"],
        "--variant", "light", "--", f"-c.extraMetadata.version={config['version']}", cwd=source, env=env)
    candidates = list((source / "apps/desktop/release/mac-arm64").glob("Hermes Light*.app"))
    if len(candidates) != 1:
        raise ValueError("Expected exactly one built Light app")
    record_candidate(config, work, source, candidates[0])


def record_candidate(config, work, source, app):
    info = inspect_app(app, config)
    resources = app / "Contents/Resources"
    (resources / "Hermes-UPSTREAM-LICENSE").write_bytes((source / "LICENSE").read_bytes())
    (resources / "INDEPENDENT-DISTRIBUTION.txt").write_text(
        f"Independent Hermes Light distribution from {config['tag']} ({config['commit']}).\n"
        "Upstream: Nous Research / https://github.com/NousResearch/hermes-agent\n"
        "No local agent payload. Distribution and Homebrew updates are maintained separately.\n")
    manifest = {
        "config": config,
        "app": str(app.resolve()),
        "source": str(source.resolve()),
        "bundle_identifier": info["CFBundleIdentifier"],
        "app_version": info["CFBundleShortVersionString"],
        "notarization_status": "not_submitted",
        "packaging_commit": run('git', 'rev-parse', 'HEAD', capture=True).stdout.strip(),
    }
    write_json(work / "candidate.json", manifest)
    print(f"Light client candidate: {app}")
    print("Candidate is not distributable until signing and notarization succeed.")


def select_identity(requested=None, keychain=None):
    args = ["security", "find-identity", "-v", "-p", "codesigning"]
    if keychain:
        args.append(keychain)
    listing = run(*args, capture=True).stdout
    identities = re.findall(r'([A-Fa-f0-9]{40}) "(Developer ID Application:[^"]+)"', listing)
    if requested:
        identities = [(digest, name) for digest, name in identities if requested in (digest, name)]
    if len(identities) != 1:
        raise ValueError("Select one Developer ID Application identity, or pass --identity")
    return identities[0][0]


def notarize(config, work, profile, identity, keychain=None):
    require_mac()
    manifest = json.loads((work / "candidate.json").read_text())
    if manifest["config"] != config:
        raise ValueError("Candidate configuration differs from this release")
    app, source = Path(manifest["app"]), Path(manifest["source"])
    inspect_app(app, config)
    identity = select_identity(identity, keychain)
    prepared = json.loads((source / ".build/desktop-job/prepared.json").read_text())
    run(
        prepared["node"], ROOT / "packaging/sign-app.mjs", source, app, identity,
        str(keychain) if keychain else ''
    )
    upload = work / "notary-upload.zip"
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, upload)
    credentials = ['--keychain-profile', profile]
    if keychain:
        credentials += ['--keychain', str(keychain)]
    result = run("xcrun", "notarytool", "submit", upload, *credentials,
                 "--wait", "--timeout", "30m", "--output-format", "json", capture=True)
    submission = json.loads(result.stdout)
    manifest.update(notarization_id=submission["id"], notarization_status=submission["status"])
    write_json(work / "candidate.json", manifest)
    if submission["status"] != "Accepted":
        raise ValueError(
            f"Apple notarization is {submission['status']}; inspect submission {submission['id']}"
        )
    run("xcrun", "stapler", "staple", app)
    verify_app(app, config)
    archive = work / archive_name(config)
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, archive)
    manifest.update(archive=str(archive.resolve()), sha256=checksum(archive))
    write_json(work / "release.json", manifest)
    print(f"Notarized release: {archive}")


def verify_app(app, config):
    inspect_app(app, config)
    run("codesign", "--verify", "--deep", "--strict", app)
    run("xcrun", "stapler", "validate", app)
    run("spctl", "--assess", "--type", "execute", "--verbose", app)


def release_tag(config):
    return f"hermes-light-v{config['version']}-r{config['revision']}"


def archive_name(config):
    return f"HermesLight-{config['version']}-r{config['revision']}-macos-arm64.zip"


def validate_release(manifest, config):
    if manifest.get("config") != config or manifest.get("notarization_status") != "Accepted":
        raise ValueError("Only this release's Apple-accepted artifact can be published")
    archive = Path(manifest["archive"])
    if not archive.is_file() or checksum(archive) != manifest.get("sha256"):
        raise ValueError("Release archive checksum mismatch")
    return archive


def cask_text(manifest, config):
    validate_release(manifest, config)
    return render_cask(config, manifest["sha256"], Path(manifest["app"]).name,
                       manifest["bundle_identifier"])


def render_cask(config, sha256, app, bundle_identifier):
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("An exact archive SHA-256 is required")
    if app != f"Hermes Light {config['commit'][:7]}.app":
        raise ValueError("Unexpected Light app name")
    if bundle_identifier != "com.nousresearch.hermes-light-" + config["commit"][:7]:
        raise ValueError("Unexpected Light bundle identifier")
    repository = config["distribution_repository"]
    url = f"https://github.com/{repository}/releases/download/{release_tag(config)}/{archive_name(config)}"
    # JSON string quoting is also valid Ruby string quoting for these values.
    quote = json.dumps
    return f'''cask "hermes-light" do
  version {quote(config["version"] + "," + str(config["revision"]))}
  sha256 {quote(sha256)}

  url {quote(url)}
  name "Hermes Light"
  desc "Remote Hermes client without a local agent runtime"
  homepage "https://github.com/{repository}"

  depends_on arch: :arm64
  depends_on macos: :ventura

  app {quote(app)}, target: "Hermes Light.app"

  uninstall quit: {quote(bundle_identifier)}

  caveats "Independent Light build. Updates are distributed through this Homebrew tap."
end
'''


def sync_cask(config):
    """Recover the cask from published metadata without downloading the app."""
    tag, repository = release_tag(config), config["distribution_repository"]
    published = json.loads(run("gh", "release", "view", tag, "--repo", repository,
                               "--json", "assets,isDraft", capture=True).stdout)
    manifest = json.loads(run(
        "gh", "release", "download", tag, "--repo", repository,
        "--pattern", "release-manifest.json", "--output", "-", capture=True
    ).stdout)
    if (published["isDraft"] or manifest.get("config") != config
            or manifest.get("notarization_status") != "Accepted"):
        raise ValueError("Published release must match this pin and be Apple-accepted")
    sha256 = manifest.get("sha256", "")
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Published release must include an exact archive SHA-256")
    assets = [asset for asset in published["assets"] if asset["name"] == archive_name(config)]
    if len(assets) != 1 or assets[0].get("digest") != "sha256:" + sha256:
        raise ValueError("Published archive digest does not match release metadata")
    app = manifest.get("app_name", f"Hermes Light {config['commit'][:7]}.app")
    text = render_cask(config, sha256, app, manifest["bundle_identifier"])
    destination = ROOT / "Casks/hermes-light.rb"
    destination.write_text(text)
    print(f"Cask ready: {destination}; review, commit and push it to publish the tap update")


def get_release(config):
    result = subprocess.run(
        ['gh', 'release', 'view', release_tag(config), '--repo',
         config['distribution_repository'], '--json', 'assets,isDraft'],
        capture_output=True, text=True,
    )
    if result.returncode:
        if 'release not found' in result.stderr.lower() or 'HTTP 404' in result.stderr:
            return None
        raise ValueError('Unable to inspect the release (API failure)')
    return json.loads(result.stdout)


def assert_current_pin(config):
    content = run(
        'gh', 'api',
        f"repos/{config['distribution_repository']}/contents/packaging/source.json?ref=main",
        capture=True,
    ).stdout
    remote = json.loads(base64.b64decode(json.loads(content)['content']))
    if remote != config:
        raise ValueError('Main pins another release; refusing stale publication')


def verify_assets(published, files):
    indexed = {asset['name']: asset for asset in published['assets']}
    for path in files:
        asset = indexed.get(path.name)
        if asset is None or asset.get('digest') != 'sha256:' + checksum(path):
            raise ValueError('Release asset is missing or has a different digest; refusing publication')


def assert_new_release(config):
    assert_current_pin(config)
    if get_release(config) is not None:
        raise ValueError('Release already exists; see docs/RELEASING.md before retrying')
    refs = run('git', 'ls-remote', '--tags',
               f"https://github.com/{config['distribution_repository']}.git",
               f"refs/tags/{release_tag(config)}", capture=True).stdout
    if refs.strip():
        raise ValueError('The release tag already exists; choose a new packaging revision')


def publish(config, work):
    require_mac()
    assert_new_release(config)
    manifest = json.loads((work / "release.json").read_text())
    archive = validate_release(manifest, config)
    app = Path(manifest["app"])
    verify_app(app, config)
    tag, repository = release_tag(config), config["distribution_repository"]
    visibility = json.loads(run(
        "gh", "repo", "view", repository, "--json", "isPrivate", capture=True
    ).stdout)
    if visibility["isPrivate"]:
        raise ValueError("The combined Light client and Homebrew tap repository must be public")
    target_commit = manifest.get('packaging_commit', '')
    if not re.fullmatch(r'[a-f0-9]{40}', target_commit):
        raise ValueError('Publication requires an exact packaging commit')
    notes = work / "release-notes.md"
    notes.write_text(
        f"Independent Apple Silicon Hermes Light client from upstream {config['tag']} "
        f"({config['commit']}). Includes no local agent/Python payload.\n\n"
        f"Developer ID signed, Apple notarization {manifest['notarization_id']} accepted, "
        "ticket stapled and Gatekeeper verified.\n"
    )
    # Distribution metadata deliberately excludes local filesystem paths.
    public_fields = (
        "config", "bundle_identifier", "app_version", "sha256",
        "notarization_id", "notarization_status",
    )
    public = {key: manifest[key] for key in public_fields}
    public["app_name"] = app.name
    public['packaging_commit'] = target_commit
    public_manifest = work / "release-manifest.json"
    write_json(public_manifest, public)
    run(
        'gh', 'release', 'create', tag, '--draft', '--repo', repository,
        '--target', target_commit,
        '--title', f"Hermes Light {config['version']} revision {config['revision']}",
        '--notes-file', notes,
    )
    run('gh', 'release', 'upload', tag, public_manifest, archive, '--repo', repository)
    verify_assets(get_release(config), (public_manifest, archive))
    assert_current_pin(config)
    run('gh', 'release', 'edit', tag, '--repo', repository, '--draft=false', '--latest')
    destination = ROOT / "Casks/hermes-light.rb"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(cask_text(manifest, config))
    print(f"Cask generated: {destination}; commit it to publish the tap update")


def release_all(config, work, source, profile, identity, keychain=None):
    assert_new_release(config)
    build(config, work, source)
    notarize(config, work, profile, identity, keychain)
    publish(config, work)


def clean_work(work):
    work = work.resolve()
    if work in (ROOT.resolve(), Path.home(), Path("/")):
        raise ValueError("Refusing to clean an unsafe work directory")
    temporary_files = (
        "source", "candidate", "candidate.json", "notary-upload.zip",
        "release.json", "release-notes.md",
    )
    for name in temporary_files:
        target = work / name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
    print(f"Temporary build files removed from {work}; release ZIP and public metadata preserved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["update", "release", "build", "notarize", "publish", "cask", "clean"]
    )
    parser.add_argument("--work", type=Path)
    parser.add_argument("--tag", help="Stable upstream tag to pin, used by update")
    parser.add_argument("--revision", type=int, help="Packaging revision, used by update")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--profile", default="hermes-light-notary")
    parser.add_argument("--identity")
    parser.add_argument('--keychain', type=Path, help='Explicit signing/notarization Keychain')
    args = parser.parse_args()
    config = read_config()
    work = args.work or DEFAULT_WORK / release_tag(config)
    source = args.source or work / "source"
    if args.action == "update":
        update_source(config, args.tag, args.revision)
    elif args.action == "build":
        build(config, work, source)
    elif args.action == "notarize":
        notarize(config, work, args.profile, args.identity, args.keychain)
    elif args.action == "publish":
        publish(config, work)
    elif args.action == "cask":
        sync_cask(config)
    elif args.action == "clean":
        clean_work(work)
    else:
        release_all(config, work, source, args.profile, args.identity, args.keychain)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"Light client release: {exc}", file=sys.stderr)
        raise SystemExit(1)
