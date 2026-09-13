# Ingestion, Parts, Merges and Mutations

## The parts model

Every insert writes a new part. Background merges combine parts into larger ones. Two forces pull against each other:

- Fewer, larger parts → faster reads (fewer parts touched per query).
- Larger parts → merges cost more CPU, memory and time.

Reads and writes cannot be decoupled: a high part count penalizes query latency no matter how good the schema is, and merging more aggressively to fix it consumes CPU the queries wanted.

## Insert rules

- One insert block produces **one part per partition it touches**. Size batches so that is one part.
- **Batch per partition.** An insert spanning many partitions creates one part per partition — the fastest way to hit `TOO_MANY_PARTS`.
- Stagger flush timing per partition so historical partitions stop receiving writes entirely.
- **Pre-sort rows by the sorting key before inserting.** Measured on a single node: 2.1M rows/s unsorted → 5.6M rows/s sorted → 8.8M rows/s sorted with `max_insert_threads=8`.
- Retries must not duplicate. Duplicates silently corrupt materialized views and aggregate stats, and are painful to detect later.
- Put a buffer/backpressure layer in front (Kafka, or a custom queue). Traffic spikes without one collapse ingestion.

## Insert tunables

| Setting | Effect |
|---|---|
| `max_insert_block_size` | Size of blocks the **server** forms when it parses row formats (default 1048449 rows), e.g. over HTTP. For `INSERT ... SELECT` and MV writes the max is only enforced with `use_strict_insert_block_limits=1` (default 0 since 26.4); otherwise `min_insert_block_size_rows/bytes` govern block size. When `clickhouse-client` reads from a file it parses locally and the setting applies client-side; with the native protocol the client's own batching decides block size |
| `min_insert_block_size_rows` | 150,000 to 10,000,000 depending on memory budget |
| `max_insert_threads` | 8-16 for bulk loads |
| `async_insert` | Server-side buffering of small inserts into larger parts; default flipped to 1 in OSS 26.2 — verify on your version |
| `wait_for_async_insert` | Default 1: the client waits for the buffer flush, so an ack means the data is durable. Set to 0 for fire-and-forget throughput — that is where the crash-before-flush loss risk lives |
| `async_insert_busy_timeout_max_ms` | Max buffer wait before flush — 200 ms (1000 on Cloud). Paired with `async_insert_max_data_size` — 10 MiB (100 MiB on Cloud) |
| `insert_quorum` | Default **0** (disabled — one replica acks). Set >1 or `auto` for durability, at write-latency cost |

## Merge tunables

| Setting | Effect |
|---|---|
| `background_pool_size` | Concurrent merge threads — raise when the merge queue grows |
| `max_bytes_to_merge_at_max_space_in_pool` | Caps part size; too high yields slow, memory-hungry merges |

Monitor merge queue depth and merge memory. When merges cannot keep up, you have three levers: more merge threads, upstream backpressure, or relaxed queue-size limits — in that order of preference.

## TOO_MANY_PARTS

The error fires at `parts_to_throw_insert` (default 3000 active parts in a single partition); inserts are already artificially delayed from `parts_to_delay_insert` (default 1000). `max_parts_in_total` (default 100000) caps the table. Raising these thresholds buys time but pushes the cost onto every read — treat them as alarms, not tuning knobs.

Causes, in order of likelihood:
1. Inserting into many partitions at once → batch per partition.
2. Small frequent inserts → batch larger, or enable `async_insert`.
3. Materialized views generating parts on every insert → reduce MV count/cardinality.
4. Merge throughput saturated → raise `background_pool_size`, add hardware.

At sustained peak (50 nodes, 400-500M rows/s), load average reached 57.9 on 32-core machines — merge thread saturation is the real ceiling, not insert bandwidth.

## Mutations

`ALTER TABLE ... UPDATE / DELETE` rewrite entire parts and compete with merges for I/O and CPU. Avoid them in steady-state design.

Alternatives, cheapest first:
- `ALTER TABLE ... DROP PARTITION` for bulk removal — metadata-only.
- Lightweight `DELETE FROM`: still a mutation (`ALTER TABLE UPDATE _row_exists = 0` underneath) but masks rows through the hidden `_row_exists` column and hardlinks untouched columns in **wide** parts; compact parts are fully rewritten.
- Lightweight `UPDATE ... SET`: writes patch parts; introduced 25.7, still **beta**; requires table settings `enable_block_number_column` and `enable_block_offset_column`.
- `ReplacingMergeTree` with a version column: rows sharing the **`ORDER BY`** key (not `PRIMARY KEY`) are deduplicated at merge time, keeping the highest version. Caveats that matter: merges happen at an unpredictable time and only within a partition, so duplicates can persist indefinitely and rows with the same key in different partitions are never collapsed. Reads that must be exact need `FINAL` (or an aggregation that tolerates duplicates).
- `SummingMergeTree` / `AggregatingMergeTree` to fold rows during merges instead of updating them.

Track `system.mutations` — stuck mutations are a common silent failure.

## TTL

TTL moves and deletes data through merges. If TTL boundaries do not line up with partition boundaries, it triggers large numbers of merges. Align them.

## Kafka ingestion

- `kafka_num_consumers`: consumer threads — more throughput, more memory and connections.
- `kafka_max_block_size`: rows accumulated before flushing to the MergeTree table. Larger = fewer parts, higher latency.
- Formats: `JSONEachRow` is readable but slow; Protobuf/Avro are faster and compact but need schemas.
- A single server with Kafka ingestion can reach ~2M rows/s.

## Scaling shape

- Ingestion scales linearly with shards — roughly +2.1M rows/s per added shard in the reference benchmark, since writes need no coordination.
- Reads scale across replicas via parallel replicas (`enable_parallel_replicas=1`, `max_parallel_replicas`, `cluster_for_parallel_replicas`); `clusterAllReplicas()` is for debugging only and duplicates rows on replicated tables.
- Stable 1B rows/s required 90-100 nodes, not the 50 that hit that number as an unstable peak.

## Storage tiers

- Hot local NVMe/SSD cache + S3 cold tier. Pure S3 cannot deliver low latency.
- ClickHouse's part layout is optimized for local disks; on S3 it produces excessive write operations. Use **compact parts** to cut operation count and avoid rate limits.
- Zero-copy replication works but is buggy: watch for data loss, orphaned S3 objects, and risky part operations.

## Sources

- https://www.tinybird.co/blog/what-i-learned-operating-clickhouse
- https://www.tinybird.co/blog/clickhouse-streaming-analytics
- https://www.tinybird.co/blog/1b-rows-per-second-clickhouse
- https://clickhouse.com/docs/reference/settings/session-settings/async-insert
- https://clickhouse.com/docs/reference/settings/merge-tree-settings/other
- https://clickhouse.com/docs/sql-reference/statements/delete
- https://clickhouse.com/docs/sql-reference/statements/update
- https://clickhouse.com/docs/engines/table-engines/mergetree-family/replacingmergetree
- https://clickhouse.com/docs/engines/table-engines/integrations/kafka
