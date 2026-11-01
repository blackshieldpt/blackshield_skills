# Harness contract

Every coding skill must run on Claude Code and on other Agent Skills hosts
(OpenCode, Codex) without silently losing a guarantee. This file is the shared
contract those skills cite. It is documentation only; skills adopt it as they migrate to the forge adapter.

## Invocation text

Claude Code substitutes `$ARGUMENTS` before the skill body is read. Other hosts
do not perform that substitution; they pass the text after the skill name.

A skill therefore refers to **the invocation text** and reads it from whichever
place its host provides: `$ARGUMENTS` on Claude Code, the trailing user text on
other hosts. A skill must not require a substitution the host lacks, and must
treat an empty invocation text as "no arguments", not as an error.

## Base directory

The base directory is the directory containing the skill's `SKILL.md`. Resolve
every bundled script, reference and helper relative to it.

`${CLAUDE_PLUGIN_ROOT}` is a Claude-only fallback. Use it only when the skill
cannot locate its own directory; never make it the primary mechanism.

## Model roles

Claude Code skills keep their explicit `model: opus|haiku|sonnet` pins for now.
Other hosts may expose these optional environment variables instead:

- `AGENT_MODEL_REVIEWER` — the model for refutation and review passes.
- `AGENT_MODEL_SWEEP` — the model for broad sweep and search passes.
- `AGENT_MODEL_EXECUTOR` — the model for plan execution and mechanical work.

If a host does not read a role variable, keep the session model. A migrated skill must not silently downgrade a reviewer: when a skill needs a reviewer
model and neither the pin nor the role applies, say so in the report instead of
using a cheaper model without disclosure.

## Subagents

If the host supports fresh-context subagents, use them for refutation and
independent passes. If it does not, do the pass inline and label it `unvetted`.

`unvetted` is not `self-vetted`. In `review`, `self-vetted` already means the
refutation pass was deliberately skipped on a low-grade finding; `unvetted`
means the host could not give the pass fresh context at all. Do not merge the
two labels.

## Worktrees

Use `isolation: "worktree"` when the host supports it. Otherwise create the
worktree manually with `git worktree add`, record its path in the report, and
remove it when done.

## Forge

Use `coding/skills/_shared/forge.py` for repository, issue, pull request,
check and label operations. Each skill or reference that runs it names it `$F` and carries this paragraph:

`$F` is `_shared/forge.py` next to this skill's directory: `<base directory>/../_shared/forge.py`, where the base directory holds this skill's `SKILL.md` (for a reference file, the skill that loaded it). Resolve it to an absolute path once. `${CLAUDE_PLUGIN_ROOT}/skills/_shared/forge.py` is a Claude-only fallback. Subagents do not see this paragraph; pass them the absolute path. On a non-zero exit, print stderr and follow this skill's stop rule: 2 = usage, unsupported forge or missing configuration; 3 = authentication; 4 = not found; 5 = conflict; 1 = other failure. Exit 2 alone does not prove the forge is unsupported.

`--repo` is a subcommand option: `python3 "$F" pr view N --repo HOST/OWNER/NAME`, never `python3 "$F" --repo HOST/OWNER/NAME pr view N`. Prefer `HOST/OWNER/NAME` whenever the host is known.

When detection prints `unsupported forge` (exit 2), stop and report the host and configuration; do not guess an API or fall back to unauthenticated HTML scraping.

A missing token is exit 3 and stops the write command. Forgejo/Gitea anonymous
reads are allowed; writes are not. Token values never appear in output, and
error messages name the environment variable only.

## Scratchpad

A skill's "scratchpad directory" is the host's per-session scratch directory
when it has one (Claude Code does). Otherwise create one outside the repository
with `mktemp -d`, use that one path for the whole run, record it in the report,
and remove it when done.
