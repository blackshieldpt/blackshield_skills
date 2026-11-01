# Error Handling / Graceful Shutdown

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

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
if err != nil { response.Http500(g, err); return }   // logs err; the client sees a generic 500
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
blueprint.RegisterDestructor(func() error { return pool.Stop(blueprint.ShutdownContext()) }) // pool: registered FIRST, runs LAST
blueprint.RegisterDestructor(func() error { consumer.Close(); return nil })                  // consumer: registered SECOND, runs SECOND
blueprint.RegisterDestructor(func() error {                                                  // API: registered LAST, runs FIRST
    return server.Shutdown(blueprint.ShutdownContext())
})
```

**Never pass `app.Context` / `GetContext()` (or a context derived from it) to a destructor's
`Shutdown`/`Stop`.** On a signal, `Run` cancels it before the destructors run, so
`server.Shutdown` returns `context.Canceled` at once and requests in flight are dropped. On
`AbortFatal` it is live with no deadline, so `Shutdown` waits for the slowest request.

`blueprint.ShutdownContext()` has one deadline, counted from the start of `Shutdown` and shared by
all destructors. The default is 5s; change it with `blueprint.SetShutdownTimeout(d)`, and keep it
below the orchestrator's grace period (`docker stop`: 10s). It exists in core releases after v0.15.0
(oddbit-project/blueprint#145). If `go.mod` pins core v0.15.0 or earlier, use a fresh context
instead:

```go
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()
return server.Shutdown(ctx)
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
