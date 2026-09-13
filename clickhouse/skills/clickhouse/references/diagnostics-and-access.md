# Diagnostics and Access Control

## Part A — Query diagnostics

### EXPLAIN

| Variant | Shows | Use when |
|---|---|---|
| `EXPLAIN` / `EXPLAIN PLAN` | Plan steps; since 26.7 `explain_query_plan_default = 'pretty'` turns on the readable layout (`legacy` for the old one); options `header`, `description`, `indexes`, `projections`, `actions`, `json`, `sorting`, `distributed` | Which steps run, in what order |
| `EXPLAIN indexes = 1` | Per-index pruning: `Parts N/M`, `Granules N/M` | Is the primary key / partition / skip index actually pruning |
| `EXPLAIN PLAN actions = 1` | Every expression per step (`INPUT`, `FUNCTION`, `ALIAS`) | Where a function blocks index use or PREWHERE |
| `EXPLAIN PIPELINE` | Processors and parallelism (`× N`); `graph = 1` emits DOT | Thread count, `Resize`, spill/merge stages |
| `EXPLAIN ESTIMATE` | `parts`, `rows`, `marks` to read per table; MergeTree only | Cheap cost estimate before running |
| `EXPLAIN SYNTAX` | Rewritten query after AST optimization | See what the optimizer did to your SQL |
| `EXPLAIN ANALYZE` | Runs the query, annotates plan with time/rows/memory per stage | Actual vs planned; subject to quotas and limits |

Reading `indexes = 1`: each entry has `Type` (`Partition Min-Max`, `Partition`, `PrimaryKey`, `Skip`, `Statistics`), `Keys`, `Condition`, then `Parts: <after>/<before>` and `Granules: <after>/<before>`.

```
ReadFromMergeTree (default.t)
Indexes:
  MinMax       Keys: y   Condition: (y in [1, +inf))          Parts: 4/5  Granules: 11/12
  PrimaryKey   Keys: x, y Condition: and((x in [11, +inf)), (y in [1, +inf)))  Parts: 2/3  Granules: 6/10
```

Granules unchanged (`N/N`) on `PrimaryKey` means the filter is not on a sorting-key prefix — fix the key or the predicate, not the query.

### system.query_log

Rows are written per event; filter on `type`:

| `type` | Meaning |
|---|---|
| `QueryStart` | Successful start |
| `QueryFinish` | Successful end — use this for timing |
| `ExceptionBeforeStart` | Parse/permission/quota errors |
| `ExceptionWhileProcessing` | Failed during execution (OOM, timeouts) |

Columns that matter: `query_duration_ms`, `read_rows`, `read_bytes`, `written_rows`, `result_rows`, `memory_usage` (peak for the query), `peak_threads_usage`, `ProfileEvents` (`Map(String, UInt64)` of counters; names match `system.events`), `Settings` (only settings changed for the query), `exception_code`, `exception`, `query_id`, `initial_query_id` (parent in a distributed chain), `query_kind`, `tables`, `log_comment`, `client_name`, `http_user_agent`.

```sql
SELECT query_id, query_duration_ms, read_rows, formatReadableSize(read_bytes) AS rb,
       formatReadableSize(memory_usage) AS mem, ProfileEvents['FileOpen'] AS file_opens
FROM system.query_log
WHERE type = 'QueryFinish' AND event_time > now() - INTERVAL 1 HOUR
  AND log_comment = 'dashboard:top_tenants'
ORDER BY query_duration_ms DESC LIMIT 20;
```

Logging settings: `log_queries` (default 1), `log_queries_min_type` (default `QUERY_START`), `log_queries_min_query_duration_ms` (default 0), `log_queries_probability` (default 1), `log_query_views` (default 1), `log_query_threads` (default 0), `log_processors_profiles` (default 1). The table is buffered — `SYSTEM FLUSH LOGS` before reading recent rows.

### Tagging and killing queries

- `SETTINGS log_comment = 'svc:endpoint'` — free text, limited by `max_query_size` (default 262144); lands in `system.query_log.log_comment`. Tag every app query.
- `query_id` — set from the client (`clickhouse-client --query_id=...`, HTTP `?query_id=`) to correlate with `system.processes`, `system.trace_log`, `system.text_log`.
- Any setting can be passed to `clickhouse-client` as `--<setting>=<value>`; `--progress` (`tty` in interactive, `off` in batch; `err` sends to stderr), `--time`/`-t` prints wall time, `--format`/`-f` (default `TabSeparated`), `--stacktrace`.

