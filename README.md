# FedSentinel

A portfolio-ready **self-healing data pipeline** demo that ingests edge security “insights” events (JSONL), continuously rebuilds a clean **Silver Parquet** dataset with rollback + quarantine, and runs **DuckDB** “Gold” analytics queries (KPIs).

This project is intentionally designed to look and behave like a production-shaped mini system:
- **Bronze**: append-only raw JSONL inbox (`edge_insights.jsonl`)
- **Silver (self-healing)**: scheduled rebuild to Parquet every 60s (atomic publish + `.prev` rollback)
- **Gold**: DuckDB SQL model + KPI queries (region volume, risk distribution, top actions, LLM vs fallback)

---

## Architecture (Bronze → Silver → Gold)

**Data flow**
1. Producers write raw events to **Bronze**: `/data/inbox/edge_insights.jsonl`
2. The **Healer** container runs on an interval (default 60s):
   - parses + validates each JSON line
   - parses nested `payload` JSON
   - quarantines malformed lines
   - writes **Silver** to Parquet using atomic publish:
     - write `edge_insights_silver.parquet.tmp`
     - validate tmp is readable
     - rotate last known good to `edge_insights_silver.parquet.prev`
     - promote tmp → `edge_insights_silver.parquet`
   - emits run metrics and a “last status” file
3. DuckDB reads **Silver Parquet** and builds **Gold** tables, then runs analytics queries.

**Key artifacts (in the inbox volume)**
- `edge_insights.jsonl` — Bronze (raw)
- `edge_insights_silver.parquet` — Silver (current)
- `edge_insights_silver.parquet.prev` — Silver rollback (last known good)
- `edge_insights_bad_rows.jsonl` — quarantine (malformed lines + parsing errors)
- `healer_metrics.jsonl` — append-only run metrics (one JSON per run)
- `healer_status.json` — last-run status snapshot (`ok` / `degraded`)

---

## Requirements

- Docker Desktop (includes Docker Compose)
- macOS/Linux recommended (Windows works via WSL2)
- No local Python needed (all analytics run in containers)

---

## Quick Start

### 1) Start the self-healing Silver pipeline (healer + watchdog)

```bash
docker compose -f infra/docker-compose.yml up -d healer healer-watchdog
```

Tail logs:

```bash
docker logs -f fedsentinel-healer
docker logs -f fedsentinel-healer-watchdog
```

Verify Silver + status files exist:

```bash
docker exec -it fedsentinel-healer sh -lc "ls -lah /data/inbox | sed -n '1,160p'"
docker exec -it fedsentinel-healer sh -lc "cat /data/inbox/healer_status.json || true"
```

---

## Self-healing Demo (Recommended)

This is the easiest way to prove the behavior end-to-end: the demo script will:
- ensure healer + watchdog are running
- show baseline metrics/status
- inject one malformed Bronze record
- wait for the next heal cycle
- show quarantine + updated metrics
- run DuckDB Gold + KPI queries successfully

```bash
./scripts/demo_self_healing.sh
```

**What “success” looks like**
- `healer_metrics.jsonl` shows a run where `bad_rows_skipped` increases (the injected bad record)
- `edge_insights_bad_rows.jsonl` gains a new entry
- `edge_insights_silver.parquet` continues to be fresh and readable
- DuckDB KPIs run without JSON parsing failures

---

## Analytics (DuckDB Gold)

### What gets built
- `gold.edge_security_insights` — analytics table built from Silver Parquet

### Run the analytics job
The demo script already runs analytics, but you can run it directly:

```bash
docker run --rm -it \
  -v infra_fedsentinel_inbox:/data/inbox \
  -v "$PWD/analytics":/analytics \
  python:3.11-slim bash -lc "
    pip -q install duckdb==1.1.3 pandas pyarrow &&
    python /analytics/run_duckdb.py
  "
```

### KPI queries
Queries live in:
- `analytics/queries.sql`

