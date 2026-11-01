# Community Gotchas

Pitfalls collected from the ClickHouse blog, Altinity KB, PostHog, Cloudflare and GitHub issues. The most frequent ones are also summarised in `SKILL.md` §7; this file is the full list. Defaults were checked against `src/Core/Settings.cpp`, `src/Storages/MergeTree/MergeTreeSettings.cpp` and `FormatFactorySettings.h` on master (Sept 2026; the deduplication, MV-dedup, `max_insert_threads`, `join_algorithm`, FINAL and JSON/Variant/Dynamic settings re-checked against master on 2026-11-01, other rows not re-checked since) — older servers differ, run `SELECT name, value, changed FROM system.settings WHERE name = '...'` and `system.merge_tree_settings` before relying on a default. Source keys resolve in the table at the bottom.

## Query semantics

| Symptom | Cause | Fix | Source |
|---|---|---|---|
| `SELECT ... FINAL` slow even with a tight `WHERE` | FINAL reads **every ORDER BY column** to dedup, and merges parts across partitions | Fewer/lighter key columns (UInt64 ≈3x faster than UUID); `do_not_merge_across_partitions_select_final=1` (default 0; correct only when rows sharing a key never span partitions, otherwise FINAL returns duplicates; since 26.2 `enable_automatic_decision_for_merging_across_partitions_for_final=1` turns it on automatically when all partition-key columns are in the primary key) plus `OPTIMIZE ... PARTITION x FINAL` on closed partitions; skip indexes work under FINAL since 25.6. After a 26.3+ upgrade also check partition pruning: FINAL stops pruning partitions whose key columns are not in the sorting key (opt-out `defer_partition_pruning_after_final` from 26.5 only) | ALT-FINAL, DOC-RMT-GUIDE |
| `argMax`/`argMin` return a different row each run | Ties on the max value are **not deterministic**; NULLs in `arg` or `val` are skipped | Add a tiebreaker to the value: `argMax(x, (ts, id))`; `any()` has the same problem | DOC-ARGMAX |
| `count(col)` ≠ `count()` | `count(col)` counts non-NULL only; `count()`/`count(*)` count rows | Use `count()` for row counts; `optimize_functions_to_subcolumns=1` makes `count(nullable)` read only the null map | DOC-COUNT |
| `SELECT count() FROM t` went from ms to seconds | Trivial count comes from part metadata; it is disabled with `FINAL`, lightweight deletes present, or row policies | Expected; count on a filtered key range instead, or avoid `DELETE FROM` on that table | DOC-COUNT |
| `LEFT JOIN` shows `0`/`''` where NULL was expected; `sum()` over empty set is `0`, `avg()` is `nan` | `join_use_nulls=0` fills unmatched cells with type defaults; `aggregate_functions_null_for_empty=0` | Set the two settings to 1 for ANSI behaviour (result types become `Nullable`), or test with `isNull(r.id)` → `r.id = 0` explicitly | ALT-DEFAULTS |
| `IN (SELECT ...)` on a Distributed table is slow / floods the cluster | Plain `IN` runs the subquery on **every shard**, and if the subquery is also Distributed, each shard fans out again (N² requests) | `GLOBAL IN` / `GLOBAL JOIN` (subquery runs once on the initiator, result broadcast as a temp table) or `prefer_global_in_and_join=1`; `distributed_product_mode` defaults to `deny` and will error on the N² shape | DOC-IN |
| `LIMIT n BY key` returns wrong or varying rows | Without `ORDER BY` blocks are unordered (multi-threaded); the `OFFSET` form applies **per block** | Always pair `LIMIT BY` with `ORDER BY`; avoid `LIMIT off, n BY` | DOC-LIMITBY |
| `WITH TOTALS` numbers don't match a manual `sum()` / clients see an extra row | Totals ignore rows removed by `HAVING` (`totals_mode` default `after_having_exclusive` in source, docs still say `before_having`); key columns in the totals row are zeros/empty strings; in TSV/CSV the totals row arrives after a blank line, in JSON as a separate `totals` field | Set `totals_mode` explicitly; parse `totals` from JSON formats rather than TSV | DOC-GROUPBY, SRC-SETTINGS |
| `ORDER BY x` with no `LIMIT` OOMs or spills | Whole result set is sorted in memory; since 25.1 `max_bytes_ratio_before_external_sort=0.5` spills to disk instead (slow) | Add `LIMIT`; order by a **prefix of the sorting key** so `optimize_read_in_order` streams; for `ORDER BY non_key LIMIT n` a full scan is unavoidable | DOC-ORDERBY, CH13 |
| `... WHERE selective ORDER BY key LIMIT 1000000` is slower than without ORDER BY | `optimize_read_in_order` reads in key order single-stream until the LIMIT is filled — bad when the WHERE discards most rows | `SETTINGS optimize_read_in_order=0` for that query | DOC-ORDERBY |
| `GROUP BY ... LIMIT 1` still scans the whole table | LIMIT cannot short-circuit an aggregation | `optimize_aggregation_in_order=1` (default 0) with `GROUP BY` on a sorting-key prefix; measured 27.55M → 4.81M rows read | CH13 |
| Query runs far past `max_execution_time` | Limit is **projected**: after `timeout_before_checking_execution_speed` (10 s) ClickHouse estimates total time from current speed; the check only fires at designated points — never while merging aggregation states or during analysis | `timeout_before_checking_execution_speed=0` for wall-clock; `timeout_overflow_mode='break'` returns partial data (aggregates may return nothing); use `KILL QUERY` / client-side timeouts as the real guard | SRC-SETTINGS, DOC-COMPLEX |
| `max_result_rows`/`max_result_bytes` returned more than the limit | Checked per block; the last block is never cut | Treat as an approximate guard; use `LIMIT` for exact caps | SRC-SETTINGS |
| `toStartOfWeek(d)` gives Sunday, `toMonday(d)` gives Monday | `toStartOfWeek` default `mode=0` = week starts Sunday (MySQL convention) | `toStartOfWeek(d, 1)` or `toMonday(d)` for ISO weeks | SRC-WEEK |
| `WHERE dt = '2024-01-15 13:00:00'` matches but `WHERE dt = toDateTime('2024-01-15 13:00:00')` does not | A bare string literal is parsed in the **column's** timezone; one-argument `toDateTime()` uses the session/server timezone (`session_timezone`, default '' = server tz) | Always pass the tz: `toDateTime('...', 'Europe/Berlin')`; store `DateTime('UTC')` | ALT-TZ |
| `toDate(ts)`, `toStartOfDay(ts)`, `toString(ts)` differ between clickhouse-client, HTTP and a driver | Conversion happens server-side in the column tz, else `session_timezone`, else server tz; drivers may set their own session tz | Pin `session_timezone` per connection or pass tz arguments; DST zones produce 23/25-hour day buckets | ALT-TZ |
| `NULL IN (...)` rows vanish; `GROUP BY nullable` has a NULL group | `IN` yields 0 for NULL on either side; `GROUP BY` treats `NULL == NULL` | `nullIn()` / `isNotDistinctFrom()` when NULL should match | DOC-IN, DOC-GROUPBY |
| String `ORDER BY` / comparisons look wrong with accents | Strings compare **byte-wise**, no collation | `ORDER BY s COLLATE 'en'` or `lower()`/`normalizeUTF8NFC()` first | DOC-CMP |