```sql
SELECT query_id, user, elapsed, read_rows, formatReadableSize(peak_memory_usage) AS peak, query
FROM system.processes ORDER BY elapsed DESC;

KILL QUERY WHERE query_id = 'abc' ASYNC;           -- default; SYNC waits; TEST only lists
KILL QUERY ON CLUSTER c WHERE user = 'etl' SYNC;   -- use ON CLUSTER on clusters and Cloud
```

`is_cancelled = 1` in `system.processes` means the kill was requested but the query has not reached a cancellation point yet.

### Other logs

| Table | What | Enable / tune |
|---|---|---|
| `system.query_views_log` | Per-MV work inside an insert: `view_name`, `view_target`, `view_duration_ms`, `read_rows`, `written_rows`, `peak_memory_usage`, `status`, `exception_code`, `initial_query_id` | `query_views_log` server section + `log_query_views = 1` |
| `system.text_log` | Server log lines: `level` (Fatal=1 … Trace=8), `logger_name`, `message`, `query_id`, `thread_id` | `text_log` server section; `text_log.level` caps verbosity |
| `system.trace_log` | Sampled stacks: `trace_type` in `Real`, `CPU`, `Memory`, `MemorySample`, `MemoryPeak`, `ProfileEvent`, `JemallocSample`; `trace`, `query_id`, `size` | `query_profiler_real_time_period_ns` / `query_profiler_cpu_time_period_ns` (default 1000000000 = 1 s; Cloud real-time 3 s), `memory_profiler_sample_probability` (default 0), `memory_profiler_step` (default 4194304) |

`send_logs_level` (default `fatal`; values `test|trace|debug|information|warning|error|fatal|none`) streams server logs for your own query back to the client: `clickhouse-client --send_logs_level=trace` shows the index-selection and pipeline trace lines for that query without touching `text_log`.

Flame graph (needs `allow_introspection_functions = 1`, default 0):

```sql
SET query_profiler_cpu_time_period_ns = 10000000;  -- 10 ms sampling, then run the query
```
```bash
clickhouse client --allow_introspection_functions=1 -q \
 "SELECT arrayJoin(flameGraph(arrayReverse(trace))) FROM system.trace_log
  WHERE trace_type = 'CPU' AND query_id = 'xxx'" | flamegraph.pl > cpu.svg
```
Memory: `SET memory_profiler_sample_probability = 1, max_untracked_memory = 1;` then `flameGraph(trace, size)` with `trace_type = 'MemorySample'` and `flamegraph.pl --countname=bytes --color=mem`.

### Parts, merges, mutations, errors

```sql
-- part count and size per partition (the TOO_MANY_PARTS view)
SELECT database, table, partition, count() AS parts, sum(rows) AS rows,
       formatReadableSize(sum(bytes_on_disk)) AS on_disk,
       countIf(part_type = 'Compact') AS compact, max(level) AS max_level
FROM system.parts WHERE active
GROUP BY database, table, partition ORDER BY parts DESC LIMIT 20;

-- what is merging right now
SELECT database, table, elapsed, round(progress, 2) AS progress, num_parts, is_mutation,
       formatReadableSize(total_size_bytes_compressed) AS size,
       formatReadableSize(memory_usage) AS mem, merge_type, merge_algorithm
FROM system.merges ORDER BY elapsed DESC;

-- stuck mutations
SELECT database, table, mutation_id, command, create_time, parts_to_do, is_done,
       latest_failed_part, latest_fail_time, latest_fail_reason
FROM system.mutations WHERE NOT is_done ORDER BY create_time;
KILL MUTATION WHERE database = 'db' AND mutation_id = 'mutation_42.txt';  -- already-mutated parts are not rolled back

-- error counters since start (name, code, value, last_error_time, last_error_message, remote)
SELECT name, code, value, last_error_time, last_error_message
FROM system.errors WHERE value > 0 ORDER BY value DESC LIMIT 20;

-- settings that differ from defaults in this session/profile
SELECT name, value, default, changed, readonly, min, max FROM system.settings WHERE changed = 1;
```

Notes: `parts_to_do = 0` on a replicated table can still be `is_done = 0` while a long `INSERT` holds a block number. `system.errors` counters increment on some benign paths — alert on deltas of specific codes, not on any non-zero value. `system.settings.tier` (`Production`/`Beta`/`Experimental`) tells you whether a setting is safe to rely on.

---

## Part B — Access control and quotas

### Enabling SQL-driven access

