# Service Layer

Part of the Blueprint skill. Section numbers match the original guide.

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

Use atomic status tracking (see `references/components.md` §11.2) for Start/Stop lifecycle.

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
