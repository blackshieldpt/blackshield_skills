# Main — post-merge integration on the default branch

`$F` is `_shared/forge.py` next to this skill's directory: `<base directory>/../_shared/forge.py`, where the base directory holds this skill's `SKILL.md` (for a reference file, the skill that loaded it). Resolve it to an absolute path once. Always run it with `python3 -I`: `-I` ignores `PYTHON*` variables and user site-packages, so a module planted in the repository being worked on, or reached through `PYTHONPATH`, is not imported. `${CLAUDE_PLUGIN_ROOT}/skills/_shared/forge.py` is a Claude-only fallback. Subagents do not see this paragraph; pass them the absolute path. On a non-zero exit, print stderr and follow this skill's stop rule: 2 = usage, unsupported forge or missing configuration; 3 = authentication; 4 = not found; 5 = conflict; 1 = other failure. Exit 2 alone does not prove the forge is unsupported.

The Hard rules, project rules, app start procedure and report format are in `SKILL.md`; this file is the procedure for `main [<since-ref>] [--at <sha>]`.

## 1. Head

- Run `git fetch <upstream> <main>`. On failure, the verdict is STOPPED.
- Then fetch tags best-effort: `git fetch --tags <upstream>`. A failure there, such as a moved tag that git refuses to clobber, costs only the default range. Report it.
- `<sha>` is the full SHA from `git rev-parse --verify <upstream>/<main>^{commit}`, or from the `--at` argument normalized the same way (the `gate` mode passes one, and then this procedure prints no QA-VERDICT line of its own). An `--at` SHA must pass `git merge-base --is-ancestor <sha> <upstream>/<main>`, or the verdict is STOPPED. Use `<sha>` everywhere below, never the literal `HEAD`.
- Check `git version`. Below 2.29, the culprit search is not run ("git < 2.29").
- If `<scratchpad>/qa-main-<first 12 of sha>` is still registered from a crashed run, remove it with `git worktree remove --force` first. Create a disposable detached worktree: `git worktree add --detach <scratchpad>/qa-main-<first 12 of sha> <sha>`. Remove it with `--force` on every exit path.
- On the forge-failure fallback, `<upstream>` may be a fork's own copy, so GREEN is not possible: the verdict is at most NOT RUN ("upstream unverified").

## 2. Run

