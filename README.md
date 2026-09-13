# Claude Skills

Custom [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skills used at Blackshield, packaged as plugins.

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

Install from this directory as a marketplace:

```
/plugin marketplace add /path/to/claude_skills
/plugin install blueprint@blackshield-skills
/plugin install coding@blackshield-skills
/plugin install clickhouse@blackshield-skills
```

Or load one plugin for a single session:

```bash
claude --plugin-dir /path/to/claude_skills/coding
```
