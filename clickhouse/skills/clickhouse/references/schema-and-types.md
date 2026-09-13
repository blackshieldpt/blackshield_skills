# Schema and Types

Projections, dictionaries, schema evolution and data-type choices. Facts below are from the ClickHouse docs as of 2026-09 (URLs in Sources).

## Projections

A projection is a hidden table stored **inside each part** of the parent: an anonymous MergeTree (or `AggregatingMergeTree` when the projection has `GROUP BY`) whose `ORDER BY` becomes its primary key. The optimizer picks it automatically when it answers the query cheaper. Two uses: a second sorting key for the same data, and pre-aggregation.

```sql
ALTER TABLE t ADD PROJECTION by_user (SELECT user_id, ts, url ORDER BY user_id, ts);
ALTER TABLE t MATERIALIZE PROJECTION by_user;        -- mutation: builds it for existing parts
ALTER TABLE t ADD PROJECTION daily (SELECT toDate(ts) AS d, count() GROUP BY d);
ALTER TABLE t DROP PROJECTION by_user;
ALTER TABLE t CLEAR PROJECTION by_user IN PARTITION 202609;
```

Rules: `SELECT <cols> [GROUP BY] [ORDER BY]` — **no `WHERE`, no JOIN**; `*MergeTree` only; `ALIAS` columns cannot appear in the projection `ORDER BY` (use `MATERIALIZED` or an inline expression). Since 25.5 a projection may store only `sorting key + _part_offset`, acting as a secondary index with little storage.

| Setting | Default | Meaning |
|---|---|---|
| `optimize_use_projections` | 1 | Let the optimizer use projections |
| `optimize_use_projection_filtering` | 1 | Use projections to filter parts even when they are not read |
| `force_optimize_projection` | 0 | Throw if no projection is used (test your DDL with this) |
| `force_optimize_projection_name` | `''` | Throw unless this projection is used |
| `preferred_optimize_projection_name` | `''` | Hint which projection to try first |
| `deduplicate_merge_projection_mode` (table) | `throw` | On non-classic engines (`Replacing`, `Aggregating`, …) and `OPTIMIZE DEDUPLICATE`: `throw` / `drop` / `rebuild` / `ignore` (may return wrong results) |
| `lightweight_mutation_projection_mode` (table) | `throw` | Lightweight `DELETE` on a table with projections: `throw` / `drop` (drop affected projection parts) / `rebuild` |

Check usage with `EXPLAIN projections = 1` or `system.query_log.projections`.

Projection vs. second table / MV:

| Prefer a projection | Prefer an MV + target table |
|---|---|
| Same rows, different `ORDER BY`, no filter, maintained automatically and transactionally with the part | Filtered / joined / transformed data; different TTL or partitioning; aggregation whose target you also query directly |
| Query pattern is `SELECT ... WHERE x = ? ORDER BY x` on a column not in the key | You need to backfill in chunks or drop/rebuild independently of the source |

Limitations and costs:

- **Not used with `FINAL`.** A `ReplacingMergeTree` read with `FINAL` never hits a projection.
- Every insert and merge writes the projection too — extra storage and write amplification. Measure insert throughput before and after.
- Projection TTL = parent TTL; partitioning = parent partitioning.
- Lightweight `DELETE` and non-trivial merges on deduplicating engines are **rejected by default** (`throw` above). Choose `drop` or `rebuild` deliberately; `rebuild` costs merge CPU, `drop` silently loses the projection for those parts until it is re-materialized.
- `ALTER TABLE ... UPDATE/DELETE` mutations rewrite the projection parts along with the parent — mutation cost scales with projection count.
- `MATERIALIZE PROJECTION` is a mutation: rewrites whole parts, asynchronous unless `mutations_sync` (default 0) is set to 1 (wait on this replica) or 2 (all replicas).

## Dictionaries

In-memory key → attributes map, refreshed on an interval, queried with `dictGet` — the standard replacement for a small-right-side JOIN.

