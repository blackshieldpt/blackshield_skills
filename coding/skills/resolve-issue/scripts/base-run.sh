#!/usr/bin/env bash
# Run a command at the branch's base commit with the named test files copied in.
# Nothing in the working tree is stashed or touched. Exit status is the command's;
# the script's own errors exit 2 so they are never mistaken for a failing test.
#
# usage: base-run.sh [-C <dir>] [-r <remote>] -t <test-path> [-t <test-path> ...] [-s '<setup command>'] -- <command ...>
#   -C  run as if started in <dir> (the checkout or linked worktree whose branch is tested)
#   -r  remote to diff against (default origin; a fork passes its upstream); the branch name still comes from default-branch.sh
#   -t  repo-relative file or directory to copy into the base checkout
#   -s  shell command run inside the base checkout before the tests (fixtures, symlinks)
# Setup and command run at the root of the base checkout, whatever the caller's cwd.
set -uo pipefail
usage() { echo "base-run.sh: error: usage: $0 [-C <dir>] [-r <remote>] -t <test-path> [-t ...] [-s <setup-cmd>] -- <command>" >&2; exit 2; }
die() { echo "base-run.sh: error: $*" >&2; exit 2; }
# realpath -m is GNU-only; macOS/BSD fall back to python3 (same semantics: symlinks
# resolved, missing components kept).
abspath() {
  local out
  if out=$(realpath -m -- "$1" 2>/dev/null); then printf '%s\n' "$out"
  else python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1"; fi
}
here=$(cd "$(dirname "$0")" && pwd) || die "cannot locate the script directory"
tests=(); setup=""; cdir=""; remote=origin
while [ $# -gt 0 ]; do
  case "$1" in
    -t) [ $# -ge 2 ] || usage; tests+=("$2"); shift 2 ;;
    -s) [ $# -ge 2 ] || usage; setup="$2"; shift 2 ;;
    -C) [ $# -ge 2 ] || usage; cdir="$2"; shift 2 ;;
    -r) [ $# -ge 2 ] || usage; remote="$2"; shift 2 ;;
    --) shift; break ;;
    *) usage ;;
  esac
done
[ $# -gt 0 ] || { echo "base-run.sh: no command given" >&2; usage; }
[ ${#tests[@]} -gt 0 ] || { echo "base-run.sh: no test paths given" >&2; usage; }
[ -z "$cdir" ] || cd "$cdir" 2>/dev/null || die "cannot enter $cdir"

root=$(git rev-parse --show-toplevel) || die "not inside a git repository"
for t in "${tests[@]}"; do
  full=$(abspath "$root/$t") || die "cannot resolve $t"
  case "$full" in "$root"/*) ;; *) die "$t is outside the repo" ;; esac
  [ -e "$full" ] || die "$t not found (paths are repo-relative)"
done
for a in "$@"; do
  # a bare path, or the path after the first '=/' (--opt=/path, VAR=/path, --opt=key=/path)
  case "$a" in /*) v=$a ;; *=/*) v=/${a#*=/} ;; *) continue ;; esac
  # compare resolved paths: on macOS /var is /private/var, and show-toplevel is physical
  p=$(abspath "$v") || die "cannot resolve '$a'"
  case "$p" in "$root"/*|"$root") die "command argument '$a' is an absolute path into the working tree; use a relative path so the base copy is run" ;; esac
done

main=$("$here/default-branch.sh") || die "default branch could not be resolved"
GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh} -o BatchMode=yes" \
  git -C "$root" fetch -q "$remote" "$main" 2>/dev/null \
  || echo "base-run.sh: $remote unreachable, using last fetched $remote/$main" >&2
base=$(git -C "$root" merge-base "$remote/$main" HEAD) || die "cannot find merge-base with $remote/$main"
# drop only this script's own stale worktrees; never a general prune (other agents pin
# worktrees by name)
git -C "$root" worktree list --porcelain | sed -n 's|^worktree \(.*/base-run\.[^/]*\)$|\1|p' | while IFS= read -r w; do
  [ -d "$w" ] || git -C "$root" worktree remove --force "$w" >/dev/null 2>&1 || true
done
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
"$@"; rc=$?
case $rc in 126|127) echo "base-run.sh: warning: exit $rc: a command was not found or not executable in the base checkout (the command runs at its root); that is a reproduction only if the claim is that it is missing at base" >&2 ;; esac
exit $rc
