# Testing detail

`$S` is the skill's `scripts/` directory, defined in SKILL.md Setup.

## Evidence table

One row per claim. Fill the base-run columns in phase 3 and the mutation columns in
phase 4. The reviewer receives it and the PR body carries it. A row with an empty column
is an unverified claim.

| claim | test | failure at base | hunk reverted | assertion that failed under mutation |
|---|---|---|---|---|

## Base run

`$S/base-run.sh` creates a throwaway worktree at `<base>`, copies the named test files
in, runs an optional setup command, then runs the test command there. It never stashes or
modifies the working tree, so it is safe on a branch with commits, on re-entry, and inside
an agent worktree. If `origin` is unreachable it uses the last fetched `origin/<main>`.
Pass `-s` for any gitignored setup the repo needs (fixtures, symlinks); project rules list
them. Test paths are repo-relative and must stay inside the repo.

Expected outcome: nonzero exit, and the failure text names the symptom from the issue.
When the test lives in a module with other tests, run that one test by name; unrelated
failures in the same module are not the reproduction.

## Mutation

Run only the test under mutation during the loop; run the full suites once at the end.
`$S/mutate.sh` copies the file to a backup directory inside `.git/` and back, keyed by
path. Neither `git checkout -- <file>` nor `git stash` is safe: on an uncommitted branch
the first discards the implementation, and on any branch the second discards uncommitted
test edits.

If the repo forbids parallel suite runs, this loop is serial by construction. Do not
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