```sql
CREATE DICTIONARY country_dict
(
    id   UInt64,
    name String,
    tier UInt8 DEFAULT 0
)
PRIMARY KEY id
SOURCE(CLICKHOUSE(host 'localhost' port 9000 user 'default' password '' db 'ref' table 'countries'))
LAYOUT(HASHED())
LIFETIME(MIN 600 MAX 900);
```

Full syntax: `CREATE [OR REPLACE] DICTIONARY [IF NOT EXISTS] [db.]name [ON CLUSTER c] (cols) PRIMARY KEY k1[, k2] SOURCE(...) LAYOUT(...) LIFETIME({MIN a MAX b | n}) [SETTINGS(...)] [COMMENT '...']`. `SOURCE(CLICKHOUSE(QUERY 'SELECT ...'))` also works.

Sources (`SOURCE(...)`):

| Source | Example |
|---|---|
| ClickHouse | `CLICKHOUSE(host 'h' port 9000 user 'u' password '' db 'd' table 't')` |
| MySQL | `MYSQL(host 'h' port 3306 user 'u' password '' db 'd' table 't')` |
| PostgreSQL | `POSTGRESQL(host 'h' port 5432 user 'u' password '' db 'd' table 't')` |
| HTTP | `HTTP(url 'http://h/data' format 'JSON')` |
| File | `FILE(path './user_files/x.tsv' format 'TabSeparated')` — must live under `user_files_path` |
| Executable | `EXECUTABLE(command 'cat /path' format 'TabSeparated')` |

Others: ODBC, MongoDB, Redis, Cassandra, Executable Pool, YAMLRegExpTree, Null.

Layouts (`LAYOUT(...)`):

| Layout | Key | Notes |
|---|---|---|
| `FLAT()` | `UInt64`, < `max_array_size` (default 500 000; `initial_array_size` 1024) | Fastest; array indexed by key — wastes memory on sparse ids |
| `HASHED()` | `UInt64` | Tens of millions of keys; `SHARDS` (1), `MAX_LOAD_FACTOR` (0.5) |
| `SPARSE_HASHED()` | `UInt64` | Less memory, more CPU |
| `HASHED_ARRAY()` | `UInt64` | Best memory/speed balance with many attributes — docs' recommended default |
| `COMPLEX_KEY_HASHED()` / `COMPLEX_KEY_HASHED_ARRAY()` | any tuple (strings, composite) | Same as above for non-`UInt64` keys |
| `RANGE_HASHED()` + `RANGE(MIN start MAX end)` | `UInt64` + range | Lookup by key and date: `dictGet(d, attr, id, date)`; `range_lookup_strategy` `min` (default) / `max`; `NULL` `range_max` = open |
| `CACHE(SIZE_IN_CELLS n)` / `COMPLEX_KEY_CACHE` | `UInt64` / tuple | Fixed-size LRU-style cache; misses fetch `WHERE id IN (...)` from source. Least efficient layout; needs ≥ 99% hit rate; **ClickHouse source not recommended** (random point reads) |
| `SSD_CACHE(...)` | `UInt64` | Cache with SSD-resident data, in-memory index |
| `DIRECT()` / `COMPLEX_KEY_DIRECT()` | `UInt64` / tuple | Nothing in memory; every lookup queries the source. Not for local files |
| `IP_TRIE()` | CIDR prefix | Longest-prefix IP lookups |

`LIFETIME`: `LIFETIME(MIN 600 MAX 900)` reloads at a uniformly random point in the window (spreads source load across replicas); `LIFETIME(0)` never reloads. While reloading, the old copy still serves reads; only the **initial** load blocks. Add `SETTINGS(invalidate_query = 'SELECT max(updated_at) FROM ...')` to skip a reload when the row is unchanged (ClickHouse/MySQL/PostgreSQL/ODBC sources); file sources check mtime. `SYSTEM RELOAD DICTIONARY name` forces it.

Functions:

```sql
dictGet('country_dict', 'name', toUInt64(country_id))           -- missing key → attribute DEFAULT
dictGet('country_dict', ('name', 'tier'), id)                    -- tuple of attributes
dictGetOrDefault('country_dict', 'name', id, 'unknown')
dictGetOrNull('country_dict', 'name', id)
dictHas('country_dict', id)                                      -- UInt8
dictGetHierarchy('org_dict', id)                                 -- HIERARCHICAL attribute
```

Instead of a JOIN:

```sql
-- Bad: right side hashed on every query
SELECT e.ts, c.name FROM events e JOIN countries c ON c.id = e.country_id WHERE e.ts > now() - 3600
-- Good: lookup only on rows that survived the filter
SELECT ts, dictGet('country_dict', 'name', country_id) AS name FROM events WHERE ts > now() - 3600
```

`JOIN ... ON` against a dictionary-backed table with `join_algorithm = 'direct'` (in the default list) uses the in-memory structure directly; `INNER`/`LEFT` only, single-column equality key that must equal the dictionary key (direct join also works against MergeTree/EmbeddedRocksDB right tables). Do **not** filter heavily on `dictGet` results — it is evaluated row by row with no index; put filterable attributes in the table instead.

Gotchas:

- Data is held **uncompressed, per node**. `bytes_allocated` in `system.dictionaries` is the real cost — often far above the source table's compressed size.
- `dictionaries_lazy_load` (server, default 1): each dictionary loads on first use, so the first query pays the load and a failing source throws from `dictGet`. With 0 all dictionaries load at startup; the server waits for them unless `wait_dictionaries_load_at_startup` (default 1) is 0.
- Every replica reloads independently: N replicas × `LIFETIME` window = N full reads of the source. Use `MIN/MAX` jitter and `invalidate_query`.
- Monitor: `SELECT name, status, element_count, bytes_allocated, load_factor, hit_rate, found_rate, last_exception, loading_duration FROM system.dictionaries`. `status` is `NOT_LOADED | LOADED | FAILED | LOADING | LOADED_AND_RELOADING | FAILED_AND_RELOADING | NOT_EXIST`. Alert on `FAILED*` and on `last_exception != ''`.

## Schema evolution

| Operation | Cost |
|---|---|
| `ADD COLUMN` | Metadata. Nothing written; missing data is served as the default until parts merge |
| `DROP COLUMN` | Deletes the column files per part — near-instant, but **not** for primary/sampling-key columns |
| `RENAME COLUMN` | Metadata. Not for columns in `ORDER BY` / `PRIMARY KEY` |
| `MODIFY COLUMN ... DEFAULT expr` / `COMMENT` / `CODEC` / `TTL` | Metadata (new codec applies to new parts/merges) |
| `MODIFY COLUMN name NewType` | **Mutation, rewrites the column in every part.** Key columns only if the change does not alter the data |
| `MATERIALIZE COLUMN` | Mutation: writes the DEFAULT/MATERIALIZED value into existing parts |
| `ALTER TABLE ... UPDATE / DELETE` | Mutation: rewrites whole parts. Cannot touch primary- or partition-key columns |
| `MODIFY COLUMN ADD ENUM VALUES` (Enum) | Metadata; removing a value is safe only if never used; renumbering throws |

ALTER waits for running reads on the table and blocks new ones until metadata changes are applied; mutations then run in the background. `mutations_sync` 0/1/2 and `alter_sync` (default 1) control waiting. Watch `system.mutations`; `KILL MUTATION` is the only undo.

Sorting key:

```sql
ALTER TABLE t ADD COLUMN region LowCardinality(String), MODIFY ORDER BY (tenant, ts, region);  -- ok
ALTER TABLE t MODIFY ORDER BY (tenant, ts, existing_col);                                        -- error
```

`MODIFY ORDER BY` can only **append** columns added by `ADD COLUMN` in the **same ALTER, without a DEFAULT expression**; existing columns, expressions over them, and sort-direction changes are rejected because old parts stay physically sorted as written. Anything else = new table + `INSERT ... SELECT`. There is no `ALTER` that changes the **partition key** at all; `MOVE PARTITION ... TO TABLE` also requires identical partition, sorting and primary keys, so a repartition is always a full copy.

Column kinds:

