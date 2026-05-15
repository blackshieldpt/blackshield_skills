---
name: blueprint
description: "Guidelines for building Go applications on the Blueprint framework (github.com/oddbit-project/blueprint) and GoAuth authentication library (github.com/blackshieldpt/goauth). Covers architecture, providers, HTTP API patterns, database access, authentication, testing, migrations, error handling, and style rules. Use when code imports github.com/oddbit-project/blueprint or github.com/blackshieldpt/goauth, or user mentions Blueprint framework, Blueprint provider, Blueprint container, or GoAuth. Skip when the project has no Go files or no Blueprint/GoAuth dependency."
version: 1.0.0
---

# Blueprint Application Skill

Guidelines for building Go applications on the Blueprint framework (`github.com/oddbit-project/blueprint`). Covers architecture, patterns, conventions, and style rules. All patterns are proven in production.

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

## 4. Configuration

### 4.1 Config Struct

Each service has a top-level `Config` with nested provider configs. JSON tags map to config file sections. Always declare a `NewConfig()` constructor that returns sensible defaults — Blueprint provider configs expose their own `New*Config()` constructors, and custom sub-configs should follow the same pattern.

```go
type Config struct {
    WebApi     *ApiConfig              `json:"webApi"`
    Db         *pgsql.ClientConfig     `json:"pgsql"`
    Metadata   *etcd.Config            `json:"etcd"`
    Prometheus *prometheus.Config       `json:"prometheus"`
    Log        *log.Config             `json:"log"`
    App        *AppConfig              `json:"app"`
}

func NewConfig() *Config {
    return &Config{
        WebApi:     NewApiConfig(),
        Db:         pgsql.NewClientConfig(),
        Metadata:   &etcd.Config{},
        Prometheus: prometheus.NewConfig(),
        Log:        log.NewDefaultConfig(),
        App:        NewAppConfig(),
    }
}
```

### 4.2 Validation

Always provide `Validate()` that chains sub-validations:

```go
func (c *Config) Validate() error {
    if err := c.WebApi.Validate(); err != nil {
        return err
    }
    if err := c.Db.Validate(); err != nil {
        return err
    }
    if err := c.Metadata.Validate(); err != nil {
        return err
    }
    // ...
    return nil
}
```

### 4.3 Loading

```go
cfg := NewConfig()
m.AbortFatal(m.Config.Get(cfg))       // load entire config
m.AbortFatal(cfg.Validate())

// or load a single section:
logCfg := log.NewDefaultConfig()
m.Config.GetKey("log", logCfg)
```

### 4.4 Config File Format

JSON files in `config/`. Each service has its own template:

```json
{
  "webApi": {
    "apiServer": { "host": "0.0.0.0", "port": 8080 },
    "security": {}
  },
  "pgsql": { "host": "localhost", "port": 5432, "database": "myapp" },
  "etcd": { "endpoints": ["localhost:2379"] },
  "prometheus": { "host": "0.0.0.0", "port": 2111 },
  "log": { "level": "info" },
  "app": { "adminUser": "admin@localhost" }
}
```

---

## 5. Dependency Injection

### 5.1 Context Structs

Two-level DI: an **AppContext** holds infrastructure, a **ServiceContext** holds business services.

```go
type AppContext struct {
    PgDb     *db.SqlClient
    Metadata metadata.Provider
    Auth     *goauth.AuthService
    Logger   *log.Logger
    Svc      *ServiceContext
}

type ServiceContext struct {
    User     *service.UserService
    Product  *service.ProductService
    Order    *service.OrderService
}

func NewServiceContext(ctx context.Context, appCtx *AppContext) (*ServiceContext, error) {
    return &ServiceContext{
        User:    service.NewUserService(ctx, appCtx.PgDb, appCtx.Logger),
        Product: service.NewProductService(ctx, appCtx.PgDb, appCtx.Logger),
        Order:   service.NewOrderService(ctx, appCtx.PgDb, appCtx.Logger),
    }, nil
}
```

### 5.2 Service Container (Flat Alternative)

For simpler services without GoAuth or complex infrastructure, use a single flat container that creates repositories once and wires all services:

```go
type Container struct {
    Entity  *EntityService
    Site    *SiteService
    Asset   *AssetService
    Network *NetworkService
}

func NewContainer(ctx context.Context, conn *db.SqlClient, logger *log.Logger) *Container {
    entityRepo := repository.NewEntityRepository(ctx, conn, logger)
    siteRepo := repository.NewSiteRepository(ctx, conn, logger)
    assetRepo := repository.NewAssetRepository(ctx, conn, logger)
    networkRepo := repository.NewNetworkRepository(ctx, conn, logger)

    return &Container{
        Entity:  NewEntityService(entityRepo, logger),
        Site:    NewSiteService(siteRepo, logger),
        Asset:   NewAssetService(assetRepo, logger),
        Network: NewNetworkService(networkRepo, entityRepo, logger),
    }
}
```

Services that need cross-domain validation receive multiple repositories (e.g., `NetworkService` receives `entityRepo` to verify FK existence).

### 5.3 Factory Functions

Cross-cutting concerns are initialized via factory functions in `pkg/factories/`. Factories use `app.AbortFatal(err)` for unrecoverable errors and register destructors for cleanup.

```go
func BuildLog(app *blueprint.Container, logger *log.Logger) *log.Logger {
    logCfg := log.NewDefaultConfig()
    if err := app.Config.GetKey("log", logCfg); err != nil {
        return logger // no custom config, return default
    }
    app.AbortFatal(logCfg.Validate())
    app.AbortFatal(log.Configure(logCfg))
    result, err := logCfg.Logger()
    app.AbortFatal(err)
    return result
}

func BuildMetadataClient(app *blueprint.Container, cfg *etcd.Config, logger *log.Logger) metadata.Provider {
    logger.Info("initializing etcd client...")
    app.AbortFatal(cfg.Validate())
    client, err := etcd.NewClient(cfg)
    app.AbortFatal(err)
    meta := metadata.NewEtcdMetadata(client, logger)
    blueprint.RegisterDestructor(func() error {
        return client.Close()
    })
    return meta
}
```

---

## 6. Service Layer

### 6.1 Repository Pattern

**Direct repository** — for simpler services, hold `db.Repository` directly in the service:

```go
type ProductService struct {
    repo   db.Repository
    logger *log.Logger
    ctx    context.Context
    mx     sync.Mutex
}

func NewProductService(ctx context.Context, conn *db.SqlClient, logger *log.Logger) *ProductService {
    if logger == nil {
        logger = log.New("svcProduct")
    }
    return &ProductService{
        repo:   db.NewRepository(ctx, conn, entity.TblProduct),
        logger: logger,
        ctx:    ctx,
    }
}
```

**Separate repository package** — for larger domains with complex queries, extract repositories into a `repository/` package. The repository encapsulates all query logic; services hold repository instances instead of raw `db.SqlClient`:

```go
// repository/entity_repo.go
type EntityRepository struct {
    repo   db.Repository
    logger *log.Logger
    ctx    context.Context
}

func NewEntityRepository(ctx context.Context, conn *db.SqlClient, logger *log.Logger) *EntityRepository {
    return &EntityRepository{
        repo:   db.NewRepository(ctx, conn, entity.TblEntity),
        logger: logger,
        ctx:    ctx,
    }
}

func (r *EntityRepository) GetByID(id uuid.UUID) (*entity.Entity, error) {
    record := &entity.Entity{}
    err := r.repo.FetchOne(r.repo.SqlSelect().Where(goqu.C("id_entity").Eq(id)), record)
    if err != nil {
        if errors.Is(err, sql.ErrNoRows) {
            return nil, nil
        }
        return nil, err
    }
    return record, nil
}
```

```go
// service/entity_service.go
type EntityService struct {
    entityRepo *repository.EntityRepository
    logger     *log.Logger
}

func NewEntityService(entityRepo *repository.EntityRepository, logger *log.Logger) *EntityService {
    return &EntityService{entityRepo: entityRepo, logger: logger}
}
```

Use the separate repository approach when multiple services need the same query logic, or when query complexity warrants its own layer.

### 6.2 Query Patterns

```go
// Fetch list
result := make([]*entity.ProductRecord, 0)
err := s.repo.Fetch(s.repo.SqlSelect().Where(goqu.C("status").Eq(1)), &result)

// Fetch single
record := &entity.ProductRecord{}
err := s.repo.FetchOne(s.repo.SqlSelect().Where(goqu.C("id").Eq(id)), record)
```

**Raw SQL expressions with `goqu.L()`** — use for PostgreSQL-specific operators and functions that GOQU doesn't model natively:

```go
// Range containment
qry := r.repo.SqlSelect().Where(goqu.L("valid_during @> ?::timestamptz", atTime))

// Function-based filter
qry := r.repo.SqlSelect().Where(goqu.L("upper_inf(valid_during)"))

// Combined with typed columns
qry := r.repo.SqlSelect().
    Where(goqu.C("id_entity").Eq(id)).
    Where(goqu.L("upper_inf(valid_during)"))
```

### 6.3 PostgreSQL Records

Records mapped to PostgreSQL use `db:"column_name"` struct tags for column mapping and `json:"fieldName"` for API serialization. Use `goqu:"skipinsert,skipupdate"` on auto-generated columns (serial PKs):

```go
type OrderRecord struct {
    Id        int64      `json:"id" db:"id_order" goqu:"skipinsert,skipupdate"`
    CreatedAt time.Time  `json:"createdAt" db:"created_at"`
    UpdatedAt time.Time  `json:"updatedAt" db:"updated_at"`
    UserId    uuid.UUID  `json:"userId" db:"fk_user"`
    Status    int        `json:"status" db:"status"`
    Name      string     `json:"name" db:"name"`
    Attributes Attributes `json:"attributes" db:"attributes"`
}
```

