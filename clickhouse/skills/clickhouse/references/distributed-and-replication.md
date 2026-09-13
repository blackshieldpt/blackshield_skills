# Distributed Tables, Replication, Keeper, Backups and Cloud

## Cluster config (`remote_servers`)

```xml
<remote_servers>
  <cluster_2S_2R>
    <shard>
      <internal_replication>true</internal_replication>
      <replica><host>clickhouse-01</host><port>9000</port></replica>
      <replica><host>clickhouse-03</host><port>9000</port></replica>
    </shard>
    <shard>...</shard>
  </cluster_2S_2R>
</remote_servers>
<macros><shard>01</shard><replica>01</replica></macros>
<zookeeper>
  <node><host>clickhouse-keeper-01</host><port>9181</port></node>
  ...
</zookeeper>
```

- `internal_replication` defaults to **false**: a Distributed insert is written to *every* replica of the shard. Set it **true** whenever the local table is `Replicated*MergeTree` — write to one replica, let replication do the rest.
- Optional `<weight>` per shard: rows are distributed proportionally to weight.
- Replicated table path convention: `ReplicatedMergeTree('/clickhouse/tables/{shard}/table_name', '{replica}')`, macros substituted per host.

## Distributed engine

```sql
CREATE TABLE db.t_dist ON CLUSTER c AS db.t_local
ENGINE = Distributed(cluster, database, table[, sharding_key[, policy_name]])
```

- `sharding_key` is required for INSERTs through the Distributed table (unless `insert_distributed_one_random_shard`) and for `optimize_skip_unused_shards`. Choices: `rand()` (even spread, no locality), a column (`UserID`), or a hash (`intHash64(UserID)`) — hash the entity id when queries `JOIN`/`IN` on it, so local (non-GLOBAL) subqueries are correct.
- **Write to local tables directly** for full control and maximum throughput (one part per shard, no relay); write through the Distributed table when you want ClickHouse to route.
- Inserts through Distributed are **background** by default: files are queued on the initiator and shipped later.

| Setting | Default | Notes |
|---|---|---|
| `distributed_foreground_insert` (alias `insert_distributed_sync`) | 0 (Cloud: 1) | 1 = synchronous insert into every shard; the ack means the shard has the data |
| `distributed_background_insert_sleep_time_ms` / `_max_sleep_time_ms` | 100 / 30000 | Send interval; grows exponentially on errors |
| `distributed_background_insert_batch` | 0 | Send multiple queued files per operation |
| `distributed_background_insert_split_batch_on_failure` | 0 | Retry a failed batch file-by-file |
| `distributed_insert_skip_read_only_replicas` | 0 | Skip read-only replicas on INSERT (24.3+) |
| `prefer_localhost_replica` | 1 | Process locally when the initiator is a replica of the shard. Disable when using `max_parallel_replicas` without `parallel_replicas_custom_key` |
| `load_balancing` | `random` | Replica choice for reads: `random`, `nearest_hostname`, `hostname_levenshtein_distance`, `hostname_longest_common_prefix/suffix`, `in_order`, `first_or_random` (+ `load_balancing_first_offset`), `round_robin`. All pick among replicas with the fewest errors first |
| `skip_unavailable_shards` | 0 | 1 = return partial results silently. `skip_unavailable_shards_mode` (`unavailable_or_table_missing`) decides which errors count |
| `distributed_product_mode` | `deny` | Behaviour of a Distributed table inside a subquery of a distributed query: `deny` (throw), `local` (rewrite to local table), `global` (rewrite to GLOBAL), `allow` |
| `prefer_global_in_and_join` | 0 | Rewrite every `IN`/`JOIN` to `GLOBAL` |
| `max_replica_delay_for_distributed_queries` / `fallback_to_stale_replicas_for_distributed_queries` | | Lag tolerance when reading replicated tables through Distributed |

Engine-level table settings: `fsync_after_insert` (false), `fsync_directories` (false), `bytes_to_throw_insert` / `bytes_to_delay_insert` (0 = unlimited queued bytes), `max_delay_to_insert` (60 s), `flush_on_detach` (true).

### IN / JOIN on distributed tables

