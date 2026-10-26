# Python Audit Pack

## Contents

- [Applicability](#applicability)
- [Map Python trust boundaries](#map-python-trust-boundaries)
- [High-value source and sink seeds](#high-value-source-and-sink-seeds)
- [Framework review](#framework-review)
- [Runtime and language traps](#runtime-and-language-traps)
- [Concurrency, workers, and background jobs](#concurrency-workers-and-background-jobs)
- [Defense validation](#defense-validation)
- [Tool-assisted checks](#tool-assisted-checks)

## Applicability

Use this pack for Python services, CLIs, workers, automation, data pipelines,
serverless functions, notebooks promoted into production, plugins, and native
extension/FFI boundaries.

Distinguish:

- production, build, administrative, migration, test, fixture, and example code;
- synchronous, asynchronous, threaded, multiprocess, and distributed-worker
  execution;
- trusted internal data from data that was originally user-controlled or can be
  modified by a lower-trust principal;
- type annotations from runtime parsing and validation;
- pure Python from C extensions, `ctypes`, `cffi`, Cython, or other native code.

Type hints, dataclasses, and Pydantic model annotations are not security controls
unless runtime code parses the actual boundary with the intended configuration.

## Map Python trust boundaries

Find:

1. WSGI/ASGI routes, middleware, dependency injection, RPC handlers, WebSockets,
   CLI commands, queue consumers, scheduled jobs, file imports, and plugin hooks.
2. Authentication identity construction and authorization/tenant policy calls.
3. ORM/database access, raw-query escape hatches, cache keys, and object lookups.
4. Template rendering, dynamic evaluation/imports, subprocesses, filesystem access,
   outbound HTTP, and redirects.
5. Pickle/YAML/XML/archive/document/media/model loaders and custom decoders.
6. Secrets, tokens, PII, logs, traces, exports, temporary files, and serialized
   response models.
7. `pyproject.toml`, requirements/lockfiles, package indexes, build backends, entry
   points, editable/path dependencies, and install hooks.
8. Native modules and every Python/native ownership, length, buffer, and exception
   boundary.

## High-value source and sink seeds

Use these to seed traces, not as standalone findings.

| Family | Sources/sinks and review targets |
|---|---|
| Code/eval injection | `eval`, `exec`, `compile`, `ast`/expression evaluators, dynamic imports, user-selected modules/classes/functions, notebook execution |
| Command/argument injection | `os.system`, `os.popen`, `subprocess` calls, `shell=True`, command interpreters, attacker-controlled executable names, flags, response files, and operands |
| SQL/query injection | DB-API `execute`/`executemany`, f-strings/formatting/concatenation, SQLAlchemy textual SQL, Django `raw`/`RawSQL`/legacy raw APIs, user-controlled identifiers/order/filter operators |
| Template/XSS | Jinja/Django/Mako templates, `Markup`, `mark_safe`, `safe` filters, disabled autoescape, format strings generating markup, redirects and response headers |
| Files/paths | `open`, `pathlib`, `os` file operations, framework `send_file` helpers, uploads, archive extraction, temp files, symlinks, config/plugin paths |
| SSRF/open redirect | `requests`, `httpx`, `aiohttp`, `urllib`, cloud/client SDKs, URL previews/imports/webhooks, redirect targets, proxy environment variables, DNS and redirect behavior |
| Deserialization | `pickle`, `dill`, `shelve`, `joblib`, object-capable model/checkpoint loaders, unsafe YAML constructors, `marshal`, custom reducers/hooks |
| XML/parsers | SAX/DOM/lxml/custom XML configuration, entity/network access, schema/XInclude, archive/document/image parsers, unbounded JSON/form/multipart input |
| Authentication/session | Route decorators/dependencies, login/reset flows, token/JWT verification, cookie/session configuration, OAuth state/nonce/PKCE, WebSocket authentication |
| Authorization | Request-controlled object IDs, ORM lookups without owner/tenant scope, serializer/model updates, admin/worker/direct-object paths, client-provided roles/status |
| Cryptography/secrets | `random` for security values, weak hashes/password storage, static keys/nonces, disabled TLS verification, custom SSL contexts, token comparisons, secrets in logs/reprs/responses |
| Availability | Regexes, recursive parsing, large integer/string/container allocation, unbounded request bodies/uploads, async fan-out, queue retries, decompression, expensive validation |
| Native/FFI | `ctypes`, `cffi`, Cython, buffer protocol, `memoryview`, extension APIs, pointer/size conversions, callbacks, and ownership across runtimes |

Search project wrappers and aliases as well as standard-library names. A safe-looking
repository helper may contain the actual dangerous mode.

## Framework review

### Django and Django REST Framework

- Enumerate URL patterns, nested includes, class-based views, DRF routers, admin
  actions, management commands, signals, and task entry points.
- Verify authentication, permission classes, object-level permissions, tenant
  scoping, and queryset filtering on every read/write path.
- Review raw SQL, dynamic `order_by`/field/operator selection, serializer writable
  fields, nested updates, bulk APIs, and model/admin actions.
- Inspect `mark_safe`, safe filters, autoescape blocks, HTML email, redirects,
  response headers, file responses, and user-controlled template names.
- Check CSRF exemptions and the actual authentication mechanism. Token-authenticated
  APIs and cookie-authenticated browser flows have different CSRF properties.
- Review host/proxy assumptions, debug/error exposure, secret/config sources,
  upload/media roots, signed values, caches, and cross-user response caching.
- Treat middleware order and alternate ASGI/WSGI/worker paths as part of the policy.

### FastAPI and Starlette

- Enumerate application/router mounts, WebSockets, middleware, exception handlers,
  dependency overrides, background tasks, and direct calls to endpoint functions.
- Confirm authentication/authorization dependencies are attached at the effective
  route/router/application level and apply to all methods.
- Check Pydantic model configuration for unknown fields, aliases, coercion, custom
  validators, nested depth/size, and model-to-ORM mass assignment.
- Review response models and manual responses for excessive fields, secrets, and
  cross-tenant objects.
- Inspect CORS, trusted-host/proxy middleware, redirect construction, static/file
  serving, multipart/body limits, docs/management exposure, and error detail.
- Detect blocking I/O/CPU work inside async handlers and unbounded task/fan-out
  creation where an attacker controls request frequency or work size.

### Flask

- Enumerate blueprints, route methods, before-request hooks, error handlers, CLI
  commands, extensions, and direct view invocation.
- Confirm every blueprint/route receives intended authentication, authorization,
  CSRF, rate, and tenant controls.
- Review Jinja autoescape boundaries, `Markup`, `send_file`/`send_from_directory`,
  redirects, session secret/key rotation, proxy fix configuration, uploads, and
  debug mode.
- Check extension defaults against the installed version and actual application
  configuration; do not assume an extension is active from its import alone.

### Celery, RQ, and other workers

- Treat queue producers and brokers as trust boundaries; establish who can publish,
  alter, replay, or inspect tasks.
- Review task serializers, argument validation, task-name dispatch, result storage,
  credentials, retries, acknowledgements, idempotency, and privilege.
- Verify authorization is not lost when a web request becomes a background task;
  carry a constrained identity/tenant context rather than trusting arbitrary task
  arguments.
- Bound retry storms, fan-out, memory, result size, and long-lived resource use.

## Runtime and language traps

- `assert` is removable under optimized execution. Never accept it as an
  authentication, authorization, validation, or integrity control.
- Python annotations and `typing` constructs do not validate runtime values.
- `getattr`, `setattr`, import hooks, entry points, and dotted-path resolution can
  expose dangerous methods or security-sensitive fields.
- Format strings can traverse object attributes/keys or become a second interpreter;
  assess what object graph and formatting capability is exposed.
- DB-API parameters protect query values, not identifiers, keywords, operators, or
  whole clauses.
- Argument-array subprocess APIs avoid shell parsing but can still permit option
  injection, attacker-selected executables, and dangerous operand semantics.
- `pathlib.resolve`/normalization alone does not prevent link races or bind the later
  file operation to the validated object.
- `tempfile.mktemp` and predictable/shared temporary locations introduce races;
  inspect creation, permissions, links, cleanup, and privilege boundaries.
- Pickle-family formats can execute code during loading. Establish provenance and
  integrity even when the file is "local."
- Archive extraction behavior and safety filters vary by Python/runtime/library
  version and options. Verify the deployed version, member types, links,
  permissions, overwrite order, and extraction root.
- Broad `except`/fallback paths can turn failed authentication, verification,
  parsing, or policy lookup into fail-open behavior.
- Exception objects, dataclass/Pydantic reprs, debug pages, and structured logging
  can disclose secrets and PII transitively.
- Mutable defaults, module globals, caches, and singleton dependencies can leak state
  across users or requests.
- `sys.path`, the current directory, `PYTHONPATH`, editable installs, and plugin
  discovery can cause untrusted module loading in privileged processes.
- Numeric conversion, extremely large integers, decimals, and attacker-controlled
  container sizes can create correctness or resource-exhaustion bugs even without
  native memory corruption.

## Concurrency, workers, and background jobs

- Verify every task, thread, process, coroutine, and callback has a bounded lifetime,
  cancellation path, deadline, and result/error owner.
- Check `asyncio.create_task`, gather/fan-out, executors, thread pools, and process
  pools for attacker-controlled unbounded work.
- Do not assume the GIL makes check-and-update, filesystem, database, cache, or
  multi-process operations atomic.
- Inspect shared globals, caches, context variables, connection/session reuse, and
  worker lifecycle for cross-request identity leakage.
- Review cancellation and timeout cleanup for partially committed state, leaked
  resources, and duplicate retries.
- Check one-time token use, quotas, balances, inventory, and idempotency at the
  durable transaction boundary, not with an in-process lock alone.

## Defense validation

Accept a defense only after confirming:

- the runtime schema parses the actual entry point and constrains unknown fields,
  coercion, sizes, depth, and semantic relationships;
- SQL values are bound using the driver's real parameter mechanism and dynamic
  identifiers/operators are allowlisted;
- subprocess execution avoids a shell and constrains executable, flags, operands,
  environment, working directory, and inherited descriptors;
- file access is contained to the intended root and accounts for absolute paths,
  links, races, archive member types, and operation-specific authorization;
- template/output handling uses context-correct escaping after transformations and
  no `safe`/`Markup` bypass receives attacker-controlled content;
- outbound URL checks cover redirects, DNS, resolved addresses, proxy environment,
  credentials, schemes, and the endpoint actually connected to;
- deserialized object data has trusted provenance and integrity, or a data-only
  format/loader is used;
- authorization binds action, object, owner/tenant, and current state on all web,
  worker, admin, CLI, and batch paths;
- TLS/token/signature verification checks expected identity, issuer/audience, key,
  algorithm, expiry, and failure behavior.

Do not report:

- `subprocess.run([...])` as command injection without dangerous argument semantics;
- a parameterized DB-API/ORM query merely because request data reaches it;
- escaped Jinja/Django interpolation as XSS without an escape bypass or wrong
  context;
- `json.loads` as code-executing deserialization;
- a type annotation mismatch as a vulnerability without a reachable security
  consequence;
- a package name as vulnerable without establishing the resolved version,
  advisories, and relevant execution path/threat model.

## Tool-assisted checks

Use only tools already present unless the user authorizes installation/network
access:

- repository tests and focused safe regression tests;
- Bandit or Semgrep for seed hypotheses;
- `pip-audit` or ecosystem-native dependency tooling for resolved dependencies;
- Ruff/mypy/pyright for correctness signals that may expose missing branches or
  unsafe assumptions;
- framework route inspection and configuration checks;
- native sanitizers/static analyzers for extension modules.

Treat every tool result as a hypothesis. Reproduce the source-to-sink or invariant
evidence before reporting it.
