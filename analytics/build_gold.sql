CREATE SCHEMA IF NOT EXISTS gold;

CREATE OR REPLACE TABLE gold.edge_security_insights AS
SELECT
  node_id,
  region,
  model,
  CAST(quality_score AS DOUBLE) AS quality_score,

  -- keep timestamps as strings for now (safe); we can parse later if needed
  event_ts,
  ingest_ts,

  summary_source,
  pii_leak_risk,
  summary,

  -- DuckDB can keep lists from parquet (top_actions)
  top_actions,
  CAST(events AS BIGINT) AS events,
  CAST(avg_latency_ms AS BIGINT) AS avg_latency_ms,
  top_ip_class
FROM read_parquet('/data/inbox/edge_insights_silver.parquet');
