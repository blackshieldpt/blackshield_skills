# Component Interfaces

Part of the Blueprint skill. Section numbers (`§N.N`) are the cross-reference anchors that SKILL.md and the other reference files cite.

## 11. Component Interfaces

```go
// pkg/interfaces/interface.go
type Runnable interface {
    Start(ctx context.Context) error
    Stop(ctx context.Context) error
}
type Writer interface { Write(context.Context, []byte) error }
type RunnableWriter interface { Runnable; Writer }
```

| Interface | Implemented by |
|---|---|
| `Runnable` | any long-lived background component (consumer, worker pool, status updater) |
| `Writer` | any data sink (Kafka producer, WAL, batch writer) |
| `RunnableWriter` | a data sink that also has lifecycle (start/stop) |

### 11.1 Thread Pool

`threadpool.ThreadPool` (`github.com/oddbit-project/blueprint/threadpool`): channel-based worker
pool, panic recovery per worker, multiple dispatch strategies.

```go
type Job interface { Run(ctx context.Context) }                          // implement for custom job types
job := threadpool.FuncRunner(func(ctx context.Context) { /* work */ })   // adapter for plain functions

pool, err := threadpool.NewThreadPool(8, 1000, threadpool.WithLogger(logger))
pool.Start(ctx)
pool.Stop()                // blocking: runs every queued job, then returns; no time bound
pool.StopWithContext(ctx)  // same, but gives up when ctx is done (cancels workers, returns ctx.Err())
```

| Dispatch | Behaviour |
|---|---|
| `pool.Dispatch(job)` | blocking if queue full; **panics** if the pool is stopped or never started — use `DispatchWithContext` on any path that can race shutdown |
| `pool.TryDispatch(job)` | non-blocking, returns false if full |
| `pool.DispatchWithTimeout(job, 100*time.Millisecond)` | returns false on timeout |
| `pool.DispatchWithContext(ctx, job)` | returns error on context cancellation, or `ErrPoolNotStarted` when stopped |

Introspection: `pool.GetQueueLen()` pending jobs, `pool.GetQueueCapacity()` max queue size,
`pool.GetWorkerCount()` active workers, `pool.GetRequestCount()` total jobs processed.

**Application wrapper** — wrap `ThreadPool` with a config struct and a `WorkerFunc` type:

```go
type Config struct {
    Workers   int `json:"workers"`
    QueueSize int `json:"queueSize"`
}
type WorkerPool struct { pool *threadpool.ThreadPool; logger *log.Logger }
type WorkerFunc func(ctx context.Context)

func NewConfig() *Config { return &Config{Workers: runtime.NumCPU(), QueueSize: 1000} }

func NewWorkerPool(cfg *Config, logger *log.Logger) (*WorkerPool, error) {
    pool, err := threadpool.NewThreadPool(cfg.Workers, cfg.QueueSize, threadpool.WithLogger(logger))
    if err != nil { return nil, err }
    return &WorkerPool{pool: pool, logger: logger}, nil
}
func (p *WorkerPool) Start(ctx context.Context) error { return p.pool.Start(ctx) }
func (p *WorkerPool) Stop(ctx context.Context) error  { return p.pool.StopWithContext(ctx) } // Stop() would ignore the shutdown deadline
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
    blueprint.RegisterDestructor(func() error { return proc.Stop(blueprint.ShutdownContext()) }) // not a.Context: cancelled by then
    return proc
}
```

Config: `{"workerPool": {"workers": 12, "queueSize": 1000}}`

### 11.2 Atomic Status Tracking

`atomic.Int32` + `CompareAndSwap()` for state transitions in background components; status
constants guard Start/Stop against concurrent or duplicate calls:

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
    if !w.status.CompareAndSwap(statusStopped, statusRunning) { return ErrAlreadyStarted }
    w.ctx, w.cancel = context.WithCancel(ctx)
    w.wg.Add(1)
    go w.run()
    return nil
}

func (w *Worker) Stop(ctx context.Context) error {
    if !w.status.CompareAndSwap(statusRunning, statusStopping) { return ErrNotRunning }
    w.cancel()
    w.wg.Wait()
    w.status.Store(statusStopped)
    return nil
}

func (w *Worker) IsRunning() bool { return w.status.Load() == statusRunning }
```

`atomic.Bool` prevents concurrent execution of the same task:

```go
type Task struct { fn TaskFn; running atomic.Bool }

if task.running.CompareAndSwap(false, true) {
    go func() { defer task.running.Store(false); task.fn(ctx) }()
}
```

`atomic.Int32` tracks in-flight work (e.g. queued tasks):

```go
type TaskRunner struct { runningTasks atomic.Int32 }

