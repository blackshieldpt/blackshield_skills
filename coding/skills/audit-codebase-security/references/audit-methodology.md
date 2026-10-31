# Security Audit Methodology

## Contents

- [Operating principles](#operating-principles)
- [Phase 1: establish scope](#phase-1-establish-scope)
- [Phase 2: map the system](#phase-2-map-the-system)
- [Phase 3: select and prioritize checks](#phase-3-select-and-prioritize-checks)
- [Phase 4: trace and test hypotheses](#phase-4-trace-and-test-hypotheses)
- [Phase 5: review security invariants](#phase-5-review-security-invariants)
- [Phase 6: validate findings](#phase-6-validate-findings)
- [Severity and confidence](#severity-and-confidence)
- [Coverage accounting](#coverage-accounting)

## Operating principles

1. Treat the repository as evidence, not as a bag of suspicious keywords.
2. Trace across trust boundaries. Most vulnerabilities require a controllable
   source, a reachable sink or invariant, and a missing or bypassable control.
3. Distinguish root cause, exploitation technique, and impact.
4. Use framework behavior and deployment assumptions only when supported by the
   repository or authoritative documentation.
5. Prefer high-confidence findings. Do not inflate a report with hardening advice
   presented as exploitable vulnerabilities.
6. Keep audit work read-only unless fixes are explicitly requested.

## Phase 1: establish scope

Record:

- audit root and mode (`full`, `targeted`, or `diff`);
- user-named components, vulnerability families, and excluded paths;
- relevant commit/diff range;
- whether generated, vendored, test, example, and deployment files are in scope;
- commands and tools that may be run safely;
- important unavailable services, configuration, or runtime evidence.

For a diff audit, review enough unchanged context to answer:

- Who calls the changed code?
- What data reaches it?
- Which middleware, policy, transaction, or validation normally protects it?
- Which old path remains reachable?
- Did the change alter defaults, error behavior, serialization, or trust?

## Phase 2: map the system

Run `scripts/profile_repository.py`, then confirm its heuristic output manually.
Create a compact map of:

| Area | Questions |
|---|---|
| Entry points | Which HTTP routes, RPCs, CLIs, queues, scheduled jobs, file imports, IPC handlers, plugins, and callbacks accept lower-trust input? |
| Identities | How are users, services, devices, tenants, and administrators authenticated and represented? |
| Authorization | Where are resource ownership, roles, scopes, tenant boundaries, and privileged actions enforced? |
| Interpreters and sinks | Which databases, shells, template engines, evaluators, renderers, loggers, and dynamic loaders consume input? |
| Files and processes | Which paths, uploads, archives, temporary files, process launches, libraries, and plugins cross privilege boundaries? |
| Network | Which outbound requests, proxies, webhooks, redirects, DNS resolutions, and internal services are attacker-influenced? |
| Parsers | Which serializers, archives, XML/YAML/document/media parsers, reflection, and native decoders process untrusted bytes? |
| Data | Which secrets, credentials, PII, tokens, keys, logs, caches, exports, and backups require protection? |
| Availability | Which operations have attacker-controlled size, depth, time, fan-out, concurrency, or persistence? |
| Operations | Which manifests, images, IAM policies, listeners, CI jobs, dependencies, and defaults affect exposure? |

Do not accept labels such as "internal" or "admin" as proof of a security boundary.
Find the enforcement mechanism.

## Phase 3: select and prioritize checks

Search `vulnerability-taxonomy.md` by heading or stable identifier. Use the profile
and attack-surface map to select checks.

Prioritize within the applicable set:

1. unauthenticated, remote, and broadly reachable surfaces;
2. authentication, authorization, tenant, and privilege boundaries;
3. code execution, query, filesystem, network, parser, and native-memory sinks;
4. secrets and sensitive-data paths;
5. durable state changes and financial/security-critical workflows;
6. resource-amplifying operations;
7. deployment, dependency, and defense-in-depth issues.

This ordering is a work queue, not a severity assignment.

Common family routing:

| Repository evidence | Taxonomy families |
|---|---|
| Web UI, templates, browser code | INJ, WEB, SESS, CLIENT |
| HTTP/RPC/API server | AUTHN, AUTHZ, WEB, INJ, FILE, PARSE, DATA, DOS |
| Database or search access | INJ, AUTHZ, DATA, LOGIC |
| Uploads, archives, local storage | FILE, PARSE, AUTHZ, DOS |
| Outbound URL fetches, webhooks, proxies | WEB, AUTHZ, DATA, DOS |
| Tokens, passwords, keys, TLS | AUTHN, SESS, CRYPTO, DATA |
| Queues, retries, payments, inventory | AUTHZ, LOGIC, CONC, DOS |
| C/C++, unsafe Rust, FFI, media parsers | MEM, PARSE, CONC, DOS |
| Mobile or desktop manifests/IPC | CLIENT, AUTHN, AUTHZ, DATA, OPS |
| Containers, CI, IaC, package manifests | OPS, AUTHZ, DATA, CRYPTO |

## Phase 4: trace and test hypotheses

### Start from sources

Examples include request parameters and bodies, headers/cookies, message payloads,
uploaded files, database values originally supplied by users, configuration from a
lower-trust writer, environment variables, IPC, plugin data, and remote responses.

For every candidate:

1. Identify the exact source and its trust level.
2. Follow transformations, aliases, storage, serialization, and asynchronous hops.
3. Identify the sink or security invariant.
4. Locate every relevant validation, encoding, authorization, or integrity control.
5. Check whether controls run on the final interpreted value and in the correct
   context.
6. Check error, retry, fallback, alternate route, and legacy paths.
7. Establish realistic attacker preconditions and impact.

### Start from sinks

When the repository is large, enumerate high-risk sinks, then work backward:

- dynamic code, template, shell, or expression evaluation;
- raw database/search queries;
- HTML/DOM insertion and redirect/header construction;
- file open/write/delete/include/upload/extract;
- outbound network connections;
- deserialization and complex parsers;
- cryptographic verification and key selection;
- privileged operations, role changes, and cross-tenant data access;
- unsafe memory operations and foreign interfaces.

A sink search creates hypotheses, not findings.

### Recognize valid defenses

Judge the defense in the sink's real context:

- Parameter binding protects values, not dynamically concatenated identifiers or
  query operators.
- Process APIs with argument arrays avoid shell parsing but can still permit option
  injection or dangerous operand semantics.
- Path normalization is insufficient without binding the operation to an allowed
  root and considering links and re-resolution.
- URL allowlists must account for redirects, DNS, alternate address forms, proxy
  behavior, and the address actually connected to.
- Output encoding must match the final HTML/attribute/URL/JavaScript/CSS context and
  occur after transformations.
- Authentication does not imply authorization; role authorization does not imply
  resource ownership or tenant scoping.
- TypeScript types and schema types do not validate runtime data unless code
  performs runtime parsing.
- A sanitizer or framework default is not sufficient evidence until its mode,
  version, placement, and bypass conditions are known.

## Phase 5: review security invariants

Pattern searches under-detect context-dependent flaws. Review:

- Every sensitive action requires the intended identity, role, resource ownership,
  tenant, and current security state.
- The same policy applies to UI, API, batch, import, WebSocket, worker, and direct
  object paths.
- One-time tokens and irreversible operations remain one-time under concurrency,
  retries, crashes, and message redelivery.
- State transitions cannot be skipped, repeated, reordered, or reached from an
  invalid state.
- Failure of a policy, identity, revocation, or integrity dependency fails closed.
- Caches and globals include identity, tenant, authorization state, and relevant
  configuration in their keys/lifetimes.
- Limits apply per attacker-controlled dimension and also globally; rejected work
  is not more expensive than accepted work.
- Security decisions use one canonical representation across all layers.
- Transactions and compensation preserve invariants across partial failure.

## Phase 6: validate findings

Before reporting, try to disprove each candidate:

- Is the input actually attacker controlled in production?
- Is the path reachable with the stated privileges?
- Does middleware, a caller, a database policy, sandbox, or deployment constraint
  block it?
- Is the suspicious value constant, allowlisted, encoded, parameterized, signed, or
  otherwise constrained?
- Does the vulnerable API mode actually apply?
- Is the code generated, dead, test-only, example-only, or replaced at build time?
- Is the claimed impact possible, or merely a dangerous-looking operation?
- Is this a duplicate manifestation of another root cause?

Use a safe existing unit/integration test when it can settle a disputed fact without
executing untrusted code or destructive payloads. Do not require a working exploit
when static evidence proves the path, but do not invent runtime behavior.

## Severity and confidence

Assign severity from demonstrated impact and realistic preconditions:

| Severity | Guidance |
|---|---|
| Critical | Broadly reachable compromise of a primary security boundary, such as unauthenticated arbitrary code execution, pervasive authentication bypass, or equivalent catastrophic impact |
| High | Major confidentiality, integrity, or privilege impact with realistic exploitation and limited prerequisites |
| Medium | Meaningful security impact requiring notable privileges, user interaction, uncommon configuration, or constrained reach |
| Low | Limited impact or a narrowly exploitable weakness; do not use Low to disguise unproven speculation |

Assign confidence separately:

| Confidence | Guidance |
|---|---|
| High | Source-to-sink/invariant path and missing defense are directly established |
| Medium | Evidence is strong, but one environmental or runtime fact remains explicit and plausible |
| Low | A key fact is unknown; keep it out of the confirmed-finding list and record it as an investigation item or limitation |

Do not derive severity from CWE/CVE frequency. Do not assign a CVSS number unless the
user requests it and the required metric facts are established.

## Coverage accounting

End every audit with:

- reviewed components and entry points;
- selected taxonomy families;
- loaded skills (stack skills loaded, or "none available");
- analysis/tools executed and their scope;
- important paths or components not reviewed;
- unavailable runtime/configuration/dependency evidence;
- whether findings were confirmed, potential, or absent.

"No confirmed findings" means none survived the evidence gate in the reviewed
scope. It does not mean the codebase is vulnerability-free.
