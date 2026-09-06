#!/usr/bin/env bash
# Run a command at the branch's base commit with the named test files copied in.
# Nothing in the working tree is stashed or touched. Exit status is the command's.
#
# usage: base-run.sh -t <test-file> [-t <test-file> ...] [-s '<setup command>'] -- <command ...>
#   -t  repo-relative path of a test file to copy into the base checkout
#   -s  shell command run inside the base checkout before the tests (fixtures, symlinks)
set -euo pipefail
usage() { echo "usage: $0 -t <test-file> [-t ...] [-s <setup-cmd>] -- <command>" >&2; exit 2; }
tests=(); setup=""
while [ $# -gt 0 ]; do
  case "$1" in
    -t) [ $# -ge 2 ] || usage; tests+=("$2"); shift 2 ;;
    -s) [ $# -ge 2 ] || usage; setup="$2"; shift 2 ;;
    --) shift; break ;;
    *) usage ;;
  esac
done
[ $# -gt 0 ] || { echo "base-run.sh: no command given" >&2; usage; }
[ ${#tests[@]} -gt 0 ] || { echo "base-run.sh: no test files given" >&2; usage; }

root=$(git rev-parse --show-toplevel)
for t in "${tests[@]}"; do
  full=$(realpath -m "$root/$t")
  case "$full" in "$root"/*) ;; *) echo "base-run.sh: $t is outside the repo" >&2; exit 2 ;; esac
  [ -f "$full" ] || { echo "base-run.sh: $t not found (paths are repo-relative)" >&2; exit 2; }
done

main=$("$(dirname "$0")/default-branch.sh")
git -C "$root" fetch -q origin "$main" 2>/dev/null \
  || echo "base-run.sh: origin unreachable, using last fetched origin/$main" >&2
base=$(git -C "$root" merge-base "origin/$main" HEAD)
wt=$(mktemp -d "${TMPDIR:-/tmp}/base-run.XXXXXX")
cleanup() { git -C "$root" worktree remove --force "$wt" >/dev/null 2>&1 || true; rm -rf "$wt"; }
trap cleanup EXIT

git -C "$root" worktree add --detach "$wt" "$base" >/dev/null 2>&1
for t in "${tests[@]}"; do
  mkdir -p "$wt/$(dirname "$t")"
  cp "$root/$t" "$wt/$t"
done
if [ -n "$setup" ]; then (cd "$wt" && bash -c "$setup"); fi
echo "base-run.sh: base commit $base" >&2
cd "$wt" && "$@"
