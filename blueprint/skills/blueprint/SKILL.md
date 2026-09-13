---
name: blueprint
description: "Guidelines for building Go applications on the Blueprint framework (github.com/oddbit-project/blueprint) and GoAuth authentication library (github.com/blackshieldpt/goauth). Covers architecture, providers, HTTP API patterns, database access, authentication, testing, migrations, error handling, and style rules. Use when code imports github.com/oddbit-project/blueprint or github.com/blackshieldpt/goauth, or user mentions Blueprint framework, Blueprint provider, Blueprint container, or GoAuth. Skip when the project has no Go files or no Blueprint/GoAuth dependency."
version: 1.1.0
---

# Blueprint Application Skill

Guidelines for building Go applications on the Blueprint framework (`github.com/oddbit-project/blueprint`). Covers architecture, patterns, conventions, and style rules. All patterns are proven in production.

---

## Reference files

Load the file for the area the task touches; each is self-contained.

| File | Sections |
|---|---|
| `references/configuration-and-di.md` | Configuration; Dependency Injection |
| `references/service-layer.md` | Service Layer |
| `references/http-api.md` | HTTP API Layer |
| `references/authentication.md` | Authentication & Credentials |
| `references/errors-and-shutdown.md` | Error Handling; Graceful Shutdown |
| `references/components.md` | Component Interfaces |
| `references/database.md` | Database Patterns |
| `references/rpc.md` | Inter-Service RPC |
| `references/testing.md` | Testing |
| `references/logging.md` | Logging |
| `references/providers.md` | Providers |

---

## 1. Core Technology Stack

| Concern | Technology | Import Path |
|---|---|---|
| Base Framework | Blueprint | `github.com/oddbit-project/blueprint` |
| HTTP | Gin | `github.com/gin-gonic/gin` |
| HTTP Server | Blueprint httpserver | `github.com/oddbit-project/blueprint/provider/httpserver` |
| PostgreSQL | Blueprint pgsql (pgx) | `github.com/oddbit-project/blueprint/provider/pgsql` |
| ClickHouse | Blueprint clickhouse | `github.com/oddbit-project/blueprint/provider/clickhouse` |
| Kafka | Blueprint franz (franz-go) | `github.com/oddbit-project/blueprint/provider/franz` |
| NATS | Blueprint nats | `github.com/oddbit-project/blueprint/provider/nats` |
| etcd | Blueprint etcd | `github.com/oddbit-project/blueprint/provider/etcd` |
| S3 Storage | Blueprint s3 | `github.com/oddbit-project/blueprint/provider/s3` |
| SMTP | Blueprint smtp | `github.com/oddbit-project/blueprint/provider/smtp` |
| HMAC Auth | Blueprint hmacprovider | `github.com/oddbit-project/blueprint/provider/hmacprovider` |
| SQL Builder | GOQU | `github.com/doug-martin/goqu/v9` |
| Query Builder | Blueprint qb | `github.com/oddbit-project/blueprint/db/qb` |
| Logging | Zerolog (via Blueprint) | `github.com/oddbit-project/blueprint/log` |
| Auth | GoAuth (JWT + Argon2) | `github.com/blackshieldpt/goauth` |
| Metrics | Prometheus | `github.com/prometheus/client_golang` |
| Serialization | MessagePack (inter-service) | `github.com/vmihailenco/msgpack/v5` |
| Serialization | JSON (HTTP API) | `encoding/json` (stdlib) |
| Validation | go-playground/validator | `github.com/go-playground/validator/v10` (via Gin binding tags) |
| UUIDs | google/uuid | `github.com/google/uuid` |
| Testing | testify suites + testcontainers | `github.com/stretchr/testify`, `github.com/testcontainers/testcontainers-go` |

---

## 2. Application Lifecycle

Every service embeds `*blueprint.Container` and follows a three-phase lifecycle: **New -> Build -> Start**.

### 2.1 Entry Point (`cmd/{service}/main.go`)

```go
package main

import (
    "encoding/json"
    "flag"
    "fmt"
    "os"

    "github.com/oddbit-project/blueprint/log"
    "github.com/oddbit-project/blueprint/utils"
    "myapp/appservice"
)

var cliArgs = &appservice.CliArgs{
    ConfigFile:   flag.String("c", "config/app.json", "Config file"),
    Migrate:      flag.Bool("migrate", false, "Migrate database to latest version"),
    ShowVersion:  flag.Bool("version", false, "Show version"),
    SampleConfig: flag.Bool("sample-config", false, "Show default config"),
}

func main() {
    utils.PanicOnError(log.Configure(log.NewDefaultConfig()))
    logger := log.New("app")
    flag.Parse()

    if *cliArgs.ShowVersion {
        fmt.Printf("Version: %s\n", VERSION)
        os.Exit(0)
    }

    if *cliArgs.SampleConfig {
        cfg := appservice.NewConfig()
        output, _ := json.MarshalIndent(cfg, "", "  ")
        fmt.Println(string(output))
        os.Exit(0)
    }

    app, err := appservice.NewApp(cliArgs, logger)
    if err != nil {
        logger.Error(err, "Initialization failed")
        os.Exit(-1)
    }

    app.Start(app.Build())
}
```

