#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Scanning tracked files for secrets ..."

FOUND=0

# Patterns that indicate leaked secrets
PATTERNS=(
  'sk-ant-[A-Za-z0-9_-]{20,}'
  'sk-proj-[A-Za-z0-9_-]{20,}'
  'bot[0-9]{8,}:[A-Za-z0-9_-]{30,}'
  'bsc_[A-Za-z0-9]{20,}'
  'xai-[A-Za-z0-9]{20,}'
  'gsk_[A-Za-z0-9]{20,}'
  '(^|[[:space:]])[A-Za-z0-9+/]{40,}={1,2}([[:space:]]|$)'
  'Bearer [A-Za-z0-9._-]{20,}'
  'github_pat_[A-Za-z0-9_]{20,}'
  'gh[pousr]_[A-Za-z0-9]{20,}'
)

# Get all tracked files, skip .env.example and *.md
FILES=$(cd "$REPO_ROOT" && git ls-files | grep -v '\.env\.example$' | grep -v '\.md$' || true)

if [ -z "$FILES" ]; then
  echo "✓ No secrets detected in tracked files"
  exit 0
fi

for pattern in "${PATTERNS[@]}"; do
  while IFS= read -r file; do
    [ -z "$file" ] && continue
    FILEPATH="$REPO_ROOT/$file"
    [ -f "$FILEPATH" ] || continue
    if grep -qE "$pattern" "$FILEPATH" 2>/dev/null; then
      echo "SECRET DETECTED in $file:"
      grep -nE "$pattern" "$FILEPATH"
      FOUND=1
    fi
  done <<< "$FILES"
done

if [ "$FOUND" -eq 1 ]; then
  echo ""
  echo "ERROR: Secrets detected in tracked files. Remove them before committing."
  exit 1
fi

echo "✓ No secrets detected in tracked files"