**Common repository operations:**

```go
// Insert (uses db: tags for column mapping)
err := s.repo.Insert(record)

// Insert and return auto-generated fields
err := s.repo.InsertReturning(record, []string{"id_order"}, &record.Id)

// Fetch list with conditions
result := make([]*entity.OrderRecord, 0)
err := s.repo.Fetch(s.repo.SqlSelect().Where(goqu.C("status").Eq(1)), &result)

// Fetch with field-value map
err := s.repo.FetchWhere(db.FV{"fk_user": userId, "fk_vault": vaultId}, &result)

// Count with conditions
count, err := s.repo.CountWhere(db.FV{"fk_user": userId, "name": name})

// Update specific fields (UpdateFields(sample, setValues, whereValues))
err := s.repo.UpdateFields(&entity.OrderRecord{},
    db.FV{"status": newStatus, "updated_at": time.Now()},
    db.FV{"id_order": orderId},
)

// Delete with conditions
err := s.repo.DeleteWhere(db.FV{"fk_user": userId, "id_order": orderId})
```

**JSONB columns** — use an `Attributes` type implementing `sql.Scanner` and `driver.Valuer`:

```go
type Attributes map[string]interface{}

func (j *Attributes) Scan(value interface{}) error {
    if value == nil {
        *j = nil
        return nil
    }
    bytes, ok := value.([]byte)
    if !ok {
        return fmt.Errorf("failed to scan JSONB: expected []byte, got %T", value)
    }
    return json.Unmarshal(bytes, j)
}

func (j Attributes) Value() (driver.Value, error) {
    if j == nil {
        return nil, nil
    }
    return json.Marshal(j)
}
```

### 6.4 ClickHouse Records

Records mapped to ClickHouse use `ch:"column_name"` struct tags. ClickHouse records are used for both reading (via `ScanStruct`/`Select`) and writing (via `clickhouse.Repository.Insert`):

```go
type MetricRecord struct {
    Timestamp time.Time `json:"timestamp" ch:"event_time"`
    SourceId  string    `json:"sourceId" ch:"source_id"`
    Bytes     uint64    `json:"bytes" ch:"bytes"`
    Events    uint64    `json:"events" ch:"events"`
}
```

**ClickHouse repository** — obtained from a cluster connection:

```go
repo := cluster.Connection().NewRepository(ctx, TblMetrics)
err := repo.Insert(record)
```

**Raw queries with struct scanning:**

```go
// Single row
result := &entity.MetricStats{}
err := cluster.Connection().Conn.QueryRow(ctx, sql, args...).ScanStruct(result)

// Multiple rows
result := []MinuteRecord{}
err := cluster.Connection().Conn.Select(ctx, &result, sql, args...)
```

**Batch writes** — for high-throughput ingestion, use `RecordValues` (a `[]any` alias) with the writer pool instead of struct-based inserts:

```go
type RecordValues []any

pool.Add(clusterId, sourceId, tableName, entity.RecordValues{val1, val2, val3})
```

**ClickHouse timestamp formatting** — when using `time.Time` in GOQU WHERE clauses against ClickHouse `DateTime64` columns, format explicitly as a string. ClickHouse does not accept Go `time.Time` values directly in parameterized queries:

```go
func clickhouseTimestamp(t time.Time) string {
    return t.UTC().Format("2006-01-02 15:04:05.000")
}

qry := s.resultRepo.SqlSelect().
    Where(goqu.C("timestamp").Gte(clickhouseTimestamp(from))).
    Where(goqu.C("timestamp").Lte(clickhouseTimestamp(to)))
```

### 6.5 Buffered ClickHouse Writer

For metrics ingestion, use a buffered writer that queues records and flushes to ClickHouse periodically. Track operational health with Prometheus counters:

```go
var (
    writerRecordsDropped = promauto.NewCounterVec(prometheus.CounterOpts{
        Name: "panorama_writer_records_dropped_total",
        Help: "Total records dropped due to queue full",
    }, []string{"table"})
    writerRecordsFlushed = promauto.NewCounterVec(prometheus.CounterOpts{
        Name: "panorama_writer_records_flushed_total",
        Help: "Total records successfully flushed to ClickHouse",
    }, []string{"table"})
    writerFlushErrors = promauto.NewCounterVec(prometheus.CounterOpts{
        Name: "panorama_writer_flush_errors_total",
        Help: "Total flush attempts that failed",
    }, []string{"table"})
)

type BufferedWriter struct {
    db        *clickhouse.Client
    tableName string
    logger    *log.Logger

    queue     chan types.MetricRecord
    stopCh    chan struct{}
    doneCh    chan struct{}
    status    atomic.Int32
}
```

**Add** queues a record with non-blocking drop on backpressure:

```go
func (w *BufferedWriter) Add(record types.MetricRecord) {
    select {
    case w.queue <- record:
    default:
        writerRecordsDropped.WithLabelValues(w.tableName).Inc()
    }
}
```

**Worker** flushes on a ticker interval, draining the channel into a batch:

```go
func (w *BufferedWriter) worker() {
    defer close(w.doneCh)
    ticker := time.NewTicker(w.config.FlushInterval)
    defer ticker.Stop()

    for {
        select {
        case <-ticker.C:
            w.flushSync()
        case <-w.stopCh:
            w.flushSync()
            return
        }
    }
}
```

Use atomic status tracking (see 11.2) for Start/Stop lifecycle.

### 6.6 Modern Update API (qb Package)

Blueprint's query builder (`db/qb`) provides type-safe UPDATE queries with dialect abstraction, RETURNING clauses, and flexible options. Use `repo.SqlUpdateX()` and `repo.Do()` for repository integration.

```go
import "github.com/oddbit-project/blueprint/db/qb"

// Update via repository — preferred approach
func (s *UserService) UpdateEmail(id int, email string) error {
    record := &entity.UserRecord{Email: email, UpdatedAt: time.Now()}
    ub := s.repo.SqlUpdateX(record).
        WithOptions(&qb.UpdateOptions{
            IncludeFields: []string{"email", "updated_at"},
        }).
        Where(qb.Eq("id", id))
    return s.repo.Do(ub)
}

// Update with RETURNING clause
ub := s.repo.SqlUpdateX(record).
    WithOptions(&qb.UpdateOptions{
        ReturningFields: []string{"id", "name", "email", "updated_at"},
    }).
    Where(qb.Eq("id", id))

// Map-based partial update with raw expressions
ub := repo.SqlBuilder().Update("users", &entity.UserRecord{}).
    FieldsValues(map[string]any{
        "last_login":  time.Now(),
        "login_count": qb.Raw("login_count + 1"),
    }).
    Where(qb.Eq("id", userID))
repo.Do(ub)
```

**UpdateOptions** controls field selection:

```go
&qb.UpdateOptions{
    IncludeFields:    []string{"name", "email"},   // only these fields
    ExcludeFields:    []string{"created_at"},       // never these fields
    IncludeZeroValues: true,                        // include zero/empty values
    UpdateAutoFields:  true,                        // update `auto:"true"` tagged fields
    ReturningFields:  []string{"id", "updated_at"}, // RETURNING clause
}
```

**WHERE clause helpers** (`qb` package):

| Helper | SQL |
|---|---|
| `qb.Eq("col", val)` | `col = $1` |
| `qb.Gt` / `qb.Lt` / `qb.Gte` / `qb.Lte` | `>` / `<` / `>=` / `<=` |
| `qb.Like("col", "pat%")` | `col LIKE $1` |
| `qb.IsNull("col")` / `qb.IsNotNull` | `IS NULL` / `IS NOT NULL` |
| `qb.In("col", vals...)` | `col IN ($1, $2, ...)` |
| `qb.Between("col", lo, hi)` | `col BETWEEN $1 AND $2` |
| `qb.Compare("a", ">", "b")` | field-to-field comparison |
| `qb.Or(conds...)` | `(c1 OR c2)` |

Combine with `.Where()` (AND-chained) or `.WhereAnd()` for grouped conditions:

```go
ub.WhereAnd(
    qb.Eq("department", "eng"),
    qb.Or(qb.Eq("role", "senior"), qb.Gt("years", 5)),
)
```

**Batch operations:**

```go
sql, args, err := builder.BuildSQLBatchInsert("users", records)
stmts, argsList, err := builder.BuildSQLBatchUpdate("users", records, []string{"id"}, qb.DefaultUpdateOptions())
```

### 6.7 DB Grid (Dynamic Query Builder)

`db.Grid` builds dynamic SELECT queries with filtering, sorting, searching, and pagination from struct tags. Integrated with `db.Repository` via `GridOps`.

```go
type UserRecord struct {
    ID       int    `db:"id" json:"id" grid:"sort,filter"`
    Username string `db:"username" json:"username" grid:"sort,search,filter"`
    Email    string `db:"email" json:"email" grid:"sort,search,filter"`
    Active   bool   `db:"active" json:"active" grid:"filter"`
}
```

**Grid tags:** `sort` (orderable), `search` (text search), `filter` (equality filter). JSON field names are auto-mapped to db columns.

**Repository integration (preferred):**

```go
query, _ := db.NewGridQuery(db.SearchAny, 25, 0)
query.SearchText = "john"
query.FilterFields = map[string]any{"active": true}
query.SortFields = map[string]string{"username": db.SortAscending}
query.Page(2, 25) // page 2, 25 items/page — calculates offset

var users []*UserRecord
err := repo.QueryGrid(&UserRecord{}, query, &users)
```

**Search types:** `db.SearchNone` (0), `db.SearchStart` (1 — `%term`), `db.SearchEnd` (2 — `term%`), `db.SearchAny` (3 — `%term%`).

**Sort constants:** `db.SortAscending`, `db.SortDescending`.

