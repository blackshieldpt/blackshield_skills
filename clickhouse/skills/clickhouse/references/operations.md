# Operations

## Topology

- Run **ZooKeeper / ClickHouse Keeper on separate machines** (separate pods/hardware) from ClickHouse nodes. Co-location turns a node problem into cluster-wide read-only tables.
- Consider compute-compute separation: a dedicated write replica with failover.
- Put a load balancer in front with routing logic based on request type, current load, replica role, and consistent hashing so queries reuse warm caches.
- Interface choice: the native TCP protocol is faster and lower-overhead and is the usual default; Tinybird prefers **HTTP** for its more battle-tested tooling and load-balancer friendliness. Pick per workload.
- For rolling upgrades, the `compatibility` setting pins query-setting defaults to a previous version so behaviour does not shift mid-rollout; it is not a replication-protocol guard. Follow the official update guide for ordering and keep the version gap small.

## Workload isolation

Separate these onto different replicas; they have incompatible requirements:

- Real-time, sub-second queries (p99 objectives) — keep these replicas under ~40% load.
- Long-running ad-hoc/analytical queries.
- Backfills and batch jobs.

Real-time sub-second serving is expensive; it does not coexist with spot-instance batch work.

## Upgrades

1. Wait ~1 month after a release — format incompatibilities and bugs surface early.
2. Diff settings between versions (read the source: defaults get disabled/changed silently).
3. Run CI against current production version, target version, and master. Include analytics queries in CI.
4. Add a new replica on the target version. Watch its logs closely (ClickHouse logs many benign events at critical severity).
5. Send read traffic → then writes → then migrate the rest.
6. No DDL during the upgrade: no column adds, no data-skipping index changes, no structure modifications.
7. Do not use new-version features until the entire cluster is upgraded.
8. Test on a real cluster (≥2 nodes + Keeper). Single-instance testing misses replication lag and table-creation behavior.
9. Avoid experimental features unless you fully understand them.

## Monitoring — system tables

| Table | Use |
|---|---|
| `system.query_log` | Primary analysis surface; `ProfileEvents` exposes query internals |
| `system.processes` | Currently running queries — first stop during incidents |
| `system.part_log` | Part creation, merges, moves, removals |
| `system.merges` / `system.mutations` | In-flight merges, stuck mutations |
| `system.replicas` / `system.replication_queue` | Replication lag, queue depth |
| `system.tables` / `system.columns` | Schema inspection |
| `system.asynchronous_metrics`, `system.metrics` | Resource and cache state |

Track continuously: query count, ZooKeeper latencies, S3 errors, replication lag, DDL queue length, merge queue depth, merge memory, mutation backlog, part counts.

## Alerts

- Tables in read-only mode (always serious).
- ZooKeeper/Keeper errors.
- `max_concurrent_queries` threshold breaches.
- Segfaults — identify the triggering query fast, or it will crash every replica in turn.

## Incident playbook

| Incident | Response |
|---|---|
| Read-only tables | Check Keeper health; replica may be slow to start. Escalate in order: wait for reconnect (`readonly_start_time`) → `SYSTEM RESTART REPLICA db.table` → `SYSTEM RESTORE REPLICA db.table` (recreates lost Keeper metadata from local parts) → detach/attach → `SYSTEM DROP REPLICA` + recreate and let it resync |
| `TOO_MANY_PARTS` | Stop multi-partition writes; batch per partition; raise merge pool; backpressure upstream |
| Merges slower than inserts | Increase `background_pool_size`, apply backpressure, relax queue-size limits |
| Slow `ON CLUSTER` DDL | A replica is down; ensure application timeouts are configured for this |
| Segfault loop | Find the query in `system.query_log`, block it, then report upstream |

## Safe replica removal

1. Remove it from the load balancer.
2. Wait for in-flight inserts and queries to finish.
3. Kill remaining queries if needed.
4. Stop the node; watch replication lag while it is out and while it catches back up.

## Protections

- `max_table_size_to_drop` (default 50 GB) is the largest table a `DROP` may remove. Lowering it protects *more* tables; raising it — and above all `0`, which disables the check entirely — is what turns a mistake into a backup restore.
- Back up with `BACKUP TABLE` incrementally to S3/GCS, during low-traffic windows. Verify restores regularly; an untested backup is not a backup.

## Staffing and cost reality

- A small cluster needs a part-time operator.
- Sustained heavy load (>20k rows/s with frequent schema/query changes) needs a full-time ClickHouse engineer.
- Following the basic rules in this skill is worth 3-4x in hardware cost.
- Reading the ClickHouse source is a normal part of operating it — unusually so among databases. Most large users carry upstream patches.

## Sources

- https://www.tinybird.co/blog/what-i-learned-operating-clickhouse
- https://www.tinybird.co/blog/what-i-learned-operating-clickhouse-part-ii
- https://clickhouse.com/docs/sql-reference/statements/system
- https://clickhouse.com/docs/operations/update
- https://clickhouse.com/docs/reference/settings/session-settings/compatibility
