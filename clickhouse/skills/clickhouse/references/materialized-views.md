# Materialized Views

## Mental model

An incremental materialized view is an **insert trigger**, not a cached query. On every insert block into the source table, the MV's `SELECT` runs over that block and writes the result into the target table.

Consequences:
- MVs slow down ingestion — they execute inline with every insert.
- MVs consume memory during ingest; a badly designed one causes OOM, and an OOM during ingest means lost data.
- MVs generate parts on the target table, contributing to `TOO_MANY_PARTS`.
- An MV never sees historical data, only rows inserted after creation.

## Creation pattern

Create the target table explicitly, then attach the view with `TO`:

```sql
CREATE TABLE events_hourly
(
    hour    DateTime,
    tenant  LowCardinality(String),
    hits    AggregateFunction(count),
    users   AggregateFunction(uniq, UInt64)
)
ENGINE = AggregatingMergeTree
ORDER BY (tenant, hour);

CREATE MATERIALIZED VIEW events_hourly_mv TO events_hourly AS
SELECT
    toStartOfHour(ts) AS hour,
    tenant,
    countState()      AS hits,
    uniqState(user_id) AS users
FROM events
GROUP BY hour, tenant;
```

Query with `-Merge`, applied as late as possible:

```sql
SELECT tenant, countMerge(hits) AS hits, uniqMerge(users) AS users
FROM events_hourly
WHERE hour >= now() - INTERVAL 1 DAY
GROUP BY tenant;
```

## Engine choice

| Engine | Use |
|---|---|
| `AggregatingMergeTree` | `-State` columns: counts, uniques, quantiles, averages |
| `SummingMergeTree` | Plain additive numeric columns |
| `ReplacingMergeTree` | Latest-row-wins projections with a version column |
| `MergeTree` | Filtered/reshaped copies, no aggregation |

## POPULATE — only when it is atomic

Since 26.8, `CREATE MATERIALIZED VIEW ... POPULATE` is atomic by default (`materialized_views_populate_atomically = 1`): inserts arriving during creation are delivered exactly once. That applies only to an Atomic database with a MergeTree-family or Memory source, and only to inserts arriving through the local server: rows inserted concurrently on another replica of a `ReplicatedMergeTree` source, or through a `Distributed` table / `ON CLUSTER`, can still be missed or duplicated.

Everywhere else the legacy behaviour holds and rows inserted during view creation are **silently missed or duplicated**: servers older than 26.8, Ordinary databases, sources that are views/`Distributed`/`Merge`/`Buffer`/`Log`, `CREATE OR REPLACE ... POPULATE`, or the setting turned off. In a Replicated database `POPULATE` is refused unless `database_replicated_allow_heavy_create=1` (forbidden by default since 24.7; it can block the DDL queue), and there it always falls back to the non-atomic path. It is unsupported in ClickHouse Cloud. When in doubt, backfill manually as below.

## Backfilling

`INSERT INTO target SELECT ...` over history is the supported path, but carries risks: memory blowups, part storms, and duplicate/lost rows if it overlaps live ingestion.

Procedure:
1. Validate the MV's `SELECT` on a bounded sample first:
   ```sql
   SELECT date, count() FROM (SELECT * FROM source LIMIT 1000000) GROUP BY date
   ```
2. Backfill in bounded chunks — one partition or one time window at a time — and give each chunk its own `SETTINGS insert_deduplication_token = 'backfill-<chunk id>'` on a replicated target. Since 26.1 (`deduplicate_insert_select = enable_when_possible`) an `INSERT ... SELECT` is deduplicated only when the SELECT is stable (it has `ORDER BY ALL` and ends in a single stream) or carries a token, so a retried chunk without one lands twice.
3. Raise rows-per-block (`max_insert_block_size`, `min_insert_block_size_rows`) so the backfill creates few, large parts.
4. To run the MV chain over data you do not want to persist twice, point the MV at a **`Null` engine source table** and insert into that: the MV fires on the inserted block, the raw rows are discarded, and only the aggregate lands in the target.
5. Pick a cutover boundary so live ingestion and the backfill do not overlap.

## Cardinality traps

- `uniqExactState` on a high-cardinality column (200M+ distinct) can take down a cluster during merges — the states are enormous.
- Prefer bounded-size states: `uniqState` (adaptive sampling, ≤65536 hashes) or `uniqCombinedState` / `uniqHLL12State` (HyperLogLog).
- Watch quantile states too — `quantileExactState` has the same shape of problem.

## Cascading MVs

MVs can read from MV target tables to build successive rollups (raw → minute → hour → day). Keep chains shallow: every level multiplies ingest-time work and part generation.

## Schema evolution

To change an MV's shape without stopping ingestion: create a new MV reading the same source table, writing to a new target with the updated schema; backfill history into the new target; switch readers; drop the old MV and target.

## Refreshable materialized views

A refreshable MV has **no insert trigger**. On a schedule it runs its `SELECT` over the full source and writes the result into the target: without `APPEND` each refresh **atomically replaces** the target's contents; with `APPEND` each refresh inserts rows and keeps the old ones (not atomic). Introduced in 23.12, **production-ready since 24.10** (same release added Replicated-database support); older servers gated it behind `allow_experimental_refreshable_materialized_view = 1`, which current docs no longer list.

