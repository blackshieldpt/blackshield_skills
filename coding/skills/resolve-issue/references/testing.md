# Testing detail

`$S` and `<base>` are defined in SKILL.md Setup.

## Evidence table

One row per claim. Fill the base-run columns in phase 3 and the mutation columns in
phase 4. Keep it in a scratchpad file; post it as an issue comment at the end of phase 4
and give it to the reviewer. A row with an empty column is an unverified claim.

| claim | test file | command | setup (`-s`) | failure at base | hunk reverted | assertion that failed under mutation |
|---|---|---|---|---|---|---|

A guard from phase 3's falsification step passes at `<base>`; write "passes at base (guard)"
in that column rather than leaving it empty, so it is not read as a reproduction that did
not reproduce. It still needs a mutation hunk.

The command column is the exact `-- <command>` given to `base-run.sh`, so the reviewer
can repeat the run.

## Paths

A test file named with `-t` must **compile at `<base>`**. A single test referencing a
symbol the change introduces makes the whole file fail to build there, and the reproduction
becomes a compile error rather than the issue's symptom. Keep tests that name new symbols
in a separate file from the ones that reproduce the defect, and keep constants they need
out of the reproduction's dependencies. A compile error is the honest base failure only
when the claim is that the behaviour does not exist yet.

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
arguments, copy or setup error) exit 2 with a `base-run.sh:` line on stderr — check for
that line rather than the code alone, since pytest also exits 2 on interruption. Pass `-s` for any
gitignored setup the repo needs; project rules list them.

Expected outcome: nonzero exit, and the failure text names the symptom from the issue.
When the test lives in a module with other tests, run that one test by name; unrelated
failures in the same module are not the reproduction.

## Mutation

Run only the test under mutation during the loop; run the full suites once at the end.
`$S/mutate.sh` copies the file to a backup directory under the repo's common git
directory, keyed by path hash. `backup` refuses when a backup for that file already
exists (restore it first — never back up a mutated file over the good copy); `restore`
consumes the backup. A backup left by a crashed session therefore surfaces as a refused
`backup` on the next run: inspect it, then `restore` or delete it deliberately.
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
  locking it in;
- tests that all exercise the path the claim is about, while the defect lives on the path
  beside it — every test of a failure path passing while the bug is on the success path;
- a fix whose tests all use one input shape, where the defect is in a shape the author did
  not think of;
- a bound tested only against inputs it should reject, so nothing notices it also rejects
  inputs it should accept.

The first five are not catchable by reading the test; the base run and the mutation detect
them. The last three are not catchable by mutation either — the tests and the fix share
the author's wrong model, so they agree with each other. Phase 3's falsification step and
the phase 6 review are what find those.

## When a suite cannot run

Docker missing, wrong runtime version, a gitignored fixture absent: do not work around it
silently. Report the suite as **not run**, with the reason, in the PR body's Verification
section and to the user. If that suite covers the changed area, the PR is not ready to
merge.

A suite that is gated behind an environment variable and skips by default is **not run**
either, however green the output looks. Check what actually executed rather than the exit
status: `--- SKIP` lines and a passing package are the same colour as a passing run.

The same applies to **CI that cannot start**. When jobs fail without executing a step — a
billing or quota block, a runner that is never allocated, a queue that never drains — say
so explicitly, name the cause, and do not present the run as evidence either way. Say which
suites ran locally instead and on how many machines, and leave the merge decision to the
user: local verification on one machine is not the same claim as a green pipeline, and a
PR merged that way should say so in its body.