### 2.2 Application Struct

```go
type CliArgs struct {
    ConfigFile   *string
    Migrate      *bool
    ShowVersion  *bool
    SampleConfig *bool
}

type App struct {
    *blueprint.Container
    logger *log.Logger
    args   *CliArgs
}

func NewApp(args *CliArgs, logger *log.Logger) (*App, error) {
    cfg, err := provider.NewJsonProvider(*args.ConfigFile)
    if err != nil {
        return nil, err
    }
    return &App{
        Container: blueprint.NewContainer(cfg),
        args:      args,
        logger:    logger,
    }, nil
}
```

### 2.3 Build Phase

`Build()` initializes all dependencies, runs migrations, wires services, and registers destructors. Use `m.AbortFatal(err)` for any error that should prevent startup.

```go
func (m *App) Build() *core.AppContext {
    cfg := NewConfig()
    m.AbortFatal(m.Config.Get(cfg))
    m.AbortFatal(cfg.Validate())

    ctx := &core.AppContext{}

    // Initialize cross-cutting concerns via factories
    ctx.Logger = factories.BuildLog(m.Container, m.logger)
    ctx.Metadata = factories.BuildMetadataClient(m.Container, cfg.Metadata, ctx.Logger)

    // Initialize database
    ctx.PgDb = m.initDb(cfg.Db)

    // Run migrations if requested
    if m.args.Migrate != nil && *m.args.Migrate {
        m.AbortFatal(migrations.PgMigrate(m.Context, ctx.PgDb))
    }

    // Wire services
    var err error
    ctx.Svc, err = core.NewServiceContext(m.Context, ctx)
    m.AbortFatal(err)

    // Initialize HTTP API
    m.initWebApi(cfg.WebApi, ctx)

    return ctx
}
```

### 2.4 Start Phase

`Start()` launches the event loop. `m.Run(...)` accepts goroutine launchers and blocks until shutdown signal.

```go
func (m *App) Start(ctx *core.AppContext) {
    m.Run(
        func(app interface{}) error {
            go func() {
                m.logger.Infof("API endpoint: %s", ctx.ApiServer.Addr)
                if err := ctx.ApiServer.Start(); err != nil {
                    m.Terminate(err)
                }
            }()
            return nil
        },
    )
}
```

---

## 3. Project Structure

```
myapp/
  cmd/{service}/main.go          # entry points (flag parsing + bootstrap)
  {service}/
    application.go               # Build() + Start() lifecycle
    config.go                    # Config struct + NewConfig() + Validate()
    api/
      server.go                  # ApiServer struct + route registration
      errors.go                  # domain-specific HTTP error helpers
      common.go                  # shared helpers (ValidationError)
      {domain}.go                # handler methods grouped by domain
    core/
      context.go                 # AppContext + ServiceContext (DI roots)
    service/
      {domain}.go                # business logic (one file per domain)
      {domain}_test.go           # integration tests (testify suites)
      common.go                  # shared service constants
      fixtures_test.go           # test data factories
    data/
      {concern}.go               # database query/DDL layer
    auth/
      hasher.go                  # password hashing
    migrations/
      pgsql.go                   # migration runner (embed)
      pgsql/*.sql                # SQL migration files
  pkg/                           # shared library code (cross-service)
    entity/
      {domain}.go                # domain models (*Record, *Info structs)
      constants.go               # shared constants, table names
      errors.go                  # sentinel error constants
    interfaces/
      interface.go               # Runnable, Writer, RunnableWriter
    factories/
      log.go                     # BuildLog factory
      metadata.go                # BuildMetadataClient factory
      prometheus.go              # BuildPrometheus factory
    rpc/
      message.go                 # MessagePack envelope for inter-service RPC
    metadata/
      provider.go                # etcd metadata interface + implementation
    utils/
      strings.go, slices.go, ... # small helpers
    types/
      nullable_time.go, ...      # custom DB/serialization types
  config/
    {service}.json               # JSON config templates
    dev/                         # development environment configs
  internal/testutil/
    database.go                  # testcontainers setup
    context.go                   # test context builders
    fixtures.go                  # test data factories
  benchmark/                     # load generators / perf tests
```

Key conventions:
- `application.go` + `config.go` in each service package
- One file per domain concern in `api/` and `service/`
- Shared code in `pkg/`, service-specific code in the service package
- `internal/` for test infrastructure only

---

## 16. Naming Conventions

### 16.1 Files

