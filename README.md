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

## Usage

Use a plugin in any project with:

```bash
claude --plugin-dir /path/to/claude_skills/blueprint
```
