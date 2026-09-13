---
name: clickhouse
description: "Practices, tunables and gotchas for designing, querying and operating ClickHouse: MergeTree schema and sorting-key design, partitioning, data types and codecs, projections, dictionaries, schema evolution, query optimization (PREWHERE, JOINs, aggregate states, FINAL), EXPLAIN and system.query_log diagnostics, ingestion, insert deduplication, materialized views, merges and too-many-parts, mutations, Distributed tables, replication and Keeper, backup/restore, access control, Cloud vs OSS. Use when writing ClickHouse DDL/SQL, judging ClickHouse SQL for correctness or performance (including when a code review touches ClickHouse), tuning ingestion or query latency, designing materialized views, or debugging OOM, TOO_MANY_PARTS, read-only tables, stuck mutations, slow merges, or Keeper/ZooKeeper trouble. Skip for non-ClickHouse databases (Postgres, MySQL, Snowflake, Druid, Pinot)."
version: 1.1.2
---

# ClickHouse Skill

Operational and query-design guidance for ClickHouse. Rules here are ordered by impact: schema first, then queries, then ingestion, then operations.

Reference files, load when the task goes deep:

| File | Load when |
|---|---|
| `references/query-optimization.md` | Writing or tuning SELECTs, JOINs, aggregate states |
| `references/schema-and-types.md` | Projections, dictionaries, ALTERs / schema evolution, choosing data types |
| `references/ingestion-and-merges.md` | Inserts, batching, parts, merges, mutations, TTL |
| `references/materialized-views.md` | Creating, cascading or backfilling incremental MVs; refreshable MVs |
| `references/distributed-and-replication.md` | Distributed tables, sharding, insert dedup, Keeper, backup/restore, Cloud vs OSS |
| `references/diagnostics-and-access.md` | EXPLAIN, query_log, profiling, killing queries; users, roles, quotas, row policies |
| `references/operations.md` | Cluster ops, upgrades, monitoring, incidents |
| `references/community-gotchas.md` | Symptom-first pitfall tables from the ClickHouse blog, Altinity KB, PostHog, Cloudflare; tutorial links |

---

## 0. Live servers

Against a running ClickHouse, run freely: `SELECT` / `SHOW` / `EXPLAIN` / `DESCRIBE`,
query-scoped `SETTINGS`, session `SET` of query-level settings, `SYSTEM FLUSH LOGS`,
`SYSTEM SYNC REPLICA` / `SYSTEM SYNC DATABASE REPLICA` / `SYSTEM WAIT VIEW` (they only
wait), and `KILL ... TEST` (which only lists).
Anything else — `ALTER` (including `MODIFY SETTING`), `OPTIMIZE`, `KILL`,
`DROP`/`DETACH`/`ATTACH`, any other `SYSTEM ...`, `INSERT`/`DELETE`/`UPDATE`, server or
user config file edits (`config.xml`, `users.xml`), and state-changing CLI tools
(`clickhouse-keeper-converter`, `clickhouse-backup`) — print the exact statement or
command and the expected effect and wait for an explicit yes before executing. The
recovery commands in this skill are what to propose, not what to run unasked.

## 1. Non-negotiables

1. **The sorting key determines everything.** A bad `ORDER BY` costs 10-100x on reads. No query tuning recovers it.
2. **Read less data.** Filter first, project only the columns needed, aggregate last.
3. **Insert in big batches, rarely.** Each insert block writes one part *per partition it touches*. Frequent small inserts destroy read performance via part count.
4. **Never `SELECT *`** in production queries.
5. **Avoid heavyweight mutations** (`ALTER TABLE ... UPDATE/DELETE`) — they rewrite whole parts. Prefer, in order: partition-level `DROP PARTITION`; lightweight `DELETE FROM` (masks rows via `_row_exists`; wide parts hardlink untouched columns, compact parts are still rewritten) and lightweight `UPDATE ... SET` (patch parts; introduced 25.7, still **beta**, needs table settings `enable_block_number_column` and `enable_block_offset_column`); or `ReplacingMergeTree` + version column. `ReplacingMergeTree` deduplicates only at merge time, only within a partition, and on no guaranteed schedule, so exact reads need `FINAL`.
6. **Reads and writes are coupled.** Ingestion pressure shows up as query latency; benchmark with concurrent ingestion, not read-only.