- One concern per file, lowercase names: `product.go`, `product_category.go`
- Tests in same package: `product_test.go`
- Config: `config.go` per service package
- Lifecycle: `application.go` per service package

### 16.2 Go Identifiers

| Kind | Convention | Examples |
|---|---|---|
| Packages | lowercase, short | `entity`, `backend`, `rpc` |
| Interfaces | CamelCase, behavior-verb | `Runnable`, `Writer`, `Provider` |
| Structs | CamelCase, noun | `ProductService`, `EventDispatcher` |
| DB-mapped records | `*Record` suffix | `ProductRecord`, `OrderRecord` |
| API response DTOs | `*Info` suffix | `ProductInfo`, `UserInfo` |
| API request structs | `*Params` or `*Request` suffix | `ProductParams`, `LoginRequest` |
| Services | domain noun or `*Service` | `UserService`, `OrderService` |
| Constructors | `New*` prefix | `NewProductService()`, `NewConfig()` |
| Table name constants | `Tbl*` prefix | `TblProduct = "product"` |
| Status constants | `{Domain}Status*` | `ProductStatusActive = 1` |
| Error constants | `Err*` prefix | `ErrNotFound`, `ErrNotUnique` |
| CLI args | `CliArgs` struct | Pointer fields with `flag.*` |

### 16.3 Struct Tags

```go
type ProductRecord struct {
    Id         uuid.UUID  `json:"id" db:"id_product"`
    CreatedAt  time.Time  `json:"createdAt" db:"created_at"`
    UpdatedAt  time.Time  `json:"updatedAt" db:"updated_at"`
    Status     int        `json:"status" db:"status"`
    CategoryId uuid.UUID  `json:"categoryId" db:"fk_category"`
    Name       string     `json:"name" db:"name"`
    Attributes Attributes `json:"attributes" db:"attributes"`
}
```

Tag conventions:
- `json:` — camelCase for HTTP API
- `db:` — snake_case matching PostgreSQL column names
- `binding:` — Gin validation rules (`"required,min=3,max=128"`)
- `goqu:` — insert/update control (`"skipinsert,skipupdate"` for auto-generated PKs)
- `auto:` — marks auto-updated fields for qb UpdateBuilder (`"true"` — see `references/service-layer.md` §6.6)
- `grid:` — db.Grid capabilities (`"sort,search,filter"` — see `references/service-layer.md` §6.7)
- `ch:` — ClickHouse column mapping

---

## 17. Code Style Rules

1. **No unnecessary abstractions.** Three similar lines beat a premature helper.
2. **Minimal comments.** Only when the WHY is non-obvious. No "what" comments.
3. **Error propagation.** Return `error` up. Use sentinel errors with `errors.Is()`.
4. **Context propagation.** Pass `context.Context` through constructors and method signatures.
5. **Nil logger fallback.** Accept nil, create default with `log.New("svcName")`.
6. **Pointer receivers.** All methods on service and record types.
7. **Slice initialization.** `make([]*Type, 0)` for empty slices in return values.
8. **Validation at boundaries.** Binding tags on request structs, `Validate()` on config structs. Trust internal code.
9. **No feature flags or backwards-compat shims.** Just change the code.
10. **Import grouping.** Two groups separated by a blank line: (stdlib + project imports mixed) then (third-party `github.com/` imports).

---

## 18. Checklists

### Adding a New API Endpoint

1. Define request/response structs with `json:` + `binding:` tags.
2. Add the route in `server.go` (correct section: public vs. protected, admin vs. common).
3. Implement handler as a method on `*ApiServer`.
4. For admin endpoints, add `IsAdmin()` guard.
5. Use `httpserver.ValidateJSON(g, &req)` for POST/PUT body parsing.
6. Call the service method via `s.svc.*`.
7. Return `response.Success(g, data)` or the appropriate error helper.
8. Add or reuse a domain error helper to map sentinel errors to HTTP responses.

### Adding a New Service

1. Create `service/{domain}.go` with a struct holding `db.Repository` fields and `*log.Logger`.
2. Constructor: `NewXxxService(ctx context.Context, conn *db.SqlClient, logger *log.Logger)`.
3. Register in `ServiceContext` and wire in `NewServiceContext()`.
4. Define entity records in `pkg/entity/{domain}.go` with `json`, `db`, and `goqu` struct tags.
5. Define sentinel errors as `utils.Error()` constants.
6. Write integration tests using testify suites with testcontainers.

### Adding a New Background Service

1. Embed `*blueprint.Container` in the application struct.
2. Follow the `New() -> Build() -> Start()` lifecycle.
3. Load config with `provider.NewJsonProvider()` + `Config` struct with `Validate()`.
4. Use factories for cross-cutting concerns (logging, metrics, metadata).
5. Register destructors in reverse shutdown order (LIFO).
6. Implement `Runnable` interface for background components.
7. Use `m.Run(...)` with goroutine launchers for the main event loop.

---