`users.xml` users are static and cannot be altered by SQL. To manage everything in SQL, give one bootstrap user `<access_management>1</access_management>` (absent = 0; the shipped `users.xml` already sets it to 1 for `default`), plus `<named_collection_control>1</named_collection_control>` and `<show_named_collections_secrets>1</show_named_collections_secrets>` if it must be able to `GRANT ALL ON *.* WITH GRANT OPTION`. Entities are persisted under `access_control_path` (default `/var/lib/clickhouse/access/`). Five entity types: users, roles, row policies, settings profiles, quotas.

### `default` user hardening

`default` ships with an empty password, all privileges, and is used when a client sends no credentials. Sequence: with `access_management` on `default` (shipped default) → `CREATE USER admin ... ; GRANT ALL ON *.* TO admin WITH GRANT OPTION` → in `users.xml` set a `password_sha256_hex` for `default`, restrict `<networks>` (never leave `<ip>::/0</ip>` on an exposed host), assign `<profile>readonly</profile>` or strip its grants, and drop `access_management` from it.

### Users, roles, grants

```sql
CREATE USER analyst IDENTIFIED WITH sha256_password BY '...'   -- bcrypt_password is stronger, slower; 72-char cap
    HOST IP '10.0.0.0/8'                                       -- HOST IP | NAME | REGEXP | LIKE | LOCAL | ANY (default)
    VALID UNTIL '2026-12-31'                                   -- or VALID FOR INTERVAL 90 DAY
    DEFAULT DATABASE analytics
    GRANTEES NONE;                                             -- cannot re-grant its privileges

CREATE ROLE readers;
GRANT SELECT ON analytics.* TO readers;
GRANT SELECT(id, ts) ON analytics.pii TO readers;             -- column-level
GRANT readers TO analyst;                                      -- roles: WITH ADMIN OPTION lets the grantee re-grant the role
SET DEFAULT ROLE readers TO analyst;                           -- active at login; otherwise SET ROLE per session
GRANT SELECT ON db.t TO svc WITH GRANT OPTION;                 -- svc may pass this on
GRANT SELECT ON db.* TO svc WITH REPLACE OPTION;               -- replaces existing grants instead of appending
```

Privilege scopes: column → table → database (`db.*`) → global (`*.*`). Keyword groups: `ALL`, `NONE`, `SELECT`, `INSERT`, `ALTER`, `CREATE`, `DROP`, `SHOW`, `KILL QUERY`, `SYSTEM`, `INTROSPECTION`, `SOURCES`, `ACCESS MANAGEMENT`, `NAMED COLLECTION ADMIN`, `dictGet`. `GRANT CURRENT GRANTS ON *.* TO x` copies the caller's own grants.

### Settings profiles and constraints

```sql
CREATE SETTINGS PROFILE tenant_small
SETTINGS max_memory_usage = 4000000000 MIN 1000000000 MAX 8000000000,   -- user may move within [MIN, MAX]
         max_execution_time = 30 MAX 60,
         max_threads = 4 CONST,                                          -- CONST = READONLY: cannot change
         max_result_rows = 1000000 WRITABLE,
         readonly = 2 CHANGEABLE_IN_READONLY                             -- only with settings_constraints_replace_previous
TO readers;
```

Same clause shape (`SETTINGS var [= value] [MIN v] [MAX v] [CONST|READONLY|WRITABLE|CHANGEABLE_IN_READONLY]`) is accepted on `CREATE USER` and `CREATE ROLE`. Violations throw and leave the setting unchanged. XML equivalent lives under `<profiles><name><constraints>` with `<min>`, `<max>`, `<disallowed>`, `<readonly/>`, `<changeable_in_readonly/>`.

| `readonly` | Allowed |
|---|---|
| `0` (default) | Read, write, change settings |
| `1` | Read only; cannot change settings (except those marked `changeable_in_readonly`), nor `readonly`/`allow_ddl` |
| `2` | Read + change settings, except `readonly` itself; `SET`, `CREATE TEMPORARY TABLE` allowed |

HTTP `GET` forces `readonly = 1`; use `POST` to write. `allow_ddl` (default 1) separately blocks DDL.

### Multi-tenant isolation

Per user or profile: `max_memory_usage` (default 0 = unlimited; Cloud sets it from replica RAM) for one query, `max_memory_usage_for_user` (default 0) across the user's queries, `max_execution_time` (default 0; enforced only at block boundaries, `timeout_overflow_mode = throw|break`), `max_threads` (default `auto(N)` = cores), `max_concurrent_queries_for_user` (default 0), `max_rows_to_read` / `max_result_rows` (default 0). Put them in a profile assigned to the tenant role; use `CONST` so the tenant cannot raise them.

### Quotas

