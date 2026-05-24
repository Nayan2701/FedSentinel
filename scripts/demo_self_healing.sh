#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
HEALER_CONTAINER="${HEALER_CONTAINER:-fedsentinel-healer}"
WATCHDOG_CONTAINER="${WATCHDOG_CONTAINER:-fedsentinel-healer-watchdog}"

echo "==> Starting healer + watchdog..."
docker compose -f "$COMPOSE_FILE" up -d healer healer-watchdog

echo
echo "==> Waiting for healer_status.json and silver parquet to appear..."
deadline=$(( $(date +%s) + 120 ))
while true; do
  if docker exec "$HEALER_CONTAINER" sh -lc 'test -f /data/inbox/healer_status.json && test -f /data/inbox/edge_insights_silver.parquet'; then
    break
  fi
  if [ "$(date +%s)" -gt "$deadline" ]; then
    echo "Timed out waiting for healer_status.json / edge_insights_silver.parquet"
    docker logs --tail 100 "$HEALER_CONTAINER" || true
    exit 1
  fi
  sleep 2
done

echo
echo "==> Baseline status (last run):"
docker exec "$HEALER_CONTAINER" sh -lc 'cat /data/inbox/healer_status.json || true'
echo
echo "==> Baseline metrics (tail):"
docker exec "$HEALER_CONTAINER" sh -lc 'tail -n 3 /data/inbox/healer_metrics.jsonl || true'

echo
echo "==> Injecting ONE malformed record into bronze (should be quarantined)..."
docker exec "$HEALER_CONTAINER" sh -lc 'printf "%s\n" "{\"node_id\":\"edge-bad\",\"payload\":" >> /data/inbox/edge_insights.jsonl'

echo
echo "==> Waiting for next heal cycle (70s)..."
sleep 70

echo
echo "==> Post-injection status (last run):"
docker exec "$HEALER_CONTAINER" sh -lc 'cat /data/inbox/healer_status.json || true'
echo
echo "==> Post-injection metrics (tail):"
docker exec "$HEALER_CONTAINER" sh -lc 'tail -n 5 /data/inbox/healer_metrics.jsonl || true'

echo
echo "==> Quarantine file (tail):"
docker exec "$HEALER_CONTAINER" sh -lc 'test -f /data/inbox/edge_insights_bad_rows.jsonl && tail -n 2 /data/inbox/edge_insights_bad_rows.jsonl || echo "(no bad rows file yet)"'

echo
echo "==> Silver parquet freshness:"
docker exec "$HEALER_CONTAINER" sh -lc 'ls -lah /data/inbox/edge_insights_silver.parquet /data/inbox/edge_insights_silver.parquet.prev 2>/dev/null || true'

echo
echo "==> Watchdog state (should still be running if silver is fresh and status != degraded):"
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E "(${HEALER_CONTAINER}|${WATCHDOG_CONTAINER})" || true

echo
echo "==> Running DuckDB analytics job (gold build + KPIs)..."
docker run --rm -it \
  -v infra_fedsentinel_inbox:/data/inbox \
  -v "$PWD/analytics":/analytics \
  python:3.11-slim bash -lc "
    pip -q install duckdb==1.1.3 pandas pyarrow &&
    python /analytics/run_duckdb.py
  "

echo
echo "==> Demo complete."