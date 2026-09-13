This ClickHouse query on a MergeTree table ordered by (tenant, ts) takes 40 seconds — why, and what should I change? SELECT count() FROM events WHERE user_id = 42 AND ts > now() - INTERVAL 1 DAY
