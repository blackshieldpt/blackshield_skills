# Go Audit Pack

## Contents

- [Applicability](#applicability)
- [Map Go trust boundaries](#map-go-trust-boundaries)
- [High-value source and sink seeds](#high-value-source-and-sink-seeds)
- [HTTP, RPC, and framework review](#http-rpc-and-framework-review)
- [Runtime and language traps](#runtime-and-language-traps)
- [Concurrency and resource lifetime](#concurrency-and-resource-lifetime)
- [Native and unsafe boundaries](#native-and-unsafe-boundaries)
- [Defense validation](#defense-validation)
- [Tool-assisted checks](#tool-assisted-checks)

## Applicability

Use this pack for Go HTTP/RPC services, CLIs, controllers/operators, agents,
networking software, workers, serverless functions, plugins, and cgo/assembly/unsafe
code.

Distinguish:

- executable commands under `cmd/`, reusable packages, internal packages, generated
  code, tests/fuzz corpora, tools, and examples;
- public network handlers, internal RPC, local IPC, administrative endpoints,
  controllers/reconcilers, and background goroutines;
- standard-library behavior from router/framework/proxy behavior;
- memory-safe Go from `unsafe`, cgo, assembly, syscalls, memory mapping, and foreign
  libraries.

## Map Go trust boundaries

Find:

1. `net/http` handlers, router registrations, gRPC services/interceptors,
   WebSockets, CLIs, queue consumers, controllers, plugins, and exported library
   entry points.
2. Middleware/interceptor order and where identity, authorization, tenant, tracing,
   limits, and recovery are attached.
3. Database/query builders, caches, object stores, filesystem/process/network calls,
   templates, and dynamic dispatch.
4. JSON/YAML/XML/gob/protobuf/archive/document/media parsing and custom
   marshal/unmarshal methods.
5. Goroutine creation, worker pools, channels, shared maps/state, retries, timers,
   and context propagation.
6. TLS/token/signature code, secrets, logs, metrics labels, and error responses.
7. `go.mod`, `go.sum`, `toolchain`/replace/exclude directives, vendoring, code
   generation, build tags, linker flags, and container/deployment manifests.
8. `unsafe`, cgo, assembly, syscalls, reflection, raw sockets, and byte/slice/string
   zero-copy conversions.

## High-value source and sink seeds

Use these as trace seeds, not findings.

| Family | Sources/sinks and review targets |
|---|---|
| SQL/query injection | `database/sql` and driver/query-builder calls, `fmt`/concatenated SQL, user-selected column/order/operator names, raw ORM methods |
| Command/argument injection | `os/exec.Command`/`CommandContext`, `sh -c`/shell interpreters, attacker-controlled executable names, flags, response files, environment, and working directory |
| Templates/XSS | `html/template`, `text/template` used for HTML, typed safe-content values, custom escaping, JavaScript/URL/CSS contexts, response headers and redirects |
| Files/paths | `os.Open`/`OpenFile`/`Create`/`Remove`, `filepath`, `http.ServeFile`, uploads, archives, temp files, symlinks, plugin/library paths |
| SSRF/proxy | `http.Client`, `http.Get`, custom transports/dialers, reverse proxies, webhooks/imports/previews, redirects, DNS, proxy environment, and cloud metadata |
| Parsing/deserialization | JSON/YAML/XML/gob/protobuf/custom unmarshalling, archive/media/document parsers, decoder hooks, unbounded messages and nested structures |
| Authentication/session | Middleware/interceptors, JWT/token verification, cookies, OAuth/OIDC, password reset, mTLS identity, WebSocket upgrade, admin/debug handlers |
| Authorization | Path/body-controlled IDs, repository lookups without owner/tenant scope, gRPC method policy, batch/controller/worker paths, client-provided roles/status |
| Cryptography/secrets | `math/rand` for security values, weak hashes/password storage, `tls.Config`, `InsecureSkipVerify`, custom peer verification, static keys/nonces, token comparison, log fields |
| Availability | Missing HTTP timeouts/body caps, unbounded goroutines/channels/maps/caches, regex, deep parsing, huge `make` sizes, fan-out, retries, slow clients |
| Native/unsafe | `unsafe`, cgo `import "C"`, assembly, syscalls, `reflect`/`SliceHeader`/`StringHeader`, mmap, callbacks, pointer and length conversions |

Search interface implementations and wrappers. Security behavior commonly lives in
shared middleware, repository methods, custom transports, and generated service
bindings rather than handlers.

## HTTP, RPC, and framework review

### `net/http` and routers

- Enumerate every `Handle`/`HandleFunc`, `ServeMux`, router group/mount, method,
  fallback/not-found handler, debug/metrics/profile endpoint, and alternate server.
- Verify middleware order and route coverage. A middleware variable existing in the
  package does not prove it wraps every server or route.
- Check server read-header/read/write/idle timeouts, maximum headers, request-body
  caps, multipart temporary storage/cleanup, streaming, and slow-client behavior.
- Review `Request.URL`, `Host`, `RemoteAddr`, forwarded headers, proxy trust, absolute
  URL generation, redirects, and cookie attributes.
- Inspect `httputil.ReverseProxy`, custom directors/rewriters/transports, hop-by-hop
  headers, credentials forwarded upstream, and attacker-selected targets.
- Treat panic recovery as availability containment, not validation or proof that
  corrupted/partial state is safe.

### Gin, Chi, Echo, Fiber, and similar routers

- Enumerate route groups and inherited middleware, including static/filesystem,
  WebSocket, health/debug, and catch-all routes.
- Confirm framework context values cannot be spoofed or reused across requests.
- Review bind/decode behavior for unknown fields, coercion, body reuse, size limits,
  and mass assignment into domain/storage structs.
- Verify authentication and authorization occur after trusted proxy/identity parsing
  and before side effects.

### gRPC and protobuf

- Enumerate registered services/methods, reflection/health/admin services, unary and
  stream interceptors, and direct service method calls.
- Verify authentication/authorization on streaming setup and on per-message actions
  where identity/state can change.
- Bound message size, stream duration, concurrent streams, metadata, decompression,
  retries, and fan-out.
- Check protobuf field presence/default semantics, numeric narrowing, unknown fields,
  oneofs, resource names, and tenant/owner binding.
- Review mTLS peer identity, forwarded metadata, deadlines, status/error detail, and
  retry/idempotency interactions.

### Controllers, operators, and workers

- Treat watched resources, queue messages, labels/annotations, and external status
  as lower-trust inputs.
- Review reconciliation retries, finalizers, owner references, namespace/tenant
  scoping, credentials, and privileged API calls.
- Ensure work queues are bounded and rate-limited, poison items cannot retry
  forever, and idempotency survives process restarts.

## Runtime and language traps

- `exec.Command` does not invoke a shell by default, but option/operand injection,
  attacker-controlled executables, PATH lookup, environment, and working directory
  can still be exploitable.
- `html/template` performs contextual escaping, but typed `template.HTML`,
  `template.JS`, `template.URL`, custom escaping, or `text/template` can bypass it.
- `encoding/json` is not code-executing deserialization. Review unknown fields,
  duplicate/case-insensitive field matching, numeric precision, custom
  `UnmarshalJSON`, depth/size, and partial-object state instead.
- Standard `encoding/xml` behavior does not justify an XXE finding by API name
  alone. Establish custom entity/network resolution or the actual parser/library
  behavior.
- Ignored errors, short reads/writes, partial decoding, and using partially filled
  values after an error frequently break security invariants.
- `defer` runs at function return, not loop-iteration end; long loops can retain
  files, bodies, locks, memory, or transactions.
- Closing an HTTP response body is necessary, but draining/reuse, size limits,
  redirects, and timeouts also affect resource safety.
- Slice capacity, aliasing, `append`, subslicing, and pooled buffers can expose or
  mutate data across requests.
- Map access is unsafe under concurrent writes; a race is also a security issue when
  it affects identity, policy, object lifetime, or serialized output.
- Converting signed/untrusted values to unsigned or narrower types can bypass bounds
  and cause huge allocations even though integer arithmetic wraps predictably.
- A bounds panic or nil dereference is normally an availability finding only when an
  attacker can reach it and recovery/process topology makes the impact meaningful.
- `recover()` does not contain fatal runtime errors: detected concurrent map
  writes, a goroutine exceeding the maximum stack size (deep recursion), exceeding
  `debug.SetMaxThreads`, and out-of-memory crash the whole process. It also only
  catches panics in its own goroutine, so a panic in a goroutine a handler starts
  bypasses the router's recovery middleware.
- `os.CreateTemp` provides safe creation semantics, but directory choice,
  permissions, content sensitivity, cleanup, and later rename/use still matter.
- Build tags and platform-specific files can replace the implementation reviewed.
  Check deployed GOOS/GOARCH/tags and generated files.
- `go:generate`, cgo flags, linker flags, and module replacements execute or select
  build-time trust that imports alone do not reveal.

## Concurrency and resource lifetime

- Require a clear owner and termination condition for every goroutine.
- Propagate `context.Context` through blocking/network/database operations; confirm
  cancellation is observed and does not leave partial state.
- Check send/receive paths for goroutine leaks when callers return early, channels
  fill, consumers stop, or errors race with results.
- Bound goroutine creation, worker pools, queues, channels, retries, timers/tickers,
  maps, caches, and per-key state under attacker-controlled cardinality.
- Review channel close ownership, double close/send-after-close, nil channels,
  select starvation, and error/result channel capacity.
- Check lock coverage and ordering, copying of mutex-containing values, `sync.Once`
  initialization failures, atomic memory ordering, and mixed atomic/non-atomic
  access.
- Verify check-and-update business operations at the database/durable-store
  transaction boundary. An in-process mutex does not coordinate other replicas.
- Inspect object pools and buffer reuse for cross-user data remnants and use after
  logical release.
- Review timeout/retry/idempotency interaction so a timed-out operation cannot later
  commit and then be duplicated by a retry.

## Native and unsafe boundaries

- Treat `unsafe.Pointer`, `uintptr`, `reflect.SliceHeader`, `reflect.StringHeader`,
  zero-copy conversions, and `//go:nocheckptr` as memory-safety boundaries.
- Check pointer provenance, object liveness, `runtime.KeepAlive`, alignment, sizes,
  offsets, overflow, and garbage-collector movement/lifetime assumptions.
- At cgo boundaries, verify C allocation/free ownership, Go-pointer retention rules,
  callbacks, thread affinity, NUL termination, integer width, error transfer, and
  concurrent teardown.
- Review assembly/syscalls for ABI, buffer length, errno/partial result, platform,
  and race assumptions.
- Apply the C/C++ audit pack to native dependencies and cgo implementations.

## Defense validation

Accept a defense only after confirming:

- SQL values use driver placeholders/arguments and dynamic identifiers/operators are
  allowlisted;
- process execution avoids a shell and constrains executable resolution, flags,
  operands, environment, directory, and inherited descriptors;
- templates retain contextual escaping and no typed-safe-content or custom helper
  receives attacker-controlled markup/code/URLs;
- file operations remain within an authorized root and handle absolute paths,
  separators, links, races, archive types, permissions, and operation-specific
  authorization; `os.Root` (Go 1.24+) confines operations to a directory including
  symlink targets, while `filepath.IsLocal` (Go 1.20+) is only a lexical check that
  does not stop symlinks or later re-resolution;
- outbound network enforcement covers redirects, DNS, resolved addresses, schemes,
  credentials, proxy environment, custom transports/dialers, and the connected
  endpoint;
- decoder limits and runtime validation constrain unknown fields, sizes, nesting,
  numeric conversions, custom hooks, and semantic relationships;
- authentication/authorization middleware or interceptors cover every route/method
  and bind action, object, owner/tenant, and current state;
- TLS/token/signature verification binds expected algorithm/key/identity,
  issuer/audience, time claims, hostname, and failure behavior;
- contexts, timeouts, quotas, and concurrency limits exist at per-caller,
  per-resource, and global layers.

Do not report:

- `exec.Command` as shell injection without a shell or dangerous argument/executable
  semantics;
- `html/template` interpolation as XSS unless escaping is bypassed or the wrong
  context/template engine is used;
- `encoding/json` or protobuf decoding as arbitrary code execution;
- `fmt.Sprintf` as injection unless its result reaches an interpreter or violates a
  security invariant;
- a goroutine as a leak without a reachable path that loses its termination/owner;
- an imported module as vulnerable without establishing the resolved version and
  relevant reachable behavior.

## Tool-assisted checks

Commands that build or run project code, or need the network, run only with
explicit user authorization; never on an untrusted repository. That covers every
item below except reading existing reports: `go test`, `go vet`, and `govulncheck`
build packages and may download modules, and `govulncheck` also fetches the
vulnerability database. Without authorization, list them as recommended checks.

- `go test ./...` and focused safe tests;
- `go test -race ./...` for concurrency-sensitive code when the environment permits;
- `go vet ./...` and configured static analysis;
- `govulncheck` for resolved/reachable dependency and standard-library advisories;
- Go fuzz tests for bounded parsers and invariants;
- native sanitizers/static analysis for cgo dependencies.

Inspect commands and build scripts before any authorized execution. Tool output is a hypothesis
until repository-specific reachability and impact are established.
