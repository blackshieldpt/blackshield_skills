# Query Optimization

## Order of operations

Structure every query so work shrinks the data as early as possible:

1. Filter on sorting-key columns (granule skipping).
2. Secondary filters. `PREWHERE` is applied automatically on `*MergeTree` tables (`optimize_move_to_prewhere`, on by default) — write it manually only to override the optimizer for a predicate over few small columns that filters most rows. Since **26.10**, `optimize_move_to_prewhere_if_final` defaults to `1`; only conditions that depend on the sorting key and are deterministic within the query are moved, so `FINAL` results are unaffected. Before 26.10 the default is `0`.
`compatibility` below 26.10 restores the old behavior. A profile or query `SETTINGS` clause can override the default; check `system.settings` (`name`, `value`, `changed`) for the effective value, and do not hand-write `PREWHERE` alongside `FINAL` without understanding that a manual PREWHERE still runs before FINAL where `apply_prewhere_after_final` is false; the automatic move only takes sorting-key, deterministic conditions.
3. `IN` / row-reducing logic.
4. `JOIN`, `GROUP BY`, window functions.

## Filter early

The timings below are Tinybird's measurements on their `nyc_taxi` dataset; the absolute numbers across examples come from different runs and table sizes, so read the ratios, not the values.

Bad — 30-60 ms, 139.26k rows, 10.31 MB:
```sql
SELECT * FROM nyc_taxi ORDER BY tpep_pickup_datetime ASC
```

Good — 1-10 ms, 24.58k rows, 1.82 MB (Tinybird's example used `SELECT *`; project the
columns you need in real queries — see below):
```sql
SELECT vendorid, tpep_pickup_datetime, trip_distance
FROM nyc_taxi
WHERE trip_distance > 10
  AND tpep_pickup_datetime >= '2017-01-31 14:00:00'
ORDER BY tpep_pickup_datetime ASC
```

## Use the sorting key

Bad — full scan on an unindexed column:
```sql
SELECT * FROM data_source_sorted_by_date WHERE amount > 30
```

Good — hits the sorting key:
```sql
SELECT id, date, amount FROM data_source_sorted_by_date
WHERE id = 135246 AND date > now() - INTERVAL 3 DAY
```

## Never SELECT *

Bad — 140-180 ms, 718.55 MB:
```sql
SELECT * FROM nyc_taxi ORDER BY tpep_dropoff_datetime
```

Good — 35-60 ms:
```sql
SELECT vendorid, tpep_pickup_datetime, trip_distance
FROM nyc_taxi ORDER BY tpep_dropoff_datetime
```

## JOINs

ClickHouse builds the right-hand side in memory. Joins over ~1M+ rows on the right risk `MEMORY_LIMIT_EXCEEDED`.

Bad:
```sql
SELECT l.id, r.response_id
FROM left_data_source AS l
INNER JOIN big_right_data_source AS r ON l.id = r.id
```

Good — pre-filter the right table by the left table's keys and project only needed columns:
```sql
SELECT l.id, r.response_id
FROM left_data_source AS l
INNER JOIN (
  SELECT id, response_id
  FROM big_right_data_source
  WHERE id IN (SELECT id FROM left_data_source)
) AS r ON l.id = r.id
```

(Avoid aliasing tables `left`/`right` — those collide with function and join keywords.)

Better still: denormalize so the join disappears.

If pre-filtering is not possible, switch the algorithm instead of raising the memory limit. `join_algorithm` defaults to `direct,parallel_hash,hash,ie_join` (all in-memory); `grace_hash`, `partial_merge` and `full_sorting_merge` spill to disk and trade latency for surviving a large right side.

## GROUP BY

Bad — 50-70 ms, 9.71M rows into the aggregation:
```sql
SELECT vendorid, pulocationid, count(*)
FROM nyc_taxi
GROUP BY vendorid, pulocationid
```

Good — 20-40 ms:
```sql
SELECT vendorid, pulocationid, count(*)
FROM nyc_taxi
WHERE vendorid < 10
GROUP BY vendorid, pulocationid
```

## Aggregate states

Columns of type `AggregateFunction(...)` hold binary intermediate state. Selecting them raw returns garbage.

```sql
SELECT result FROM my_datasource                          -- binary noise
SELECT countMerge(result) AS result FROM my_datasource    -- UInt64
```

Apply `-Merge` as late in the pipeline as possible; keep intermediate nodes on `-State`.

Nested aggregates are not supported:
```sql
SELECT max(avg(number)) FROM my_datasource     -- error
```
```sql
SELECT max(avg_number) AS number
FROM (SELECT avg(number) AS avg_number FROM numbers(10))
```

