#!/usr/bin/env python3
"""Provision CI through local hidden prompts; never print credential values."""
from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import tempfile

REPOSITORY = "hardworker/hermes-light"
ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'secrets'


def command(*args, input=None):
    result = subprocess.run(list(map(str, args)), input=input, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{Path(str(args[0])).name} failed; command arguments and output withheld")
    return result.stdout


def verify_backup(backup, password, expected_fingerprint, notary=None):
    """Import into an isolated Keychain and prove its private key can sign."""
    previous = shlex.split(command("security", "list-keychains", "-d", "user"))
    with tempfile.TemporaryDirectory(prefix="hermes-signing-check-") as folder:
        folder = Path(folder)
        keychain = folder / "verify.keychain-db"
        key_password = secrets.token_urlsafe(32)
        try:
            command("security", "create-keychain", "-p", key_password, keychain)
            command("security", "unlock-keychain", "-p", key_password, keychain)
            command("security", "import", backup, "-P", password, "-k", keychain, "-T", "/usr/bin/codesign")
            command("security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
                    "-s", "-k", key_password, keychain)
            listing = command("security", "find-identity", "-v", "-p", "codesigning", keychain)
            if expected_fingerprint not in listing:
                raise RuntimeError("Backup did not restore the expected signing identity")
            import shutil
            binary = folder / "signing-check"
            shutil.copyfile("/usr/bin/true", binary)
            binary.chmod(0o700)
            command("codesign", "--force", "--sign", expected_fingerprint, "--keychain", keychain,
                    "--timestamp=none", binary)
            command("codesign", "--verify", "--strict", binary)
            if notary:
                apple_id, team_id, app_password = notary
                command('xcrun', 'notarytool', 'store-credentials', 'hermes-light-ci-check',
                        '--keychain', keychain, '--apple-id', apple_id, '--team-id', team_id,
                        '--password', app_password)
        finally:
            if keychain.exists():
                command("security", "delete-keychain", keychain)
            command("security", "list-keychains", "-d", "user", "-s", *previous)


def export_identity(destination, password, signing, certificate):
    if certificate is None:
        fingerprint, _ = keychain_identity()
        with tempfile.TemporaryDirectory(prefix='hermes-keychain-export-') as folder:
            folder = Path(folder)
            executable = folder / 'export-identity'
            command('xcrun', 'swiftc', '-module-cache-path', folder / 'modules',
                    ROOT / 'scripts/export_identity.swift', '-o', executable)
            print('macOS may request permission to export the selected Developer ID key for CI. '
                  'Use only the native Keychain dialog for that authorization.', flush=True)
            command(executable, input=json.dumps({'fingerprint':fingerprint, 'password':password,
                                                 'destination':str(destination)}))
        return
    with tempfile.TemporaryDirectory(prefix='hermes-cert-') as folder:
        pem = Path(folder) / 'certificate.pem'
        pem.write_text(command('openssl', 'x509', '-inform', 'DER', '-in', certificate))
        command('openssl', 'pkcs12', '-export', '-inkey', signing / 'developer-id-private.pem',
                '-in', pem, '-name', 'Hermes Light Developer ID', '-out', destination,
                '-passout', 'stdin', input=password + '\n')


def keychain_identity():
    listing = command('security', 'find-identity', '-v', '-p', 'codesigning')
    identities = re.findall(r'([A-Fa-f0-9]{40}) "(Developer ID Application:[^"]+)"', listing)
    selected = os.environ.get('HERMES_LIGHT_SIGNING_IDENTITY')
    if selected:
        identities = [(digest,name) for digest,name in identities if selected in (digest,name)]
    if len(identities) != 1:
        raise ValueError('Select exactly one Developer ID identity; HERMES_LIGHT_SIGNING_IDENTITY can specify its fingerprint')
    return identities[0]


def identity_inputs():
    signing = Path(os.environ.get('HERMES_LIGHT_SIGNING_DIR', PRIVATE / 'apple-signing')).expanduser()
    recovery = PRIVATE / "recovery"
    recovery.mkdir(parents=True, exist_ok=True, mode=0o700)
    recovery.chmod(0o700)
    certificate = signing / "developerID_application.cer"
    if certificate.is_file() and (signing / 'developer-id-private.pem').is_file():
        subject = command('openssl','x509','-inform','DER','-in',certificate,'-noout','-subject','-nameopt','RFC2253')
        fingerprint = command('openssl','x509','-inform','DER','-in',certificate,
                              '-noout','-fingerprint','-sha1').strip().split('=')[-1].replace(':','')
    else:
        fingerprint, name = keychain_identity()
        certificates = command('security','find-certificate','-a','-c',name,'-p')
        matched = None
        for pem in re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',certificates,re.S):
            digest = command('openssl','x509','-noout','-fingerprint','-sha1',input=pem).strip().split('=')[-1].replace(':','')
            if digest.upper() == fingerprint.upper():
                matched = pem
                break
        if matched is None:
            raise ValueError('The selected Keychain certificate could not be read')
        subject = command('openssl','x509','-noout','-subject','-nameopt','RFC2253',input=matched)
        certificate = None
    match = re.search(r"(?:^|,)OU=([A-Z0-9]{10})(?:,|$)", subject)
    if not match:
        raise ValueError("The certificate has no Developer Team ID")
    team = match.group(1)
    return signing, recovery, certificate, team, fingerprint


def backup_identity(password):
    if len(password) < 12 or any(c in password for c in "\n\r\0"):
        raise ValueError('Use a recovery password of at least 12 characters')
    signing, recovery, certificate, team, fingerprint = identity_inputs()
    backup = recovery / "hermes-light-signing.p12"
    old_mask = os.umask(0o077)
    try:
        if not backup.exists():
            export_identity(backup, password, signing, certificate)
        backup.chmod(0o600)
        verify_backup(backup, password, fingerprint)
        report={'certificate_backup':str(backup), 'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),
                'signing_fingerprint':fingerprint, 'backup_restored':True}
        (recovery/'backup-verified.json').write_text(json.dumps(report,indent=2)+'\n')
        print('Optional encrypted recovery backup restored and verified.',flush=True)
        return report
    finally:
        os.umask(old_mask)


def provision(password, apple_id, notary_password):
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", apple_id):
        raise ValueError("Enter your Apple ID email")
    if not re.fullmatch(r"[a-z]{4}(?:-[a-z]{4}){3}", notary_password):
        raise ValueError("Enter the new app-specific password generated by Apple")
    backup_report = backup_identity(password) if password is not None else {}
    signing, recovery, certificate, team, fingerprint = identity_inputs()
    old_mask = os.umask(0o077)
    try:
        ci_password = secrets.token_urlsafe(40)
        with tempfile.TemporaryDirectory(prefix='hermes-ci-certificate-') as folder:
            ci_bundle = Path(folder) / 'identity.p12'
            export_identity(ci_bundle, ci_password, signing, certificate)
            verify_backup(ci_bundle, ci_password, fingerprint, notary=(apple_id, team, notary_password))
            encoded_ci_bundle = base64.b64encode(ci_bundle.read_bytes()).decode()
        values = {
            "MACOS_SIGNING_P12_BASE64": encoded_ci_bundle,
            "MACOS_SIGNING_P12_PASSWORD": ci_password,
            "APPLE_NOTARY_APPLE_ID": apple_id,
            "APPLE_NOTARY_TEAM_ID": team,
            "APPLE_NOTARY_PASSWORD": notary_password,
        }
        for name, value in values.items():
            command("gh", "secret", "set", name, "--repo", REPOSITORY, "--env", "release", input=value)
            print(f"Configured environment secret: {name}", flush=True)
        report = dict(backup_report, signing_fingerprint=fingerprint, github_secrets_configured=True,
                      separate_ci_encryption=True)
        (recovery / "setup.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        os.umask(old_mask)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apple-id", help="Apple ID email; prompted locally if omitted")
    parser.add_argument('--backup-only', action='store_true', help='Create and verify an optional portable backup without changing CI secrets')
    args = parser.parse_args()
    print('All inputs below are hidden. The existing local Keychain identity stays available.')
    if args.backup_only:
        print('This optional backup password stays local; save it in your password manager.')
        password = getpass.getpass('Recovery password (12+ characters): ')
        if password != getpass.getpass('Confirm recovery password: '):
            raise ValueError('Recovery passwords did not match')
        backup_identity(password)
        return
    apple_id = args.apple_id or getpass.getpass("Apple ID email: ").strip()
    print("Generate a new CI-only app-specific password at account.apple.com, then enter it here.")
    provision(None, apple_id, getpass.getpass("New Apple app-specific password: ").strip())
    print("CI secrets configured. No password values were logged.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
