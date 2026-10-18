---
name: blueprint
description: "Guidelines for building Go applications on the Blueprint framework (github.com/oddbit-project/blueprint) and GoAuth authentication library (github.com/blackshieldpt/goauth). Covers architecture, providers, HTTP API patterns, database access, authentication, testing, migrations, error handling, and style rules. Use when code imports github.com/oddbit-project/blueprint or github.com/blackshieldpt/goauth, or user mentions Blueprint framework, Blueprint provider, Blueprint container, or GoAuth. Skip when the project has no Go files or no Blueprint/GoAuth dependency."
version: 1.2.5
---

# Blueprint

Conventions for Go services on `github.com/oddbit-project/blueprint`. All patterns are
proven in production; match them.

## Reference files

Load the file for the area the task touches.

| File | Covers |
|---|---|
| `references/configuration-and-di.md` | Config struct, `Validate()`, loading, JSON format; context structs, service container, factories |
| `references/service-layer.md` | Repository pattern, query patterns, PostgreSQL/ClickHouse records, buffered CH writer, `qb` update API, `db.Grid` |
| `references/http-api.md` | Server setup, body limits, handler chaining, deserialization, validators, responses, errors, pagination, CSV, sessions |
| `references/authentication.md` | GoAuth, JWT middleware, API keys, login flow, Argon2id, crypt utils, secrets, audit trail, HMAC, htpasswd |
| `references/errors-and-shutdown.md` | Sentinel errors, nil-for-not-found, error flow; destructors, goroutine launchers |
| `references/components.md` | Thread pool, atomic status tracking, counter stats, custom Prometheus collector, batch writer |
| `references/database.md` | PostgreSQL migrations, schema conventions, DB initialization, `FOR UPDATE SKIP LOCKED` job queue |
| `references/rpc.md` | MessagePack inter-service RPC |
| `references/testing.md` | testify suites, testcontainers, fixtures |
| `references/logging.md` | Logger creation, methods, nil-logger fallback |
| `references/providers.md` | SMTP, S3, TLS, NATS provider setup (ClickHouse writer is in service-layer §6.4–6.5; HMAC in authentication §8.11) |

## 1. Stack