- Plain `IN (subquery)` / `JOIN`: the query is sent to each shard and the **subquery runs on each shard against local data only**. Correct only when the data for one key lives entirely on one shard (i.e. sharded by that key).
- `GLOBAL IN` / `GLOBAL JOIN`: the initiator runs the subquery once, stores the result in a temporary table in RAM, and ships it to every shard with the query. For `LEFT`/`INNER` the right side is materialized; for `RIGHT` the left side is. Keep the shipped set small; if you need `GLOBAL` on every query, reshard instead.
- `distributed_product_mode = deny` (default) rejects a Distributed table nested inside a non-GLOBAL subquery, which is usually the bug you wanted caught.

### Queue and DDL introspection

- `system.distribution_queue`: per Distributed table — `data_files`, `data_compressed_bytes`, `error_count`, `is_blocked`, `broken_data_files`, `last_exception`, `last_exception_time`. Growing `data_files` = a shard is unreachable or slower than the insert rate. `SYSTEM FLUSH DISTRIBUTED t` forces a synchronous send (throws if a node is down); `SYSTEM STOP/START DISTRIBUTED SENDS t` pauses/resumes.
- `ON CLUSTER c` works for `CREATE`, `DROP`, `ALTER`, `RENAME`, `SYSTEM`. The query is queued in Keeper under `distributed_ddl.path` (`/clickhouse/task_queue/ddl`) and each host eventually executes it, even hosts that are down at submission time (`task_max_lifetime` 604800 s, `max_tasks_in_queue` 1000, `pool_size` 1). Order is guaranteed per host only.
- `distributed_ddl_task_timeout` (180 s; 0 = async, negative = infinite) and `distributed_ddl_output_mode` (`throw`; Cloud `none_only_active`; `*_only_active` variants do not wait for inactive replicas) decide how long the client waits and what it sees.
- `system.distributed_ddl_queue`: `entry`, `initiator_host`, `cluster`, `query`, `host`, `port`, `status` (Inactive/Active/Finished/Removing/Unknown), `exception_code`, `exception_text`, `query_duration_ms`. A stuck `ON CLUSTER` is almost always one host stuck at `Inactive`.

### `Replicated` database engine

```sql
CREATE DATABASE r ENGINE = Replicated('/clickhouse/databases/r', '{shard}', '{replica}');
-- or on all hosts with implicit args:
CREATE DATABASE r ON CLUSTER default ENGINE = Replicated;
```

Replicates **metadata only**: DDL run on any replica is executed on the initiator first, then retried on every other replica until it succeeds — no `ON CLUSTER` needed. Data replication still depends on the tables being `ReplicatedMergeTree`. `ALTER ... FREEZE|ATTACH|FETCH|DROP|DETACH PARTITION|PART` are allowed but not replicated. Use `SYSTEM SYNC DATABASE REPLICA r` to wait for the DDL queue. If `zoo_path` contains `{uuid}`, give an explicit UUID or use `ON CLUSTER`.

## Insert deduplication

Replicated tables keep a hash per inserted block in Keeper. A retried insert of the **same block** (same size, same rows, same order) is silently dropped — this is what makes client retries safe. The hash covers the block exactly: change the batch boundaries, row order, or a single value and it is a new block.

| Setting | Default | Notes |
|---|---|---|
| `deduplicate_insert` (session, 26.2+) | `enable` | Master switch for sync and async inserts: `disable`, `enable`, `backward_compatible_choice` (defer to the two legacy flags below) |
| `insert_deduplicate` | 1 | Legacy switch; only honoured when `deduplicate_insert = backward_compatible_choice` |
| `async_insert_deduplicate` | 0 | Legacy switch for async inserts; same condition. Pre-26.2, async inserts are **not** deduplicated unless you set this to 1 |
| `deduplicate_insert_select` (25.12+) | `enable_when_possible` (since 26.1) | `INSERT ... SELECT` is deduplicated only when the SELECT is stable; `force_enable` throws on unstable selects |
| `insert_deduplication_token` | `''` | Your own key instead of the content hash. Tracked **per partition**: one insert touching N partitions carries the same token in each |
| `deduplicate_blocks_in_dependent_materialized_views` | 1 (since 26.2; was 0) | Also dedup the blocks an MV writes to its target. Without it a retried source insert is dropped at the source but **re-applied through every MV**, doubling aggregates |
| `replicated_deduplication_window` (table) | 10000 | Block hashes kept in Keeper per table |
| `replicated_deduplication_window_seconds` (table) | 3600 | Hashes older than this are dropped — retries after an hour are not deduplicated |
| `non_replicated_deduplication_window` (table) | 0 | Same window for plain MergeTree; off by default |
| `replicated_deduplication_window_for_async_inserts` / `_seconds_for_async_inserts` | 10000 / 604800 | Legacy async-insert hash window (`async_blocks` znode) |

