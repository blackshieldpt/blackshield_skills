# Error Handling / Graceful Shutdown

Part of the Blueprint skill. Section numbers match the original guide.

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

When services hold `db.Repository` directly (no separate repository layer), use sentinel errors instead (see §9.1 above).

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