---

## 2. Schema design

### Sorting key (`ORDER BY`)

- Derive it from the actual query filters, not from a notion of a "primary key". ClickHouse primary keys do **not** enforce uniqueness; the primary index is sparse — it stores the key value at the first row of each granule (default one mark per 8192 rows), which lets the reader skip whole granules.
- Order columns left-to-right by **filter relevance**, and prefer **low cardinality first**:
  ```sql
  ENGINE = MergeTree
  ORDER BY (metric_name, time)   -- low-cardinality first, then time
  ```
- Common shapes: timestamp first for time-range scans; device/user id first for per-entity session analysis; a tuple of the columns most queries filter on.
- Filtering on a sorting-key column vs. a non-key column is typically **1-2 ms vs. 5-10 ms** on the same data — and the gap widens with size.

### Partitioning

- Use a coarse time-based key (`toYYYYMM(ts)`, daily at most for high volume). Partitioning is **not** an index; it is a data-management unit. Exception: FINAL-dominated `ReplacingMergeTree` tables may partition by a hash bucket of the filter key (see `references/query-optimization.md`).
- **Wrong partition keys destabilize the whole cluster.** Keep total partitions in the low thousands at most.
- Longer partition intervals tolerate late-arriving data better.
- Align TTL boundaries with partition boundaries — otherwise TTL triggers storms of merges.

### Data types

- `LowCardinality(String)` for repeated string values (names, enums, regions, metric names). Efficiency is highest below ~10k distinct values; it only starts losing to a plain `String` above ~100k.
- `DateTime64(n)` only when sub-second precision is actually used; `DateTime` otherwise.
- Avoid `Nullable` where a sentinel works — it adds a separate null map column and blocks some optimizations.
- Prefer denormalization (embed `user_tier`, `user_region` in the event row) over joins. Cost: write amplification when attributes change.

### Codecs and compression

| Data | Codec |
|---|---|
| Timestamps, monotonic counters | `CODEC(Delta, ZSTD(1))`, `CODEC(DoubleDelta, ZSTD(1))` |
| Float time series | `CODEC(Gorilla, ZSTD(1))` |
| Hot, frequently scanned columns | `CODEC(LZ4)` |
| Cold / bulk columns | `CODEC(ZSTD(1))` or `CODEC(ZSTD(2))` |

`Delta`, `DoubleDelta` and `GCD` are **data preparation codecs and cannot be used stand-alone** — always chain a general-purpose codec after them, or the DDL is rejected.

The server default is `lz4` (self-managed) / `zstd` (Cloud). `ZSTD(1)`/`ZSTD(2)` is a good explicit speed/ratio balance; test per column, characteristics vary a lot.

### Skip indexes

- Skip indexes only help when the indexed column **correlates with the sorting key**. For an uncorrelated high-cardinality identifier, change the sorting key or add a projection instead — a skip index will not prune anything.
- `minmax` for loosely-sorted numeric/date columns; `set(N)` for columns with low cardinality *within* a granule range; `bloom_filter` for membership tests over discrete values.
- `tokenbf_v1` / `ngrambf_v1` are **deprecated**; use the dedicated text index for full-text style searches (GA in 26.2 — still experimental on older servers).
- Skip indexes are built when a part is written (insert or merge). They are **not** applied retroactively to pre-existing data — propose `ALTER TABLE ... MATERIALIZE INDEX` to backfill them (§0 applies).
- `index_granularity`: default 8192. **Never go below 128.** Low values help point lookups but risk server stability (mark cache pressure). Large values (e.g. 32000) suit wide scans.

### Projections and dictionaries