## FINAL-heavy tables (ReplacingMergeTree upserts)

Case study (Jordi Villar, "Every millisecond counts"): a `ReplacingMergeTree` event table read with `FINAL` went from 85 s / 1.96B rows scanned to ~400 ms / 95k rows. The steps, in order of payoff:

| Step | Change | Result |
|---|---|---|
| Key order | Promote the columns every query filters on (`client_id, event_type, toDate(ts), ...`) to the front of `ORDER BY` | 1.96B → 257M rows read |
| Kill the JOIN | Precompute derived metrics in the application and send them **in the event row** | 257M → 50M rows, 15.7 GB → 5.6 GB |
| Fewer columns | Replace 9 read columns with 1 precomputed column (38 bytes/row saved) | 2.46 s → 1.38 s |
| Flatten SQL | Replace 3-level nested parameterized views with a flat query rendered by the application (20 KB → a few KB of SQL) | 233 ms avg / 626 ms p99 |
| Merge harder | Stale duplicates (2x rows before merge) inflate every `FINAL` read | 1.8B → 920M rows on disk |

Settings that made `FINAL` cheap:

| Setting | Value used | Why |
|---|---|---|
| `min_age_to_force_merge_seconds` (table) | 86400 (default 0) | Merge any part range older than a day, so `FINAL` has almost nothing left to deduplicate |
| `max_bytes_to_merge_at_max_space_in_pool` (table) | 100 GB (default 150 GB) | Ceiling for those forced merges; trade merge memory/CPU for fewer parts |
| `PARTITION BY (client_id % 36)` instead of `toYYYYMM(ts)` | — | With `do_not_merge_across_partitions_select_final=1` (default 0; automatic since 26.2 when every partition-key column is in the primary key) `FINAL` dedups each partition **independently**; a hash-bucket partition on the filter key lets one query hit one bucket and lets 36 workers run without a cross-partition merge. Cost: 36 parts per insert, no time-based partition drops — only for FINAL-dominated tables |
| `split_parts_ranges_into_intersecting_and_non_intersecting_final`, `split_intersecting_parts_ranges_into_layers_final` | 0 (default 1) | Disabled for this workload after measuring; leave them on unless a `FINAL` query profiles worse with them |

Granule heuristic from the case study: a trailing key column pays off when each value of the prefix covers at least `index_granularity` (8192) rows — with `(client_id, event_type, toDate(ts))`, ≥ 8192 events per client, type and day. Below that its pruning is marginal (the sparse index still excludes granules via the low-cardinality predecessors, so it is not useless, just not worth reading the column for).

## Memory errors — checklist

- Too many columns selected → project fewer, or consolidate into narrower types.
- Cross join present → rewrite; test in `LIMIT 1000000` chunks.
- Huge `GROUP BY` → filter before grouping; check cardinality of the grouping keys.
- Spilling: `max_bytes_before_external_group_by` defaults to 0, but `max_bytes_ratio_before_external_group_by` defaults to 0.5 since 25.1, so modern servers spill automatically. A spilling query is a slow query — treat it as a signal to fix the query, not as the solution.

## Throughput expectations

- ~500 MB/s scanned per machine, on average.
- A 32-core node sustains ~5 GB/s uncompressed while healthy.
- `max_threads = 1` for real-time endpoints; 4-8 for concurrent analytical workloads; up to core count for isolated heavy queries.
- To spread one query across replicas use parallel replicas (`enable_parallel_replicas=1`, `max_parallel_replicas`, `cluster_for_parallel_replicas` — `default` on Cloud). `clusterAllReplicas()` is a debugging/comparison table function — it treats every replica as a shard and returns **duplicated rows** on a replicated table.
- Sub-20 ms latency requires warm caches — size it with the `mark_cache_size` server setting and watch `MarkCacheBytes` / `MarkCacheFiles` in `system.metrics`.
- Read-only benchmarks lie. Measure with concurrent ingestion running.

## Sources

- https://www.tinybird.co/docs/classic/work-with-data/query/sql-best-practices
- https://www.tinybird.co/blog/what-i-learned-operating-clickhouse-part-ii
- https://jordivillar.com/blog/every-millisecond-counts
- https://clickhouse.com/docs/sql-reference/statements/select/prewhere
- https://clickhouse.com/docs/operations/settings/settings
- https://clickhouse.com/docs/guides/replacing-merge-tree
- https://clickhouse.com/docs/deployment-guides/parallel-replicas
- https://clickhouse.com/docs/sql-reference/table-functions/cluster
