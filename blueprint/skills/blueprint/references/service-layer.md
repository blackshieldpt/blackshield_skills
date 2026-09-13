# Service Layer

Part of the Blueprint skill. Section numbers match the original guide.

## 6. Service Layer

### 6.1 Repository Pattern

**Direct repository** — simpler services hold `db.Repository` directly:

```go
type ProductService struct {
    repo   db.Repository
    logger *log.Logger
    ctx    context.Context
    mx     sync.Mutex
}

func NewProductService(ctx context.Context, conn *db.SqlClient, logger *log.Logger) *ProductService {
    if logger == nil { logger = log.New("svcProduct") }
    return &ProductService{repo: db.NewRepository(ctx, conn, entity.TblProduct), logger: logger, ctx: ctx}
}
```

**Separate repository package** — larger domains with complex queries: extract into `repository/`.
The repository encapsulates all query logic; services hold repository instances instead of raw
`db.SqlClient`. Use it when multiple services need the same query logic, or when query complexity
warrants its own layer.

```go
// repository/entity_repo.go
type EntityRepository struct { repo db.Repository; logger *log.Logger; ctx context.Context }

func NewEntityRepository(ctx context.Context, conn *db.SqlClient, logger *log.Logger) *EntityRepository {
    return &EntityRepository{repo: db.NewRepository(ctx, conn, entity.TblEntity), logger: logger, ctx: ctx}
}

func (r *EntityRepository) GetByID(id uuid.UUID) (*entity.Entity, error) {
    record := &entity.Entity{}
    err := r.repo.FetchOne(r.repo.SqlSelect().Where(goqu.C("id_entity").Eq(id)), record)
    if err != nil {
        if errors.Is(err, sql.ErrNoRows) { return nil, nil }   // nil for not-found
        return nil, err
    }
    return record, nil
}

// service/entity_service.go
type EntityService struct { entityRepo *repository.EntityRepository; logger *log.Logger }
func NewEntityService(entityRepo *repository.EntityRepository, logger *log.Logger) *EntityService
```

### 6.2 Query Patterns

```go
result := make([]*entity.ProductRecord, 0)                                       // list
err := s.repo.Fetch(s.repo.SqlSelect().Where(goqu.C("status").Eq(1)), &result)
record := &entity.ProductRecord{}                                                 // single
err := s.repo.FetchOne(s.repo.SqlSelect().Where(goqu.C("id").Eq(id)), record)
```

`goqu.L()` — raw SQL for PostgreSQL-specific operators/functions GOQU doesn't model natively;
combines with typed columns:

```go
r.repo.SqlSelect().Where(goqu.L("valid_during @> ?::timestamptz", atTime))                  // range containment
r.repo.SqlSelect().Where(goqu.C("id_entity").Eq(id)).Where(goqu.L("upper_inf(valid_during)")) // function filter + typed column
```

### 6.3 PostgreSQL Records

Tags: `db:"column_name"` column mapping, `json:"fieldName"` API serialization,
`goqu:"skipinsert,skipupdate"` on auto-generated columns (serial PKs):

```go
type OrderRecord struct {
    Id         int64      `json:"id" db:"id_order" goqu:"skipinsert,skipupdate"`
    CreatedAt  time.Time  `json:"createdAt" db:"created_at"`
    UpdatedAt  time.Time  `json:"updatedAt" db:"updated_at"`
    UserId     uuid.UUID  `json:"userId" db:"fk_user"`
    Status     int        `json:"status" db:"status"`
    Name       string     `json:"name" db:"name"`
    Attributes Attributes `json:"attributes" db:"attributes"`
}
```

Repository operations:

| Op | Call |
|---|---|
| Insert (`db:` tags map columns) | `s.repo.Insert(record)` |
| Insert, return auto-generated fields | `s.repo.InsertReturning(record, []string{"id_order"}, &record.Id)` |
| Fetch list with conditions | `s.repo.Fetch(s.repo.SqlSelect().Where(goqu.C("status").Eq(1)), &result)` |
| Fetch with field-value map | `s.repo.FetchWhere(db.FV{"fk_user": userId, "fk_vault": vaultId}, &result)` |
| Count with conditions | `count, err := s.repo.CountWhere(db.FV{"fk_user": userId, "name": name})` |
| Update fields — `UpdateFields(sample, setValues, whereValues)` | `s.repo.UpdateFields(&entity.OrderRecord{}, db.FV{"status": newStatus, "updated_at": time.Now()}, db.FV{"id_order": orderId})` |
| Delete with conditions | `s.repo.DeleteWhere(db.FV{"fk_user": userId, "id_order": orderId})` |

