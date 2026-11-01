#!/usr/bin/env bash
# Print the remote default branch name. Takes no arguments.
set -euo pipefail
[ $# -eq 0 ] || { echo "usage: $0" >&2; exit 2; }
if b=$(git rev-parse --abbrev-ref origin/HEAD 2>/dev/null); then
  b=${b#origin/}
  # fetch never moves an existing origin/HEAD, so a renamed default branch leaves it stale
  r=$(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh} -o BatchMode=yes -o ConnectTimeout=5" \
    GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=10 git ls-remote --symref origin HEAD 2>/dev/null | awk '$1 == "ref:" && $3 == "HEAD" { sub("^refs/heads/", "", $2); print $2 }') || r=""
  if [ -n "$r" ] && [ "$r" != "$b" ]; then
    echo "default-branch.sh: warning: origin/HEAD names '$b' but origin's default branch is '$r'; run 'git remote set-head origin -a' and resolve again" >&2
  fi
  echo "$b"; exit 0
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
