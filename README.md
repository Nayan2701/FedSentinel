# FedSentinel — Self-Healing Edge Security Insights Pipeline + Dashboard

FedSentinel is a small, end-to-end data pipeline project that simulates edge security insights, **heals** malformed inputs into a clean **Silver Parquet** dataset, and serves a **Streamlit dashboard** to visualize results.

Dashboard URL: https://fedsentinel.onrender.com

## What this project demonstrates
- **Bronze → Silver pipeline**: JSONL inputs (“Bronze”) are validated/normalized into Parquet (“Silver”).
- **Self-healing behavior**: bad records are quarantined instead of breaking the pipeline.
- **Rollback-ready publishing**: Silver is published atomically (and can keep a previous version depending on your setup).
- **Observability**: a “self-healing proof” panel shows status + counts so you can verify healing happened.

---

## Repository layout (high level)
- `analytics/` — healer logic + DuckDB queries
- `app/` — Streamlit dashboard
- `infra/` — docker-compose for local running
- `scripts/` — demo scripts and helpers
- `inbox/` — local sample input folder (project-dependent)

---

## Local run (recommended)
This is the best way to run the full pipeline (dashboard + healer + any supporting services).

```bash
docker compose -f infra/docker-compose.yml up -d
```

Open dashboard:
- http://localhost:8501

### Self-healing demo
Keep the dashboard open and run:

```bash
./scripts/demo_self_healing.sh
```

What you should see:
- malformed lines are **quarantined**
- status/metrics update on the next heal cycle
- Silver stays queryable so charts continue working

---

## Dashboard (Streamlit)
The dashboard reads **Silver Parquet** and shows:
- KPIs: total insights, high-risk count, LLM summaries, avg quality
- Charts: insights by region, risk distribution, top actions
- **Self-healing proof** section:
  - last healer status (`healer_status.json`)
  - recent metrics (if enabled)
  - quarantined bad-row count
  - silver freshness / last updated

### Auto-refresh
The dashboard includes a toggleable auto-refresh (useful in demos so you can watch healing live).

---

## Deploy (Render) — Dashboard-only (R2-backed)
### Why dashboard-only?
We deploy only the Streamlit dashboard on Render because:
1) **Render Free tier does not support Persistent Disks**, which the full local pipeline uses for `/data/inbox`.
2) On the current Render plan/workspace, **Background Worker services are not available** (Render shows “service type is not available for this plan” when creating the healer worker).
3) To keep the deployment simple and stable, we deploy the dashboard and point it at an external, persistent store (**Cloudflare R2**).

This still gives you a publicly accessible dashboard and demonstrates the analytics layer cleanly.

### What gets deployed
- ✅ `fedsentinel-dashboard` (Render Web Service)
- ❌ healer (not deployed on this plan)
- ✅ persistent storage via **Cloudflare R2** for Silver Parquet + status JSON

### Prerequisite: Put Silver in R2
Your R2 bucket must contain:
- `silver/edge_insights_silver.parquet` (required)
- `status/healer_status.json` (optional)

### Render environment variables (Web Service)
Set the following env vars on the Render **Web Service**:

- `SILVER_S3_BUCKET` = your R2 bucket name (example: `silver-s3-bucket`)
- `S3_ENDPOINT_URL` = `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
- `S3_ACCESS_KEY_ID` = your R2 access key
- `S3_SECRET_ACCESS_KEY` = your R2 secret
- `SILVER_S3_KEY` = `silver/edge_insights_silver.parquet`
- `STATUS_S3_KEY` = `status/healer_status.json`

### Deploy steps (Render)
1) Render → New → **Web Service**
2) Connect this GitHub repo
3) Environment: **Docker**
4) Dockerfile: `Dockerfile.dashboard`
5) Add env vars above → Deploy

---

## Notes / Limitations
- The full self-healing pipeline is best demonstrated locally using Docker Compose.
- The Render deployment is intentionally dashboard-only due to plan limitations. If you upgrade to a plan that supports workers and/or disks, the healer can also be deployed (or replaced with a Cron job) to continuously refresh Silver in R2.

---

## Screenshots / Demo media (recommended)

- dashboard overview
  <img width="1407" height="835" alt="image" src="https://github.com/user-attachments/assets/a1fd4a68-dcec-465b-882e-01bc2b006803" />
  <img width="1377" height="782" alt="image" src="https://github.com/user-attachments/assets/736662e3-7241-4077-8a52-13c45a21355b" />


- self-healing proof panel showing status + quarantined count changing after injecting a bad record
  <img width="615" height="816" alt="Screenshot 2026-05-24 at 2 17 39 PM" src="https://github.com/user-attachments/assets/54024584-9128-4845-8c6f-f78dea286177" />