| Kind | Stored | In `SELECT *` | Insertable | Use |
|---|---|---|---|---|
| `DEFAULT expr` | yes | yes | yes (expr fills gaps) | Ordinary column with fallback |
| `MATERIALIZED expr` | yes | no (`asterisk_include_materialized_columns`) | no | Derived value computed at insert, e.g. `toDate(ts)` for a key |
| `ALIAS expr` | no | no (`asterisk_include_alias_columns`) | no | Computed at read; free to add/drop; not usable in projection `ORDER BY` |
| `EPHEMERAL [expr]` | no | no, not selectable | yes (only to feed other defaults) | Parse raw input once, derive several columns |

`ON CLUSTER c` on `CREATE/DROP/ALTER/RENAME/SYSTEM` runs the local statement on every host in `c`, queued through Keeper; unavailable hosts execute it later, order per host is preserved. `distributed_ddl_task_timeout` default 180 s, `distributed_ddl_output_mode` default `throw` — a slow replica makes the client error while the DDL still completes. For `ALTER` on `ReplicatedMergeTree` the metadata change itself replicates; `ON CLUSTER` is for non-replicated tables and the Distributed wrapper.

## Data types

| Type | Facts | Pick when |
|---|---|---|
| `JSON` | Production-ready since **25.3** (OSS). `JSON(max_dynamic_paths=1024, max_dynamic_types=32, a.b UInt32, SKIP c, SKIP REGEXP '^tmp')`. Paths are `Dynamic` subcolumns: `j.a.b`, `j.a.b.:UInt32`, `j.^a` (sub-object). `enable_json_type` is 1 since 25.3 (0 before, when the type was experimental) | Schemaless payloads; declare hot paths with a type |
| `Variant(T1, T2, ...)` | Union; `enable_variant_type` default 1 (`allow_experimental_variant_type` legacy, 0). Subcolumn `v.T` → `Nullable(T)`, `variantType(v)`. No `Nullable`/`LowCardinality(Nullable)` inside; similar numeric types need `allow_suspicious_variant_types` | Known small set of alternative types |
| `Dynamic(max_types=32)` | Any type per row; `enable_dynamic_type` default 1. `d.String`, `dynamicType(d)`; types beyond `max_types` go to a shared binary blob | Truly unknown schema; prefer `JSON` for objects |
| `Array(T)` | `ARRAY JOIN arr AS a` flattens (drops rows with empty arrays; `LEFT ARRAY JOIN` keeps them with default). Function `arrayJoin(arr)` does the same inside SELECT; `unnest` alias since 26.5 | Multi-valued attributes |
| `Nested(a T1, b T2)` | With `flatten_nested = 1` (default) it becomes `n.a Array(T1)`, `n.b Array(T2)`, equal lengths enforced on insert; `ARRAY JOIN n` yields rows. Cannot rename the structure or move subcolumns in/out | Repeated groups; one level only |
| `Map(K, V)` | Stored as `Array(Tuple(K, V))`; `m['k']` is a **linear scan** and returns the value default when missing; `m.keys` / `m.values` read one subcolumn. Keys are not unique | Sparse key/value tags; move hot keys to real columns |
| `Decimal(P, S)` | `Decimal32` P 1-9, `Decimal64` 10-18, `Decimal128` 19-38, `Decimal256` 39-76. `decimal_check_overflow` default 1 — but **no overflow check for Decimal128/256**. 128/256 are markedly slower | Money and anything exact. `Float64` sums drift (`499693.60500000004` vs `499693.605`) |
| `UUID` | 16 bytes. Sorted by its **second half** — random for v4, the counter for v7 — so a UUID first in the key destroys locality for everything after it (the sparse-index guide shows a 13x compression swing on a UInt32 column just from key order: 11.24 MiB vs 877 KiB). Key on `UUIDv7ToDateTime(id)`, a tenant/time column, or `cityHash64(id) % N` bucket, and keep the UUID later | Ids you never range-scan |
| `Enum8` / `Enum16` | 1 / 2 bytes, values `[-128,127]` / `[-32768,32767]`; unknown string → exception; extend with `ADD ENUM VALUES` | Closed, validated set |
| `LowCardinality(T)` | Dictionary-encoded; best < ~10k distinct, worse than plain > ~100k; `low_cardinality_max_dictionary_size` default 8192. Docs: prefer it to `Enum` for strings — same or better efficiency, no DDL on new values | Open but repetitive set |
| `Date` | 2 bytes, 1970-01-01 → 2149-06-06 | Day granularity |
| `Date32` | 4 bytes, 0000-01-01 → 9999-12-31 | Historical / far-future dates |
| `DateTime[(tz)]` | 4 bytes, 1970 → 2106-02-07; tz is column metadata, values are Unix seconds | Second granularity |
| `DateTime64(p[, tz])` | 8 bytes, p 0-9 (default 3); ns precision limits range to 1677 → 2262 | Only when sub-second is used |
| `IPv4` / `IPv6` | 4 bytes `UInt32` / 16 bytes `UInt128`; comparable with each other (`::ffff:a.b.c.d`) | Never `String` for addresses |
| `Nullable(T)` | Extra null-mask file per column; "almost always negatively affects performance"; `allow_nullable_key` default 0 keeps it out of keys; `.null` subcolumn | Only when `0`/`''` is a legal value you must distinguish |
| `FixedString(N)` | Padded with `\0`, longer input throws; `=` ignores trailing `\0`, `LIKE` does not | Exact-width binary: MD5 `FixedString(16)`, SHA256 `FixedString(32)`, ISO codes |
| `Bool` | `UInt8` underneath, literals `true`/`false` | Flags |
| Integers | `Int8..Int256`, `UInt8..UInt256`, 1-32 bytes; pick the narrowest that fits (`UInt16` over `Int32`), unsigned when never negative | Everything countable |

