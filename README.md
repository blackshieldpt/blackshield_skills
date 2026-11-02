# Blackshield Skills

Reusable agent skills used at Blackshield. They are packaged as Claude Code plugins
and can also be installed as plain Agent Skills in OpenCode and Codex.

## Plugins

| Plugin | Path | Skills |
|---|---|---|
| [Blueprint](#blueprint) | `blueprint/` | `blueprint` |
| [Coding](#coding) | `coding/` | `resolve-issue`, `file-issue`, `review`, `audit-codebase-security`, `improve`, `review-plan`, `execute-plan`, `reconcile-plans` |
| [ClickHouse](#clickhouse) | `clickhouse/` | `clickhouse` |

### Blueprint

Guidelines for building Go applications on the
[Blueprint framework](https://github.com/oddbit-project/blueprint), with authentication
through [GoAuth](https://github.com/blackshieldpt/goauth) (JWT + Argon2). Covers:

- Application lifecycle (New → Build → Start), project structure and conventions
- Providers: SMTP, S3, TLS, NATS, the buffered ClickHouse writer, PostgreSQL records
- HTTP APIs with Gin
- Database access with the GOQU query builder, and migrations
- Testing with testify suites and testcontainers
- Error handling and logging with Zerolog

`SKILL.md` holds the stack, lifecycle, project structure and style rules. The per-area
guides (configuration, service layer, HTTP, auth, errors, components, database, RPC,
testing, logging, providers) live under `references/` and load on demand.

**Triggers** when code imports `github.com/oddbit-project/blueprint` or
`github.com/blackshieldpt/goauth`, or when you mention Blueprint or GoAuth.

### Coding

General coding workflows. Repo-specific rules (test commands, scope-label vocabulary,
house-style examples) belong in the target repository, not here; `project-notes/` has a
ready-to-copy example.

#### Issues and pull requests

- **`resolve-issue`** takes a defect or feature from GitHub issue to merged PR. It files a
  missing issue through `file-issue`, writes one regression test per claim, runs it
  against the branch base before the fix and verifies it by mutation after. Before the PR
  opens it runs `review quick issue N`, the built-in review skills and its own
  fresh-context adversarial review. Loop-prevention rules stop repeated failed attempts.
  Requires `gh`. After editing the helper scripts in `skills/resolve-issue/scripts/`, run
  `selftest.sh`.
  *Triggers:* "fix/resolve/address/implement issue N", "open a PR for issue N", "land this
  change". Not ad-hoc fixes with no issue.
- **`file-issue`** turns a description, error or code location into a GitHub issue in the
  house style `resolve-issue` consumes. It checks for duplicates, cites every claim at the
  callee (`path:line` plus a quote), proves the trigger constructible and executes it where
  cheap. Kind, scope and severity labels are applied, with scope inferred from the project
  structure and severity graded by consequence. A fresh-context pass tries to disprove each
  claim before you see the draft, and the issue is filed with `gh` only after you confirm.
  *Triggers:* "open/file/create an issue", "report this bug", "write this up as an issue".

#### Review and audit

- **`review`** is a read-only review of a branch, PR, commit range or paths, in the
  `improve` style: light recon and stack-skill loading, an audit across correctness,
  security, tests, performance, debt, dependencies and docs, and every finding opened at
  the cited line. HIGH and MED findings go to fresh-context refuters (REFUTED /
  OVERSTATED / CONFIRMED). The report has a ranked table split into introduced and
  pre-existing findings, a tests-as-claims section, "considered and rejected", "not
  reviewed", and an APPROVE / REVISE / BLOCK verdict. It never edits code; `--comment`
  posts the report to the PR after you have seen and confirmed it.
  *Triggers:* "review my changes", "review PR N", "review the branch", "review commit X".
  It goes deeper than the built-in `/code-review`, which keeps bare "code review"; when
  both are installed, invoke it as `/coding:review`.
- **`audit-codebase-security`** is an evidence-driven security audit of a repository,
  component or diff, from [blackshieldpt/secscanner](https://github.com/blackshieldpt/secscanner),
  where the CVE analysis behind it lives. It profiles the repo with a bundled script, maps
  the attack surface, picks checks from a CVE-derived bug taxonomy plus JS/TS, Python, Go
  and C/C++ packs, and reports only findings that pass its evidence gate. `--json` output is
  checked by `scripts/validate_findings.py`. After editing the scripts, run
  `python3 -m unittest discover -s coding/skills/audit-codebase-security/scripts -p 'test_*.py'`.
  *Triggers:* "find vulnerabilities", "security audit".

#### Planning

`improve` writes plans; the other three critique, execute and track them. All four share
the reference files under `skills/improve/references/`.

- **`improve`** is a senior-advisor survey of a codebase, adapted from
  [shadcn/improve](https://github.com/shadcn/improve) (MIT). It runs recon with stack-skill
  loading, then a parallel audit across correctness, security, performance, tests, debt,
  dependencies, DX, docs and direction at `quick`, `standard` or `deep` effort. HIGH and
  MED findings go to fresh-context refuters, and the survivors become prioritized,
  self-contained handoff plans under `plans/` for a cheaper model to execute. Arguments:
  a focus (`security`, `perf`, `tests`), `next` for direction, `plan <description>` for a
  single plan, `--issues` to publish plans with `gh`. Never edits source code.
  *Triggers:* "audit this codebase", "what should we improve", "what should we build next".
- **`review-plan`** critiques and tightens a plan in `plans/` against the template and the
  per-plan falsifiability gate: a cold read by a fresh-context subagent, and a premise
  verifier that attacks the fix.
  *Triggers:* "review plan N", "tighten the plan".
- **`execute-plan`** dispatches one executor subagent on a plan in an isolated worktree,
  then reviews its diff like a tech lead (done criteria re-run, scope, tests,
  falsifiability bar) to an APPROVE / REVISE / BLOCK verdict. It refuses a drifted plan,
  and a plan that meets the premise-verifier bar but has no `Premise verified` record from
  `review-plan`. `review-merged` reviews executed plans merged together as one change.
  Never merges or pushes.
  *Triggers:* "execute plan N", "dispatch plan N to haiku".
- **`reconcile-plans`** processes the `plans/` backlog since the last session: it verifies
  DONE, investigates BLOCKED, flags stale IN PROGRESS, refreshes drifted TODOs and reports
  audit-coverage gaps.
  *Triggers:* "reconcile the plans", "what's the status of the plans".

### ClickHouse

Practices, tunables and gotchas for designing, querying and operating ClickHouse.
`SKILL.md` holds the rules that matter most; eight reference files go deeper:

- Sorting-key, partitioning, codec and data-type design; projections, dictionaries,
  schema evolution
- Query optimization: filter order, PREWHERE, JOIN strategies, aggregate states,
  FINAL-heavy tables
- Ingestion: batching, parts and merges, `TOO_MANY_PARTS`, insert deduplication,
  mutations, TTL, Kafka
- Incremental and refreshable materialized views, backfills
- Distributed tables, replication, Keeper, backup/restore, Cloud vs OSS differences
- Diagnostics (`EXPLAIN`, `system.query_log`, profiling) and access control/quotas
- Operations: topology, upgrades, monitoring, incident playbook
- About 60 community-sourced pitfalls with fixes, plus a reading list

Every setting name, default and version claim was checked against the ClickHouse docs
(September 2026). Each reference file ends with its sources; `clickhouse/sources.md` lists
the articles the core rules were distilled from.

**Triggers** when writing or reviewing ClickHouse DDL/SQL, tuning ingestion or query
latency, designing materialized views, judging ClickHouse SQL inside a code review, or
debugging OOM, `TOO_MANY_PARTS`, stuck mutations, Keeper trouble, read-only tables or slow
merges.

## Compatibility

| Area | Claude Code | OpenCode | Codex | Forgejo/Gitea |
|---|---|---|---|---|
| `blueprint`, `clickhouse` | yes | yes | yes | yes |
| `audit-codebase-security` | yes | yes | yes | yes |
| `review` local scopes | yes | yes | yes | yes |
| `review` PR reads, `reconcile-plans` PR fallback | yes | yes (`forge.py`; install `_shared`) | yes (`forge.py`; install `_shared`) | yes (`forge.py`) |
| `file-issue`, `resolve-issue`, `review --comment`, `improve --issues` | yes | yes (`forge.py`; install `_shared`) | yes (`forge.py`; install `_shared`) | yes (`forge.py`; token) |
| Plan workflows | yes | yes (`forge.py`; install `_shared`) | yes (`forge.py`; install `_shared`) | yes (`forge.py`) |

The forge adapter and the harness contract live in `coding/skills/_shared/`.
`review` PR reads and the `reconcile-plans` PR fallback run through the adapter: `gh` on GitHub, the
REST API on Forgejo/Gitea. On Forgejo/Gitea the reconcile fallback can confirm a merge but never proves
its absence: an empty list there leaves the plan unverified. On OpenCode and Codex they also need `_shared` installed beside the skills,
as the install commands below do. The write workflows (issue filing, issue resolution,
`review --comment` and `improve --issues`) and the plan workflows also run through the adapter,
with the same `_shared` install; on Forgejo/Gitea every write needs a token. Where a host cannot
start a fresh-context subagent, their disproof and review passes run inline and are labelled
`unvetted`; `file-issue` files and `resolve-issue` merges only after the user accepts that label,
and `execute-plan` hands the plan over for manual execution.

Forgejo/Gitea configuration: set `FORGE_TYPE` (`gitea` or `forgejo`) when the
host name does not identify the forge; `FORGE_URL` when the API is not at `https://<host>` (a subpath such as
`https://example.org/git` works; it applies to every repository the skills read); and a token in
`FORGE_TOKEN`, `GITEA_TOKEN` or `FORGEJO_TOKEN` for private repositories and for every write —
public reads work anonymously. GitHub and GitHub Enterprise use `gh auth`.

## Usage

### Claude Code

Add this directory as a marketplace, then install the plugins you want:

```text
/plugin marketplace add /path/to/blackshield_skills
/plugin install blueprint@blackshield-skills
/plugin install coding@blackshield-skills
/plugin install clickhouse@blackshield-skills
```

To load one plugin for a single session instead:

```bash
claude --plugin-dir /path/to/blackshield_skills/coding
```

**Trigger evals.** Each plugin has an eval suite under `<plugin>/evals/` that checks the
right skill fires, and stays silent, for representative prompts. Run it from this
directory with the path form; a bare plugin name resolves to the installed cache copy,
which has no evals:

```bash
claude plugin eval ./<plugin> --no-publish --trust-plugin
```

- A full coding suite is 31 cases × 2 runs × 2 arms at roughly $0.10 a run for a trigger
  case. Use `--case <glob>` or `--runs 1` while iterating.
- Cases with a `context.scaffold_script` need `--scaffold`. Without it the fixture is not
  placed and the case is a false negative.
- The behavioural cases `review-stays-read-only` and `review-comment-without-pr-does-not-post` also
  need `--allow-tools Bash Edit Write`. They run up to 30 turns each and cost more than a
  trigger case.
- Results land in `<plugin>/evals/results/` (ignored).

### Release gate

A release gate is a full eval run against the **current plugin version** with `--runs 2`
and both arms. `--runs 1` is for iteration only; do not cite it as a release result. After
editing a skill's content, re-run that plugin's suite: a green result from an older
version is not evidence for the current one. Results under `<plugin>/evals/results/` are
ignored by git and are not a source of truth.

### OpenCode

OpenCode reads Agent Skills directly; it does not use the Claude Code plugin marketplace.
Global skills live in `~/.config/opencode/skills/<name>/SKILL.md` and project skills in
`.opencode/skills/<name>/SKILL.md`. OpenCode also reads the compatibility locations
`~/.claude/skills`, `~/.agents/skills`, `.claude/skills` and `.agents/skills`.

```bash
(
SKILLS=/path/to/blackshield_skills
DEST="$HOME/.config/opencode/skills"
mkdir -p "$DEST" || exit 1
for d in "$SKILLS"/blueprint/skills/blueprint "$SKILLS"/clickhouse/skills/clickhouse "$SKILLS"/coding/skills/*; do
  [ "$(basename "$d")" = "_shared" ] && continue
  [ -f "$d/SKILL.md" ] || { echo "not a skill: $d" >&2; exit 1; }
  ln -sfn "$d" "$DEST/$(basename "$d")"
done
if [ -e "$DEST/_shared" ] && [ ! -L "$DEST/_shared" ]; then
  echo "refusing: $DEST/_shared exists and is not a link; move it away first" >&2
  exit 1
fi
ln -sfn "$SKILLS/coding/skills/_shared" "$DEST/_shared"
for f in blueprint/SKILL.md improve/SKILL.md _shared/forge.py; do
  if test -r "$DEST/$f"; then echo "ok: $f"; else echo "FAIL: $f" >&2; exit 1; fi
done
)
```

The block is idempotent and runs in a subshell, so an `exit 1` cannot close your terminal.

- `-sfn` replaces an existing link, and the loop aborts if a folder has no `SKILL.md`.
- `coding/skills/_shared` (the forge adapter and harness contract) is not a skill. It is
  linked after the loop as `$DEST/_shared`, because coding skills resolve
  `../_shared/forge.py`. The block refuses to replace a `$DEST/_shared` that is not a link;
  if that name collides with another collection's `_shared`, use a separate `DEST`.
- For a project-local install, set `DEST="$PWD/.opencode/skills"`; the same `mkdir -p`,
  `ln -sfn` and `test -r` checks apply.
- The `coding` skills use `../improve/references` and `../_shared/forge.py`. Symlinking
  every coding sibling and `_shared` (as above) preserves both; a flat copy of one folder
  breaks them. Without symlinks (for example on Windows), copy the whole `coding/skills`
  tree, including `_shared`, so `_shared` sits beside the skills.

To invoke a skill, name it:

```text
Use the `blueprint` skill to add a GET /products/{id} endpoint.
Use the `clickhouse` skill to explain why this query is slow.
Use the `review` skill to review my branch before I open a PR.
```

OpenCode v1 loads a skill on demand through its native `skill` tool. In OpenCode v2,
mention it as `@blueprint`, `@clickhouse` or `@review`. Do not rely on `/blueprint`;
slash-command exposure is not guaranteed in current versions.

### Codex

Codex reads repository skills from `.agents/skills/<name>/SKILL.md` (walking up to the
repository root), user skills from `~/.agents/skills/<name>/SKILL.md` and admin skills from
`/etc/codex/skills`. Some older builds only read `.codex/skills` / `~/.codex/skills`; use
the path your `codex --version` documents.

```bash
(
SKILLS=/path/to/blackshield_skills
DEST="$HOME/.agents/skills"
mkdir -p "$DEST" || exit 1
for d in "$SKILLS"/blueprint/skills/blueprint "$SKILLS"/clickhouse/skills/clickhouse "$SKILLS"/coding/skills/*; do
  [ "$(basename "$d")" = "_shared" ] && continue
  [ -f "$d/SKILL.md" ] || { echo "not a skill: $d" >&2; exit 1; }
  ln -sfn "$d" "$DEST/$(basename "$d")"
done
if [ -e "$DEST/_shared" ] && [ ! -L "$DEST/_shared" ]; then
  echo "refusing: $DEST/_shared exists and is not a link; move it away first" >&2
  exit 1
fi
ln -sfn "$SKILLS/coding/skills/_shared" "$DEST/_shared"
for f in blueprint/SKILL.md improve/SKILL.md _shared/forge.py; do
  if test -r "$DEST/$f"; then echo "ok: $f"; else echo "FAIL: $f" >&2; exit 1; fi
done
)
```

For a project-local install, set `DEST="$PWD/.agents/skills"`; the same checks apply. Do
not commit absolute symlinks into a shared repository; use relative links or copy the
whole `coding/skills` tree.

Invoke a skill explicitly with `$skill-name`, or open the `/skills` picker:

```text
$blueprint Add a GET /products/{id} endpoint to this Go service.
$clickhouse Why is this query slow, and what should I change?
$review Review my branch before I open a PR.
```

Codex can also select a skill implicitly from its description. If the initial skill list
is capped, install only the skills you need or raise `skills.max_context_tokens` in the
Codex configuration.

**Using both.** OpenCode and Codex both read `~/.agents/skills`, so one symlink tree there
works for both. OpenCode also reads `~/.claude/skills`; remove stale copies there if you
see a duplicate. OpenCode resolves duplicate IDs by precedence; Codex shows duplicate
names in its selector rather than merging them.

**Portability.** `blueprint` and `clickhouse` are the most portable skills here. The
`coding` skills are Claude Code-first: they use `$ARGUMENTS`, `${CLAUDE_PLUGIN_ROOT}`,
Claude subagent types and `gh`. The instructions load, but workflows that depend on those
harness features may not run unchanged. The `claude plugin eval` commands are Claude
Code-only.

## License

MIT; see [`LICENSE.md`](LICENSE.md). Copyright © 2026 Blackshield.
`coding/skills/improve`, `review-plan`, `execute-plan` and `reconcile-plans` are adapted
from [`shadcn/improve`](https://github.com/shadcn/improve) (MIT, copyright © 2026 shadcn);
Blackshield's changes to them are modifications, copyright © 2026 Blackshield. The other
skills are Blackshield's own work. `coding/skills/improve/LICENSE.md` carries the upstream
MIT terms with both copyright lines, annotated as original work and modifications.
