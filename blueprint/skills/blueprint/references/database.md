# Database Patterns

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

## 12. Database Patterns

### 12.1 PostgreSQL Migrations

Embed SQL files; run via Blueprint's migration manager (error returns elided):

```go
//go:embed pgsql/*.sql
var pgFs embed.FS

func PgMigrate(ctx context.Context, client *db.SqlClient) error {
    source, err := migrations.NewEmbedSource(pgFs, "pgsql")
    manager, err := pgsql.NewMigrationManager(ctx, client)
    return manager.Run(ctx, source, migrations.DefaultProgressFn)
}
```

### 12.2 Schema Conventions

| Item | Convention |
|---|---|
| Primary key | `id_{table_name}` (`id_product`) |
| Foreign key | `fk_{referenced_table}` (`fk_category`) |
| Timestamps | `created_at`, `updated_at` |
| Soft status | integer column: `1`=active, `2`=inactive, `3`=trash, `4`=deleted |
| Table name constant | `Tbl*` prefix (`TblProduct = "product"`) |

### 12.3 Database Initialization

```go
func (m *App) initDb(cfg *pgsql.ClientConfig) *db.SqlClient {
    m.AbortFatal(cfg.Validate())
    client, err := pgsql.NewClient(cfg)
    m.AbortFatal(err)
    m.AbortFatal(client.Connect())
    blueprint.RegisterDestructor(func() error { client.Disconnect(); return nil })
    return client
}
```

### 12.4 Job Queue with FOR UPDATE SKIP LOCKED

Atomically claim the next pending job without blocking other workers: the subquery selects and locks one row, `SKIP LOCKED` skips rows locked by other workers, the outer `UPDATE` claims it. Multiple workers call `Dequeue()` concurrently without duplicate processing.

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
    if errors.Is(err, sql.ErrNoRows) { return nil, nil }
    if err != nil { return nil, err }
    return job, nil
}
```

---