```sql
CREATE QUOTA tenant_q
KEYED BY user_name                                   -- or ip_address, forwarded_ip_address, client_key, normalized_query_hash, NOT KEYED
FOR INTERVAL 1 hour  MAX queries = 1000, errors = 50, result_rows = 100000000,
                         read_rows = 10000000000, execution_time = 3600,
FOR RANDOMIZED INTERVAL 1 day MAX read_bytes = 5000000000000
TO readers;
-- other limits: query_selects, query_inserts, result_bytes, written_bytes, failed_sequential_authentications; NO LIMITS; TRACKING ONLY
```

Quotas count per server (not cluster-wide) and reset on restart. `execution_time` is in seconds. Inspect with `SHOW QUOTA` and `system.quotas_usage` (`quota_name`, `quota_key`, `start_time`, `end_time`, `queries`/`max_queries`, `errors`/`max_errors`, `result_rows`, `read_rows`, `execution_time`/`max_execution_time`, ...); on Cloud wrap in `clusterAllReplicas`.

### Row policies

```sql
CREATE ROW POLICY tenant_rows ON analytics.events
    FOR SELECT USING tenant_id = currentUser()
    AS PERMISSIVE                                  -- default; permissive OR'd, restrictive AND'd
    TO readers;
CREATE ROW POLICY admin_all ON analytics.events USING 1 TO admin;
```

With `access_control_improvements.users_without_row_policies_can_read_rows` (shipped `true`) a user no policy names sees **every** row; with it `false` (old behaviour) they see none. Either way, give admins an explicit `USING 1` policy so intent does not depend on that flag. Policies apply where data is read: they do not filter a `Distributed` table on the initiator, only on the underlying tables.

### Introspection

| Table | Key columns |
|---|---|
| `system.users` | `name`, `auth_type`, `host_ip`, `host_names`, `host_names_regexp`, `host_names_like`, `default_roles_all/list/except`, `grantees_any/list/except`, `default_database`, `storage` |
| `system.grants` | `user_name`, `role_name`, `access_type`, `database`, `table`, `column`, `is_partial_revoke`, `grant_option` |
| `system.quotas_usage` | See Quotas above; `is_current = 1` marks the calling user |
| `system.settings` | `changed`, `readonly`, `min`, `max`, `default` — effective constraints for this session |

`SHOW GRANTS FOR user`, `SHOW CREATE USER user`, `SHOW ACCESS` for a full dump.

---

## Sources

- https://clickhouse.com/docs/sql-reference/statements/explain
- https://clickhouse.com/docs/operations/system-tables/query_log
- https://clickhouse.com/docs/operations/system-tables/processes
- https://clickhouse.com/docs/operations/system-tables/query_views_log
- https://clickhouse.com/docs/operations/system-tables/text_log
- https://clickhouse.com/docs/operations/system-tables/trace_log
- https://clickhouse.com/docs/sql-reference/aggregate-functions/reference/flame_graph
- https://clickhouse.com/docs/sql-reference/statements/kill
- https://clickhouse.com/docs/sql-reference/statements/system
- https://clickhouse.com/docs/concepts/features/interfaces/client
- https://clickhouse.com/docs/operations/system-tables/parts
- https://clickhouse.com/docs/operations/system-tables/merges
- https://clickhouse.com/docs/operations/system-tables/mutations
- https://clickhouse.com/docs/operations/system-tables/errors
- https://clickhouse.com/docs/operations/system-tables/settings
- https://clickhouse.com/docs/reference/settings/session-settings/{send-logs,log,log-queries,log-query,query-profiler,memory-profiler,max-threads,max,max-execution,max-result,max-rows,max-concurrent,max-memory-usage,allow,other}.md
- https://clickhouse.com/docs/operations/access-rights
- https://clickhouse.com/docs/operations/settings/settings-users
- https://clickhouse.com/docs/operations/settings/settings-profiles
- https://clickhouse.com/docs/operations/settings/constraints-on-settings
- https://clickhouse.com/docs/operations/settings/permissions-for-queries
- https://clickhouse.com/docs/operations/settings/query-complexity
- https://clickhouse.com/docs/operations/quotas
- https://clickhouse.com/docs/sql-reference/statements/create/user
- https://clickhouse.com/docs/sql-reference/statements/create/role
- https://clickhouse.com/docs/sql-reference/statements/grant
- https://clickhouse.com/docs/sql-reference/statements/create/settings-profile
- https://clickhouse.com/docs/sql-reference/statements/create/quota
- https://clickhouse.com/docs/sql-reference/statements/create/row-policy
- https://clickhouse.com/docs/operations/system-tables/users
- https://clickhouse.com/docs/operations/system-tables/grants
- https://clickhouse.com/docs/operations/system-tables/quotas_usage
