#!/usr/bin/env bash
# Syntax-check and smoke-test the helper scripts in a throwaway repo.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
bash -n "$here"/default-branch.sh "$here"/base-run.sh "$here"/mutate.sh

tmp=$(mktemp -d "${TMPDIR:-/tmp}/resolve-issue-selftest.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
fail() { echo "selftest: FAIL: $*" >&2; exit 1; }
expect_rc() { local want=$1; shift; local rc=0; "$@" >/dev/null 2>&1 || rc=$?; [ "$rc" -eq "$want" ] || fail "$* exited $rc, want $want"; }

cd "$tmp"
git init -q repo && cd repo
git config user.email t@t && git config user.name t
echo base > a && git add a && git commit -qm init && git branch -M main
git remote add origin . && git fetch -q origin && git remote set-head origin main
git switch -qc fix/1
echo changed > a && git commit -qam change
mkdir -p "t dir" && printf 'grep -q changed a\n' > "t dir/check.sh"
printf 'exit 7\n' > "t dir/rc.sh"

# default-branch: primary path (origin/HEAD), arguments rejected
[ "$("$here"/default-branch.sh)" = main ] || fail "default-branch"
expect_rc 2 "$here"/default-branch.sh extra

# base-run: usage errors
expect_rc 2 "$here"/base-run.sh
expect_rc 2 "$here"/base-run.sh -t "t dir/check.sh"
expect_rc 2 "$here"/base-run.sh -t
expect_rc 2 "$here"/base-run.sh -t "t dir/check.sh" -s
expect_rc 2 "$here"/base-run.sh -t ../escape -- true
expect_rc 2 "$here"/base-run.sh -t missing.sh -- true

# On the branch the check passes; at the base it must fail (a still says 'base').
bash "t dir/check.sh" || fail "check should pass on branch"
expect_rc 1 "$here"/base-run.sh -t "t dir/check.sh" -- bash "t dir/check.sh"
[ "$(git worktree list | wc -l)" -eq 1 ] || fail "base-run left a worktree"
[ "$(cat a)" = changed ] || fail "base-run touched the working tree"
expect_rc 7 "$here"/base-run.sh -t "t dir/rc.sh" -- bash "t dir/rc.sh"

# setup hook runs inside the base checkout; works from a subdirectory with repo-relative paths
"$here"/base-run.sh -t "t dir/check.sh" -s 'echo setup > setup.txt' -- test -f setup.txt 2>/dev/null \
  || fail "base-run setup"
(cd "t dir" && "$here"/base-run.sh -t "t dir/check.sh" -- test -f "t dir/check.sh" 2>/dev/null) \
  || fail "base-run from subdirectory"

# origin unreachable: falls back to the fetched ref
git remote set-url origin /nonexistent
expect_rc 1 "$here"/base-run.sh -t "t dir/check.sh" -- bash "t dir/check.sh"
git remote set-url origin .

# mutate: restore without backup fails; round-trip; no collision between d1/f and d1_f
expect_rc 1 "$here"/mutate.sh restore a
"$here"/mutate.sh backup a
echo mutated > a
"$here"/mutate.sh restore a
[ "$(cat a)" = changed ] || fail "mutate restore"
mkdir -p d1 && echo one > d1/f && echo two > d1_f
"$here"/mutate.sh backup d1/f && "$here"/mutate.sh backup d1_f
echo x > d1/f && "$here"/mutate.sh restore d1/f
[ "$(cat d1/f)" = one ] || fail "mutate key collision"
expect_rc 2 "$here"/mutate.sh frob a
[ ! -e "$tmp/escape" ] || fail "base-run wrote outside the worktree"

echo "selftest: ok"
