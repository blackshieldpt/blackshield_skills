# Claude Skills

Custom [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skills used at Blackshield, packaged as plugins.

## Plugins

### Blueprint

**Path:** `blueprint/`

Comprehensive guidelines for building Go applications on the [Blueprint framework](https://github.com/oddbit-project/blueprint). Covers:

- Application lifecycle (New -> Build -> Start)
- Project structure and conventions
- Provider usage (PostgreSQL, ClickHouse, Kafka, NATS, etcd, S3, SMTP)
- HTTP API patterns with Gin
- Database access with GOQU query builder
- Authentication with [GoAuth](https://github.com/blackshieldpt/goauth) (JWT + Argon2)
- Testing with testify suites and testcontainers
- Migration patterns
- Error handling and logging with Zerolog

**Triggers automatically** when code imports `github.com/oddbit-project/blueprint` or `github.com/blackshieldpt/goauth`, or when the user mentions Blueprint/GoAuth.

### Coding

**Path:** `coding/`

General coding workflows. Currently one skill:

- `resolve-issue`: takes a defect or feature from GitHub issue to merged PR. One regression
  test per claim, run against the branch base before the fix and verified by mutation after
  it; a fresh-context adversarial review before the PR opens; loop-prevention rules for
  failed attempts. Requires `gh`. Helper scripts under `skills/resolve-issue/scripts/`;
  run `selftest.sh` after editing them.

Repo-specific rules for that workflow live in the target repo, not here. A ready-to-copy
example is in `project-notes/`.

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
- ~70 community-sourced pitfalls with fixes, plus a reading list

Every setting name, default and version claim was checked against the ClickHouse docs
(September 2026). Source URLs are listed in each file; `sources.md` holds the original
Tinybird articles it was distilled from.

**Triggers automatically** when writing or reviewing ClickHouse DDL/SQL, tuning ingestion
or query latency, designing materialized views, or debugging OOM, `TOO_MANY_PARTS`,
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
