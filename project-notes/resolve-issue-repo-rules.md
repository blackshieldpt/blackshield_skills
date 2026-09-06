# resolve-issue project rules

Extracted from the `coding` plugin. This belongs in the target repo, not the plugin:
copy it into that repo's `CLAUDE.md`, or into `.claude/skills/resolve-issue-rules/SKILL.md`
with a frontmatter description "Project rules for the resolve-issue workflow". The
`resolve-issue` skill applies whatever it finds there on top of its own phases.

Detected by `test-compliance` and `test-retention` targets in the root `Makefile`.

## Issues

House style for titles and bodies: #477 to #480. Multi-claim examples: #450 listed three
consequences of one root cause; #416 named non-termination *and* partial-restore counts.
Labels are mostly unused; do not add any.

## Branches

Never target `release`. It only ever receives merges from `main` and is permanent; exclude
it from any branch cleanup. Slug examples from history: `fix/473-move-failure-reasons`,
`fix/download-session-pause-resume`, `feat/surface-bulk-failure-reasons`.

A change may span `apps/api` + `apps/web` in one PR.

Leftover `.claude/worktrees/agent-*` worktrees pin branch names; several `prNNN` ones hold
commits not on origin. Do not bulk-delete them.

## Conventions

The `shield_*` libs and Pokie are first-party. Violations of these have shipped or been
caught in review; add to the list as new ones appear.

- Database access goes through the query builder or repository layer used by the
  neighbouring code, never raw SQL strings in handlers or services.
- Errors use the existing typed error classes and the existing HTTP error mapping; do not
  raise bare exceptions or return ad hoc status codes.
- Logging uses the shared logger with structured fields, not `print` or f-strings.
- Frontend data access goes through the existing API client and hooks, not direct `fetch`.

## Tests

Root `Makefile` is canonical:

```bash
make test              # backend pytest, testcontainers, Docker required
make test-frontend     # apps/web vitest
make test-compliance   # when the area is touched
make test-retention    # when the area is touched
make e2e               # Playwright, host Node >= 24.14.1
make e2e-smoke
cd apps/web && npx tsc --noEmit   # typecheck
```

**Never run the backend suite in parallel**: not two terminals, not parallel agents.
Contention surfaces as an entire class erroring, indistinguishable from a real regression,
and costs a re-run each to disprove. If a class errors, re-run it in isolation and check a
`main` baseline worktree before reporting a regression.

Base-run and fresh-worktree setup (pass to `base-run.sh -s`):

- `cp <primary>/apps/api/src/license.dat <worktree>/license.dat`. pytest runs from the
  repo root and `LICENSE_FILE` is relative; without it every class errors in
  `build_pokie()`.
- `node_modules`: symlink from the primary checkout, but check the directory does not
  already exist first, or the result is `node_modules/node_modules`, a second React copy,
  and ~37 `.tsx` failures with `Cannot read properties of null (reading 'useRef')`.

Shipped instances of the test hazards: PR #444 asserted a known defect as intended
behaviour; PR #452's first cut reintroduced at the last chunk the bug it fixed at the
first, with its own test locking it in.

## CHANGELOG

- One bullet per change under the matching `[Unreleased]` subsection, same shape as the
  entries already there.
- #481 is the example of a review round catching a dev-only entry.

## Review and PR

There is no CI (`.github/` does not exist), so the adversarial review and local runs are
the only gate. #481 took five adversarial rounds, each of which rewrote something; its PR
body is the model for the PR layout. Merge strategy: check `git log`.
