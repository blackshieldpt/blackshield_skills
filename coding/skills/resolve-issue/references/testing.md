# Testing detail

`$S` and `<base>` are defined in SKILL.md Setup.

## Evidence table

One row per claim. Fill the base-run columns in phase 3 and the mutation columns in
phase 4. Keep it in a scratchpad file; post it as an issue comment at the end of phase 4
and give it to the reviewer. A row with an empty column is an unverified claim.

| claim | test file | command | setup (`-s`) | failure at base | hunk reverted | assertion that failed under mutation |
|---|---|---|---|---|---|---|

The command column is the exact `-- <command>` given to `base-run.sh`, so the reviewer
can repeat the run.

## Paths

`base-run.sh -t` paths are relative to the repo root, wherever the command is run from,
and must stay inside the repo; a directory (fixtures, snapshots) is accepted and copied
whole. The command after `--` must name the test by a relative path: an absolute path
runs the branch's copy of the file with the branch's configuration, not the base's, and
`base-run.sh` refuses it. `mutate.sh <file>` is relative to the current directory, like
any shell command.

## Base run

`$S/base-run.sh` creates a throwaway worktree at `<base>`, copies the named test files
in, runs an optional setup command, then runs the test command there. It never stashes or
modifies the working tree, so it is safe on a branch with commits, on re-entry, and inside
a linked worktree. The fetch never prompts; if `origin` is unreachable it uses the last
fetched `origin/<main>`. Exit status is the command's; the script's own failures (bad
arguments, copy or setup error) exit 2, so a 2 is never a failing test. Pass `-s` for any
gitignored setup the repo needs; project rules list them.

Expected outcome: nonzero exit, and the failure text names the symptom from the issue.
When the test lives in a module with other tests, run that one test by name; unrelated
failures in the same module are not the reproduction.

## Mutation

Run only the test under mutation during the loop; run the full suites once at the end.
`$S/mutate.sh` copies the file to a backup directory under the repo's common git
directory, keyed by path hash. `restore` consumes the backup, so a stale backup from an
earlier session can never be restored by mistake; back up again before the next mutation.
Neither `git checkout -- <file>` nor `git stash` is safe: on an uncommitted branch the
first discards the implementation, and on any branch the second discards uncommitted
test edits.

If project rules forbid parallel suite runs, this loop is serial by construction. Do not
dispatch agents to run mutations concurrently.

## Hazards that have shipped

A test that also passes against the pre-fix code proves nothing. Watch for:

- assertions satisfied both by the correct behaviour and by the feature never running;
- tests that fail on an ACL or fixture before reaching the logic under test;
- "sweep" tests that grep the source; they pass trivially once the string is gone;
- a test that asserts a known defect as intended behaviour;
- a PR whose last chunk reintroduces the bug its first chunk fixed, with its own test
  locking it in.

None of these are catchable by reading the test. Only the base run and the mutation
detect them.

## When a suite cannot run

Docker missing, wrong runtime version, a gitignored fixture absent: do not work around it
silently. Report the suite as **not run**, with the reason, in the PR body's Verification
section and to the user. If that suite covers the changed area, the PR is not ready to
merge.