Typical outputs include:
- Insights by region + average quality
- Risk distribution (`low/medium/high`)
- LLM vs fallback summarization rate
- Top actions by region (using list/unnest)

---

## Service Details

### Healer (Silver builder)
- Image: `python:3.11-slim`
- Code: `analytics/heal_loop.py`
- Interval: `HEAL_INTERVAL_SECONDS` (default 60)
- Inputs:
  - `BRONZE_PATH=/data/inbox/edge_insights.jsonl`
- Outputs:
  - `SILVER_PATH=/data/inbox/edge_insights_silver.parquet`
  - `BADROWS_PATH=/data/inbox/edge_insights_bad_rows.jsonl`
  - `METRICS_PATH=/data/inbox/healer_metrics.jsonl`
  - `STATUS_PATH=/data/inbox/healer_status.json`

**Self-healing behavior**
- Bad records do *not* stop the pipeline:
  - malformed JSON lines and payload parse errors are quarantined
- If publishing Silver fails:
  - the current `edge_insights_silver.parquet` remains in place
  - `.prev` retains the last known good snapshot
  - `healer_status.json` is set to `degraded`

### Watchdog (exit-on-fail “health semantics”)
- Image: `alpine:3.20`
- Purpose: fail fast if:
  - `edge_insights_silver.parquet` is missing
  - Silver is stale (older than threshold)
  - last healer status is `degraded`

This gives “healthcheck semantics” even if Compose healthchecks aren’t used.

---

**Start everything (healer + watchdog + dashboard):**
```bash
docker compose -f infra/docker-compose.yml up -d healer healer-watchdog dashboard

## Dashboard (Streamlit)

FedSentinel includes a lightweight Streamlit dashboard that reads **Silver Parquet** directly (always reflects the latest healed data).

### Start the dashboard
```bash
docker compose -f infra/docker-compose.yml up -d dashboard
```

Open:
- http://localhost:8501

### What the dashboard shows
- **KPIs**: total insights, high-risk count, LLM summaries, avg quality
- **Charts**: insights by region, risk distribution, top actions
- **Self-healing proof**:
  - last healer status (`healer_status.json`)
  - recent run metrics tail (`healer_metrics.jsonl`)
  - quarantined bad row count (`edge_insights_bad_rows.jsonl`)
  - silver freshness (mtime)

### Live self-healing demo (recommended)
Run the demo script and keep the dashboard open to watch:
- quarantined count increase after injecting a malformed record
- metrics/status update on the next heal cycle
- charts remain available because Silver publishes atomically with rollback

```bash
./scripts/demo_self_healing.sh
```
## Configuration

Most settings are environment variables in `infra/docker-compose.yml`:

- `HEAL_INTERVAL_SECONDS` — healer rebuild cadence
- `BRONZE_PATH`, `SILVER_PATH` — input/output locations
- Watchdog freshness threshold is defined in its shell logic

---

## Troubleshooting

### Watchdog keeps restarting and logs “SILVER variable not set”
Docker Compose interpolates `$VARS` in YAML. If your watchdog script references shell variables like `$SILVER`,
you must escape them as `$$SILVER` inside `docker-compose.yml`.

### Silver parquet is missing
Check healer logs:

```bash
docker logs --tail 200 fedsentinel-healer
```

Also verify the inbox volume contents:

```bash
docker exec -it fedsentinel-healer sh -lc "ls -lah /data/inbox | sed -n '1,200p'"
```

### DuckDB queries fail
Ensure `analytics/build_gold.sql` reads Silver Parquet:

```sql
FROM read_parquet('/data/inbox/edge_insights_silver.parquet');
```

---

## Project Layout

- `infra/`
  - `docker-compose.yml` — services (healer + watchdog + other infra)
- `analytics/`
  - `heal_loop.py` — self-healing Silver rebuild job
  - `build_gold.sql` — DuckDB Gold model
  - `queries.sql` — KPI queries
  - `run_duckdb.py` — runs Gold build + KPIs
- `scripts/`
  - `demo_self_healing.sh` — one-command end-to-end demo

---