Smallest type that fits, coarsest time precision that answers the query, no `Nullable` without a reason, and never a random id at the head of the sorting key.

## Sources

- https://clickhouse.com/docs/sql-reference/statements/alter/projection
- https://clickhouse.com/docs/data-modeling/projections
- https://clickhouse.com/docs/engines/table-engines/mergetree-family/mergetree#projections
- https://clickhouse.com/docs/sql-reference/statements/delete
- https://clickhouse.com/docs/reference/settings/merge-tree-settings/other
- https://clickhouse.com/docs/operations/settings/settings
- https://clickhouse.com/docs/sql-reference/statements/create/dictionary
- https://clickhouse.com/docs/reference/statements/create/dictionary/layouts/overview (+ flat, hashed, cache, direct, range-hashed)
- https://clickhouse.com/docs/reference/statements/create/dictionary/sources/overview
- https://clickhouse.com/docs/reference/statements/create/dictionary/lifetime
- https://clickhouse.com/docs/sql-reference/functions/ext-dict-functions
- https://clickhouse.com/docs/dictionary
- https://clickhouse.com/docs/concepts/features/dictionaries/best-practices
- https://clickhouse.com/docs/reference/settings/server-settings/settings/dictionaries
- https://clickhouse.com/docs/operations/system-tables/dictionaries
- https://clickhouse.com/docs/sql-reference/statements/alter (+ /column, /order-by, /partition, /update)
- https://clickhouse.com/docs/sql-reference/statements/create/table
- https://clickhouse.com/docs/sql-reference/distributed-ddl
- https://clickhouse.com/docs/engines/table-engines/mergetree-family/custom-partitioning-key
- https://clickhouse.com/docs/guides/best-practices/sparse-primary-indexes
- https://clickhouse.com/docs/best-practices/select-data-types
- https://clickhouse.com/docs/sql-reference/data-types/{newjson,variant,dynamic,nested-data-structures/nested,map,decimal,float,uuid,enum,lowcardinality,date,date32,datetime,datetime64,ipv4,ipv6,nullable,fixedstring,boolean,int-uint}
- https://clickhouse.com/docs/sql-reference/statements/select/array-join
- https://clickhouse.com/docs/sql-reference/functions/uuid-functions