## Engines and schema

| Symptom | Cause | Fix | Source |
|---|---|---|---|
| `SummingMergeTree` non-numeric column shows a random value; rows disappear | Non-key, non-summed columns get **an arbitrary value** from the merged group; a row whose summed columns are all 0 is **deleted** | Put every attribute column in `ORDER BY`; always read with `sum() ... GROUP BY` (merges are partial across parts) | DOC-SMT |
| `ReplacingMergeTree` inserted N rows, `count()` shows fewer immediately | `optimize_on_insert=1` (default) applies the engine's merge logic **within the insert block** | Expected; `SET optimize_on_insert=0` only for migrations where you need the raw rows | SRC-SETTINGS, POSTHOG-OPS |
| Same version, two rows, "wrong" one survives | On version ties the **last inserted** row wins; dedup key is `ORDER BY`, not `PRIMARY KEY` | Make the version strictly increasing; keep `ORDER BY` immutable and include the source PK | DOC-RMT |
| `is_deleted=1` rows never go away | Deleted rows are kept so a lower version can still arrive | `allow_experimental_replacing_merge_with_cleanup=1` + `OPTIMIZE TABLE t FINAL CLEANUP` (or `min_age_to_force_merge_seconds`); docs advise this only for tables with <10% deletes | DOC-RMT, DOC-RMT-GUIDE |
| Cascaded MV produces wrong aggregates from a Replacing/Summing/Collapsing table | The downstream MV receives the **inserted block**, not the merged state of the intermediate table | Aggregate in the second MV with `-State`/`-Merge`, or feed both MVs from the raw source | DOC-CASCADE |
| MV with `JOIN` misses rows and slows inserts | An MV fires only for inserts into its **left-most** table; the right side is fully scanned on every insert block | Use a dictionary (`dictGet`) instead of a JOIN in MVs; never join two streaming tables | ALT-MV |
| MV `GROUP BY` totals are wrong until merges | MV aggregates **per inserted block**; the target's engine finishes the job at merge time | Target must be `AggregatingMergeTree`/`SummingMergeTree` with `ORDER BY` = MV `GROUP BY`; read with `-Merge`/`sum()` + `GROUP BY` | ALT-MV, CH13 |
| Adding many MVs made inserts serial and slow | MVs execute sequentially, in alphabetical order, per insert | `parallel_view_processing=1` (default 0); consolidate views (>50 per table is a smell) | CH13, SRC-SETTINGS |
| MV target missing rows after `ALTER TABLE ... DELETE`/`DROP PARTITION` on the source | Incremental MVs are insert triggers only; mutations, merges and partition drops never propagate | Apply the same mutation to the target, or use a refreshable MV | CH13, ALT-MV |
| `ALTER TABLE src DROP COLUMN c` fails; MV silently broken after `MODIFY COLUMN` | Columns referenced by an MV cannot be dropped; type changes on the source are not re-validated against the MV | Drop/recreate the MV around schema changes | DOC-ALTER-COL, ALT-MV |
| Detached MV reappears after restart; "view exists" errors | `DETACH` without `PERMANENTLY` is undone on restart; a detached view is absent from `system.tables` but `SHOW CREATE` still works | `DETACH TABLE mv PERMANENTLY` | POSTHOG-OPS |
| `Nullable` column rejected in `ORDER BY`, or `IS NULL` on a key column is slow | Nullable keys need `allow_nullable_key=1`; Altinity: "NOT RECOMMENDED for general use"; `IS NULL` cannot use the index (#18728) | Use a sentinel value instead of NULL in key columns | ALT-NULLKEY |
| `Buffer` table lost rows; retries duplicated data | Buffer is RAM-only (lost on crash/OOM/`kill -9`, flushed only on SIGTERM); it re-batches rows so block-hash insert dedup on a Replicated target no longer works; `FINAL`/`SAMPLE` bypass the buffer | Prefer `async_insert`; if kept, `num_layers=1` for ordering and `SYSTEM FLUSH` before restarts | DOC-BUFFER |
| `Memory` table empty after restart | By design; `max_bytes_to_keep` makes it a ring buffer that can temporarily exceed the cap | Only for tests / hot lookups; not a queue | DOC-MEMORY |
| Lightweight `DELETE FROM` throws on a table with projections | `lightweight_mutation_projection_mode` defaults to `throw` | `drop` (drops projections) or `rebuild` (rebuilds them) — both cost | SRC-MTS |

## Ingestion

| Symptom | Cause | Fix | Source |
|---|---|---|---|
| Retried insert "succeeded" but rows are missing | Replicated tables dedup identical blocks silently (`replicated_deduplication_window` 10000 blocks since 25.9 — 1000 in 23.11-25.8, 100 before; `_seconds` 3600 since 25.10, one week before); non-replicated needs `non_replicated_deduplication_window>0` | Intended for retries; set `insert_deduplication_token` for explicit control; confirm with `system.part_log` | ALT-DEDUP, SRC-MTS |
| Re-inserting data after `ALTER TABLE ... DELETE` silently drops it | Mutations do **not** clear dedup hashes in Keeper; only `TRUNCATE`/`DROP PARTITION` do | Change the block (token) or drop the partition | ALT-DEDUP |
| Retry with `now()`/`rand()`/DEFAULT columns is *not* deduplicated | Defaults and functions are evaluated **before** hashing, so each retry hashes differently | Send explicit values or `insert_deduplication_token` | ALT-DEDUP |
| Retried insert is correct in the source table but the MV target is doubled (24.7-25.12) or missing rows (≤24.6) | Before 26.2 `deduplicate_blocks_in_dependent_materialized_views` defaults to 0 and MVs never dedup a retry: on 24.7-25.12 a retry deduplicated at the source is still pushed through every MV, doubling its aggregates; on ≤24.6 it is skipped by every MV, so rows the failed first attempt never pushed through an MV stay lost (1 since 26.2: each MV target dedups on its own). `async_insert` dedup is off unless `async_insert_deduplicate=1` (superseded by `deduplicate_insert`, default `enable`, since 26.2) | Set it to 1 on pre-26.2 servers (it throws with `async_insert=1` on 24.3-25.12); keep MV `SELECT`s deterministic | SRC-SETTINGS, ALT-DEDUP |
| `Too many partitions for single INSERT block (more than 100)` | `max_partitions_per_insert_block=100` | Batch per partition (raising the limit re-creates the `TOO_MANY_PARTS` problem) | SRC-SETTINGS |
| Part count in `system.parts` is far above `parts_to_throw_insert` yet inserts work | Inactive parts stay for `old_parts_lifetime` (480 s); thresholds count `active=1` only (`inactive_parts_to_throw_insert` is a separate limit, default 0 = off) | `WHERE active` in every `system.parts` query | SRC-MTS, DOC-PARTS |
| Rows inserted into a `Distributed` table never reach shards | Async by default: rows are spooled as `.bin` files on the initiator and pushed in background; a rough restart loses the spool; failed sends stall silently | Monitor `system.distribution_queue` (`error_count`, `last_exception`); `fsync_after_insert`/`fsync_directories` on the Distributed table; or `distributed_foreground_insert=1` / insert into local tables directly | DOC-DIST, ALT-DIST |
| Kafka rows lost without error after a node crash | Offset is committed after the block is pushed to MVs, before the part is fsynced; a lost page cache drops the rows | `fsync_after_insert=1`, `fsync_part_directory=1` on the target MergeTree; experimental Keeper-stored offsets (`kafka_keeper_path`) | DOC-KAFKA |
| Kafka duplicates after a broker outage | Two commits (ClickHouse part, then Kafka offset) → at-least-once only | Dedup downstream (`ReplacingMergeTree`, `insert_deduplication_token`) | ALT-KAFKA |
| One bad message stalls the Kafka consumer | Default `kafka_handle_error_mode='default'` throws; `kafka_skip_broken_messages` is format-dependent | `kafka_handle_error_mode='stream'` and route `_error`/`_raw_message` to a dead-letter table via a second MV, or `'dead_letter_queue'` | DOC-KAFKA, ALT-KAFKA |
| Kafka table silently ate messages when someone ran `SELECT` on it | Direct `SELECT` from Kafka/RabbitMQ/NATS/FileLog/S3Queue tables is blocked since 21.12 and always refused while MVs are attached; with `stream_like_engine_allow_direct_select=1` and no MV it consumes — committing on select is off (`kafka_commit_on_select=false`) but the rows are still gone from that read | Never query the Kafka table; use MVs | DOC-KAFKA, POSTHOG-INGEST |
| Multiple MVs on one Kafka table, target tables disagree | Inserts into several MVs are not atomic; a failure mid-way commits some | One MV to a `Null`/raw table, fan out from there | ALT-KAFKA |
| Extra Kafka consumers do nothing | `kafka_num_consumers` > topic partitions → idle consumers; also capped by physical cores | Consumers ≤ partitions; `kafka_thread_per_consumer=1` for parallel flushes | DOC-KAFKA |
| JSON field with a typo silently ignored; `null` became `0`/`''` | `input_format_skip_unknown_fields=1`, `input_format_null_as_default=1`, `input_format_defaults_for_omitted_fields=1` are all **on by default** | Set them to 0 in the ingest profile to fail loudly; `date_time_input_format` is `best_effort` since 26.5 (`basic` before) so `2024-01-01T00:00:00Z` parses — check yours | SRC-FFS |
| `Too many parts` while ingestion was "paused" via `SYSTEM STOP MERGES` | Inserts continued; merges did not | Stop inserts before stopping merges; `SYSTEM START MERGES` promptly | POSTHOG-OPS |

## DDL, mutations, TTL

| Symptom | Cause | Fix | Source |
|---|---|---|---|
| `ALTER ... UPDATE/DELETE` returns instantly, data unchanged | `mutations_sync=0`; `ALTER` on replicated tables is queued and async | `mutations_sync=2` (all replicas) or poll `system.mutations`; status is **per replica** — query `clusterAllReplicas(c, system.mutations)` | ALT-MUT, SRC-SETTINGS |
| Mutation with `WHERE id IN (SELECT ... FROM big)` OOMs | Parts are mutated in parallel; each task builds its own hash set | Use a dictionary / `dictHas()`; cap with `max_replicated_mutations_in_queue` (8) | ALT-MUT |
| `KILL MUTATION` left the table half-updated; `is_killed=1, is_done=0` for hours | Kill is not a rollback; a killed mutation can sit behind another long one | Expect partial state; re-issue a corrective mutation; kill blockers first | ALT-MUT, POSTHOG-OPS |
| `MODIFY COLUMN ... Enum8(...)` stuck, column unreadable (`Unknown element 'x' for type Enum8`) | The enum omits a value present in the data; mutation fails, metadata already changed | `KILL MUTATION`, `MODIFY COLUMN` back to `String`, re-run with the full value list; check `system.parts_columns` | ALT-ENUM |
| New `DEFAULT now()` / `MATERIALIZED` column has different values on old rows each query | `ADD COLUMN` is metadata-only; old parts compute the expression at read time until merged/materialized | `ALTER TABLE t MATERIALIZE COLUMN c` (a mutation); sort-key columns cannot be materialized; on ≥24.2 `MATERIALIZE COLUMN` keeps explicitly-inserted values for DEFAULT columns | DOC-ALTER-COL, ALT-ALTERS |
| `MODIFY TTL` triggered a full-table rewrite | `materialize_ttl_after_modify=1` materializes the new TTL over every part | `SET materialize_ttl_after_modify=0; ALTER ... MODIFY TTL ...; ALTER ... MATERIALIZE TTL IN PARTITION p` one partition at a time | ALT-TTL |
| Expired rows still on disk hours later | TTL runs only during merges; `merge_with_ttl_timeout` = 14400 s (4 h) minimum between TTL merges per table | Lower `merge_with_ttl_timeout` **and** set `ttl_only_drop_parts=1` (default 0) so whole parts are dropped instead of rewritten; partition at the TTL granularity | DOC-TTL, ALT-DEFAULTS |
| `OPTIMIZE TABLE t FINAL` as a routine job | Rewrites every part in every partition (≈3 h per TB) and re-optimizes single-part partitions; returns success even if nothing merged; cannot fix `Too many parts` | Use `OPTIMIZE ... PARTITION p FINAL` on closed partitions only; `optimize_throw_if_noop=1` to see no-ops; `optimize_skip_merged_partitions=1` | ALT-OPT, DOC-OPT |
| Recreated replicated table, replica sync explodes / stale queue entries | Reused Keeper path with leftovers; orphaned entries can make mutations hang "for months" (`parts_to_do` lists missing parts) | Never reuse a `zoo_path`; `SYSTEM DROP REPLICA` before recreating; use `{uuid}` in the path | POSTHOG-OPS |
| `system.query_log` / `part_log` / `metric_log` filling the disk | Log tables have no TTL by default | Add `<ttl>` in `config.xml` (`event_date + INTERVAL 30 DAY DELETE`) with `ttl_only_drop_parts=1`; disable `query_thread_log`/`metric_log` if you have external monitoring | ALT-SETTINGS |

## Interfaces

| Symptom | Cause | Fix | Source |
|---|---|---|---|
| HTTP 200 but the body ends with an exception | Headers are sent once streaming starts; errors arrive mid-stream | `wait_end_of_query=1` (+ `http_response_buffer_size`) to hold headers until completion; `http_write_exception_in_output_format=1` for JSON/XML; always check `X-ClickHouse-Exception-Code` | DOC-HTTP |
| `SET max_threads=1` over HTTP has no effect | HTTP is stateless; `SET` needs `session_id=...` on every request (60 s idle timeout, one query at a time per session) | Use `?max_threads=1` URL params or `SETTINGS` in the query; the `SETTINGS` clause is query-scoped in any interface | DOC-HTTP, DOC-QLEVEL |
| `Syntax error` when the query is split between URL and POST body; 414 on big queries | A line feed is inserted between the two parts; URL is capped at 1 MiB (`http_max_uri_size`) | Send the whole query in the body, or only `INSERT ... FORMAT x` in the URL with data in the body | DOC-HTTP |
| `clickhouse-client -q "A; B"` behaves differently across versions; inline `INSERT` swallows the next statement | Multi-query became the default in 24.8 (PR #63898, previously `--multiquery`/`-n`); in multi-query mode `INSERT ... VALUES` ends at `;` but other formats end at a **blank line** (`\n\n`) | Put data on a new line after `FORMAT`; `SETTINGS` must come **before** `FORMAT` (everything after is data); one statement per `-q` in scripts | DOC-INSERT, GH-63898 |
| Driver says "Multi-statements are not allowed" on a query ending with `;` | Some clients (clickhouse-connect, Redash) reject trailing semicolons | Strip the `;` | GH-CC-376 |
| UInt64 arrives as a string in JSON (or as a truncated number) | `output_format_json_quote_64bit_integers` quotes big ints for JS safety — default 1 until 25.8, **0 since 25.8** (the setting description still says "quoted by default") | Set it explicitly per query | SRC-FFS |
| `allow_experimental_*` feature works locally, refused in Cloud/prod | Experimental features need the flag per session/profile; Cloud requires support to enable | Do not build on experimental features; `beta` features are supported, `experimental` are not | CH13 |

## Tutorials and references worth reading

| Link | Good for |
|---|---|
| [Getting started? 13 mistakes](https://clickhouse.com/blog/common-getting-started-issues-with-clickhouse) | The canonical first read: parts, keys, mutations, LIMIT, MV, memory |
| [Top 10 best practice tips](https://clickhouse.com/blog/10-best-practice-tips) | Numbers behind sorting key, types, partitioning, dictionaries vs joins |
| [A practical introduction to primary indexes](https://clickhouse.com/docs/guides/best-practices/sparse-primary-indexes) | Deep dive on granules, marks and why key order matters |
| [ReplacingMergeTree guide](https://clickhouse.com/docs/guides/replacing-merge-tree) | FINAL, `is_deleted`, partition design for CDC-style upserts |
| [Deduplicating inserts on retries](https://clickhouse.com/docs/guides/developer/deduplicating-inserts-on-retries) | Exactly how block dedup and MVs interact |
| [Cascading materialized views](https://clickhouse.com/docs/guides/developer/cascading-materialized-views) | Block-not-table semantics with a worked example |
| [Altinity KB: FINAL clause speed](https://kb.altinity.com/altinity-kb-queries-and-syntax/altinity-kb-final-clause-speed/) | Version-by-version FINAL settings |
| [Altinity KB: Materialized views](https://kb.altinity.com/altinity-kb-schema-design/materialized-views/) | Every MV limitation in one page |
| [Altinity KB: Insert deduplication](https://kb.altinity.com/altinity-kb-schema-design/insert_deduplication/) | Why retries vanish and how `ALTER DELETE` interacts |
| [Altinity KB: Mutations](https://kb.altinity.com/altinity-kb-queries-and-syntax/mutations/) | Diagnosing stuck/expensive mutations |
| [Altinity KB: Settings to adjust](https://kb.altinity.com/altinity-kb-setup-and-maintenance/altinity-kb-settings-to-adjust/) and [Defaults to reconsider](https://kb.altinity.com/altinity-kb-setup-and-maintenance/useful-setting-to-turn-on/) | Production checklist for self-managed clusters |
| [Altinity: hidden timezone rules of DateTime](https://altinity.com/blog/the-hidden-timezone-rules-of-datetime-in-clickhouse) | Reproducible examples for every tz surprise |
| [Altinity: Kafka engine FAQ](https://altinity.com/blog/clickhouse-kafka-engine-faq) | At-least-once semantics, sizing, error handling |
| [PostHog ClickHouse manual](https://posthog.com/handbook/engineering/clickhouse) | Real operations runbook: schema case studies, ingestion, ops incidents |
| [Cloudflare: HTTP analytics for 6M req/s](https://blog.cloudflare.com/http-analytics-for-6m-requests-per-second-using-clickhouse/) | SummingMergeTree + `sumMap` schema; `index_granularity` 32 on tiny aggregate tables gave -50% latency (2018 — conflicts with §2's ≥128 floor, measure) |
| [Uber: schema-agnostic log analytics](https://www.uber.com/blog/logging/) | Array-pair storage for arbitrary log fields at PB scale |
| [Jordi Villar: Every millisecond counts](https://jordivillar.com/blog/every-millisecond-counts) | Step-by-step 200x speedup of a `ReplacingMergeTree`+`FINAL` workload: key order, dropping JOINs, forced merges, hash-bucket partitioning |

## Source keys

| Key | URL |
|---|---|
| CH13 | https://clickhouse.com/blog/common-getting-started-issues-with-clickhouse |
| ALT-FINAL | https://kb.altinity.com/altinity-kb-queries-and-syntax/altinity-kb-final-clause-speed/ |
| ALT-TZ | https://altinity.com/blog/the-hidden-timezone-rules-of-datetime-in-clickhouse |
| ALT-DEFAULTS | https://kb.altinity.com/altinity-kb-setup-and-maintenance/useful-setting-to-turn-on/ |
| ALT-SETTINGS | https://kb.altinity.com/altinity-kb-setup-and-maintenance/altinity-kb-settings-to-adjust/ |
| ALT-MV | https://kb.altinity.com/altinity-kb-schema-design/materialized-views/ |
| ALT-DEDUP | https://kb.altinity.com/altinity-kb-schema-design/insert_deduplication/ |
| ALT-MUT | https://kb.altinity.com/altinity-kb-queries-and-syntax/mutations/ |
| ALT-ENUM | https://kb.altinity.com/altinity-kb-queries-and-syntax/altinity-kb-alter-modify-column-is-stuck-the-column-is-inaccessible/ |
| ALT-ALTERS | https://kb.altinity.com/altinity-kb-setup-and-maintenance/alters/ |
| ALT-TTL | https://kb.altinity.com/altinity-kb-queries-and-syntax/ttl/modify-ttl/ |
| ALT-OPT | https://kb.altinity.com/altinity-kb-queries-and-syntax/altinity-kb-optimize-vs-optimize-final/ |
| ALT-NULLKEY | https://kb.altinity.com/engines/mergetree-table-engine-family/altinity-kb-nulls-in-order-by/ , https://github.com/ClickHouse/ClickHouse/issues/18728 |
| ALT-KAFKA | https://altinity.com/blog/clickhouse-kafka-engine-faq |
| ALT-DIST | https://kb.altinity.com/altinity-kb-setup-and-maintenance/altinity-kb-data-migration/distributed-table-cluster/ |
| POSTHOG-OPS | https://posthog.com/handbook/engineering/clickhouse/operations |
| POSTHOG-INGEST | https://posthog.com/handbook/engineering/clickhouse/data-ingestion |
| DOC-ARGMAX | https://clickhouse.com/docs/sql-reference/aggregate-functions/reference/argmax |
| DOC-COUNT | https://clickhouse.com/docs/sql-reference/aggregate-functions/reference/count |
| DOC-IN | https://clickhouse.com/docs/sql-reference/operators/in |
| DOC-LIMITBY | https://clickhouse.com/docs/sql-reference/statements/select/limit-by |
| DOC-GROUPBY | https://clickhouse.com/docs/sql-reference/statements/select/group-by |
| DOC-ORDERBY | https://clickhouse.com/docs/sql-reference/statements/select/order-by |
| DOC-CMP | https://clickhouse.com/docs/sql-reference/functions/comparison-functions |
| DOC-COMPLEX | https://clickhouse.com/docs/operations/settings/query-complexity |
| DOC-SMT | https://clickhouse.com/docs/engines/table-engines/mergetree-family/summingmergetree |
| DOC-RMT | https://clickhouse.com/docs/engines/table-engines/mergetree-family/replacingmergetree |
| DOC-RMT-GUIDE | https://clickhouse.com/docs/guides/replacing-merge-tree |
| DOC-CASCADE | https://clickhouse.com/docs/guides/developer/cascading-materialized-views |
| DOC-ALTER-COL | https://clickhouse.com/docs/sql-reference/statements/alter/column |
| DOC-BUFFER | https://clickhouse.com/docs/engines/table-engines/special/buffer |
| DOC-MEMORY | https://clickhouse.com/docs/engines/table-engines/special/memory |
| DOC-DIST | https://clickhouse.com/docs/engines/table-engines/special/distributed |
| DOC-KAFKA | https://clickhouse.com/docs/engines/table-engines/integrations/kafka |
| DOC-PARTS | https://clickhouse.com/docs/knowledgebase/exception-too-many-parts |
| DOC-TTL | https://clickhouse.com/docs/guides/developer/ttl |
| DOC-OPT | https://clickhouse.com/docs/sql-reference/statements/optimize |
| DOC-HTTP | https://clickhouse.com/docs/interfaces/http |
| DOC-QLEVEL | https://clickhouse.com/docs/operations/settings/query-level |
| DOC-INSERT | https://clickhouse.com/docs/sql-reference/statements/insert-into |
| GH-63898 | https://github.com/ClickHouse/ClickHouse/pull/63898 |
| GH-CC-376 | https://github.com/ClickHouse/clickhouse-connect/issues/376 |
| SRC-SETTINGS | https://github.com/ClickHouse/ClickHouse/blob/master/src/Core/Settings.cpp |
| SRC-MTS | https://github.com/ClickHouse/ClickHouse/blob/master/src/Storages/MergeTree/MergeTreeSettings.cpp |
| SRC-FFS | https://github.com/ClickHouse/ClickHouse/blob/master/src/Core/FormatFactorySettings.h |
| SRC-WEEK | https://github.com/ClickHouse/ClickHouse/blob/master/src/Functions/toCustomWeek.cpp |
