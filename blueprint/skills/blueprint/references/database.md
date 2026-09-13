# Database Patterns

Part of the Blueprint skill. Section numbers match the original guide.

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