- **Projection** = hidden copy of the same rows inside each part with a different `ORDER BY` (or a `GROUP BY` pre-aggregate); picked automatically. Use for a second sorting key when there is no filter/JOIN/transform. Costs write amplification on every insert and merge; **not used with `FINAL`**; lightweight `DELETE` on a table with projections throws unless `lightweight_mutation_projection_mode` is `drop`/`rebuild`. Test with `force_optimize_projection=1`.
- **Dictionary** = in-memory key → attributes map (`CREATE DICTIONARY ... LAYOUT(HASHED_ARRAY()) LIFETIME(MIN 600 MAX 900)`) read with `dictGet`. Standard replacement for a small-right-side JOIN, including inside MVs. Held uncompressed **per replica**; check `bytes_allocated` in `system.dictionaries`; `dictGet` results are not indexed, so do not filter on them.

### Schema evolution

- Cheap (metadata): `ADD COLUMN`, `DROP COLUMN`, `RENAME COLUMN` (not key columns), `MODIFY COLUMN ... DEFAULT/CODEC/TTL`. Expensive (mutation, rewrites parts): `MODIFY COLUMN` type change, `MATERIALIZE COLUMN/INDEX/PROJECTION`, `UPDATE/DELETE`.
- `MODIFY ORDER BY` can only **append** columns added in the same `ALTER` without a `DEFAULT`; the partition key cannot be changed at all. Anything else is a new table + `INSERT ... SELECT`.
- `DEFAULT` (stored, insertable) vs `MATERIALIZED` (stored, computed at insert, hidden from `SELECT *`) vs `ALIAS` (computed at read, free) vs `EPHEMERAL` (input-only).

### Type rules

