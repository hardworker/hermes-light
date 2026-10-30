#!/usr/bin/env python3
"""Check tracked source and docs without printing sensitive matches."""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".p8", ".cer",
                      ".certsigningrequest", ".mobileprovision", ".provisionprofile", ".dmg", ".zip"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "service credential": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|xai-[A-Za-z0-9_-]{20,}|(?:gsk|bsc)_[A-Za-z0-9_-]{20,})"),
    "app-specific password": re.compile(r"\b[a-z]{4}(?:-[a-z]{4}){3}\b"),
    "bearer credential": re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}"),
    "personal home path": re.compile(r"/(?:Users|home)/[^/\s]+/"),
}
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def findings(name, data):
    path = Path(name)
    messages = []
    if (path.suffix.lower() in PRIVATE_EXTENSIONS
            or set(path.parts) & {"secrets", ".ssh", ".aws"}
            or (path.name.startswith(".env") and path.name != ".env.example")):
        messages.append(f"{name}: sensitive file must remain outside tracked source")
    for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        reasons = {label for label, pattern in PATTERNS.items() if pattern.search(line)}
        for email in EMAIL.finditer(line):
            domain = email.group(1).lower()
            if domain not in {"example.com", "example.org", "example.net", "users.noreply.github.com"}:
                reasons.add("personal or operational email")
        for match in IPV4.finditer(line):
            try:
                address = ipaddress.ip_address(match.group())
                if not address.is_loopback:
                    reasons.add("hardcoded network address")
            except ValueError:
                pass
        if reasons:
            messages.append(f"{name}:{number}: {', '.join(sorted(reasons))}")
    return messages


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def check(history=False):
    messages, seen = [], set()
    for raw_name in git("ls-files", "-z").split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode()
        path = ROOT / name
        if path.is_file():
            messages.extend(findings(name, path.read_bytes()))
    if history:
        for commit in git("rev-list", "--all").decode().splitlines():
            for entry in git("ls-tree", "-rz", commit).split(b"\0"):
                if not entry:
                    continue
                meta, raw_name = entry.split(b"\t", 1)
                _, kind, oid = meta.split()
                if kind != b"blob" or (raw_name, oid) in seen:
                    continue
                seen.add((raw_name, oid))
                name = raw_name.decode()
                for message in findings(name, git("cat-file", "blob", oid.decode())):
                    messages.append(f"history {commit[:7]}: {message}")
    for message in messages:
        print(message)
    if messages:
        print("Repository hygiene check failed; matched values were not displayed.")
        return 1
    print("Tracked files and docs" + (" and available Git history" if history else "") + " passed hygiene checks.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Also inspect available Git history")
    raise SystemExit(check(parser.parse_args().history))