```sql
CREATE MATERIALIZED VIEW [db.]name [ON CLUSTER c]
REFRESH EVERY|AFTER interval [OFFSET interval]
[RANDOMIZE FOR interval]
[DEPENDS ON [db.]name [, ...]]
[SETTINGS refresh_retries = 2, refresh_retry_initial_backoff_ms = 100, refresh_retry_max_backoff_ms = 60000, all_replicas = 0]
[APPEND]
[TO [db.]target] [(columns)] [ENGINE = engine]
[EMPTY]
AS SELECT ...
```

| Clause | Semantics |
|---|---|
| `EVERY 1 DAY` | Aligned to the clock (midnight UTC); `EVERY 1 MONTH OFFSET 5 DAY 2 HOUR` = 6th day 02:00 |
| `AFTER 30 MINUTE` | Relative to completion of the previous refresh; no `OFFSET` |
| `RANDOMIZE FOR 1 HOUR` | Random jitter on the scheduled time — spread load across many views |
| `DEPENDS ON other_rmv` | Refresh starts only after the dependency's refresh for that slot completes; a non-refreshable table or typo yields status `MissingDependencies` and the view never runs |
| `EMPTY` | Skip the immediate first refresh at creation |
| `APPEND` | Snapshot/time-series pattern: keep every refresh's rows |

Use when the query needs a **JOIN**, a **full recomputation** (top-N, dedup by "latest wins", denormalization from dimension tables), or a **batch cadence** is enough; keep incremental MVs for single-table aggregates — they scale far better. `DEPENDS ON` chains replace an external DAG scheduler.

```sql
CREATE MATERIALIZED VIEW top_tenants_rmv
REFRESH EVERY 10 MINUTE RANDOMIZE FOR 1 MINUTE TO top_tenants AS
SELECT e.tenant_id, d.name, sum(e.amount) AS revenue
FROM events e JOIN dim_tenant d ON d.tenant_id = e.tenant_id
GROUP BY e.tenant_id, d.name ORDER BY revenue DESC LIMIT 100;

CREATE MATERIALIZED VIEW revenue_snapshots_rmv
REFRESH EVERY 1 HOUR DEPENDS ON top_tenants_rmv APPEND TO revenue_snapshots AS
SELECT now() AS ts, sum(revenue) AS total FROM top_tenants;
```

Operate:

```sql
SELECT database, view, status, last_success_time, last_refresh_time, next_refresh_time,
       exception, retry, progress, read_rows, written_rows
FROM system.view_refreshes;

SYSTEM REFRESH VIEW db.name;   -- out-of-schedule run now
SYSTEM WAIT VIEW db.name;      -- block until the running refresh finishes (use after CREATE)
SYSTEM STOP VIEW db.name;      -- disable schedule, cancel in-flight; START VIEW re-enables (no immediate run)
SYSTEM CANCEL VIEW db.name;    -- interrupt the current refresh on this replica
ALTER TABLE db.name MODIFY REFRESH EVERY 30 MINUTE SETTINGS refresh_retries = 5;  -- replaces ALL refresh parameters
```

`system.view_refreshes.status` includes `Scheduled`, `WaitingForDependencies`, `MissingDependencies`, `RunningOnAnotherReplica`; `exception` and `retry` show why a refresh is failing (retries: `refresh_retries` default 2, `-1` = infinite, backoff 100 ms → 60 s).

Caveats:
- Requires an Atomic or Replicated database.
- In a Replicated database (and ClickHouse Cloud) replicas coordinate through Keeper so **one replica runs each refresh**; `last_refresh_replica` tells which. `all_replicas = 1` disables coordination — only meaningful with `APPEND`, where each replica appends independently. `SYSTEM STOP REPLICATED VIEW` acts on all replicas.
- A refresh is a full query: it pays full scan/JOIN cost every interval and holds the memory of that query. Set `max_memory_usage`/`max_execution_time` via `SETTINGS` in the view definition or the owning user's profile.
- `EVERY`-aligned views with the same period refresh in lockstep — add `RANDOMIZE FOR` when many views share a cadence.
- `DEPENDS ON` gates the start only: with equal `EVERY` periods the dependent waits for the dependency's refresh of that slot, nothing else is synchronized.


## Sources

- https://www.tinybird.co/blog/what-i-learned-operating-clickhouse-part-ii
- https://www.tinybird.co/blog/what-are-materialized-views-and-why-do-they-matter-for-realtime
- https://clickhouse.com/docs/sql-reference/statements/create/view
- https://clickhouse.com/docs/sql-reference/aggregate-functions/reference/uniq
- https://clickhouse.com/docs/materialized-view/refreshable-materialized-view
- https://clickhouse.com/docs/sql-reference/statements/create/view
- https://clickhouse.com/docs/sql-reference/statements/alter/view
- https://clickhouse.com/docs/sql-reference/statements/system
- https://clickhouse.com/docs/operations/system-tables/view_refreshes
- https://clickhouse.com/docs/whats-new/changelog/2023 (23.12) and https://clickhouse.com/docs/whats-new/changelog/2024 (24.10)