**Custom filter functions:**

```go
grid, _ := db.NewGrid("users", &UserRecord{})
grid.AddFilterFunc("active", func(value any) (any, error) {
    switch v := value.(type) {
    case string:
        return v == "1" || v == "true", nil
    default:
        return value, nil
    }
})
```

**Standalone usage** (without repository):

```go
grid, _ := db.NewGrid("users", &UserRecord{})
if err := grid.ValidQuery(query); err != nil { ... }
stmt, _ := grid.Build(nil, query)
sql, args, _ := stmt.ToSQL()
```

---

## 7. HTTP API Layer

### 7.1 Server Setup

```go
type ApiConfig struct {
    ApiServer *httpserver.ServerConfig `json:"apiServer"`
    CORS      *security.SecurityConfig `json:"security"`
}

type ApiServer struct {
    *httpserver.Server
    svc    *core.ServiceContext
    appCtx *core.AppContext
}

func NewApiServer(cfg *ApiConfig, appCtx *core.AppContext, logger *log.Logger) (*ApiServer, error) {
    srv, err := httpserver.NewServer(cfg.ApiServer, logger)
    if err != nil {
        return nil, err
    }
    srv.UseDefaultSecurityHeaders()

    api := &ApiServer{Server: srv, svc: appCtx.Svc, appCtx: appCtx}

    v1 := srv.Group("/v1")
    v1.Use(gzip.Gzip(gzip.DefaultCompression))

    // Public endpoints (before auth middleware)
    v1.POST("/login", api.login)

    // Auth middleware
    v1.Use(httpauth.JWTMiddleware(appCtx.Auth.TokenService()))

    // Protected endpoints
    v1.GET("/product", api.listProducts)
    v1.POST("/product", api.createProduct)
    v1.GET("/product/:id", api.getProduct)

    return api, nil
}
```

### 7.2 Request Body Size Limit

Limit request body size to prevent large-payload DoS attacks. Apply early in the middleware chain:

```go
func maxBodyMiddleware(maxBytes int64) gin.HandlerFunc {
    return func(g *gin.Context) {
        if g.Request.Body != nil {
            g.Request.Body = http.MaxBytesReader(g.Writer, g.Request.Body, maxBytes)
        }
        g.Next()
    }
}

// Usage: 1 MiB limit
v1.Use(maxBodyMiddleware(1 << 20))
```

### 7.3 Handler Chaining

Concatenate optional middleware slices with a final handler for flexible per-route middleware composition:

```go
func chainHandlers(handler gin.HandlerFunc, middleware ...[]gin.HandlerFunc) []gin.HandlerFunc {
    var result []gin.HandlerFunc
    for _, mw := range middleware {
        result = append(result, mw...)
    }
    return append(result, handler)
}
```

Usage — apply different middleware combinations per route:

```go
authRL := []gin.HandlerFunc{authRateLimiter}
generalRL := []gin.HandlerFunc{generalRateLimiter}
jwtMW := []gin.HandlerFunc{jwtMiddleware}

v1.POST("/login", chainHandlers(h.Login, authRL)...)
v1.POST("/logout", chainHandlers(h.Logout, generalRL, jwtMW)...)
v1.GET("/me", chainHandlers(h.Me, generalRL, jwtMW)...)
```

### 7.4 Request Deserialization

**JSON body** — use `httpserver.ValidateJSON()`. It binds JSON, runs `binding:` tag validation, and sends a 400 with field-level errors automatically on failure. Returns `false` if validation failed (response already sent — just return):

```go
req := &entity.ProductParams{}
if !httpserver.ValidateJSON(g, req) {
    return  // 400 already sent with validation details
}
```

The request struct uses Gin's `binding` tags for validation:

```go
type ProductParams struct {
    Name        string `json:"name" binding:"required,min=3,max=128"`
    CategoryId  string `json:"categoryId" binding:"required,uuid4"`
    Description string `json:"description" binding:"max=255"`
    Price       int    `json:"price" binding:"required,gt=0"`
}
```

**Path parameters** — use `g.Param()`. Always validate/parse immediately and return 400 on failure:

```go
id, err := uuid.Parse(g.Param("id"))
if err != nil {
    response.Http400(g, "invalid id")
    return
}
```

**Query parameters** — use `g.Query()` for optional values:

```go
limitStr := g.Query("limit")      // returns "" if absent
tz := g.Query("tz")
```

### 7.5 Custom Validators

Register custom validation tags via Gin's binding engine in an `init()` function:

```go
func init() {
    if v, ok := binding.Validator.Engine().(*validator.Validate); ok {
        v.RegisterValidation("tz", func(fl validator.FieldLevel) bool {
            loc := fl.Field().String()
            if len(loc) > 0 {
                _, err := time.LoadLocation(loc)
                return err == nil
            }
            return true
        })
    }
}
```

Then use in struct tags: `binding:"required,tz"`

### 7.6 Response Helpers

Blueprint's `response` package (`github.com/oddbit-project/blueprint/provider/httpserver/response`):

```go
// Success — 200 with JSON body
response.Success(g, data)

// Error responses — set status code + JSON error body
response.Http400(g, "human-readable message")   // 400 Bad Request
response.Http401(g)                              // 401 Unauthorized
response.Http403(g)                              // 403 Forbidden
response.Http404(g)                              // 404 Not Found
response.Http429(g)                              // 429 Too Many Requests
response.Http500(g, err)                         // 500 Internal Server Error
```

**Field-level validation errors** — send structured per-field error details:

```go
response.ValidationError(g, []httpserver.ValidationError{
    {Field: "email", Message: "email already exists"},
    {Field: "name", Message: "name is required"},
})
```

**Raw JSON response structs** — for SSE, streaming, or custom response shapes:

```go
// Success envelope
msg := &response.JSONResponse{
    Success: true,
    Data:    payload,
}

// Error envelope
msg := &response.JSONResponseError{
    Success: false,
    Error: response.ErrorDetail{
        Message: "connection failed",
    },
}
```

### 7.7 ValidationError Builder

Wrap `httpserver.ValidationError` in a fluent builder for cleaner handler code:

```go
type ValidationError struct {
    fields []httpserver.ValidationError
}

func NewValidationError() *ValidationError {
    return &ValidationError{fields: make([]httpserver.ValidationError, 0)}
}

func (v *ValidationError) AddField(fieldName string, error string) *ValidationError {
    v.fields = append(v.fields, httpserver.ValidationError{Field: fieldName, Message: error})
    return v
}

func (v *ValidationError) Send(g *gin.Context) {
    response.ValidationError(g, v.fields)
}
```

Usage:

```go
NewValidationError().
    AddField("name", "name already exists").
    Send(g)
```

### 7.8 Domain Error Helpers

Map sentinel errors to HTTP responses. One helper per domain — keeps handlers clean:

```go
var internalError = fmt.Errorf("internal error")

func productError(g *gin.Context, err error, message ...string) {
    if errors.Is(err, entity.ErrNotFound)  { response.Http404(g); return }
    if errors.Is(err, entity.ErrDb)        { response.Http500(g, internalError); return }
    if errors.Is(err, entity.ErrNotUnique) {
        NewValidationError().AddField("name", "name already exists").Send(g)
        return
    }
    if len(message) > 0 {
        response.Http400(g, message[0])
        return
    }
    response.Http400(g, "operation failed")
}
```

### 7.9 Shared Param Helpers

For path parameters used across multiple handlers, extract into a helper that writes its own error response. Callers just check `err != nil` and return:

```go
func (s *ApiServer) _getRecord(g *gin.Context) (*entity.Record, error) {
    id, err := uuid.Parse(g.Param("id"))
    if err != nil {
        response.Http400(g, "invalid id")
        return nil, err
    }
    record, err := s.svc.Product.GetById(id.String())
    if err != nil {
        response.Http404(g)
    }
    return record, err
}

// In handler:
record, err := s._getRecord(g)
if err != nil {
    return  // response already sent
}
```

### 7.10 Handler Pattern Summary

Full handler template showing the canonical order of operations:

```go
func (s *ApiServer) updateProduct(g *gin.Context) {
    // 1. Auth guard (if admin-only)
    if !s.CurrentUser(g).IsAdmin() {
        response.Http401(g)
        return
    }

    // 2. Parse + validate path params
    id, err := uuid.Parse(g.Param("id"))
    if err != nil {
        response.Http400(g, "invalid id")
        return
    }

    // 3. Parse + validate JSON body
    req := &entity.ProductParams{}
    if !httpserver.ValidateJSON(g, req) {
        return
    }

    // 4. Call service
    record, err := s.svc.Product.Update(g.Request.Context(), id.String(), req)
    if err != nil {
        // 5. Map error to HTTP response
        log.FromContext(g).Error(err, "error updating product", log.KV{"id": id})
        productError(g, err)
        return
    }

    // 6. Success
    response.Success(g, record)
}
```

### 7.11 Pagination

For list endpoints, support optional pagination via query parameters. Place pagination utilities in `pkg/pagination/`:

```go
type PaginationParams struct {
    Page     int64
    PageSize int64
}

type PaginatedDetails struct {
    CurrentPage int64       `json:"currentPage"`
    TotalPages  int64       `json:"totalPages"`
    TotalCount  int64       `json:"totalCount"`
    Rows        interface{} `json:"rows"`
}

func (p *PaginationParams) IsPaginated() bool {
    return p.Page > 0
}
```

**Parsing from query params:**

```go
func ParsePagination(g *gin.Context) *pagination.PaginationParams {
    params := &pagination.PaginationParams{PageSize: 100}
    if v := g.Query("page"); v != "" {
        params.Page, _ = strconv.ParseInt(v, 10, 64)
    }
    if v := g.Query("pageSize"); v != "" {
        params.PageSize, _ = strconv.ParseInt(v, 10, 64)
    }
    return params
}
```

