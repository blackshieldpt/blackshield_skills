# Blackshield Skills

Reusable agent skills used at Blackshield, packaged as Claude Code plugins and
also installable as plain Agent Skills in OpenCode and Codex.

## Plugins

### Blueprint

**Path:** `blueprint/`

Comprehensive guidelines for building Go applications on the [Blueprint framework](https://github.com/oddbit-project/blueprint). Covers:

- Application lifecycle (New -> Build -> Start)
- Project structure and conventions
- Provider setup (SMTP, S3, TLS, NATS), buffered ClickHouse writer, PostgreSQL records
- HTTP API patterns with Gin
- Database access with GOQU query builder
- Authentication with [GoAuth](https://github.com/blackshieldpt/goauth) (JWT + Argon2)
- Testing with testify suites and testcontainers
- Migration patterns
- Error handling and logging with Zerolog

`SKILL.md` carries the stack, lifecycle, project structure and style rules; the
per-area guides (configuration, service layer, HTTP, auth, errors, components, database,
RPC, testing, logging, providers) live under `references/` and are loaded on demand.

**Triggers automatically** when code imports `github.com/oddbit-project/blueprint` or `github.com/blackshieldpt/goauth`, or when the user mentions Blueprint/GoAuth.

### Coding

**Path:** `coding/`

General coding workflows:

- `resolve-issue`: takes a defect or feature from GitHub issue to merged PR. Triggers on
  "fix/resolve/address/implement issue N", "open a PR for issue N", "land this change" —
  not on ad-hoc fixes with no issue. Files missing issues through `file-issue`, runs
  `review quick issue N` and the built-in review skills before its own adversarial
  round. One regression
  test per claim, run against the branch base before the fix and verified by mutation after
  it; a fresh-context adversarial review before the PR opens; loop-prevention rules for
  failed attempts. Requires `gh`. Helper scripts under `skills/resolve-issue/scripts/`;
  run `selftest.sh` after editing them.
- `file-issue`: turns a description, error, or code location into a GitHub issue in the
  house style `resolve-issue` consumes. Duplicate check; every claim cited at the callee
  (`path:line` + quote), trigger proven constructible, executed where cheap; kind/scope/
  severity labels with scope inferred from the project structure; severity graded by
  consequence; a fresh-context pass tries to disprove each claim before the user sees the
  draft; filed with `gh` only after confirmation. Triggers on "open/file/create an issue",
  "report this bug", "write this up as an issue".
- `review`: read-only review of a branch, PR, range, or paths in the `improve` style —
  light recon and stack-skill loading, audit across correctness/security/tests/perf/
  debt/deps/docs, every finding opened at the cited line, HIGH and MED findings sent
  to fresh-context refuters (REFUTED / OVERSTATED / CONFIRMED), then a ranked table
  split into introduced vs pre-existing, a tests-as-claims section, "considered and
  rejected", "not reviewed", and an APPROVE / REVISE / BLOCK verdict. Never edits code;
  `--comment` posts the report to the PR after it has been shown and confirmed. Triggers on "review my
  changes", "review PR N", "review the branch", "review commit X". Deeper than the
  built-in `/code-review`, which keeps bare "code review"; invoke explicitly as
  `/coding:review` when both are installed.
- `improve`: senior-advisor survey of a codebase (adapted from
  [shadcn/improve](https://github.com/shadcn/improve), MIT) — recon with stack-skill
  loading, a parallel audit across correctness/security/perf/tests/debt/deps/DX/docs/
  direction at `quick`/`standard`/`deep` effort, HIGH and MED findings sent to
  fresh-context refuters, then prioritized, self-contained handoff plans under `plans/`
  for a cheaper model to execute. Focus args (`security`, `perf`, `tests`), `next` for
  direction, `plan <description>` for a single plan, `--issues` to publish plans with
  `gh`. Never edits source code. Triggers on "audit this codebase", "what should we
  improve", "what should we build next". The four reference files under
  `skills/improve/references/` are shared with the three skills below.
- `review-plan`: critiques and tightens a plan in `plans/` against the template and the
  per-plan falsifiability gate; cold read by a fresh-context subagent and a premise
  verifier that attacks the fix. Triggers on "review plan N", "tighten the plan".
- `execute-plan`: refuses a drifted plan, or one that meets the premise-verifier bar with no
  `Premise verified` record from `review-plan`; otherwise dispatches one executor subagent on a plan in an
  isolated worktree and reviews its diff like a tech lead — done criteria re-run, scope,
  tests, falsifiability bar — to an APPROVE / REVISE / BLOCK verdict; `review-merged`
  reviews executed plans merged together as one change. Never merges or pushes.
  Triggers on "execute plan N", "dispatch plan N to haiku".
- `reconcile-plans`: processes the `plans/` backlog since the last session — verifies
  DONE, investigates BLOCKED, flags stale IN PROGRESS, refreshes drifted TODOs, reports
  audit-coverage gaps. Triggers on "reconcile the plans", "what's the status of the plans".
- `audit-codebase-security`: evidence-driven security audit of a repository, component, or
  diff (from [blackshieldpt/secscanner](https://github.com/blackshieldpt/secscanner), where
  the CVE analysis behind it lives). Profiles the repo with a bundled script, maps the
  attack surface, selects checks from a CVE-derived bug taxonomy plus JS/TS, Python, Go and
  C/C++ packs, and reports only findings that pass its evidence gate; `--json` output is
  checked by `scripts/validate_findings.py`. Triggers on "find vulnerabilities", "security
  audit". Run `python3 -m unittest discover -s coding/skills/audit-codebase-security/scripts
  -p 'test_*.py'` after editing the scripts.

Repo-specific rules for these workflows (test commands, scope-label vocabulary, house-style
examples) live in the target repo, not here. A ready-to-copy example is in `project-notes/`.

### ClickHouse

**Path:** `clickhouse/`

Practices, tunables and gotchas for designing, querying and operating ClickHouse. `SKILL.md`
holds the rules that matter most; eight reference files go deeper:

- Sorting-key, partitioning, codec and data-type design; projections, dictionaries, schema evolution
- Query optimization: filter order, PREWHERE, JOIN strategies, aggregate states, FINAL-heavy tables
- Ingestion: batching, parts and merges, `TOO_MANY_PARTS`, insert deduplication, mutations, TTL, Kafka
- Incremental and refreshable materialized views, backfills
- Distributed tables, replication, Keeper, backup/restore, Cloud vs OSS differences
- Diagnostics (`EXPLAIN`, `system.query_log`, profiling) and access control/quotas
- Operations: topology, upgrades, monitoring, incident playbook
- ~60 community-sourced pitfalls with fixes, plus a reading list

Every setting name, default and version claim was checked against the ClickHouse docs
(September 2026). Each reference file ends with its sources; `clickhouse/sources.md` lists
the articles the core rules were distilled from.

**Triggers automatically** when writing or reviewing ClickHouse DDL/SQL, tuning ingestion
or query latency, designing materialized views, judging ClickHouse SQL inside a code
review, or debugging OOM, `TOO_MANY_PARTS`, stuck mutations, Keeper trouble,
read-only tables or slow merges.

## Usage

### Claude Code

Install from this directory as a marketplace:

```
/plugin marketplace add /path/to/blackshield_skills
/plugin install blueprint@blackshield-skills
/plugin install coding@blackshield-skills
/plugin install clickhouse@blackshield-skills
```

Each plugin carries a trigger eval suite under `<plugin>/evals/` (does the right skill
fire, and stay silent, for representative prompts). Run one with
`claude plugin eval ./<plugin> --no-publish --trust-plugin` from this directory (the path form — a bare plugin name resolves to the installed cache copy, which carries no evals); a full
coding suite is 18 cases × 2 runs × 2 arms at roughly $0.10 a run, so use `--case <glob>` or
`--runs 1` when iterating. Results land in `<plugin>/evals/results/` (ignored).
Cases with a `context.scaffold_script` (the Blueprint import and hard-negative
cases) require `--scaffold`; without it the fixture is not placed and the case
is a false negative.

Or load one plugin for a single session:

```bash
claude --plugin-dir /path/to/blackshield_skills/coding
```

### Release gate

A release gate is a full eval run against the **current plugin version** with
`--runs 2` and both arms. `--runs 1` is for iteration only; do not cite it as a
release result. After editing a skill's content, re-run that plugin's suite — a
green result from an older version is not evidence for the current one. Results
live under `<plugin>/evals/results/` (ignored) and are not a source of truth.

### OpenCode

OpenCode reads Agent Skills directly; it does not use the Claude Code plugin
marketplace. Global skills live in `~/.config/opencode/skills/<name>/SKILL.md`;
project skills live in `.opencode/skills/<name>/SKILL.md`. OpenCode also reads
the compatibility locations `~/.claude/skills`, `~/.agents/skills`,
`.claude/skills` and `.agents/skills`.

The commands below are idempotent. `-sfn` replaces an existing link, and the
loop aborts if a folder has no `SKILL.md`.

```bash
SKILLS=/path/to/blackshield_skills
DEST="$HOME/.config/opencode/skills"
mkdir -p "$DEST"
for d in "$SKILLS"/blueprint/skills/blueprint "$SKILLS"/clickhouse/skills/clickhouse "$SKILLS"/coding/skills/*; do
  [ -f "$d/SKILL.md" ] || { echo "not a skill: $d" >&2; exit 1; }
  ln -sfn "$d" "$DEST/$(basename "$d")"
done
test -r "$DEST/blueprint/SKILL.md"
test -r "$DEST/improve/SKILL.md"
```

For a project-local install, use `DEST="$PWD/.opencode/skills"`; the same
`mkdir -p`, `ln -sfn` and `test -r` checks apply.

The `coding` skills use `../improve/references`. Symlinking every coding sibling
(as above) preserves it; a flat copy of only one folder breaks it. If symlinks
are unavailable (for example on Windows), copy the whole `coding/skills` tree
so the siblings stay together.

Invocation:

```text
Use the `blueprint` skill to add a GET /products/{id} endpoint.
Use the `clickhouse` skill to explain why this query is slow.
Use the `review` skill to review my branch before I open a PR.
```

OpenCode v1 loads a skill on demand through its native `skill` tool. In
OpenCode v2, mention it as `@blueprint`, `@clickhouse` or `@review`. Do not rely
on `/blueprint`; slash-command exposure is not guaranteed in current versions.

### Codex

Codex reads repository skills from `.agents/skills/<name>/SKILL.md` (walking up
to the repository root), user skills from `~/.agents/skills/<name>/SKILL.md`,
and admin skills from `/etc/codex/skills`. Some older builds only read
`.codex/skills` / `~/.codex/skills`; use the path your `codex --version`
documents.

```bash
SKILLS=/path/to/blackshield_skills
DEST="$HOME/.agents/skills"
mkdir -p "$DEST"
for d in "$SKILLS"/blueprint/skills/blueprint "$SKILLS"/clickhouse/skills/clickhouse "$SKILLS"/coding/skills/*; do
  [ -f "$d/SKILL.md" ] || { echo "not a skill: $d" >&2; exit 1; }
  ln -sfn "$d" "$DEST/$(basename "$d")"
done
test -r "$DEST/blueprint/SKILL.md"
test -r "$DEST/improve/SKILL.md"
```

For a project-local install, use `DEST="$PWD/.agents/skills"` and the same
checks. Do not commit absolute symlinks into a shared repository; use relative
links or copy the whole `coding/skills` tree.

Invoke a skill explicitly with `$skill-name`, or open the `/skills` picker:

```text
$blueprint Add a GET /products/{id} endpoint to this Go service.
$clickhouse Why is this query slow, and what should I change?
$review Review my branch before I open a PR.
```

Codex can also select a skill implicitly from its description. If the initial
skill list is capped, install only the skills you need or raise
`skills.max_context_tokens` in the Codex configuration.

Both OpenCode and Codex read `~/.agents/skills`, so one symlink tree there
works for both. OpenCode also reads `~/.claude/skills`; remove stale copies
there if you see a duplicate. OpenCode resolves duplicate IDs by precedence;
Codex shows duplicate names in its selector rather than merging them.

`blueprint` and `clickhouse` are the most portable skills here. The `coding`
skills are Claude Code-first: they use `$ARGUMENTS`,
`${CLAUDE_PLUGIN_ROOT}`, Claude subagent types and `gh`. The instructions load,
but workflows that depend on those harness features may not run unchanged.
The `claude plugin eval` commands are Claude Code-only.

## License

MIT. Original work copyright © 2026 shadcn; adapted in
`coding/skills/improve`, `review-plan`, `execute-plan` and `reconcile-plans`;
modifications copyright © 2026 Blackshield. See [`LICENSE.md`](LICENSE.md). The
`coding/skills/improve` skill is adapted from
[`shadcn/improve`](https://github.com/shadcn/improve); its original notice is
retained in `coding/skills/improve/LICENSE.md`.