| Concern | Import |
|---|---|
| Framework | `github.com/oddbit-project/blueprint` |
| HTTP | `github.com/gin-gonic/gin` via `github.com/oddbit-project/blueprint/provider/httpserver` |
| PostgreSQL (pgx) | `github.com/oddbit-project/blueprint/provider/pgsql` |
| ClickHouse | `github.com/oddbit-project/blueprint/provider/clickhouse` |
| Kafka (franz-go) | `github.com/oddbit-project/blueprint/provider/franz` |
| NATS / etcd / S3 / SMTP | `github.com/oddbit-project/blueprint/provider/{nats,etcd,s3,smtp}` |
| HMAC auth | `github.com/oddbit-project/blueprint/provider/hmacprovider` |
| SQL builder | `github.com/doug-martin/goqu/v9`; updates via `github.com/oddbit-project/blueprint/db/qb` |
| Logging | Zerolog via `github.com/oddbit-project/blueprint/log` |
| Auth | `github.com/blackshieldpt/goauth` (JWT + Argon2); store `github.com/blackshieldpt/goauth/store/pgstore`; HTTP helpers `github.com/oddbit-project/blueprint/provider/httpserver/auth` (import as `bpauth` beside the project's own `auth/` package) |
| Metrics | `github.com/prometheus/client_golang` |
| Serialization | MessagePack `github.com/vmihailenco/msgpack/v5` (inter-service); `encoding/json` (HTTP) |
| Validation | `github.com/go-playground/validator/v10` through Gin `binding:` tags |
| UUIDs | `github.com/google/uuid` |
| Tests | `github.com/stretchr/testify` suites + `github.com/testcontainers/testcontainers-go` |

## 2. Lifecycle: New → Build → Start

Every service embeds `*blueprint.Container`.

**`cmd/{service}/main.go`** — flags into a `CliArgs` struct of pointer fields:
`ConfigFile` (`-c config/app.json`), `Migrate` (`-migrate`), `ShowVersion` (`-version`),
`SampleConfig` (`-sample-config`); then:

```go
utils.PanicOnError(log.Configure(log.NewDefaultConfig()))
logger := log.New("app")
flag.Parse()
// -version → print VERSION, exit 0; -sample-config → json.MarshalIndent(appservice.NewConfig()), exit 0
app, err := appservice.NewApp(cliArgs, logger)   // err → logger.Error(err, "Initialization failed"); os.Exit(-1)
app.Start(app.Build())
```

**`NewApp`** — `provider.NewJsonProvider(*args.ConfigFile)` → `&App{Container:
blueprint.NewContainer(cfg), args, logger}`.

**`Build() *core.AppContext`** — every failure goes through `m.AbortFatal(err)`:

```go
cfg := NewConfig()
m.AbortFatal(m.Config.Get(cfg)); m.AbortFatal(cfg.Validate())
ctx := &core.AppContext{}
ctx.Logger   = factories.BuildLog(m.Container, m.logger)
ctx.Metadata = factories.BuildMetadataClient(m.Container, cfg.Metadata, ctx.Logger)
ctx.PgDb     = m.initDb(cfg.Db)
if m.args.Migrate != nil && *m.args.Migrate { m.AbortFatal(migrations.PgMigrate(m.Context, ctx.PgDb)) }
svc, err := core.NewServiceContext(m.Context, ctx); m.AbortFatal(err); ctx.Svc = svc
m.initWebApi(cfg.WebApi, ctx)
return ctx
```

Order: config → cross-cutting factories → databases → migrations (flag-gated) → services →
HTTP. Destructors are registered as each dependency is built (see errors-and-shutdown).

**`Start(ctx)`** — `m.Run(launchers...)` blocks until the shutdown signal; each launcher is
`func(app interface{}) error` that starts a goroutine and returns nil. The API launcher
logs `ctx.ApiServer.Addr`, runs `ctx.ApiServer.Start()`, and on error calls
`m.Terminate(err)`.

## 3. Project structure

```
cmd/{service}/main.go        flags + bootstrap
{service}/
  application.go             Build() + Start()
  config.go                  Config + NewConfig() + Validate()
  api/  server.go errors.go common.go {domain}.go      ApiServer, routes, error helpers, ValidationError, handlers
  core/ context.go           AppContext + ServiceContext (DI roots)
  service/ {domain}.go {domain}_test.go common.go fixtures_test.go   logic, suites, shared constants, factories
  data/ {concern}.go         query/DDL layer
  auth/ hasher.go            password hashing
  migrations/ pgsql.go pgsql/*.sql   runner (embed) + SQL files
pkg/                         shared across services
  entity/ {domain}.go constants.go errors.go   *Record/*Info models, Tbl* names, Err* sentinels
  interfaces/ interface.go   Runnable, Writer, RunnableWriter
  factories/ log.go metadata.go prometheus.go   BuildLog, BuildMetadataClient, BuildPrometheus
  rpc/ message.go            MessagePack envelope
  metadata/ provider.go      etcd metadata
  utils/  types/             helpers; nullable_time.go etc.
config/ {service}.json  dev/   JSON templates; dev-environment configs
internal/testutil/ database.go context.go fixtures.go   testcontainers, test contexts, factories
benchmark/                   load generators / perf tests
```

One file per domain in `api/` and `service/`; shared code in `pkg/`, service-specific
code in the service package; `internal/` is test infrastructure only.

## 4. Naming

Files: one concern, lowercase (`product_category.go`); tests in the same package
(`product_test.go`); `config.go` and `application.go` per service package.

| Kind | Convention | Example |
|---|---|---|
| Package | lowercase, short | `entity`, `rpc` |
| Interface | behaviour verb | `Runnable`, `Writer`, `Provider` |
| Struct | noun | `ProductService`, `EventDispatcher` |
| DB record | `*Record` | `ProductRecord` |
| API response | `*Info` | `ProductInfo` |
| API request | `*Params` / `*Request` | `ProductParams`, `LoginRequest` |
| Service | noun or `*Service` | `OrderService` |
| Constructor | `New*` | `NewProductService()` |
| Table name | `Tbl*` | `TblProduct = "product"` |
| Status | `{Domain}Status*` | `ProductStatusActive = 1` |
| Error | `Err*` | `ErrNotFound`, `ErrNotUnique` |
| CLI args | `CliArgs`, pointer fields from `flag.*` | |

Struct tags on records, e.g. `` `json:"categoryId" db:"fk_category"` ``:

| Tag | Meaning |
|---|---|
| `json` | camelCase, HTTP API |
| `db` | snake_case, PostgreSQL column |
| `binding` | Gin validation, `"required,min=3,max=128"` |
| `goqu` | `"skipinsert,skipupdate"` on generated PKs |
| `auto` | `"true"` = auto-updated by `qb` UpdateBuilder (service-layer §6.6) |
| `grid` | `"sort,search,filter"` for `db.Grid` (service-layer §6.7) |
| `ch` | ClickHouse column |

## 5. Style rules

1. No unnecessary abstractions — three similar lines beat a premature helper.
2. Comments only for a non-obvious *why*.
3. Return `error` up; sentinel errors with `errors.Is()`.
4. Pass `context.Context` through constructors; services keep it and methods take it only when they do I/O that outlives the call.
5. Accept a nil logger; default to `log.New("svcName")`.
6. Pointer receivers on service and record types.
7. `make([]*T, 0)` for empty return slices.
8. Validate at boundaries (`binding:` tags, config `Validate()`); trust internal code.
9. No feature flags, no backwards-compat shims.
10. Imports in two groups: stdlib + project, then third-party `github.com/`.

## 6. Checklists

**New endpoint** — request/response structs with `json:` + `binding:` → route in
`server.go` (public/protected, admin/common section) → handler method on `*ApiServer`,
`IsAdmin()` guard for admin → `httpserver.ValidateJSON(g, &req)` for bodies → call
`s.svc.*` → `response.Success(g, data)` or a domain error helper mapping sentinels to HTTP.

**New service** — `service/{domain}.go` struct with `db.Repository` fields + `*log.Logger`;
`NewXxxService(ctx context.Context, conn *db.SqlClient, logger *log.Logger)`; register
in `ServiceContext` / `NewServiceContext()`; records in `pkg/entity/{domain}.go` with
`json`/`db`/`goqu` tags; sentinels as `utils.Error()` constants; testify + testcontainers
integration tests.

**New background service** — embed `*blueprint.Container`; New → Build → Start; config via
`provider.NewJsonProvider()` + `Validate()`; factories for logging/metrics/metadata;
destructors registered in reverse shutdown order (LIFO); components implement `Runnable`;
`m.Run(...)` launchers for the event loop.