In that worktree, run `setup` once (with the host's maximum timeout), then `suite`, `e2e` and `smoke` one after another, never in parallel unless the rules allow it.

- Start the app with the SKILL.md procedure before any suite marked `needs-app`, and before `e2e` and `smoke` unless they are marked `self-hosted`. Stop the app after each suite.
- Before a self-hosted suite, run ready if it is declared; if it answers, the suite is NOT RUN. Another server would otherwise be tested in place of this build. A self-hosted suite with no declared ready is NOT RUN (server identity unverifiable).
- Record each command, its exit code and the failing test names. A suite passed only when it exits 0 and its output shows no failing test. A non-zero exit with no failing test named and no build/crash signature (for example a coverage threshold) is UNCLASSIFIED.
- Hard rule 3 applies:
  - A suite that cannot start, or that skips as a whole, is NOT RUN. Individual skipped tests are listed.
  - An `e2e` or `smoke` that turns out to be the unit suite (no app or service reached) is NOT RUN and reported as such.
  - When every failure is a fixture or setup error with one shared cause (Docker missing, a container that never started), that suite is NOT RUN, not failing.
- A setup or suite failure whose output shows a compile or crash error from the repo's own sources is a build or crash failure, recorded as <name>: build/crash. A compile error, a runner's build failure, or a panic before any test are examples. A missing tool, runtime, service or container is NOT RUN, never build/crash.
- A declared suite that does not run makes the verdict NOT RUN. An undeclared `e2e` or `smoke` is listed as "not declared". An undeclared `suite` is NOT RUN.
- A test command named anywhere in the project rules is covered only when it appears verbatim inside a declared QA command's value. Conditional commands ("when the area is touched") count as uncovered. List uncovered commands; they make GREEN impossible, and the verdict is NOT RUN.

## 3. Flaky check

For each failing test, re-run that test alone 3 times at the same commit with `test-one` (or `test-one-<suite>`), starting and stopping the app around each run when its suite needs it. Classify each run from the runner's output, the way step 5 does. A run whose output does not show the named test running makes the test UNCLASSIFIED.

- 1 or 2 passes: provisionally FLAKY (record the pattern; the whole-suite rule below can override it). 0 passes: CONSISTENT.
- Never classify a test FLAKY from a single re-run.
- For any test with at least one pass alone, re-run the whole suite up to twice. A test that fails in every whole-suite run is ORDER-DEPENDENT, not FLAKY, whatever its alone results. FLAKY needs 1 or 2 passes alone, or 3 passes alone, together with at least one passing whole-suite run. ORDER-DEPENDENT counts as RED: order and shared-state dependence is the typical two-PR integration bug.
- Grouping limits only the culprit search; every failing test gets its own flaky check. Group CONSISTENT failures by suite and error signature, and bisect at most one representative per group, at most 5 groups. Report the other group members as "culprit not searched (same signature as <test>)", and groups beyond the cap as "culprit not searched (cap)". If the number of failing tests makes per-test checks impractical, every test left unchecked is UNCLASSIFIED (RED).
- Without a declared `test-one`, a failing test is UNCLASSIFIED. It counts as RED, and no culprit search runs for it.

## 4. Range

Only for CONSISTENT failures and build/crash failures. ORDER-DEPENDENT failures are not bisected: `test-one` passes at every commit, so bisect would blame `<sha>`. Report "culprit not searched" for them.

- A `<since-ref>` argument must lie on main's first-parent line: `git rev-list --first-parent <sha> | grep -qx "$(git rev-parse <since-ref>^{commit})"`. Otherwise report "since-ref not on main's first-parent history; culprit search not run". A tag on a merged release branch passes `--is-ancestor` yet breaks a first-parent bisect.
- With no argument, use `git describe --tags --abbrev=0 --first-parent <sha>^`. If there is no such tag, or it is fewer than 10 first-parent commits back, use the 50th first-parent ancestor instead: `git rev-list --first-parent -n 51 <sha> | tail -1`. Report which one was used.

## 5. Culprit

Write a script to the scratchpad directory, outside the worktree.

- It first `cd`s into the worktree and aborts with 125 unless `git rev-parse --show-toplevel` equals `(cd <worktree> && pwd -P)` (git prints the physical path; macOS temp directories are symlinks). It exports `QA_WORKTREE` and `QA_PRIMARY` like every rule command. Inside the script, "the SHA under test" (for the SKILL.md HEAD and `version` checks) is the `git rev-parse HEAD` that bisect checked out. It starts the app with the SKILL.md procedure written as a plain shell sequence: a poll loop bounded by `ready-timeout`, and per-run file names.
- It runs `setup` and starts the app when the test's suite needs it. "ready answered before start" makes it exit 125, and so does the self-hosted pre-check. It then runs `test-one <name>`. For a build/crash failure it runs `setup` and the suite instead. It exits 1 when the same error signature recurs, and 0 only when setup and the build finish with no compile or crash error at all; any other compile or crash error exits with code 125. That overrides the 125 rule for setup errors carrying the target signature.
- A `trap` on EXIT stops the app, then runs `git reset --hard -q && git clean -ffdxq` in the worktree. `setup` re-runs at every step, so files it wrote, ignored ones included, never block the next checkout.
- Exit codes come from the output, never from the runner's exit status alone, because runners overload exit codes (`go test` returns 1 for build and test failures alike):
  - 1 only when the output shows the named test ran and failed (or, for build/crash, the same build failure recurred);
  - 0 only when the output shows it ran and passed (for build/crash: no compile or crash error at all);
  - every other outcome "exits 125" (bisect's skip): setup, app-start and ready failures, a test that does not exist at that commit, and output that cannot be classified.
- Before bisecting, confirm the script exits 1 at `<sha>`. If it does not, name no culprit.
- since-ref must exit 0: run the script at `<since-ref>` (`git -C <worktree> checkout --detach <since-ref>`, then back to `<sha>`). Exit 1: report "pre-existing at `<since-ref>`" and do not bisect. Exit 125 because the test is absent at `<since-ref>`: find the first first-parent commit F after it where `test-one` output shows the test running; if the script exits 0 at F, bisect from F instead, and if it exits 1 there, report "test first appears at F; no culprit". Any other exit 125: report "since-ref not testable; culprit search not run".
- Bisect step by step, so no single host command outlives the host's timeout. In the worktree run `git bisect start --first-parent <sha> <since-ref>`, giving the peeled SHA of the since-ref, never the tag name. Then, one tool call per step, run the script (in the background when it may exceed the host timeout) and feed its result to `git bisect good`, `git bisect bad` or `git bisect skip`. Always finish with `git bisect reset`. If a step is interrupted, kill the recorded process group, run the reset and clean, and mark that commit `git bisect skip`.
- If bisect ends with only skipped commits left, report the candidate list and name no culprit. If the boundary is a commit where the test first appears, report "test first appears at C; no culprit".
- Confirm the culprit C: the script exits 1 at C and exits 0 at C^1, 3 times each. Otherwise name no culprit and report that the culprit was not confirmed.

## 6. Attribution

- First, look for a PR number in the culprit's own message: `(#N)`, `Merge pull request #N`, `Reviewed-on: …/pulls/N`. Verify it with `python3 -I "$F" pr view N --repo "$REPO"`: its `merge_commit` must equal the culprit. A failed `pr view` rejects only that candidate; only a failed `repo` or `pr list` skips attribution.
- Failing that, match the culprit SHA against `merge_commit` in `python3 -I "$F" pr list --repo "$REPO" --state merged --limit 200`.
- Failing that (an earlier commit of a rebase merge), walk forward with `git rev-list --reverse --first-parent --ancestry-path <culprit>..<sha>` to the first commit that equals some PR's `merge_commit`. Attribute the culprit to that PR only when all of these hold:
  - that `merge_commit` has one parent (a rebase or fast-forward landing; squash and true merges never land their own commits on the first-parent line);
  - every first-parent commit from C through `merge_commit` matches, in order, the last entries of its `commit_messages`, compared after stripping whitespace;
  - the PR has fewer than 100 commits (the adapter truncates larger ones).
- Otherwise report the bare commit: a direct push, a bot commit, or a PR outside the window. --file never files against a bare commit.

## 7. CI signal

Run `python3 -I "$F" pr checks N --repo "$REPO"` and read `state` and `statuses`.

- `success`: "PR CI green (may not include `<test>`)". When `state` is `unknown`, every status is `success` or `unknown`, and at least one is `success`, report "green apart from unknown checks (skipped, neutral, stale or warning)".
- `failure`: "merged with failing CI".
- Otherwise: "no CI signal".

Claim "integration-only: green alone, red after merge" only when all of these hold, checked after `git bisect reset`:

- M is the confirmed culprit and equals the attributed PR's `merge_commit`;
- M is a true merge (`git rev-list --parents -n1 M` prints three fields);
- the merge changed something beyond the PR side (`! git diff --quiet M^2 M`);
- the test exists at `M^2`, and the script exits 0 there in each of 3 runs.

`M^2` is an ancestor of `<sha>`, so Hard rule 4 allows running it.

## 8. Verdict

Pick the verdict from this list: first match wins, in this order:

1. RED: any CONSISTENT, ORDER-DEPENDENT or UNCLASSIFIED failure, or any build/crash failure. Name the culprit PR, the bare commit, "pre-existing", "culprit not searched", "culprit not confirmed", "test first appears at C", "no range" (no since-ref could be resolved), "since-ref not on main's first-parent history", "since-ref not testable", "git < 2.29" or "shallow clone".
2. NOT RUN, with the reasons (uncovered test commands and unverified artifact provenance included).
3. FLAKY-ONLY: at least one failure, and every failure FLAKY.
4. GREEN: `suite` ran and passed, and every other declared suite ran and passed.

Report `<sha>`, the range used, the suites that ran, those not declared and those uncovered. Known limits: a test renamed inside the range blames the renaming commit, and a direct push with a generic message ("fix tests") can be wrongly attributed. Remove the worktree and end with `QA-VERDICT: main <RED|NOT-RUN|FLAKY-ONLY|GREEN|STOPPED> <sha>`, printing `-` in place of `<sha>` when STOPPED. When `gate` runs this procedure, omit that line: the gate's own line ends the report.