Smallest integer that fits; `Date`/`DateTime` unless sub-second is used; `Decimal` for money (`Float64` sums drift); `Enum` or `LowCardinality(String)` for closed sets; never a random `UUID` at the head of the sorting key (key order alone swings compression ~13x in the docs' example — key on tenant/time or a hash bucket first); `Map` lookups are linear scans, promote hot keys to columns; `JSON` type is production-ready since 25.3. Full table in `references/schema-and-types.md`.

---

## 3. Query rules (apply in this order)

1. Filter on **sorting-key columns**.
2. Apply secondary filters. ClickHouse already moves eligible `WHERE` conditions into `PREWHERE` automatically (`optimize_move_to_prewhere`, on by default, `*MergeTree` engines only); write `PREWHERE` by hand only to override the optimizer for a predicate over few small columns that filters most rows. **With `FINAL` the automatic move is off** (`optimize_move_to_prewhere_if_final` defaults to 0, since PREWHERE runs before FINAL and can skew results) — do not hand-write `PREWHERE` alongside `FINAL` without understanding that.
3. `IN` and other row-reducing logic.
4. `JOIN`, `GROUP BY`, window functions — **last**.

Additional rules:

- Project explicit columns. `SELECT *` on a wide table can be 4-20x the bytes read.
- Filter before `GROUP BY`; the row count entering the aggregation is the cost driver.
- JOINs over ~1M rows on the right side risk `MEMORY_LIMIT_EXCEEDED` on the default in-memory hash path. Fixes, in order: denormalize the join away; pre-filter the right table with a subquery keyed on the left table's ids; switch `join_algorithm` to a spilling algorithm (`grace_hash`, `partial_merge`, `full_sorting_merge`).
- Use `-State` / `-Merge` combinators for `AggregateFunction` columns, and apply `-Merge` **as late as possible** in the pipeline.
- Nested aggregates (`max(avg(x))`) are not supported — use a subquery.
- Rule of thumb: **~500 MB/s scan per machine**; a 32-core box sustains roughly **5 GB/s uncompressed** healthily.

See `references/query-optimization.md` for worked good/bad examples.

---

## 4. Key tunables

| Setting | Guidance |
|---|---|
| `max_threads` | Real-time low-latency queries: **1**. Analytical queries: 4-8, or up to core count on dedicated boxes. More threads at high QPS needs a lot of hardware. |
| `max_concurrent_queries` | Always set. This is the main overload guard; do not remove it. Server-setting default is 0 (unlimited); the shipped `config.xml` sets 1000. |
| `max_memory_usage` | Per-query cap. Raise deliberately for large `GROUP BY`/`JOIN`/window queries. |
| `max_bytes_before_external_group_by` / `max_bytes_ratio_before_external_group_by` | Spilling `GROUP BY` to disk. The byte setting defaults to 0, but since **25.1** the ratio setting defaults to **0.5**, so automatic spilling is **on** by default on modern builds (and Cloud sets the byte variant to half the replica memory). Spilling is slow — treat it as a symptom and fix the query rather than relying on it. |
| `max_insert_block_size` / `min_insert_block_size_rows` | Larger blocks → fewer, larger parts. 150k-10M rows depending on memory. |
| `max_insert_threads` | 8-16 for bulk loads. |
| `async_insert` | Buffers small inserts server-side into larger parts; enabled by default on recent versions (OSS default flipped to 1 in 26.2) — confirm with `system.settings`. With the default `wait_for_async_insert=1` the client waits for the flush, so an ack means durable; only `wait_for_async_insert=0` returns immediately and risks loss on crash. Flush triggers: `async_insert_busy_timeout_max_ms` (200 ms; 1000 on Cloud) and `async_insert_max_data_size` (10 MiB; 100 MiB on Cloud). |
| `background_pool_size` | Merge concurrency; raise when the merge queue backs up. |
| `max_bytes_to_merge_at_max_space_in_pool` | Caps part size. Too high → huge, slow, memory-hungry merges. |
| `insert_quorum` | Default **0** — quorum disabled, writes ack from one replica. Set >1 (or `auto`) for durability, at write-latency cost. |
| `max_partitions_to_read` | 100-1000 is reasonable for streaming workloads; a guard against fan-out queries. |
| `max_table_size_to_drop` | Largest table a `DROP` is allowed to remove; default 50 GB. **Lowering it increases protection**; raising it, and especially `0` (which means no restriction at all), removes it. |

---

## 5. Ingestion

- Batch to produce **one part per insert**. Batch **per partition** — an insert block spanning N partitions writes N parts, which is the fastest route to `TOO_MANY_PARTS`.
- Stagger flush times per partition so historical partitions stop receiving writes.
- **Pre-sort data before inserting.** Measured: 2.1M → 5.6M rows/s on a single node; 8.8M rows/s with `max_insert_threads=8`.
- Put a **queue/backpressure layer in front of ClickHouse** (Kafka or custom). Traffic spikes without it collapse ingestion.
- Retries are safe on `Replicated*MergeTree` **only** because identical blocks are deduplicated by hash (`replicated_deduplication_window` 10000 blocks / 3600 s). Keep batches deterministic or set `insert_deduplication_token`; on pre-26.2 servers also set `deduplicate_blocks_in_dependent_materialized_views=1` or a retried insert is dropped at the source and **re-applied through every MV**. Plain `MergeTree` has no dedup unless `non_replicated_deduplication_window>0`.
- Sharded clusters: write to the **local** tables directly when you control routing; inserts through a `Distributed` table are async by default (spooled on the initiator, watch `system.distribution_queue`), set `internal_replication=true` for replicated shards, and use `GLOBAL IN`/`GLOBAL JOIN` unless the data is sharded by the join key.
- Ingestion scales linearly with shards (~2.1M rows/s per shard in the referenced benchmark). To scale a *single* read across replicas use parallel replicas (`enable_parallel_replicas=1`, `max_parallel_replicas`, and `cluster_for_parallel_replicas` — `default` on Cloud) — **not** `clusterAllReplicas()`, which treats each replica as a shard and returns duplicated rows on a replicated table.
- Kafka engine: tune `kafka_num_consumers` (throughput vs. memory/connections) and `kafka_max_block_size` (larger = fewer parts, higher latency). Prefer Protobuf/Avro over `JSONEachRow` for speed.

---

## 6. Materialized views

- An incremental MV is an **insert trigger**: it runs on every insert block into the source table. It costs ingest latency, memory, and part generation on the target.
- Define the **target table first**, `CREATE MATERIALIZED VIEW ... TO target`.
- Use `AggregatingMergeTree` + `-State` combinators in the MV, `-Merge` at query time.
- **Avoid `POPULATE`** unless you are on ≥ 26.8 with an Atomic database and a MergeTree-family (or Memory) source, where it is atomic by default (`materialized_views_populate_atomically = 1`). Anywhere else — older servers, Ordinary DBs, view/Distributed/Merge/Buffer/Log sources, `CREATE OR REPLACE`, or when disabled — rows inserted during view creation can be missed or duplicated. It is unsupported in Replicated databases and ClickHouse Cloud.
- Backfill with `INSERT INTO ... SELECT` in bounded chunks; validate the MV query with `LIMIT 1000000` first.
- `uniqExactState` on a 200M+ cardinality column can kill a cluster at merge time. Use bounded-size states: `uniqState` (adaptive sampling) or `uniqCombinedState` / `uniqHLL12State` (HyperLogLog).
- MVs are a top cause of OOM and of data loss during ingest incidents. Monitor their memory and `system.query_views_log`.
- An incremental MV fires only for inserts into its **left-most** table; JOINs inside MVs rescan the right side per block — use `dictGet`. Mutations, merges and `DROP PARTITION` on the source never propagate.
- **Refreshable MVs** (`CREATE MATERIALIZED VIEW ... REFRESH EVERY 1 HOUR [APPEND] TO target AS SELECT ...`, GA 24.10) recompute periodically and swap atomically: use them for JOINs, dedup, or anything that needs the full table rather than the insert block. Inspect with `system.view_refreshes`, force with `SYSTEM REFRESH VIEW`.

See `references/materialized-views.md`.

---

## 7. Common gotchas → solutions

| Symptom | Cause | Fix |
|---|---|---|
| `TOO_MANY_PARTS` (thrown at `parts_to_throw_insert`, default 3000 per partition; inserts are already delayed from `parts_to_delay_insert`, default 1000) | Inserts across many partitions, or small frequent inserts | Batch per partition; larger insert blocks; `async_insert`; raise `background_pool_size`. Raising the thresholds themselves hides the problem and hurts reads |
| "Merges are processing significantly slower than inserts" | Merge throughput saturated | Increase `background_pool_size`, apply backpressure upstream, relax queue-size limits |
| `MEMORY_LIMIT_EXCEEDED` on JOIN | Right table too big | Pre-filter right side with `IN (SELECT id FROM left)`; reduce columns; chunk with `LIMIT` |
| OOM on ingest | Materialized view aggregation | Reduce MV cardinality, avoid `uniqExact*`, shrink insert blocks |
| Table in read-only mode | Keeper/ZooKeeper failure or replica startup delay | Alert on it; recover **in order**: wait for reconnect → `SYSTEM RESTART REPLICA` → `SYSTEM RESTORE REPLICA` → detach/attach → drop + recreate + resync (see `references/distributed-and-replication.md`) |
| Query got slower after upgrade | Changed setting defaults | Diff settings between versions; check `system.query_log` |
| Binary garbage in results | Reading an `AggregateFunction` column directly | Apply the `-Merge` combinator |
| Slow `ON CLUSTER` DDL | A replica is down | Set client timeouts; check DDL queue length |
| S3 cost/rate-limit spikes | Part-per-operation write pattern on tiered storage | Use compact parts; hot local SSD cache + S3 cold tier |
| `SELECT ... FINAL` slow | Reads every `ORDER BY` column, dedups every unmerged duplicate, merges across partitions | Lighter key; `min_age_to_force_merge_seconds` so old parts are already merged; `do_not_merge_across_partitions_select_final=1` (automatic since 26.2 when partition-key columns are in the primary key); for FINAL-dominated tables partition by a hash bucket of the filter key so FINAL parallelises per partition (see `references/query-optimization.md`) |
| `count()` dropped after `ALTER ... DELETE` and re-insert of the same rows | Mutations do not clear Keeper dedup hashes; the re-insert is silently deduplicated | Change the block (`insert_deduplication_token`) or `DROP PARTITION` |
| `SummingMergeTree` attribute column shows random values / rows vanish | Non-key, non-summed columns take an arbitrary value; all-zero rows are deleted | Put every attribute in `ORDER BY`; always read with `sum() ... GROUP BY` |
| Expired TTL rows still on disk | TTL runs during merges, `merge_with_ttl_timeout` 4 h minimum | Lower it; `ttl_only_drop_parts=1`; partition at TTL granularity |
| `OPTIMIZE TABLE FINAL` as a cron job | Rewrites every part in every partition | Only `OPTIMIZE ... PARTITION p FINAL` on closed partitions |
| `ALTER UPDATE/DELETE` "did nothing" | Mutations are async (`mutations_sync=0`) | `mutations_sync=2` or poll `system.mutations` on every replica |
| `toStartOfWeek` returns Sunday | mode 0 = Sunday start | `toStartOfWeek(d, 1)` / `toMonday` |
| DateTime literal compares differently by client | Bare string parsed in column tz, `toDateTime(s)` in session tz | Pass tz explicitly; store `DateTime('UTC')`; pin `session_timezone` |
| HTTP 200 with exception in body | Headers sent once streaming starts | `wait_end_of_query=1`; check `X-ClickHouse-Exception-Code` |

About sixty, symptom-first, in `references/community-gotchas.md`.

---

## 8. Operations

- Run **ZooKeeper/Keeper on separate machines** from ClickHouse nodes. Co-location cascades failures into read-only tables.
- Separate workloads: real-time queries, ad-hoc long queries, and backfills must not share replicas. Keep p99-sensitive replicas under ~40% load.
- Upgrades: add a replica on the new version → watch logs → read traffic → write traffic → rest of cluster. No DDL and no new features until the whole cluster is upgraded. Wait ~1 month after a release. Avoid experimental features.
- Test on a real cluster (≥2 nodes + Keeper), never a single instance — replication behavior differs.
- Monitor `system.query_log` (incl. `ProfileEvents`), `system.processes`, `system.part_log`, `system.merges`, `system.mutations`, `system.replicas` (`is_readonly`, `absolute_delay`, `queue_size`), `system.replication_queue`, `system.distribution_queue`, `system.dictionaries`.
- Diagnose a slow query with `EXPLAIN indexes = 1` first: `Granules N/N` on the `PrimaryKey` line means the filter is not on a sorting-key prefix. Tag app queries with `SETTINGS log_comment`, set `query_id` from the client, `KILL QUERY WHERE query_id = ...` (`ON CLUSTER` on clusters). `max_execution_time` is a projected estimate, not a wall clock — keep client timeouts.
- Multi-tenant isolation is done with settings profiles (`max_memory_usage`, `max_execution_time`, `max_threads` with `MIN`/`MAX`/`CONST` constraints), `CREATE QUOTA ... FOR INTERVAL 1 HOUR MAX queries = N, read_rows = M`, and `CREATE ROW POLICY`. SQL-driven access needs `access_management=1` on a bootstrap user (already set for `default` in the shipped `users.xml`); lock down `default`.
- Backups: `BACKUP TABLE|DATABASE|ALL ... TO S3(...) [SETTINGS base_backup = ...] [ASYNC]`, poll `system.backups`. One replica is enough for replicated tables. Test restores.
- Cloud: `SharedMergeTree`, no sharding (scale reads with parallel replicas), `ON CLUSTER` unnecessary, `insert_quorum` a no-op, most server settings locked, different defaults (`max_table_size_to_drop` 1 TB, `max_parts_in_total` 10000, `async_insert_*`).
- Alert on: read-only tables, ZooKeeper errors, concurrent-query limit breaches, replication lag, DDL queue length, merge queue depth, segfaults (and the query that caused them).

See `references/operations.md`.

---
