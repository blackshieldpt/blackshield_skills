#!/usr/bin/env bash
# Run a command at the branch's base commit with the named test files copied in.
# Nothing in the working tree is stashed or touched. Exit status is the command's;
# the script's own errors exit 2 so they are never mistaken for a failing test.
#
# usage: base-run.sh -t <test-path> [-t <test-path> ...] [-s '<setup command>'] -- <command ...>
#   -t  repo-relative file or directory to copy into the base checkout
#   -s  shell command run inside the base checkout before the tests (fixtures, symlinks)
set -uo pipefail
usage() { echo "usage: $0 -t <test-path> [-t ...] [-s <setup-cmd>] -- <command>" >&2; exit 2; }
die() { echo "base-run.sh: $*" >&2; exit 2; }
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
[ ${#tests[@]} -gt 0 ] || { echo "base-run.sh: no test paths given" >&2; usage; }

root=$(git rev-parse --show-toplevel) || die "not inside a git repository"
for t in "${tests[@]}"; do
  full=$(realpath -m -- "$root/$t")
  case "$full" in "$root"/*) ;; *) die "$t is outside the repo" ;; esac
  [ -e "$full" ] || die "$t not found (paths are repo-relative)"
done
for a in "$@"; do
  case "$a" in "$root"*) die "command argument '$a' is an absolute path into the working tree; use a relative path so the base copy is run" ;; esac
done

main=$("$(dirname "$0")/default-branch.sh") || exit 2
GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh} -o BatchMode=yes" \
  git -C "$root" fetch -q origin "$main" 2>/dev/null \
  || echo "base-run.sh: origin unreachable, using last fetched origin/$main" >&2
base=$(git -C "$root" merge-base "origin/$main" HEAD) || die "cannot find merge-base with origin/$main"
git -C "$root" worktree prune
wt=$(mktemp -d "${TMPDIR:-/tmp}/base-run.XXXXXX") || die "mktemp failed"
cleanup() { git -C "$root" worktree remove --force "$wt" >/dev/null 2>&1 || true; rm -rf "$wt"; }
trap cleanup EXIT

git -C "$root" worktree add --detach "$wt" "$base" >/dev/null 2>&1 || die "worktree add failed"
for t in "${tests[@]}"; do
  dest="$wt/$t"
  mkdir -p "$(dirname "$dest")" || die "cannot create directory for $t at base"
  rm -rf -- "$dest"
  cp -r -- "$root/$t" "$dest" || die "cannot copy $t"
done
if [ -n "$setup" ]; then (cd "$wt" && bash -c "$setup") || die "setup command failed"; fi
echo "base-run.sh: base commit $base" >&2
cd "$wt" || die "cannot enter worktree"
"$@"
