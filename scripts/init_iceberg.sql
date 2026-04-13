CREATE NAMESPACE IF NOT EXISTS nessie.fedsentinel;

CREATE TABLE IF NOT EXISTS nessie.fedsentinel.edge_insights (
  node_id STRING,
  event_ts TIMESTAMP,
  insight_type STRING,
  quality_score DOUBLE,
  payload STRING
)
USING iceberg
PARTITIONED BY (days(event_ts));

SHOW TABLES IN nessie.fedsentinel;