func (t *TaskRunner) Queue(ctx context.Context, fn WorkerFunc) error {
    t.runningTasks.Add(1)
    wrapFn := func(ctx context.Context) {
        defer t.runningTasks.Add(-1)
        fn(ctx)
    }
    if err := t.workerPool.Queue(ctx, wrapFn); err != nil {
        t.runningTasks.Add(-1)
        return err
    }
    return nil
}
func (t *TaskRunner) RunningTasks() int { return int(t.runningTasks.Load()) }
```

`DispatchWithContext` returns an error when the context is canceled or the pool is stopped; the
job never runs, so the counter must be rolled back on that path or it stays high forever.

### 11.3 Atomic Counter Stats

`atomic.Uint64` fields grouped in a stats struct: lock-free counters updated from any goroutine
(`stats.NewBatchCount.Add(1)`, `stats.SSLOperations.Add(uint64(domainCount))`); read with
`.Load()` for Prometheus collection or health endpoints.

```go
type Stats struct {
    NewBatchCount atomic.Uint64
    SPFOperations atomic.Uint64
    SSLOperations atomic.Uint64
    FlushCount    atomic.Uint64
    SuccessCount  atomic.Uint64
    FailedCount   atomic.Uint64
}
```

### 11.4 Custom Prometheus Collector

Metrics derived from application stats objects: implement `prometheus.Collector` instead of
`promauto` globals — decouples metric definitions from the code that updates them.

| Desc field | Name | Help | Source |
|---|---|---|---|
| `batchCount` | `app_batch_total` | Total batches created | `taskStats.NewBatchCount` |
| `flushCount` | `app_flush_total` | Total flush operations | `writerStats.FlushCount` |
| `successCount` | `app_success_total` | Total successful results | `writerStats.SuccessCount` |
| `failedCount` | `app_failed_total` | Total failed results | `writerStats.FailedCount` |

```go
type Collector struct {
    batchCount, flushCount, successCount, failedCount *prometheus.Desc
    taskStats   *tasks.Stats
    writerStats *writer.Stats
}

func NewCollector(taskStats *tasks.Stats, writerStats *writer.Stats) *Collector {
    return &Collector{
        batchCount: prometheus.NewDesc("app_batch_total", "Total batches created", nil, nil),
        // flushCount, successCount, failedCount likewise
        taskStats:   taskStats,
        writerStats: writerStats,
    }
}

func (c *Collector) Describe(ch chan<- *prometheus.Desc) {
    ch <- c.batchCount; ch <- c.flushCount; ch <- c.successCount; ch <- c.failedCount
}

func (c *Collector) Collect(ch chan<- prometheus.Metric) {
    if c.taskStats != nil {
        ch <- prometheus.MustNewConstMetric(c.batchCount, prometheus.CounterValue, float64(c.taskStats.NewBatchCount.Load()))
    }
    if c.writerStats != nil {
        ch <- prometheus.MustNewConstMetric(c.flushCount, prometheus.CounterValue, float64(c.writerStats.FlushCount.Load()))
        // successCount, failedCount likewise
    }
}
```

Register during Build: `prometheus.MustRegister(stats.NewCollector(taskStats, writerStats))`.

Use `promauto` (`references/service-layer.md` §6.5) when metrics are updated directly at the call
site; a custom collector when they derive from shared stats objects or external state.

### 11.5 Batch Writer

`github.com/oddbit-project/blueprint/batchwriter` — thread-safe buffered batch processor; flushes
when capacity is reached or the flush interval expires.

```go
writer, err := batchwriter.NewBatchWriter(
    ctx,
    100,              // capacity (batch size)
    5*time.Second,    // flush interval
    func(records ...any) { for _, r := range records { row := r.(*MyRecord); /* bulk insert */ } },
    batchwriter.WithLogger(logger),
    batchwriter.WithClearBuffers(true),   // help GC on long-running services
    batchwriter.WithQueueCapacity(50),    // input channel capacity (default: 10)
)
```

| Call | Behaviour |
|---|---|
| `writer.Add(record)` | blocks if input channel full |
| `ok := writer.TryAdd(record)` | non-blocking, returns false if full; ~6x faster than `Add` — use for high-throughput where occasional drops are acceptable |
| `err := writer.AddWithContext(ctx, record)` | context-aware |
| `writer.FlushNow()` | force immediate flush |
| `writer.Stop()` | graceful shutdown, flushes remaining |
| `m := writer.GetMetrics()` | `m.RecordsAdded`, `m.RecordsProcessed`, `m.RecordsDropped`, `m.FlushCount`, `m.LastFlushDuration`, `m.AvgFlushDuration` |

Panics in the flush function are recovered and logged; the writer continues operating.

---
