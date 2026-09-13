# Error Handling / Graceful Shutdown

Part of the Blueprint skill. Section numbers match the original guide.

## 9. Error Handling

### 9.1 Sentinel Errors

Define in `pkg/entity/` using Blueprint's `utils.Error()`:

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

- With a separate repository layer, repositories return `(*T, nil)` (nil pointer, nil error) for missing records instead of a sentinel; convert `sql.ErrNoRows` at the repository boundary.
- Services holding `db.Repository` directly (no separate repository layer) use sentinel errors instead (§9.1).

```go
// Repository
func (r *EntityRepository) GetByID(id uuid.UUID) (*entity.Entity, error) {
    record := &entity.Entity{}
    err := r.repo.FetchOne(r.repo.SqlSelect().Where(goqu.C("id_entity").Eq(id)), record)
    if errors.Is(err, sql.ErrNoRows) { return nil, nil }
    if err != nil { return nil, err }
    return record, nil
}

// Handler
entity, err := svc.Entity.GetByID(id)
if err != nil { response.Http500(g, errInternalError); return }
if entity == nil { response.Http404(g); return }
```

### 9.3 Error Flow

| Layer | Rule |
|---|---|
| Service | Sentinel errors or nil-for-not-found depending on architecture; `errors.Is()` for sentinels, nil-check for not-found with separate repositories |
| API | Domain error helpers map errors to HTTP status codes |
| Build | `m.AbortFatal(err)` for fatal init errors (prevents startup) |

---

## 10. Graceful Shutdown

### 10.1 Registering Destructors

`blueprint.RegisterDestructor(fn)`. Destructors run in **LIFO order** (last registered runs first) — register in **reverse order** of the desired shutdown sequence:

```go
blueprint.RegisterDestructor(func() error { return pool.Stop(app.Context) }) // pool: registered FIRST, runs LAST
blueprint.RegisterDestructor(func() error { consumer.Close(); return nil })  // consumer: registered SECOND, runs SECOND
blueprint.RegisterDestructor(func() error {                                  // API: registered LAST, runs FIRST
    ctx, cancel := context.WithTimeout(app.Context, 1*time.Second)
    defer cancel()
    return server.Shutdown(ctx)
})
```

### 10.2 Goroutine Launchers

`m.Run(...)` takes launcher functions; each launches a goroutine or blocking call and calls `m.Terminate(err)` if it fails:

```go
m.Run(
    func(app interface{}) error {
        go func() {
            if err := server.Start(); err != nil { m.Terminate(err) }
        }()
        return nil
    },
)
```

---