**JSONB columns** — an `Attributes` type implementing `sql.Scanner` and `driver.Valuer`; nil
round-trips as nil:

```go
type Attributes map[string]interface{}

func (j *Attributes) Scan(value interface{}) error {
    if value == nil { *j = nil; return nil }
    bytes, ok := value.([]byte)
    if !ok { return fmt.Errorf("failed to scan JSONB: expected []byte, got %T", value) }
    return json.Unmarshal(bytes, j)
}

func (j Attributes) Value() (driver.Value, error) {
    if j == nil { return nil, nil }
    return json.Marshal(j)
}
```

### 6.4 ClickHouse Records

`ch:"column_name"` tags; the same record reads (`ScanStruct`/`Select`) and writes
(`clickhouse.Repository.Insert`):

```go
type MetricRecord struct {
    Timestamp time.Time `json:"timestamp" ch:"event_time"`
    SourceId  string    `json:"sourceId" ch:"source_id"`
    Bytes     uint64    `json:"bytes" ch:"bytes"`
    Events    uint64    `json:"events" ch:"events"`
}
```

| Use | Call |
|---|---|
| Repository from cluster connection | `repo := cluster.Connection().NewRepository(ctx, TblMetrics)`; `repo.Insert(record)` |
| Raw query, single row | `result := &entity.MetricStats{}`; `cluster.Connection().Conn.QueryRow(ctx, sql, args...).ScanStruct(result)` |
| Raw query, multiple rows | `result := []MinuteRecord{}`; `cluster.Connection().Conn.Select(ctx, &result, sql, args...)` |
| Batch writes (high-throughput ingestion) | `type RecordValues []any` with the writer pool instead of struct inserts: `pool.Add(clusterId, sourceId, tableName, entity.RecordValues{val1, val2, val3})` |

**Timestamp formatting** — ClickHouse does not accept Go `time.Time` directly in parameterized
queries; in GOQU WHERE clauses against `DateTime64` columns format explicitly as a string:

```go
func clickhouseTimestamp(t time.Time) string { return t.UTC().Format("2006-01-02 15:04:05.000") }

qry := s.resultRepo.SqlSelect().
    Where(goqu.C("timestamp").Gte(clickhouseTimestamp(from))).
    Where(goqu.C("timestamp").Lte(clickhouseTimestamp(to)))
```

### 6.5 Buffered ClickHouse Writer

Metrics ingestion: queue records, flush to ClickHouse periodically. Track health with
`promauto.NewCounterVec(prometheus.CounterOpts{Name, Help}, []string{"table"})`:

| Var | Name | Help |
|---|---|---|
| `writerRecordsDropped` | `panorama_writer_records_dropped_total` | Total records dropped due to queue full |
| `writerRecordsFlushed` | `panorama_writer_records_flushed_total` | Total records successfully flushed to ClickHouse |
| `writerFlushErrors` | `panorama_writer_flush_errors_total` | Total flush attempts that failed |

```go
type BufferedWriter struct {
    db        *clickhouse.Client
    tableName string
    config    *WriterConfig
    logger    *log.Logger

    queue     chan types.MetricRecord
    stopCh    chan struct{}
    doneCh    chan struct{}
    status    atomic.Int32
}

// Add: non-blocking, drop on backpressure
func (w *BufferedWriter) Add(record types.MetricRecord) {
    select {
    case w.queue <- record:
    default:
        writerRecordsDropped.WithLabelValues(w.tableName).Inc()
    }
}

// worker: flush on ticker interval, draining the channel into a batch; final flush on stop
func (w *BufferedWriter) worker() {
    defer close(w.doneCh)
    ticker := time.NewTicker(w.config.FlushInterval)
    defer ticker.Stop()
    for {
        select {
        case <-ticker.C: w.flushSync()
        case <-w.stopCh: w.flushSync(); return
        }
    }
}
```

