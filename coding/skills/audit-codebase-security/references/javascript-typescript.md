# JavaScript and TypeScript Audit Pack

## Contents

- [Applicability](#applicability)
- [Map runtime boundaries](#map-runtime-boundaries)
- [High-value source and sink seeds](#high-value-source-and-sink-seeds)
- [Framework review](#framework-review)
- [Runtime and language traps](#runtime-and-language-traps)
- [Defense validation](#defense-validation)
- [Tool-assisted checks](#tool-assisted-checks)

## Applicability

Use this pack for Node.js/Deno/Bun services, serverless functions, browser clients,
Electron, React/Next.js, Vue/Nuxt, Angular, Express, Fastify, Koa, Nest, and packages
that transform or execute untrusted data.

First distinguish:

- server, browser, worker, build-time, test, and Electron/native contexts;
- code that runs in a trusted developer environment from production code;
- client-side checks from server-enforced controls;
- TypeScript compile-time types from runtime validation.

Do not treat a TypeScript type annotation as input validation.

## Map runtime boundaries

Find:

1. Route, RPC, WebSocket, queue, server action, worker, CLI, and IPC registration.
2. Middleware/guards and their order.
3. Authentication claim construction and authorization policy calls.
4. Data access layers, raw escape hatches, and tenant predicates.
5. Rendering boundaries and browser/server data transfer.
6. File uploads, archives, filesystem/process calls, URL fetchers, and redirects.
7. Dynamic imports, plugin discovery, eval/template/expression engines, and build
   hooks.
8. Package manifests, lockfiles, workspaces, lifecycle scripts, and overrides.

Follow framework-generated route maps and decorators; a grep of handler names alone
is incomplete.

## High-value source and sink seeds

Use these only to seed traces. Confirm arguments, data flow, options, and
reachability before reporting.

| Family | Sources/sinks and review targets |
|---|---|
| XSS | `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, React `dangerouslySetInnerHTML`, Vue `v-html`, direct template compilation, URL/attribute/script contexts, sanitizer configuration |
| SQL/query injection | String-built calls to database clients, `query`/`execute` raw text, Prisma `$queryRawUnsafe`/`$executeRawUnsafe`, Sequelize literals/raw replacements, Knex raw fragments, user-selected field/order/operator names |
| Command/argument injection | `child_process.exec`/`execSync`, `spawn`/`execFile` with `shell: true`, command interpreters, Electron shell APIs, option injection in argument arrays |
| Code/template injection | `eval`, indirect eval, `Function`, string timers, `vm`, dynamic template compilation, expression engines, unsafe dynamic `import()` or module paths |
| Files/paths | `fs` reads/writes/deletes, `sendFile`, static serving, archive extractors, upload destinations, temporary paths, path prefix tests, symlink behavior |
| SSRF/open redirect | `fetch`, Axios/undici/got/request clients, URL preview/webhook/import features, proxy targets, `redirect`, `Location`, trusted-host and forwarded-header construction |
| Deserialization/parsing | Non-JSON object serializers, YAML full loaders, XML parsers, prototype-reviving formats, archive/document/image parsers, unbounded JSON/body parsing |
| Prototype pollution | Recursive merge/default/set helpers, dotted/bracket path setters, user-controlled property keys, `Object.assign` into security-sensitive targets, inherited property checks |
| Auth/session | Route guard placement, JWT `verify` options and key selection, cookie options, session rotation/revocation, OAuth state/nonce/PKCE, password reset, WebSocket upgrade auth |
| Authorization | Object lookup by request ID, missing tenant predicates, GraphQL resolver policy, server actions, batch/export/import paths, client-provided roles/owners/status |
| Crypto/secrets | `Math.random` for security values, deprecated cipher APIs, static keys/nonces, signature decode without verify, insecure TLS agent options, secrets in source/logs/client bundles |
| DoS | Regex on unbounded input, JSON/body/upload limits, deep recursion/merge, unbounded `Promise.all`, queues/caches/maps without caps, request fan-out, synchronous CPU/filesystem work |
| Browser boundaries | `postMessage`, `window.open` without `noopener`, `target=_blank` only with an explicit `rel="opener"` (current browsers imply `noopener` for it), service workers, DOM URL sinks, local/session storage, CORS assumptions, CSRF, clickjacking |
| Electron/desktop | `nodeIntegration`, `contextIsolation`, preload bridges, IPC caller/channel validation, navigation/new-window handlers, custom protocols, filesystem/process methods exposed to renderer |

Useful seed searches may include API names above plus repository-specific wrappers.
Search definitions and call sites; security-sensitive behavior is frequently hidden
behind a helper with a harmless name.

## Framework review

### Express, Fastify, Koa, and Nest

- Enumerate routers/controllers and all mount prefixes.
- Verify authentication and authorization middleware order and coverage, including
  error/fallback routes.
- Check `trust proxy`, forwarded headers, host-derived URLs, body limits, static
  roots, upload middleware, and error handlers.
- Confirm validation transforms do not enable mass assignment or silently retain
  unknown properties.
- Check async error propagation and whether rejected promises crash or bypass
  cleanup/policy.

### Next.js and React

- Separate Server Components, Client Components, Route Handlers, middleware, server
  actions, build-time generation, and browser code.
- Treat server actions and route handlers as externally callable boundaries; UI
  visibility is not authorization.
- Do not accept middleware as the only authorization layer. Next.js before 12.3.5,
  13.5.9, 14.2.25, and 15.2.3 let a client skip middleware with the
  `x-middleware-subrequest` header (CVE-2025-29927); check the resolved version and
  whether handlers or data access re-check authorization.
- Review cache keys and revalidation for user/tenant data.
- Check serialization from server to client for secrets and excessive fields.
- Review redirects, image/URL fetchers, HTML injection, CSRF assumptions, and
  environment variables exposed through client bundles.

### Vue/Nuxt and Angular

- Review raw HTML directives/bindings, URL bindings, SSR hydration data, server
  routes/actions, state serialization, and client-only guards.
- Confirm framework sanitization applies to the exact binding/context and was not
  bypassed with trust/bypass APIs.
- Check shared SSR state and caches for cross-request or cross-user contamination.

### GraphQL

- Enforce authentication and authorization in resolvers/data access, not only at
  the schema/UI level.
- Check node/global IDs, nested resolvers, batch loaders, aliases, fragments,
  introspection policy, depth/complexity, and field-level data exposure.
- Ensure data-loader and response caches include identity and tenant context.

## Runtime and language traps

- Plain objects inherit properties. Use own-property checks and safe maps for
  attacker-controlled keys.
- Object spread/assignment can copy security-sensitive fields; schema parsing must
  reject or strip unknown fields intentionally.
- JavaScript number precision can corrupt large identifiers, money, timestamps, and
  limits. Validate safe integer ranges or use an appropriate exact representation.
- URL parsing, path normalization, Unicode, and header handling may differ across
  frontend, proxy, framework, and backend layers.
- Promise concurrency does not make check-and-update sequences atomic.
- Async cancellation, timeout, and retry paths can leak resources or duplicate
  durable operations.
- Dynamic property access can bypass field allowlists or select dangerous methods.
- Regexes are stateful with some flags and can have catastrophic worst cases.
- Package lifecycle scripts and build plugins execute with developer/CI privileges.
- Browser-bundled environment variables and source maps are public regardless of
  naming conventions.

## Defense validation

Accept a defense only after confirming:

- runtime schemas parse the actual boundary and constrain unknown fields, types,
  sizes, nested depth, and semantic relationships;
- SQL values are parameterized and dynamic identifiers/operators are allowlisted;
- process execution avoids the shell and rejects dangerous option/operand semantics;
- file access uses a canonical contained path and accounts for link/re-resolution
  behavior;
- HTML is encoded/sanitized for the final context after all transformations;
- outbound URL enforcement covers scheme, credentials, redirects, DNS, resolved
  addresses, proxies, and the connected endpoint;
- authorization includes action, resource, owner/tenant, and current state;
- JWT/token verification binds algorithm, key, issuer, audience, subject/scope, and
  time claims;
- rate/resource limits exist at the correct per-user/per-tenant/global layers;
- a framework default is active in the deployed mode and version evidenced by the
  repository.

Do not report:

- `JSON.parse` alone as unsafe deserialization;
- React's ordinary escaped interpolation as XSS;
- a parameterized query merely because a request value reaches it;
- `spawn` with a constant executable and constrained arguments as shell injection;
- a path join alone without a containment escape and sensitive operation;
- a development dependency CVE without establishing the installed/resolved version
  and relevant production or build threat model.

## Tool-assisted checks

Commands that build or run project code, or need the network, run only with
explicit user authorization; never on an untrusted repository. That covers
repository tests, builds, package scripts, ESLint, `npm audit`/`yarn audit`/`pnpm audit`
(query the registry), and Semgrep registry rules. Without authorization, list them
as recommended checks:

- repository tests and focused safe regression tests;
- `npm audit` or the workspace's package-manager audit against the lockfile;
- configured ESLint security rules (ESLint executes the repository's config files)
  or Semgrep for seed hypotheses;
- TypeScript compiler checks for unchecked `any`/unknown boundaries.

Treat every tool result as a hypothesis until repository-specific reachability and
impact are established.