Rules:
- Make batches deterministic (same rows, same order, same boundaries) or set `insert_deduplication_token` per batch from the producer (Kafka offset range, file name + chunk id).
- The window is 10k blocks / 1 h per table: a slow trickle of retries beyond that lands as duplicates. Size the window to your retry horizon.
- On pre-26.2 servers, set `deduplicate_blocks_in_dependent_materialized_views = 1` explicitly wherever MVs consume a replicated source and clients retry. The MV docs prescribe exactly this pair (`insert_deduplicate` + `deduplicate_blocks_in_dependent_materialized_views`) for exactly-once through view chains.
- Keeper retries inside the insert itself: `insert_keeper_max_retries` (20), backoff 100 ms → 10000 ms.

## Keeper

What lives there for a replicated table: the replica list and their sessions, the per-table log of actions (GET_PART, MERGE_PARTS, MUTATE_PART ...), block hashes for dedup, quorum state, mutation entries, the `ON CLUSTER` DDL queue, and `Replicated` database metadata. Roughly **ten znode writes per INSERT**; the docs size one Keeper ensemble at "several hundred INSERTs per second" across the whole cluster — another reason to batch. Keeper is **not** touched by `SELECT`.

- Run **3 (or 5) dedicated hosts**, odd count; 3 tolerates one failure. Never co-locate with ClickHouse servers.
- ClickHouse Keeper vs ZooKeeper: C++ + RAFT instead of Java + ZAB, linearizable writes, compatible client protocol (ClickHouse cannot tell them apart), **incompatible snapshot/log format** — migrate with `clickhouse-keeper-converter` during a maintenance window with ingestion stopped (`SYSTEM STOP MERGES` first). Runs standalone or embedded in `clickhouse-server`; port 9181 client, 9234 RAFT.
- Coordination defaults: `session_timeout_ms` 100000, `operation_timeout_ms` 10000, `heart_beat_interval_ms` 500, `dead_session_check_period_ms` 500, `snapshot_distance` 100000, `reserved_log_items` 100000, `force_sync` true (fsync each log write — keep it; only `local` disks honour it). Put `log_storage_path` on a quiet, fast disk; `compress_logs` cuts I/O; raise `max_requests_batch_size` for high part counts.
- Keeper memory soft limit = `max_memory_usage_soft_limit_ratio` (0.9) × RAM.

### Replication-queue limits (table settings)

| Setting | Default |
|---|---|
| `max_replicated_merges_in_queue` | 1000 |
| `max_replicated_mutations_in_queue` | 8 |
| `max_replicated_merges_with_ttl_in_queue` | 1 |
| `max_replicated_logs_to_keep` | 1000 — an inactive replica that falls further behind than this is **lost** and must be re-synced |
| `zookeeper_session_expiration_check_period` | 60 s |
| `replicated_can_become_leader` | 1 |

### Read-only / "Session expired"

`system.replicas.is_readonly` is set when the config has no Keeper section, when the Keeper session cannot be re-initialised, or **during** re-initialisation; `is_session_expired` is "basically the same". Causes, in order of frequency: Keeper quorum lost; Keeper latency/GC pauses exceeding the session timeout (slow disk under `force_sync`, co-located Keeper, overloaded network); the ClickHouse process stalled (swap, huge merge) and missed heartbeats; Keeper metadata for the table deleted. Effects: `INSERT` throws, `SELECT` still works. Recover in order: wait for reconnect (`readonly_start_time` tells you how long) → `SYSTEM RESTART REPLICA` → `SYSTEM RESTORE REPLICA` (metadata lost but parts present) → `SYSTEM DROP REPLICA` + recreate.

### What to watch

- `system.replicas`: `is_readonly`, `is_session_expired`, `absolute_delay`, `queue_size`, `inserts_in_queue` (large = wrong), `merges_in_queue`, `future_parts`, `parts_to_check`, `active_replicas` vs `total_replicas`, `lost_part_count` (only increases), `zookeeper_exception`, `last_queue_update_exception` (unparsable log entries after a version skew).
- `system.replication_queue`: `type`, `create_time`, `num_tries`, `last_exception`, `num_postponed`, `postpone_reason`, `is_currently_executing`, `source_replica`, `new_part_name`, `parts_to_merge`. Old `create_time` + high `num_tries` = a stuck entry; read `last_exception`.
- `system.zookeeper` needs `WHERE path = '...'` or `path IN (...)`:
  ```sql
  SELECT name, numChildren, dataLength, mtime
  FROM system.zookeeper WHERE path = '/clickhouse/tables/01/events/replicas';
  ```
