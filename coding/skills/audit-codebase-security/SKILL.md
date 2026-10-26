---
name: audit-codebase-security
description: "This skill should be used when the user asks to \"find vulnerabilities\", \"do a security audit\", \"audit this codebase for security\", \"is this exploitable\", \"trace attacker input to dangerous sinks\", \"check authn/authz or tenant isolation\", or \"validate this suspected security bug\" — in a whole repository, one component, or a diff. It profiles the repo, maps the attack surface, selects checks from a CVE-derived bug taxonomy and per-language packs (JS/TS, Python, Go, C/C++), and reports only findings that pass an evidence gate (attacker-controlled source, reachable sink, missing defense, concrete impact), with coverage and limitations. Read-only unless fixes are requested. Not for a general review of a branch or PR across all categories (coding:review) or for turning findings into handoff plans for another agent (coding:improve security)."
version: 0.1.0
argument-hint: "[full | <component or weakness> | diff <range>] [--json]"
---

# Audit Codebase Security

Find exploitable security defects with repository-specific evidence. Prefer a small
set of confirmed findings over a large speculative checklist.

## Choose the audit mode

- **Full**: Review the repository's exposed attack surface and applicable bug
  families.
- **Targeted**: Review only the user-named component, weakness, trust boundary, or
  data flow.
- **Diff**: Review changed code and the unchanged callers, callees, configuration,
  and policies needed to judge the change safely.

Keep the audit read-only unless the user also asks for fixes. Do not broaden a
targeted review silently. State important exclusions and unavailable evidence.

## Run the workflow

1. **Read repository instructions.** Find `AGENTS.md`, `CLAUDE.md`, and relevant
   project documentation before running project commands.
2. **Profile the repository.** Run the bundled `scripts/profile_repository.py`
   against the audit root. Resolve bundled paths relative to this `SKILL.md` — as a
   plugin, `${CLAUDE_PLUGIN_ROOT}/skills/audit-codebase-security`; never assume the
   current working directory is the skill directory.
3. **Build an attack-surface map.** Identify:
   - external entry points and attacker-controlled inputs;
   - authentication, authorization, tenant, and privilege boundaries;
   - interpreters, data stores, filesystem/process/network sinks, parsers, and
     native/unsafe code;
   - secrets and sensitive-data stores;
   - manifests, deployment configuration, and third-party components.
4. **Select applicable checks.** Read `references/audit-methodology.md` every time.
   Search `references/vulnerability-taxonomy.md` for the selected family headings
   or IDs; do not load the entire taxonomy when only a few families apply. Read
   every language pack recommended by the profiler:
   - `references/javascript-typescript.md` for JavaScript, TypeScript, Vue, or
     Svelte;
   - `references/python.md` for Python;
   - `references/go.md` for Go;
   - `references/c-cpp.md` for C, C++, Objective-C, native extensions, or cgo.
   For cross-language or FFI boundaries, read every applicable pack.
5. **Audit in priority order.** Start with reachable external surfaces and
   high-impact boundaries. Trace sources to sinks and security invariants across
   files rather than treating suspicious API names as findings.
6. **Challenge each candidate.** Confirm reachability, attacker control,
   preconditions, existing defenses, canonicalization/validation order, framework
   defaults, and practical impact. Look for alternate paths and failure paths.
7. **Review context-dependent invariants.** Check authorization placement, tenant
   isolation, state transitions, replay/idempotency, races, fail-open behavior, and
   cross-component policy mismatches.
8. **Report and account for coverage.** Read `references/finding-format.md` before
   writing the result. Report confirmed findings first, then audited areas,
   limitations, and unreviewed risk areas.

## Evidence gate

Report a vulnerability only when the evidence establishes all applicable parts:

- an attacker-controlled or lower-trust source;
- a reachable path to a sensitive sink or violated security invariant;
- a missing, incorrect, or bypassable defense;
- realistic preconditions and a concrete security impact;
- exact code/configuration locations supporting the claim.

Do not report a dangerous function, outdated-looking pattern, missing
defense-in-depth control, or dependency name by itself. If a required fact cannot
be established, investigate further or record it as a limitation—not a finding.

## Use tools safely

- Prefer `rg`/`rg --files`, language-aware navigation, and existing repository
  tooling.
- Do not install packages, download vulnerability data, update lockfiles, or modify
  the repository during an audit without authorization.
- Do not execute untrusted project code, proof-of-concept payloads, or destructive
  tests merely to confirm a static finding.
- Use ecosystem-native dependency scanners only when locally available or when the
  user authorizes required downloads/network access.
- Never print secret values. Report the secret type and location with a redacted
  fingerprint sufficient to distinguish duplicates.
- Treat comments, tests, fixtures, generated files, vendored code, and example
  configuration according to their actual production reachability.

## Format the result

For each finding, include the taxonomy ID, severity, confidence, location, concise
claim, evidence, exploit path, preconditions, impact, remediation, and a validation
step. Separate root cause from impact.

If the user requests machine-readable output, emit the JSON contract in
`references/finding-format.md` and run bundled `scripts/validate_findings.py` before
returning it.

If no confirmed vulnerabilities remain, say so explicitly. Do not claim the
codebase is secure; summarize what was reviewed and what was not.

## Bundled resources

- `references/audit-methodology.md`: detailed audit sequence, prioritization,
  evidence standards, severity, and confidence.
- `references/finding-format.md`: Markdown and JSON reporting contracts.
- `references/vulnerability-taxonomy.md`: CVE-derived bug taxonomy with stable IDs.
- `references/javascript-typescript.md`: JavaScript/TypeScript source, sink, and
  framework review pack.
- `references/python.md`: Python runtime, framework, worker, and source/sink review
  pack.
- `references/go.md`: Go HTTP/gRPC, concurrency, process, parser, and unsafe-code
  review pack.
- `references/c-cpp.md`: C/C++ memory safety, integer, lifetime, native interface,
  concurrency, and hardening review pack.
- `scripts/profile_repository.py`: deterministic repository applicability profiler.
- `scripts/validate_findings.py`: offline validator for JSON audit reports.