**Applying to queries:**

```go
func (r *EntityRepository) GetAll(params *pagination.PaginationParams) (*pagination.PaginatedDetails, error) {
    totalCount, err := r.repo.Count(r.repo.SqlSelect())
    if err != nil {
        return nil, err
    }

    qry := r.repo.SqlSelect().Order(goqu.C("name").Asc())
    if params.IsPaginated() {
        qry = qry.
            Limit(uint(params.PageSize)).
            Offset(uint((params.Page - 1) * params.PageSize))
    }

    result := make([]*entity.Entity, 0)
    if err := r.repo.Fetch(qry, &result); err != nil {
        return nil, err
    }

    totalPages := int64(0)
    if params.IsPaginated() {
        totalPages = (totalCount + params.PageSize - 1) / params.PageSize
    }

    return &pagination.PaginatedDetails{
        CurrentPage: params.Page,
        TotalPages:  totalPages,
        TotalCount:  totalCount,
        Rows:        result,
    }, nil
}
```

When `page=0` or absent, return all rows with no pagination wrapper.

### 7.12 CSV Export

Stream CSV directly to the HTTP response for data export endpoints:

```go
func exportEntitiesCSV(g *gin.Context, svc *service.Container) {
    entities, err := svc.Entity.List(nil)
    if err != nil {
        log.FromContext(g).Errorf(err, "failed to list entities for CSV export")
        response.Http500(g, errInternalError)
        return
    }

    fname := fmt.Sprintf("entities-%s.csv", time.Now().Format("20060102"))
    g.Header("Content-Disposition", fmt.Sprintf("attachment; filename=%q", fname))
    g.Header("Content-Type", "text/csv; charset=utf-8")
    g.Status(200)

    w := csv.NewWriter(g.Writer)
    w.Write([]string{"ID", "Name", "Email", "Status"})
    for _, e := range entities {
        w.Write([]string{
            e.ID.String(),
            e.Name,
            e.Email,
            strconv.Itoa(e.Status),
        })
    }
    w.Flush()

    if err := w.Error(); err != nil {
        log.FromContext(g).Errorf(err, "failed to write CSV")
    }
}
```

Set `Content-Disposition` with `attachment` to trigger browser download. Write headers first, then stream rows. Check `w.Error()` after flush — errors at this point are network issues (client disconnected), not data issues.

### 7.13 Session Management

Blueprint provides cookie-based session management with encryption, multiple storage backends, and security features.

```go
import (
    "github.com/oddbit-project/blueprint/provider/httpserver/session"
    "github.com/oddbit-project/blueprint/provider/kv"
    "github.com/oddbit-project/blueprint/crypt/secure"
)

// Setup: memory backend (dev) or Redis (prod)
sessionConfig := session.NewConfig()
sessionConfig.CookieName = "app_session"
sessionConfig.ExpirationSeconds = 3600
sessionConfig.IdleTimeoutSeconds = 1800
sessionConfig.EncryptionKey = secure.DefaultCredentialConfig{
    PasswordEnvVar: "SESSION_ENCRYPTION_KEY",
}

backend := kv.NewMemoryKV() // or redis.NewClient(redisConfig) for prod
sessionManager, err := srv.UseSession(sessionConfig, backend, logger)
```

**Using sessions in handlers:**

```go
func handler(g *gin.Context) {
    sess := session.Get(g)

    sess.Set("key", "value")
    val, ok := sess.GetString("key")
    sess.Has("key")
    sess.Delete("key")

    // Typed getters
    intVal, ok := sess.GetInt("count")
}
```

**Identity management** — register custom types with `gob.Register()` in `init()`:

```go
func init() { gob.Register(&UserIdentity{}) }

// Login: set identity + regenerate session ID
sess := session.Get(g)
sess.SetIdentity(&UserIdentity{ID: user.ID, Username: user.Username})
sessionManager.Regenerate(g) // prevents session fixation

// Read identity
identity, ok := sess.GetIdentity()
user := identity.(*UserIdentity)

// Logout
sessionManager.Clear(g)
```

**Flash messages** — one-time values that persist across a single redirect:

```go
sess.FlashString("Operation successful!")
// After redirect:
msg, ok := sess.GetFlashString()
```

**Backends:** `kv.NewMemoryKV()` (dev/single-instance), `redis.NewClient()` (production/distributed). Custom backends implement `SetTTL`/`Get`/`Delete`/`Prune`.

**Marshallers:** GOB (default, binary, fast), JSON (`sessionStore.WithMarshaller(session.NewJSONMarshaller())`), or custom implementing `Marshaller` interface.

### 7.14 Request Helpers

```go
import "github.com/oddbit-project/blueprint/provider/httpserver/request"

// Content type detection
if request.IsJSONRequest(g) {
    g.JSON(200, data)
} else {
    g.HTML(200, "template.html", data)
}

// Content type constants
request.ContentTypeJson   // "application/json"
request.ContentTypeHtml   // "text/html"
request.ContentTypeBinary // "application/octet-stream"

// CSRF protection
router.Use(request.CSRFMiddleware())
csrfToken := request.GenerateCSRFToken(g) // pass to templates
```

The `response` package (see 7.6) auto-detects JSON vs HTML requests via `request.IsJSONRequest()` internally.

---

## 8. Authentication & Credentials

### 8.1 GoAuth Integration

Initialize GoAuth with an ECDSA key pair for JWT signing. If no keys are provided, generate ephemeral ES256 keys at startup:

```go
func NewGoAuthService(ctx context.Context, pgClient *db.SqlClient, privPEM, pubPEM string) (*goauth.AuthService, *pgstore.Store, error) {
    store := pgstore.New(ctx, pgClient)

    if privPEM == "" || pubPEM == "" {
        var err error
        privPEM, pubPEM, err = generateES256Keys()
        if err != nil {
            return nil, nil, fmt.Errorf("generating JWT keys: %w", err)
        }
    }

    cfg := goauth.NewConfig()
    cfg.Local.Enabled = true
    cfg.JWT.SigningMethod = "ES256"
    cfg.JWT.PrivateKeyPEM = privPEM
    cfg.JWT.PublicKeyPEM = pubPEM

    svc, err := goauth.NewAuthService(ctx, cfg, store)
    if err != nil {
        return nil, nil, err
    }
    return svc, store, nil
}
```

Wire into `AppContext` during `Build()`:

```go
authSvc, authStore, err := auth.NewGoAuthService(m.Context, ctx.PgDb, "", "")
m.AbortFatal(err)
ctx.Auth = authSvc
ctx.AuthStore = authStore
```

### 8.2 JWT Middleware

Apply GoAuth's JWT middleware to protected route groups. Public endpoints must be registered **before** the middleware:

```go
v1 := srv.Group("/v1")

// Public (no auth)
v1.POST("/login", api.login)
v1.POST("/refresh", api.refreshToken)

// Auth barrier — all routes below require a valid JWT
v1.Use(httpauth.JWTMiddleware(appCtx.Auth.TokenService()))

// Protected
v1.GET("/product", api.listProducts)
```

### 8.3 API Key Authentication

For internal or machine-to-machine services that don't need user sessions, use Blueprint's token list auth instead of GoAuth. Authenticates via a static list of API keys in the `X-API-Key` header:

```go
type ApiConfig struct {
    Server   *httpserver.ServerConfig `json:"server"`
    AuthKeys []string                 `json:"authKeys"`
}
```

```json
{
  "api": {
    "server": { "host": "0.0.0.0", "port": 8080 },
    "authKeys": ["key1", "key2"]
  }
}
```

Wire into the server with `srv.UseAuth()`:

```go
srv, err := httpserver.NewServer(cfg.Server, logger)
if err != nil {
    return nil, err
}
srv.UseAuth(auth.NewAuthTokenList("X-API-Key", cfg.AuthKeys))
```

All routes registered after `UseAuth()` require a valid key. No login/refresh flow needed.

### 8.4 Login / Token Flow

```go
// Login handler
func (s *ApiServer) login(g *gin.Context) {
    var req LoginRequest
    if !httpserver.ValidateJSON(g, &req) {
        return
    }

    tokens, user, err := s.svc.User.Authenticate(req.Email, req.Password)
    if err != nil {
        log.FromContext(g).Error(err, "login failed")
        response.Http401(g)
        return
    }

    response.Success(g, &LoginResponse{
        AccessToken:  tokens.AccessToken,
        RefreshToken: tokens.RefreshToken,
        Name:         user.Name,
    })
}

// Refresh handler
func (s *ApiServer) refreshToken(g *gin.Context) {
    var req RefreshTokenRequest
    if !httpserver.ValidateJSON(g, &req) {
        return
    }
    tokens, err := s.svc.User.RefreshToken(req.RefreshToken, nil)
    if err != nil {
        response.Http401(g)
        return
    }
    response.Success(g, tokens)
}
```

### 8.5 Extracting Current User

Retrieve the authenticated user from the JWT context. Cache in `gin.Context` to avoid repeated DB lookups per request:

```go
func (s *ApiServer) CurrentUser(g *gin.Context) *entity.UserInfo {
    if v, exists := g.Get("currentUser"); exists {
        return v.(*entity.UserInfo)
    }
    userID, err := httpauth.GetUserIDFromContext(g)
    if err != nil {
        return nil
    }
    info, err := s.svc.User.GetUserInfo(userID)
    if err != nil {
        return nil
    }
    g.Set("currentUser", info)
    return info
}
```

### 8.6 Password Hashing (Argon2id)

Use `Argon2Hasher` for storing passwords. The hasher interface supports transparent rehashing when parameters change:

```go
type PasswordHasher interface {
    Generate(password string) (string, error)
    Verify(hashedPassword, password string) (bool, func() (string, error), error)
}
```

