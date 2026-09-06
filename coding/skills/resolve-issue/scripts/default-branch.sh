#!/usr/bin/env bash
# Print the remote default branch name. Takes no arguments.
set -euo pipefail
[ $# -eq 0 ] || { echo "usage: $0" >&2; exit 2; }
if b=$(git rev-parse --abbrev-ref origin/HEAD 2>/dev/null); then
  echo "${b#origin/}"; exit 0
fi
if command -v gh >/dev/null 2>&1; then
  if b=$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null) && [ -n "$b" ]; then
    echo "$b"; exit 0
  fi
fi
for c in main master; do
  if git show-ref --verify -q "refs/remotes/origin/$c"; then echo "$c"; exit 0; fi
done
echo "default-branch.sh: cannot determine the default branch; run 'git remote set-head origin -a'" >&2
exit 1
