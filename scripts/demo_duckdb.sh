#!/usr/bin/env bash
set -euo pipefail

echo "[demo] building gold table in duckdb..."
docker exec -i fedsentinel-duckdb duckdb /tmp/fedsentinel.duckdb < /analytics/build_gold.sql

echo
echo "[demo] running analytics queries..."
docker exec -i fedsentinel-duckdb duckdb /tmp/fedsentinel.duckdb < /analytics/queries.sql