- `Generate()` returns `$argon2id$v=19$m=65536,t=4,p=12$salt$hash`
- `Verify()` returns a match bool + an optional rehash function (non-nil when stored hash uses outdated parameters)
- Uses constant-time comparison (`crypto/subtle.ConstantTimeCompare`)
- Defaults: 64 MB memory, 4 iterations, 12 parallelism, 16-byte salt, 32-byte key

```go
hasher := auth.NewArgon2Hasher()
hash, err := hasher.Generate("password")
match, rehashFn, err := hasher.Verify(hash, "password")
if match && rehashFn != nil {
    newHash, _ := rehashFn()  // re-hash with current parameters
}
```

### 8.7 Blueprint Crypt Utilities

Blueprint provides `crypt/` subpackages for secure token and PIN generation.

**Secure tokens** (`crypt/token`): URL-safe base64 tokens for invite codes, password reset links, API keys:

```go
import bptoken "github.com/oddbit-project/blueprint/crypt/token"

code, err := bptoken.GenerateSecureBase64Token(32) // 32-char secure token
```

**PINs** (`crypt/pin`): short alphanumeric codes for user verification (invite PINs, 2FA):

```go
import "github.com/oddbit-project/blueprint/crypt/pin"

pinCode, err := pin.GenerateAlphanumeric(6)              // generate 6-char PIN
match := pin.CompareAlphanumeric(userInput, storedPin)    // constant-time compare
```

**Random alphanumeric IDs** (via `crypto/rand`): for source tokens and identifiers that must be unpredictable:

```go
func GenerateRandomAlphanumericID() string {
    bytes := make([]byte, 48)
    _, err := rand.Read(bytes)
    // ... base64 encode, filter to alphanumeric, return 32+ chars
}
```

### 8.8 Constant-Time Comparison

Always use `crypto/subtle.ConstantTimeCompare` when validating tokens or secrets to prevent timing attacks:

```go
import "crypto/subtle"

if subtle.ConstantTimeCompare([]byte(stored), []byte(provided)) == 1 {
    // valid
}
```

This applies to: source token validation, PIN comparison, API key checks, any secret comparison at a system boundary.

### 8.9 Secrets in Configuration

Blueprint provides `crypt/secure` config structs for declaring secrets. They support three sources (in priority order): plaintext value, environment variable, or file. Use `Fetch()` to resolve the secret — it reads from whichever source is set and clears the env var after reading.

**`secure.DefaultCredentialConfig`** — for passwords:

```go
import "github.com/oddbit-project/blueprint/crypt/secure"

type SmtpConfig struct {
    Host     string                       `json:"host"`
    Port     int                          `json:"port"`
    Username string                       `json:"username"`
    secure.DefaultCredentialConfig                          // embeds Password, PasswordEnvVar, PasswordFile
}
```

```json
{
  "smtp": {
    "host": "smtp.example.com",
    "port": 587,
    "username": "user@example.com",
    "passwordEnvVar": "SMTP_PASSWORD"
  }
}
```

```go
password, err := cfg.Smtp.Fetch()  // reads env var, clears it, returns value
```

**`secure.KeyConfig`** — for encryption keys, API keys, and similar non-password secrets:

```go
type EncryptionConfig struct {
    Algorithm string           `json:"algorithm"`
    secure.KeyConfig                                // embeds Key, KeyEnvVar, KeyFile
}
```

```json
{
  "encryption": {
    "algorithm": "aes256",
    "keyEnvVar": "ENCRYPTION_KEY"
  }
}
```

```go
key, err := cfg.Encryption.Fetch()  // same resolution logic as DefaultCredentialConfig
if cfg.Encryption.IsEmpty() {
    // no key source configured
}
```

Both structs implement the `secure.CredentialConfig` interface (`Fetch() (string, error)` + `IsEmpty() bool`). Never inline secrets in config JSON — always use `*EnvVar` or `*File` fields for production.

### 8.10 Audit Trail

For security-sensitive operations, emit structured audit events via an `Auditor` interface. Audit failures must never break application flows.

```go
type AuditEvent struct {
    Time      time.Time
    Event     string            // e.g. "login.local", "user.create", "entity.export"
    ActorID   uuid.UUID         // who performed the action (Nil if unknown)
    UserID    uuid.UUID         // user affected (Nil for pre-auth failures)
    TenantID  uuid.UUID
    IP        string
    UserAgent string
    Success   bool
    Error     string            // failure reason (empty on success)
    Metadata  map[string]string // extra context (provider, target ID, etc.)
}

type Auditor interface {
    Log(ctx context.Context, event AuditEvent)
}
```

**PostgreSQL-backed auditor** — writes asynchronously via a buffered channel to avoid blocking callers:

```go
type PgAuditor struct {
    repo   db.Repository
    logger *log.Logger
    ch     chan AuditEvent
    done   chan struct{}
}

func NewPgAuditor(ctx context.Context, client *db.SqlClient, logger *log.Logger) *PgAuditor {
    a := &PgAuditor{
        repo:   db.NewRepository(ctx, client, "audit_events"),
        logger: logger,
        ch:     make(chan AuditEvent, 1024),
        done:   make(chan struct{}),
    }
    go a.loop()
    return a
}

func (a *PgAuditor) Log(_ context.Context, event AuditEvent) {
    select {
    case a.ch <- event:
    default:
        a.logger.Warn("audit buffer full, dropping event: " + event.Event)
    }
}

func (a *PgAuditor) Close() {
    close(a.ch)
    <-a.done
}

func (a *PgAuditor) loop() {
    defer close(a.done)
    for evt := range a.ch {
        a.persist(evt)
    }
}
```

**Gin handler helper** — extracts actor from JWT claims and emits:

```go
func emitAudit(g *gin.Context, auditor Auditor, event string, success bool, errMsg string, meta map[string]string) {
    var actorID, tenantID uuid.UUID
    if claims, err := httpauth.GetClaimsFromContext(g); err == nil {
        actorID = claims.UserID
        tenantID = claims.TenantID
    }

    auditor.Log(g.Request.Context(), AuditEvent{
        Time:      time.Now().UTC(),
        Event:     event,
        ActorID:   actorID,
        UserID:    actorID,
        TenantID:  tenantID,
        IP:        g.ClientIP(),
        UserAgent: g.Request.UserAgent(),
        Success:   success,
        Error:     errMsg,
        Metadata:  meta,
    })
}
```

Define event type constants per domain:

```go
const (
    AuditLoginLocal  = "login.local"
    AuditLoginOIDC   = "login.oidc"
    AuditUserCreate  = "user.create"
    AuditUserUpdate  = "user.update"
    AuditEntityExport = "entity.export"
)
```

### 8.11 HMAC Authentication

Blueprint's HMAC provider implements HMAC-SHA256 signatures with optional replay protection (nonces + timestamps). Two modes: **simple** (`SHA256Sign`/`SHA256Verify`) and **secure** (`Sign256`/`Verify256` with nonce-based replay prevention).

```go
import (
    "github.com/oddbit-project/blueprint/crypt/secure"
    "github.com/oddbit-project/blueprint/provider/hmacprovider"
    "github.com/oddbit-project/blueprint/provider/hmacprovider/store"
    "github.com/oddbit-project/blueprint/provider/httpserver/auth"
)

// Setup
key, _ := secure.GenerateKey()
secret, _ := secure.NewCredential([]byte("my-secret"), key, false)
keyProvider := hmacprovider.NewSingleKeyProvider("client-key", secret)
memoryStore := store.NewMemoryNonceStore(
    store.WithTTL(1*time.Hour),
    store.WithCleanupInterval(15*time.Minute),
)
provider := hmacprovider.NewHmacProvider(keyProvider,
    hmacprovider.WithNonceStore(memoryStore),    // replay protection backend
    hmacprovider.WithKeyInterval(5*time.Minute), // timestamp tolerance
    hmacprovider.WithMaxInputSize(1024*1024),    // DoS protection
)

// Sign and verify (secure mode with replay protection)
hash, timestamp, nonce, _ := provider.Sign256("client-key", strings.NewReader(data))
keyId, valid, _ := provider.Verify256(strings.NewReader(data), hash, timestamp, nonce)

// Simple mode (no replay protection)
hash, _ := provider.SHA256Sign("client-key", strings.NewReader(data))
keyId, valid, _ := provider.SHA256Verify(strings.NewReader(data), hash)
```

**HTTP middleware integration:**

```go
hmacAuth := auth.NewHMACAuthProvider(provider)
router.Use(auth.AuthMiddleware(hmacAuth))

// In handler: extract identity
keyId, ok := auth.GetHMACIdentity(g)
keyId, timestamp, nonce, ok := auth.GetHMACDetails(g)
```

HTTP headers: `X-HMAC-Hash`, `X-HMAC-Timestamp`, `X-HMAC-Nonce`.

**Key providers:** `NewSingleKeyProvider` (single key), or implement `HMACKeyProvider` interface for multi-tenant (`GetKey(keyId) (*secure.Credential, error)`, `ListKeyIds() []string`).

**Nonce stores:** Memory (`store.NewMemoryNonceStore` with TTL/eviction), Redis (`store.NewRedisStore`), KV (`store.NewKvStore`). Eviction policies: `EvictNone()`, `EvictAll()`, `EvictHalfLife()`.

**Note:** keyId cannot contain dots (`.`) — used internally for concatenation.

### 8.12 HTPasswd

Apache-style password file management with multiple hash algorithms. Thread-safe, supports file I/O.