- `SYSTEM SYNC REPLICA t [STRICT | LIGHTWEIGHT [FROM 'r1'] | PULL]` — `LIGHTWEIGHT` waits only for fetch/attach/drop entries, not merges.
- Keeper itself: 4-letter-words `mntr` (`zk_avg_latency`, `zk_outstanding_requests`, `zk_num_alive_connections`, `zk_server_state`), `srvr`, `stat`.

## Backup and restore

```sql
BACKUP  TABLE db.t [AS db.t_in_backup] | DICTIONARY db.d | DATABASE db | TEMPORARY TABLE t | VIEW v |
        ALL [EXCEPT {TABLES|DATABASES} ...]
        [ON CLUSTER 'c']
        TO   File('<path>') | Disk('<disk>', '<path>/') | S3('<endpoint>/<path>', '<key>', '<secret>') | AzureBlobStorage(...)
        [SETTINGS ...] [ASYNC]
RESTORE ... FROM ... [SETTINGS ...] [ASYNC]
```

Disk backups need explicit allow-listing:

```xml
<storage_configuration><disks><backups><type>local</type><path>/backups/</path></backups></disks></storage_configuration>
<backups><allowed_disk>backups</allowed_disk><allowed_path>/backups/</allowed_path></backups>
```

- Incremental: `BACKUP TABLE db.t TO S3(...'/inc') SETTINGS base_backup = S3(...'/base')`. Restore names only the incremental; the base is pulled in automatically. Chain incrementals off the last full backup, not off each other, unless you accept the whole chain as a single point of failure.
- `ASYNC` returns immediately; poll `system.backups` (`id`, `name`, `base_backup_name`, `status`, `error`, `start_time`, `end_time`, `num_files`, `total_size`, `compressed_size`, `files_read`). Statuses: `CREATING_BACKUP`, `BACKUP_CREATED`, `BACKUP_FAILED`, `RESTORING`, `RESTORED`, `RESTORE_FAILED`, `*_CANCELLED`. History in `system.backup_log`.
- Settings: `compression_method`/`compression_level`, `password` (zip only), `structure_only` (DDL, no data), `allow_non_empty_tables` (restore into a non-empty table — duplicates data), `restore_table_data`, `restore_access_entities`, `restore_functions` (all three default to `!structure_only`), `PARTITIONS '1', '4'` to scope a table backup. Server: `allow_concurrent_backups` / `allow_concurrent_restores` (true), `remove_backup_files_after_failure` (true).
- Included when named: tables, dictionaries, views/MVs (as `CREATE` statements), temporary tables, UDFs, and SQL-managed access entities (users, roles, row policies, profiles, quotas — exported to `accessXX.txt`, replayed on restore). Not included: XML-defined users, server config, Keeper state. `ALL` still lets you `EXCEPT TABLES system.query_log ...` to skip log tables.
- On replicated tables a backup taken on one replica is enough; restore then re-syncs the others. `ON CLUSTER` runs the backup on every host.
- Alternatives: `ALTER TABLE ... FREEZE PARTITION` (hardlink snapshot, copy elsewhere, `ATTACH PARTITION` to restore), filesystem snapshots on a replica excluded from serving, or a second consumer of the source stream. `clickhouse-backup` (github.com/Altinity/clickhouse-backup) automates the FREEZE/upload path — mature, and still common for pre-`BACKUP` tooling.
- Test restores. An untested backup is not a backup.

## Cloud vs OSS