Start/Stop lifecycle: atomic status tracking (`references/components.md` §11.2).

### 6.6 Modern Update API (qb Package)

`github.com/oddbit-project/blueprint/db/qb`: type-safe UPDATE with dialect abstraction, RETURNING
clauses, and flexible options. Repository integration via `repo.SqlUpdateX()` + `repo.Do()` —
preferred:

```go
func (s *UserService) UpdateEmail(id int, email string) error {
    record := &entity.UserRecord{Email: email, UpdatedAt: time.Now()}
    ub := s.repo.SqlUpdateX(record).
        WithOptions(&qb.UpdateOptions{IncludeFields: []string{"email", "updated_at"}}).
        Where(qb.Eq("id", id))
    return s.repo.Do(ub)
}

// RETURNING
ub := s.repo.SqlUpdateX(record).
    WithOptions(&qb.UpdateOptions{ReturningFields: []string{"id", "name", "email", "updated_at"}}).
    Where(qb.Eq("id", id))

// Map-based partial update with raw expressions
ub := repo.SqlBuilder().Update("users", &entity.UserRecord{}).
    FieldsValues(map[string]any{"last_login": time.Now(), "login_count": qb.Raw("login_count + 1")}).
    Where(qb.Eq("id", userID))
repo.Do(ub)
```

`qb.UpdateOptions` (field selection):

| Field | Effect |
|---|---|
| `IncludeFields: []string{"name", "email"}` | only these fields |
| `ExcludeFields: []string{"created_at"}` | never these fields |
| `IncludeZeroValues: true` | include zero/empty values |
| `UpdateAutoFields: true` | update `auto:"true"` tagged fields |
| `ReturningFields: []string{"id", "updated_at"}` | RETURNING clause |

WHERE helpers (`qb`):

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

`.Where()` is AND-chained; `.WhereAnd()` groups conditions:
`ub.WhereAnd(qb.Eq("department", "eng"), qb.Or(qb.Eq("role", "senior"), qb.Gt("years", 5)))`

Batch:

```go
sql, args, err := builder.BuildSQLBatchInsert("users", records)
stmts, argsList, err := builder.BuildSQLBatchUpdate("users", records, []string{"id"}, qb.DefaultUpdateOptions())
```

### 6.7 DB Grid (Dynamic Query Builder)

`db.Grid` builds dynamic SELECT queries (filter, sort, search, pagination) from struct tags;
integrated with `db.Repository` via `GridOps`. `grid` tags: `sort` (orderable), `search` (text
search), `filter` (equality filter). JSON field names auto-map to db columns.

```go
type UserRecord struct {
    ID       int    `db:"id" json:"id" grid:"sort,filter"`
    Username string `db:"username" json:"username" grid:"sort,search,filter"`
    Email    string `db:"email" json:"email" grid:"sort,search,filter"`
    Active   bool   `db:"active" json:"active" grid:"filter"`
}
```

Repository integration (preferred):

```go
query, _ := db.NewGridQuery(db.SearchAny, 25, 0)
query.SearchText = "john"
query.FilterFields = map[string]any{"active": true}
query.SortFields = map[string]string{"username": db.SortAscending}
query.Page(2, 25) // page 2, 25 items/page — calculates offset

var users []*UserRecord
err := repo.QueryGrid(&UserRecord{}, query, &users)
```

- Search types: `db.SearchNone` (0), `db.SearchStart` (1 — `%term`), `db.SearchEnd` (2 — `term%`), `db.SearchAny` (3 — `%term%`).
- Sort constants: `db.SortAscending`, `db.SortDescending`.

Custom filter functions and standalone usage (without repository):

```go
grid, _ := db.NewGrid("users", &UserRecord{})
grid.AddFilterFunc("active", func(value any) (any, error) {
    if v, ok := value.(string); ok { return v == "1" || v == "true", nil }
    return value, nil
})

if err := grid.ValidQuery(query); err != nil { ... }
stmt, _ := grid.Build(nil, query)
sql, args, _ := stmt.ToSQL()
```

---
