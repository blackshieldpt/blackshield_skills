#!/usr/bin/env bash
# Syntax-check and smoke-test the helper scripts in throwaway repos.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
bash -n "$here"/default-branch.sh "$here"/base-run.sh "$here"/mutate.sh

tmp=$(mktemp -d "${TMPDIR:-/tmp}/resolve-issue selftest.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
fail() { echo "selftest: FAIL: $*" >&2; exit 1; }
expect_rc() { local want=$1; shift; local rc=0; "$@" >/dev/null 2>&1 || rc=$?; [ "$rc" -eq "$want" ] || fail "$* exited $rc, want $want"; }

mkrepo() { # $1 dir, $2 default branch name
  git init -q "$1" && cd "$1"
  git config user.email t@t && git config user.name t
  echo base > a && git add a && git commit -qm init && git branch -M "$2"
  git remote add origin . && git fetch -q origin && git remote set-head origin "$2"
}

cd "$tmp"; mkrepo repo main
git switch -qc fix/1
echo changed > a && git commit -qam change
mkdir -p "t dir/fixtures" && printf 'grep -q changed a\n' > "t dir/check.sh"
printf 'exit 7\n' > "t dir/rc.sh"; echo fx > "t dir/fixtures/f"
printf 'test -f "t dir/fixtures/f"\n' > "t dir/needs-fixture.sh"

# default-branch: primary path (origin/HEAD), arguments rejected
[ "$("$here"/default-branch.sh)" = main ] || fail "default-branch"
expect_rc 2 "$here"/default-branch.sh extra

# base-run: usage and argument errors exit 2
expect_rc 2 "$here"/base-run.sh
expect_rc 2 "$here"/base-run.sh -t "t dir/check.sh"
expect_rc 2 "$here"/base-run.sh -t
expect_rc 2 "$here"/base-run.sh -t "t dir/check.sh" -s
expect_rc 2 "$here"/base-run.sh -t ../escape -- true
expect_rc 2 "$here"/base-run.sh -t missing.sh -- true
expect_rc 2 "$here"/base-run.sh -t "t dir/check.sh" -- bash "$PWD/t dir/check.sh"   # absolute path refused

# On the branch the check passes; at the base it must fail (a still says 'base').
bash "t dir/check.sh" || fail "check should pass on branch"
expect_rc 1 "$here"/base-run.sh -t "t dir/check.sh" -- bash "t dir/check.sh"
[ "$(git worktree list | wc -l)" -eq 1 ] || fail "base-run left a worktree"
[ "$(cat a)" = changed ] || fail "base-run touched the working tree"
expect_rc 7 "$here"/base-run.sh -t "t dir/rc.sh" -- bash "t dir/rc.sh"
expect_rc 0 "$here"/base-run.sh -t "t dir/rc.sh" -- true

# directories copied whole; setup hook runs inside the base checkout; subdirectory cwd
expect_rc 0 "$here"/base-run.sh -t "t dir/needs-fixture.sh" -t "t dir/fixtures" -- bash "t dir/needs-fixture.sh"
expect_rc 0 "$here"/base-run.sh -t "t dir/check.sh" -s 'echo setup > setup.txt' -- test -f setup.txt
expect_rc 2 "$here"/base-run.sh -t "t dir/check.sh" -s 'false' -- true              # setup failure is 2, not 1
(cd "t dir" && expect_rc 0 "$here"/base-run.sh -t "t dir/check.sh" -- test -f "t dir/check.sh")

# internal copy failure (a regular file at base where the test's directory must go) is 2
(
  cd "$tmp" && mkrepo repo3 main
  echo f > blk && git add blk && git commit -qm blk && git fetch -q origin main
  git switch -qc fix/3 && git rm -q blk && mkdir blk && printf 'true\n' > blk/test.sh
  expect_rc 2 "$here"/base-run.sh -t blk/test.sh -- bash blk/test.sh
)
cd "$tmp/repo"

# origin unreachable: falls back to the fetched ref, no prompt, no hang
git remote set-url origin /nonexistent
expect_rc 1 "$here"/base-run.sh -t "t dir/check.sh" -- bash "t dir/check.sh"
git remote set-url origin .

# mutate: restore without backup fails; round-trip consumes the backup; no key collision;
# cwd-relative paths from a subdirectory; non-file rejected
expect_rc 1 "$here"/mutate.sh restore a
"$here"/mutate.sh backup a
expect_rc 1 "$here"/mutate.sh backup a                                         # refuses to overwrite
echo mutated > a
"$here"/mutate.sh restore a
[ "$(cat a)" = changed ] || fail "mutate restore"
expect_rc 1 "$here"/mutate.sh restore a                                        # consumed
mkdir -p d1 && echo one > d1/f && echo two > d1_f
"$here"/mutate.sh backup d1/f && "$here"/mutate.sh backup d1_f
echo x > d1/f && "$here"/mutate.sh restore d1/f
[ "$(cat d1/f)" = one ] || fail "mutate key collision"
(cd d1 && "$here"/mutate.sh backup f && echo y > f && "$here"/mutate.sh restore f && [ "$(cat f)" = one ]) || fail "mutate from subdirectory"
expect_rc 2 "$here"/mutate.sh backup d1
expect_rc 2 "$here"/mutate.sh frob a

# mutate inside a linked worktree uses the common git dir
git worktree add -q --detach "$tmp/wt" HEAD
(cd "$tmp/wt" && "$here"/mutate.sh backup a && echo z > a && "$here"/mutate.sh restore a && [ "$(cat a)" = changed ]) || fail "mutate in linked worktree"
[ -d "$tmp/repo/.git/mutate-backups" ] || fail "backups not in common git dir"
git worktree remove --force "$tmp/wt"

# non-main default branch
cd "$tmp"; mkrepo repo2 trunk
[ "$("$here"/default-branch.sh)" = trunk ] || fail "default-branch trunk"
git switch -qc fix/2 && echo changed > a && git commit -qam change
printf 'grep -q changed a\n' > c.sh
expect_rc 1 "$here"/base-run.sh -t c.sh -- bash c.sh

echo "selftest: ok"
