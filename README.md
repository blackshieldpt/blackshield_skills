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

## Usage

Install from this directory as a marketplace:

```
/plugin marketplace add /path/to/claude_skills
/plugin install blueprint@blackshield-skills
/plugin install coding@blackshield-skills
```

Or load one plugin for a single session:

```bash
claude --plugin-dir /path/to/claude_skills/coding
```