```go
import "github.com/oddbit-project/blueprint/provider/htpasswd"

container := htpasswd.NewContainer()

// Add user (bcrypt by default)
container.AddUserPassword("alice", "secret123")

// Add with specific algorithm
container.AddUserWithHash("bob", "pass", htpasswd.HashTypeBcrypt)

// Verify
valid, err := container.VerifyUser("alice", "secret123")

// User management
container.UserExists("alice")
container.ListUsers()
container.Count()
container.DeleteUser("alice")

// File I/O
container, err := htpasswd.NewFromFile("/path/to/.htpasswd")
container.Write(file) // io.Writer
```

**Hash algorithms:** bcrypt (default, recommended), Apache MD5 (`$apr1$`), SHA256 (`{SHA256}`), SHA512 (`{SHA512}`), SHA1 (`{SHA}`, deprecated), crypt (deprecated), plain (dev only).

---

## 9. Error Handling

### 9.1 Sentinel Errors

Define sentinel errors in `pkg/entity/` using Blueprint's `utils.Error()`:

```go
const (
    ErrNotFound    = utils.Error("record not found")
    ErrDb          = utils.Error("database operation failed")
    ErrNotAllowed  = utils.Error("operation not allowed")
    ErrInUse       = utils.Error("record is in use")
    ErrNotUnique   = utils.Error("record is not unique")
    ErrIncomplete  = utils.Error("record is incomplete, operation not allowed")
    ErrInvalidArgs = utils.Error("invalid arguments")
)
```

### 9.2 Nil-for-Not-Found Convention

When using a separate repository layer, repositories return `(*T, nil)` for missing records instead of a sentinel error. Convert `sql.ErrNoRows` at the repository boundary:

```go
// Repository
func (r *EntityRepository) GetByID(id uuid.UUID) (*entity.Entity, error) {
    record := &entity.Entity{}
    err := r.repo.FetchOne(r.repo.SqlSelect().Where(goqu.C("id_entity").Eq(id)), record)
    if err != nil {
        if errors.Is(err, sql.ErrNoRows) {
            return nil, nil
        }
        return nil, err
    }
    return record, nil
}

// Handler
entity, err := svc.Entity.GetByID(id)
if err != nil {
    response.Http500(g, errInternalError)
    return
}
if entity == nil {
    response.Http404(g)
    return
}
```

When services hold `db.Repository` directly (no separate repository layer), use sentinel errors instead (see 9.1).

### 9.3 Error Flow

- **Service layer**: returns sentinel errors or nil-for-not-found depending on architecture. Use `errors.Is()` for sentinel errors; nil-check for not-found when using separate repositories.
- **API layer**: maps errors to HTTP status codes via domain error helpers.
- **Build phase**: `m.AbortFatal(err)` for fatal init errors (prevents startup).

---

## 10. Graceful Shutdown

### 10.1 Registering Destructors

Register with `blueprint.RegisterDestructor(fn)`. Destructors execute in **LIFO order** (last registered runs first).

Register in **reverse order** of desired shutdown sequence:

```go
// Pool shutdown (registered FIRST, runs LAST)
blueprint.RegisterDestructor(func() error {
    return pool.Stop(app.Context)
})

// Consumer shutdown (registered SECOND, runs SECOND)
blueprint.RegisterDestructor(func() error {
    consumer.Close()
    return nil
})

// API shutdown (registered LAST, runs FIRST)
blueprint.RegisterDestructor(func() error {
    ctx, cancel := context.WithTimeout(app.Context, 1*time.Second)
    defer cancel()
    return server.Shutdown(ctx)
})
```

### 10.2 Goroutine Launchers

Use `m.Run(...)` with function slices. Each function launches a goroutine or blocking call. If any fails, call `m.Terminate(err)`:

```go
m.Run(
    func(app interface{}) error {
        go func() {
            if err := server.Start(); err != nil {
                m.Terminate(err)
            }
        }()
        return nil
    },
)
```

---

## 11. Component Interfaces

```go
// pkg/interfaces/interface.go

type Runnable interface {
    Start(ctx context.Context) error
    Stop(ctx context.Context) error
}

type Writer interface {
    Write(context.Context, []byte) error
}

type RunnableWriter interface {
    Runnable
    Writer
}
```

- **Runnable**: any long-lived background component (consumer, worker pool, status updater).
- **Writer**: any data sink (Kafka producer, WAL, batch writer).
- **RunnableWriter**: a data sink that also has lifecycle (start/stop).

### 11.1 Thread Pool

Blueprint provides `threadpool.ThreadPool` — a channel-based worker pool with panic recovery per worker and multiple dispatch strategies.

**Core types:**

```go
import "github.com/oddbit-project/blueprint/threadpool"

// Job interface — implement for custom job types
type Job interface {
    Run(ctx context.Context)
}

// FuncRunner — adapter for plain functions
job := threadpool.FuncRunner(func(ctx context.Context) {
    // work here
})
```

**Direct usage:**

```go
pool, err := threadpool.NewThreadPool(8, 1000, threadpool.WithLogger(logger))
pool.Start(ctx)

// Dispatch variants
pool.Dispatch(job)                                      // blocking if queue full
pool.TryDispatch(job)                                   // non-blocking, returns false if full
pool.DispatchWithTimeout(job, 100*time.Millisecond)     // returns false on timeout
pool.DispatchWithContext(ctx, job)                       // returns error on context cancellation

// Introspection
pool.GetQueueLen()       // pending jobs
pool.GetQueueCapacity()  // max queue size
pool.GetWorkerCount()    // active workers
pool.GetRequestCount()   // total jobs processed

pool.Stop()  // blocking, waits for all workers to finish
```

**Application wrapper** — wrap `ThreadPool` with a config struct and a `WorkerFunc` type for cleaner service integration:

```go
type Config struct {
    Workers   int `json:"workers"`
    QueueSize int `json:"queueSize"`
}

type WorkerPool struct {
    pool   *threadpool.ThreadPool
    logger *log.Logger
}

type WorkerFunc func(ctx context.Context)

func NewConfig() *Config {
    return &Config{
        Workers:   runtime.NumCPU(),
        QueueSize: 1000,
    }
}

func NewWorkerPool(cfg *Config, logger *log.Logger) (*WorkerPool, error) {
    pool, err := threadpool.NewThreadPool(cfg.Workers, cfg.QueueSize, threadpool.WithLogger(logger))
    if err != nil {
        return nil, err
    }
    return &WorkerPool{pool: pool, logger: logger}, nil
}

func (p *WorkerPool) Start(ctx context.Context) error {
    return p.pool.Start(ctx)
}

func (p *WorkerPool) Stop(ctx context.Context) error {
    return p.pool.Stop()
}

func (p *WorkerPool) Queue(ctx context.Context, fn WorkerFunc) error {
    return p.pool.DispatchWithContext(ctx, threadpool.FuncRunner(fn))
}
```

**Factory for Blueprint lifecycle** — validate config, start pool, register destructor:

```go
func WorkerFactory(a *blueprint.Container, cfg *worker.Config, logger *log.Logger) *worker.WorkerPool {
    a.AbortFatal(cfg.Validate())
    proc, err := worker.NewWorkerPool(cfg, logger)
    a.AbortFatal(err)
    a.AbortFatal(proc.Start(a.Context))
    blueprint.RegisterDestructor(func() error {
        proc.Stop(a.Context)
        return nil
    })
    return proc
}
```

**Config example:**

```json
{
  "workerPool": {
    "workers": 12,
    "queueSize": 1000
  }
}
```

### 11.2 Atomic Status Tracking

Use `atomic.Int32` with `CompareAndSwap()` for safe state transitions in background components. Define status constants and guard Start/Stop against concurrent or duplicate calls:

```go
const (
    statusStopped  int32 = 0
    statusRunning  int32 = 1
    statusStopping int32 = 2
)

type Worker struct {
    status atomic.Int32
    wg     sync.WaitGroup
    ctx    context.Context
    cancel context.CancelFunc
}

func (w *Worker) Start(ctx context.Context) error {
    if !w.status.CompareAndSwap(statusStopped, statusRunning) {
        return ErrAlreadyStarted
    }
    w.ctx, w.cancel = context.WithCancel(ctx)
    w.wg.Add(1)
    go w.run()
    return nil
}

func (w *Worker) Stop(ctx context.Context) error {
    if !w.status.CompareAndSwap(statusRunning, statusStopping) {
        return ErrNotRunning
    }
    w.cancel()
    w.wg.Wait()
    w.status.Store(statusStopped)
    return nil
}

func (w *Worker) IsRunning() bool {
    return w.status.Load() == statusRunning
}
```

Use `atomic.Bool` to prevent concurrent execution of the same task:

```go
type Task struct {
    fn      TaskFn
    running atomic.Bool
}

if task.running.CompareAndSwap(false, true) {
    go func() {
        defer task.running.Store(false)
        task.fn(ctx)
    }()
}
```

Use `atomic.Int32` to track in-flight work (e.g. queued tasks):

```go
type TaskRunner struct {
    runningTasks atomic.Int32
}

func (t *TaskRunner) Queue(ctx context.Context, fn WorkerFunc) error {
    t.runningTasks.Add(1)
    wrapFn := func(ctx context.Context) {
        defer t.runningTasks.Add(-1)
        fn(ctx)
    }
    return t.workerPool.Queue(ctx, wrapFn)
}

func (t *TaskRunner) RunningTasks() int {
    return int(t.runningTasks.Load())
}
```

### 11.3 Atomic Counter Stats

Use `atomic.Uint64` structs for lock-free operational counters updated from multiple goroutines. Group related counters into a stats struct:

```go
type Stats struct {
    NewBatchCount    atomic.Uint64
    SPFOperations    atomic.Uint64
    SSLOperations    atomic.Uint64
    FlushCount       atomic.Uint64
    SuccessCount     atomic.Uint64
    FailedCount      atomic.Uint64
}
```

Update from any goroutine without locks:

```go
stats.NewBatchCount.Add(1)
stats.SSLOperations.Add(uint64(domainCount))
```

Read with `.Load()` for Prometheus collection or health endpoints.

### 11.4 Custom Prometheus Collector

For metrics derived from application stats objects, implement the `prometheus.Collector` interface instead of using `promauto` globals. This decouples metric definitions from the code that updates them:

```go
type Collector struct {
    batchCount   *prometheus.Desc
    flushCount   *prometheus.Desc
    successCount *prometheus.Desc
    failedCount  *prometheus.Desc

    taskStats   *tasks.Stats
    writerStats *writer.Stats
}

func NewCollector(taskStats *tasks.Stats, writerStats *writer.Stats) *Collector {
    return &Collector{
        batchCount: prometheus.NewDesc(
            "app_batch_total",
            "Total batches created",
            nil, nil,
        ),
        flushCount: prometheus.NewDesc(
            "app_flush_total",
            "Total flush operations",
            nil, nil,
        ),
        successCount: prometheus.NewDesc(
            "app_success_total",
            "Total successful results",
            nil, nil,
        ),
        failedCount: prometheus.NewDesc(
            "app_failed_total",
            "Total failed results",
            nil, nil,
        ),
        taskStats:   taskStats,
        writerStats: writerStats,
    }
}

func (c *Collector) Describe(ch chan<- *prometheus.Desc) {
    ch <- c.batchCount
    ch <- c.flushCount
    ch <- c.successCount
    ch <- c.failedCount
}

func (c *Collector) Collect(ch chan<- prometheus.Metric) {
    if c.taskStats != nil {
        ch <- prometheus.MustNewConstMetric(c.batchCount, prometheus.CounterValue,
            float64(c.taskStats.NewBatchCount.Load()))
    }
    if c.writerStats != nil {
        ch <- prometheus.MustNewConstMetric(c.flushCount, prometheus.CounterValue,
            float64(c.writerStats.FlushCount.Load()))
        ch <- prometheus.MustNewConstMetric(c.successCount, prometheus.CounterValue,
            float64(c.writerStats.SuccessCount.Load()))
        ch <- prometheus.MustNewConstMetric(c.failedCount, prometheus.CounterValue,
            float64(c.writerStats.FailedCount.Load()))
    }
}
```

Register during Build:

```go
collector := stats.NewCollector(taskStats, writerStats)
prometheus.MustRegister(collector)
```

Use `promauto` (see 6.5) when metrics are updated directly at the call site. Use a custom collector when metrics are derived from shared stats objects or external state.

### 11.5 Batch Writer

Thread-safe buffered batch processor. Flushes when capacity is reached or flush interval expires.

```go
import "github.com/oddbit-project/blueprint/batchwriter"

writer, err := batchwriter.NewBatchWriter(
    ctx,
    100,              // capacity (batch size)
    5*time.Second,    // flush interval
    func(records ...any) {
        // process batch — e.g. bulk insert
        for _, r := range records {
            row := r.(*MyRecord)
            // ...
        }
    },
    batchwriter.WithLogger(logger),
    batchwriter.WithClearBuffers(true),   // help GC on long-running services
    batchwriter.WithQueueCapacity(50),    // input channel capacity (default: 10)
)

// Add records
writer.Add(record)                        // blocks if input channel full
ok := writer.TryAdd(record)              // non-blocking, returns false if full
err := writer.AddWithContext(ctx, record) // context-aware

// Control
writer.FlushNow() // force immediate flush
writer.Stop()     // graceful shutdown, flushes remaining

// Metrics
m := writer.GetMetrics()
// m.RecordsAdded, m.RecordsProcessed, m.RecordsDropped, m.FlushCount,
// m.LastFlushDuration, m.AvgFlushDuration
```

**Performance:** `TryAdd` is ~6x faster than `Add` (non-blocking). Use `TryAdd` for high-throughput where occasional drops are acceptable.

**Error handling:** Panics in the flush function are recovered and logged. The writer continues operating after a panic.

---

## 12. Database Patterns

### 12.1 PostgreSQL Migrations

Embed SQL files and run via Blueprint's migration manager:

```go
//go:embed pgsql/*.sql
var pgFs embed.FS

func PgMigrate(ctx context.Context, client *db.SqlClient) error {
    source, err := migrations.NewEmbedSource(pgFs, "pgsql")
    if err != nil {
        return err
    }
    manager, err := pgsql.NewMigrationManager(ctx, client)
    if err != nil {
        return err
    }
    return manager.Run(ctx, source, migrations.DefaultProgressFn)
}
```

### 12.2 Schema Conventions

- Primary keys: `id_{table_name}` (e.g., `id_product`)
- Foreign keys: `fk_{referenced_table}` (e.g., `fk_category`)
- Timestamps: `created_at`, `updated_at`
- Soft status: integer column (`1`=active, `2`=inactive, `3`=trash, `4`=deleted)
- Table name constants: `Tbl*` prefix (e.g., `TblProduct = "product"`)

### 12.3 Database Initialization

```go
func (m *App) initDb(cfg *pgsql.ClientConfig) *db.SqlClient {
    m.AbortFatal(cfg.Validate())
    client, err := pgsql.NewClient(cfg)
    m.AbortFatal(err)
    m.AbortFatal(client.Connect())
    blueprint.RegisterDestructor(func() error {
        client.Disconnect()
        return nil
    })
    return client
}
```

### 12.4 Job Queue with FOR UPDATE SKIP LOCKED

For concurrent job processing, use PostgreSQL's `FOR UPDATE SKIP LOCKED` to atomically claim the next pending job without blocking other workers:

```go
func (s *JobQueueService) Dequeue(ctx context.Context) (*types.Job, error) {
    query := `UPDATE job_queue
        SET status = $1, started_at = NOW()
        WHERE id_job_queue = (
            SELECT id_job_queue FROM job_queue
            WHERE status = $2
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id_job_queue, job_type, status, priority, payload, result,
                  max_retries, retry_count, created_at, started_at, completed_at`

    job := &types.Job{}
    err := s.repo.Db().QueryRowxContext(ctx, query, types.JobStatusRunning, types.JobStatusPending).StructScan(job)
    if err != nil {
        if err == sql.ErrNoRows {
            return nil, nil
        }
        return nil, err
    }
    return job, nil
}
```

The subquery selects and locks a single row; `SKIP LOCKED` skips rows already locked by other workers. The outer `UPDATE` atomically claims it. Multiple workers can call `Dequeue()` concurrently without duplicate processing.

---

## 13. Inter-Service RPC

### 13.1 Message Envelope

MessagePack-based envelope for Kafka / WAL messages:

```go
type Message struct {
    Type      int                `json:"messageType"`
    Version   int                `json:"version"`
    CreatedAt time.Time          `json:"createdAt"`
    Data      msgpack.RawMessage `json:"data"`
}
```

### 13.2 Marshal / Unmarshal

```go
// Producer side
bytes, err := rpc.MarshalMsgPack(TypeEvent, 1, &eventPayload)

// Consumer side
msg, err := rpc.UnmarshalMessage(record.Value)
if msg.Type == TypeEvent {
    payload := &EventPayload{}
    err = msg.UnmarshalData(payload)
}
```

### 13.3 Domain Record Serialization

Records that travel over RPC implement Serialize/Deserialize:

```go
func (r *MyRecord) Serialize() ([]byte, error) {
    return msgpack.Marshal(r)
}

func DeserializeMyRecord(data []byte) (*MyRecord, error) {
    result := &MyRecord{}
    return result, msgpack.Unmarshal(data, result)
}
```

---

## 14. Testing

### 14.1 Test Infrastructure

Use testcontainers for real database instances:

```go
// internal/testutil/database.go
type TestContext struct {
    Ctx       context.Context
    Databases *TestDatabases
}

func NewPgTestContext(t *testing.T) *TestContext { ... }
func (tc *TestContext) Cleanup() { ... }
```

### 14.2 Test Suite Pattern

Use testify suites for integration tests:

```go
type ProductServiceTestSuite struct {
    suite.Suite
    tc      *testutil.TestContext
    service *ProductService
}

func (suite *ProductServiceTestSuite) SetupSuite() {
    suite.tc = testutil.NewPgTestContext(suite.T())
    // run migrations, create fixtures, wire services
}

func (suite *ProductServiceTestSuite) TearDownSuite() {
    suite.tc.Cleanup()
}

func TestProductServiceSuite(t *testing.T) {
    suite.Run(t, new(ProductServiceTestSuite))
}
```

### 14.3 Test Naming

- Suite runner: `TestXxxSuite(t *testing.T)`
- Test methods: `func (suite *XxxSuite) TestMethodName_Scenario()`
- Unique data helpers for isolation: `uniqueEmail(prefix)`, `uniqueName(prefix)`

### 14.4 Assertions

```go
suite.Require().NoError(err)           // fatal on error
suite.Assert().Equal(expected, actual)  // non-fatal
suite.Assert().NotNil(value)
suite.Assert().NotEmpty(value)
```

### 14.5 Makefile Targets

```makefile
test:              ## Run all tests
    go test ./...

test-unit:         ## Unit tests only
    go test -short ./...

test-integration:  ## Integration tests (with testcontainers)
    go test ./service/... -v

test-race:         ## Tests with race detector
    go test -race ./...

test-coverage:     ## Coverage report
    go test -coverprofile=coverage.out ./...

test-file:         ## Run tests in specific directory
    go test -v ./$(dir $(FILE))/...
```

---

## 15. Logging

### 15.1 Logger Creation

```go
logger := log.New("component-name")
```

### 15.2 Log Methods

```go
logger.Info("message")
logger.Infof("formatted %s", value)
logger.Error(err, "context message")
logger.Errorf(err, "formatted error %s", value)
logger.Info("message", log.KV{"key": value})
```

### 15.3 In Gin Handlers

```go
log.FromContext(g).Error(err, "failed to process request")
```

### 15.4 Nil Logger Fallback

Services accept `nil` logger and create a default:

```go
func NewMyService(ctx context.Context, conn *db.SqlClient, logger *log.Logger) *MyService {
    if logger == nil {
        logger = log.New("svcMy")
    }
    // ...
}
```

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
- `auto:` — marks auto-updated fields for qb UpdateBuilder (`"true"` — see 6.6)
- `grid:` — db.Grid capabilities (`"sort,search,filter"` — see 6.7)
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

## 19. Providers

### 19.1 SMTP Provider

Email client with multiple auth methods, TLS, attachments, and batch sending.

```go
import "github.com/oddbit-project/blueprint/provider/smtp"

config := smtp.NewConfig()
config.Host = "smtp.gmail.com"
config.Port = 587
config.Username = "sender@example.com"
config.AuthType = "plain"             // plain|login|crammd5|xoauth2|scram-sha-1|scram-sha-256|noauth
config.From = "noreply@example.com"
config.TLSEnable = true
config.Bcc = "audit@example.com"      // auto-BCC all messages
// Secure credential: config.PasswordEnvVar = "SMTP_PASSWORD"

mailer, err := smtp.NewMailer(config)

// Send with plain+HTML body and attachments
msg, err := mailer.NewMessage(
    []string{"user@example.com"},
    "Subject",
    smtp.WithFrom("custom@example.com"),
    smtp.WithBody("Plain text", "<h1>HTML</h1>"),
    smtp.WithAttachment("/path/to/file.pdf"),
)
mailer.Send(msg)

// Batch sending (NewMessage returns *gomail.Msg from github.com/wneessen/go-mail)
var msgs []*gomail.Msg
for _, r := range recipients {
    m, _ := mailer.NewMessage([]string{r.Email}, "Newsletter",
        smtp.WithBody(plain, html))
    msgs = append(msgs, m)
}
mailer.Send(msgs...)
```

**Errors:** `smtp.ErrInvalidTo`, `smtp.ErrInvalidFrom`, `smtp.ErrSMTPServer`, `smtp.ErrMessage`.

### 19.2 S3 Provider

S3-compatible storage (AWS S3, MinIO, DigitalOcean Spaces, Backblaze B2).

```go
import "github.com/oddbit-project/blueprint/provider/s3"

config := s3.NewConfig()
config.Endpoint = "localhost:9000"
config.Region = "us-east-1"
config.AccessKeyID = "minioadmin"
config.PasswordEnvVar = "S3_SECRET_KEY"
config.UseSSL = false
config.ForcePathStyle = true // required for MinIO

client, err := s3.NewClient(config, logger)
client.Connect(ctx)
defer client.Close()

// Bucket operations
bucket, _ := client.Bucket("my-bucket")
bucket.Create(ctx, s3.BucketOptions{Region: "us-east-1"})
exists, _ := bucket.Exists(ctx)
buckets, _ := client.ListBuckets(ctx)

// Object operations
bucket.PutObject(ctx, "key.txt", reader, size)
reader, _ := bucket.GetObject(ctx, "key.txt")
bucket.PutObjectStream(ctx, "key.txt", reader)         // unknown size
bucket.GetObjectStream(ctx, "key.txt", writer)          // stream to writer
bucket.DeleteObject(ctx, "key.txt")
bucket.CopyObject(ctx, "src.txt", "dest-bucket", "dst.txt")

// List and metadata
objects, _ := bucket.ListObjects(ctx, s3.ListOptions{Prefix: "docs/", MaxKeys: 100})
exists, _ := bucket.ObjectExists(ctx, "key.txt")
info, _ := bucket.HeadObject(ctx, "key.txt") // size, etag, content-type

// Presigned URLs
downloadURL, _ := bucket.PresignGetObject(ctx, "key.txt", 1*time.Hour)
uploadURL, _ := bucket.PresignPutObject(ctx, "new.txt", 30*time.Minute)
```

**Upload options:** `ContentType`, `CacheControl`, `Metadata`, `Tags`, `StorageClass`, `ServerSideEncryption`.

**Multipart config:** `MultipartThreshold` (default 100MB), `PartSize` (10MB), `Concurrency` (5).

**Connection pooling:** `MaxIdleConns` (50), `MaxIdleConnsPerHost` (10), `MaxConnsPerHost` (20), `IdleConnTimeout` (60s). Tune up for high-throughput workloads.

**Errors:** `s3.ErrBucketNotFound`, `s3.ErrBucketAlreadyExists`, `s3.ErrObjectNotFound`, `s3.ErrClientNotConnected`.

### 19.3 TLS Configuration

Blueprint provides shared TLS config structs embedded by other providers (SMTP, S3, NATS, httpserver).

```go
import "github.com/oddbit-project/blueprint/provider/tls"

// Client TLS (embedded in S3, SMTP, NATS configs)
clientCfg := &tls.ClientConfig{
    TLSCA:                  "/path/to/ca.crt",
    TLSCert:                "/path/to/client.crt",  // for mutual TLS
    TLSKey:                 "/path/to/client.key",
    TLSEnable:              true,
    TLSInsecureSkipVerify:  false,
}
tlsConfig, err := clientCfg.TLSConfig()

// Server TLS
serverCfg := &tls.ServerConfig{
    TLSCert:           "/path/to/server.crt",
    TLSKey:            "/path/to/server.key",
    TLSAllowedCACerts: []string{"/path/to/ca.crt"}, // client cert verification
    TLSCipherSuites:   []string{"TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384"},
    TLSMinVersion:     "1.3",
    TLSMaxVersion:     "1.3",
    TLSAllowedDNSNames: []string{"client.example.com"},
    TLSEnable:         true,
}
```

**Defaults:** TLS 1.3, strong cipher suites. Supports encrypted private keys via `TlsKeyCredential` (env var or file).

### 19.4 NATS Provider

Pub/sub messaging with core NATS and JetStream persistent streams.

**Core NATS (ephemeral pub/sub):**

```go
import "github.com/oddbit-project/blueprint/provider/nats"

// Producer
cfg := &nats.ProducerConfig{
    URL: "nats://localhost:4222", Subject: "events.created",
    AuthType: nats.AuthTypeNone,
}
producer, _ := nats.NewProducer(cfg, logger)
defer producer.Disconnect()

producer.Publish([]byte("raw data"))
producer.PublishJSON(myStruct)

// Request-Reply
reply, _ := producer.Request("req.subject", []byte("data"), 5*time.Second)

// Consumer
consumerCfg := &nats.ConsumerConfig{
    URL: "nats://localhost:4222", Subject: "events.created",
    AuthType: nats.AuthTypeNone,
    ConsumerOptions: nats.ConsumerOptions{QueueGroup: "my-service"},
}
consumer, _ := nats.NewConsumer(consumerCfg, logger)
defer consumer.Disconnect()

consumer.Subscribe(ctx, func(ctx context.Context, msg nats.Message) error {
    log.Info("received", log.KV{"data": string(msg.Data)})
    return nil
})
```

**Auth types:** `nats.AuthTypeNone`, `nats.AuthTypeBasic` (Username/Password), `nats.AuthTypeToken`.

**JetStream (persistent streams with acks):**

```go
// Producer
jsCfg := &nats.JSProducerConfig{
    JSConnectionConfig: nats.JSConnectionConfig{
        URL: "nats://localhost:4222", AuthType: nats.AuthTypeNone,
    },
    Subject: "orders.created",
    Stream: nats.StreamConfig{
        Name: "ORDERS", Subjects: []string{"orders.>"},
        Storage: "file", MaxAge: 24 * time.Hour, Replicas: 1,
    },
    AutoCreateStream: true, // false in prod — stream must pre-exist
}
jsProducer, _ := nats.NewJSProducer(jsCfg, logger)
defer jsProducer.Disconnect()

ack, _ := jsProducer.Publish(ctx, []byte(`{"id":"o-1"}`))
jsProducer.PublishJSON(ctx, myStruct)
fut, _ := jsProducer.PublishAsync(ctx, "orders.created", data) // non-blocking

// Consumer
jsConsumerCfg := &nats.JSConsumerConfig{
    JSConnectionConfig: nats.JSConnectionConfig{
        URL: "nats://localhost:4222", AuthType: nats.AuthTypeNone,
    },
    StreamName:    "ORDERS",
    Durable:       "orders-worker",
    FilterSubject: "orders.created",
    AckPolicy:     "explicit",
    AckWait:       30 * time.Second,
    MaxDeliver:    5,
    DeliverPolicy: "all", // "all"|"last"|"new"
}
jsConsumer, _ := nats.NewJSConsumer(jsConsumerCfg, logger)
defer jsConsumer.Disconnect()

// Continuous consumption: nil return = auto-Ack, error return = auto-Nak
jsConsumer.Consume(ctx, func(ctx context.Context, msg nats.JSMessage) error {
    return processOrder(ctx, msg.Data())
})

// Pull-style batch fetch
msgs, _ := jsConsumer.Fetch(50, 2*time.Second)
for _, m := range msgs {
    if err := process(m.Data()); err != nil {
        m.Nak()
        continue
    }
    m.Ack()
}
```

**StreamConfig:** `Retention` (`"limits"` | `"interest"` | `"workqueue"`), `Storage` (`"file"` | `"memory"`), `MaxAge`, `MaxMsgs`, `MaxBytes`, `Replicas`. Use `Native *jetstream.StreamConfig` escape hatch for advanced fields.

**JSMessage methods:** `Ack()`, `Nak()`, `InProgress()` (extend ack-wait), `Term()` (permanent reject), `Metadata()` (sequence, delivery count).

---