- Cloud replaces `ReplicatedMergeTree` with **`SharedMergeTree`**: parts live on object storage, metadata in Keeper, replicas share one copy. `MergeTree`, `Replicated*MergeTree` and the `Replacing/Collapsing/Aggregating/Summing/VersionedCollapsing/Graphite` variants are rewritten to `Shared*` automatically; the stored engine shows as `SharedReplacingMergeTree('/clickhouse/tables/{uuid}/{shard}', '{replica}')`.
- **No sharding.** Hundreds of replicas per table; scale by adding replicas, not shards. Distributed tables and `remote_servers` are not part of the model; scale a single read with parallel replicas (`enable_parallel_replicas` = `allow_experimental_parallel_reading_from_replicas`, 0/1/2; `max_parallel_replicas` 1000; `cluster_for_parallel_replicas = 'default'`). Parallel replicas are off with `FINAL`, do not use projections, and hurt small or JOIN-heavy queries.
- **`ON CLUSTER` is unnecessary**: DDL is stored centrally (Shared Catalog / `Shared` database engine) and replicated to every replica, including ones that start later. `DROP` succeeds with replicas offline.
- Consistency: replication is asynchronous and leaderless; every insert is already a quorum write to shared storage, so `insert_quorum` / `insert_quorum_parallel` are no-ops. `select_sequential_consistency` still works but costs Keeper round-trips per SELECT — prefer `SYSTEM SYNC REPLICA LIGHTWEIGHT` when reading from a different replica than you wrote to.
- Introspection changes: `system.replication_queue` → `system.virtual_parts`; `system.replicated_fetches` → `system.shared_merge_tree_fetches`.
- Unsupported: self-managed Keeper; `Log`/`StripeLog`/`TinyLog`, `EmbeddedRocksDB`, `HDFS`, `Hive`, `JDBC`, `Redis`, `SQLite`, `TimeSeries`, `YTsaurus` engines; the `MySQL` *database* engine (table engine is fine); all experimental features (transactions, time-window functions, WASM/executable UDFs, NLP functions). UDFs run with default system settings. Most server settings are not user-configurable.
- Default differences: `max_parts_in_total` 10,000 (OSS 100,000); `max_table_size_to_drop` 1 TB (OSS 50 GB); `distributed_foreground_insert` 1; `distributed_ddl_output_mode` `none_only_active`; `distributed_ddl_entry_format_version` 6 (OSS 5); `async_insert_busy_timeout_max_ms` 1000 (OSS 200); `async_insert_max_data_size` 100 MiB (OSS 10 MiB); default compression `zstd` (OSS `lz4`).
- Backups: automatic daily backup with 24 h retention by default, as full + incremental chains; `BACKUP ... TO S3(...)` / GCS (S3 endpoint + HMAC) / `AzureBlobStorage(...)` works for bring-your-own-bucket, one distinct path (UUID) per backup or `BACKUP_ALREADY_EXISTS`. Only S3/GCS/Azure targets are documented for Cloud.

## Sources

- https://clickhouse.com/docs/engines/table-engines/special/distributed
- https://clickhouse.com/docs/architecture/horizontal-scaling
- https://clickhouse.com/docs/architecture/cluster-deployment
- https://clickhouse.com/docs/sql-reference/distributed-ddl
- https://clickhouse.com/docs/sql-reference/operators/in
- https://clickhouse.com/docs/sql-reference/statements/select/join
- https://clickhouse.com/docs/sql-reference/statements/system
- https://clickhouse.com/docs/sql-reference/statements/create/view
- https://clickhouse.com/docs/engines/database-engines/replicated
- https://clickhouse.com/docs/engines/table-engines/mergetree-family/replication
- https://clickhouse.com/docs/reference/settings/session-settings/{insert,deduplicate-insert,async-insert,distributed,distributed-background,distributed-ddl,load-balancing,prefer,skip-unavailable-shards,parallel-replicas,allow-experimental,max,cluster,insert-keeper,other}
- https://clickhouse.com/docs/reference/settings/merge-tree-settings/{replicated-deduplication-window,max-replicated,other}
- https://clickhouse.com/docs/reference/settings/server-settings/settings/{distributed-ddl,backups,zookeeper}
- https://clickhouse.com/docs/operations/system-tables/{distribution_queue,distributed_ddl_queue,replication_queue,replicas,zookeeper,backups}
- https://clickhouse.com/docs/guides/sre/keeper/clickhouse-keeper
- https://clickhouse.com/docs/operations/backup
- https://clickhouse.com/docs/concepts/features/backup-restore/{local-disk,s3-endpoint,restore-settings,alternative-methods}
- https://clickhouse.com/docs/cloud/reference/shared-merge-tree
- https://clickhouse.com/docs/products/cloud/features/infrastructure/{shared-catalog,parallel-replicas}
- https://clickhouse.com/docs/products/cloud/guides/cloud-compatibility
- https://clickhouse.com/docs/products/cloud/features/backups/overview
- https://clickhouse.com/docs/products/cloud/guides/backups/bring-your-own-backup/backup-restore-using-commands
