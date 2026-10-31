This table is `ENGINE = MergeTree ORDER BY (tenant, ts)` and this query takes 40 seconds:

SELECT count() FROM events WHERE user_id = 42 AND ts > now() - INTERVAL 1 DAY

Why is it slow, and what should I change?
