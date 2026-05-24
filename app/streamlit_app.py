import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone

import boto3
import duckdb
import pandas as pd
import streamlit as st


# -----------------------
# R2/S3 config (required)
# -----------------------
S3_ENDPOINT_URL = os.environ["S3_ENDPOINT_URL"]
S3_ACCESS_KEY_ID = os.environ["S3_ACCESS_KEY_ID"]
S3_SECRET_ACCESS_KEY = os.environ["S3_SECRET_ACCESS_KEY"]

S3_BUCKET = os.environ["SILVER_S3_BUCKET"]
SILVER_KEY = os.getenv("SILVER_S3_KEY", "silver/edge_insights_silver.parquet")
STATUS_KEY = os.getenv("STATUS_S3_KEY", "status/healer_status.json")

BADROWS_KEY = os.getenv("BADROWS_S3_KEY", "quarantine/edge_insights_bad_rows.jsonl")  # optional

TMP_DIR = Path("/tmp/fedsentinel-dashboard")
TMP_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_SILVER = TMP_DIR / "edge_insights_silver.parquet"
LOCAL_STATUS = TMP_DIR / "healer_status.json"


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
    )


def download_key_to_file(key: str, local_path: Path) -> bool:
    s3 = s3_client()
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(S3_BUCKET, key, str(local_path))
        return True
    except Exception:
        return False


def head_key(key: str):
    s3 = s3_client()
    return s3.head_object(Bucket=S3_BUCKET, Key=key)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_mtime_utc(path: Path):
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=10)
def fetch_remote_files():
    """
    Downloads Silver + Status from R2 to /tmp.
    Uses ETag check to avoid redundant downloads when unchanged.
    """
    # Silver
    silver_head = head_key(SILVER_KEY)
    silver_etag = silver_head.get("ETag", "").strip('"')
    silver_last_modified = silver_head.get("LastModified")

    etag_path = TMP_DIR / ".silver_etag"
    prev = etag_path.read_text().strip() if etag_path.exists() else ""

    if (not LOCAL_SILVER.exists()) or (prev != silver_etag):
        ok = download_key_to_file(SILVER_KEY, LOCAL_SILVER)
        if ok:
            etag_path.write_text(silver_etag)

    # Status (best-effort; doesn't have to exist)
    try:
        _ = head_key(STATUS_KEY)
        download_key_to_file(STATUS_KEY, LOCAL_STATUS)
    except Exception:
        pass

    return {
        "silver_etag": silver_etag,
        "silver_last_modified": silver_last_modified.isoformat() if silver_last_modified else None,
        "local_silver_path": str(LOCAL_SILVER),
        "local_status_path": str(LOCAL_STATUS),
        "fetched_at": utc_now_iso(),
    }


@st.cache_data(ttl=10)
def load_tables(local_silver_path: str):
    con = duckdb.connect(database=":memory:")
    con.execute("INSTALL parquet; LOAD parquet;")

    df = con.execute("SELECT * FROM read_parquet(?)", [local_silver_path]).fetchdf()

    by_region = con.execute(
        """
        SELECT region,
               count(*) AS insights,
               round(avg(CAST(quality_score AS DOUBLE)), 3) AS avg_quality
        FROM read_parquet(?)
        GROUP BY 1
        ORDER BY insights DESC
        """,
        [local_silver_path],
    ).fetchdf()

    risk_dist = con.execute(
        """
        SELECT pii_leak_risk, count(*) AS insights
        FROM read_parquet(?)
        GROUP BY 1
        ORDER BY insights DESC
        """,
        [local_silver_path],
    ).fetchdf()

    llm_vs_fallback = con.execute(
        """
        SELECT summary_source, count(*) AS insights
        FROM read_parquet(?)
        GROUP BY 1
        ORDER BY insights DESC
        """,
        [local_silver_path],
    ).fetchdf()

    top_actions = con.execute(
        """
        WITH exploded AS (
          SELECT region, unnest(top_actions) AS action
          FROM read_parquet(?)
          WHERE top_actions IS NOT NULL
        )
        SELECT region, action, count(*) AS occurrences
        FROM exploded
        GROUP BY 1,2
        ORDER BY occurrences DESC
        LIMIT 50
        """,
        [local_silver_path],
    ).fetchdf()

    return df, by_region, risk_dist, llm_vs_fallback, top_actions


st.set_page_config(page_title="FedSentinel Dashboard", layout="wide")
st.title("FedSentinel — Edge Security Insights Dashboard (R2-backed)")

# -----------------------
# Controls
# -----------------------
with st.sidebar:
    st.header("Controls")
    c1, c2 = st.columns(2)
    with c1:
        auto_refresh = st.toggle("Auto-refresh", value=True)
    with c2:
        refresh_seconds = st.selectbox("Interval (sec)", [5, 10, 15, 30, 60], index=2)

    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()

# Auto-refresh
if auto_refresh:
    time.sleep(int(refresh_seconds))
    st.cache_data.clear()
    st.rerun()

# -----------------------
# Fetch remote (R2)
# -----------------------
meta = fetch_remote_files()

if not Path(meta["local_silver_path"]).exists():
    st.error(
        f"Silver Parquet not available yet. Waiting for healer to publish to R2: s3://{S3_BUCKET}/{SILVER_KEY}"
    )
    st.stop()

status = read_json(Path(meta["local_status_path"])) or {}

# -----------------------
# Self-healing proof
# -----------------------
st.subheader("Self-healing proof (R2 persistence, rollback-ready, quarantine)")

p1, p2, p3, p4 = st.columns(4)
with p1:
    st.metric("Healer status", status.get("status", "unknown"))
with p2:
    st.metric("Rows written (last run)", status.get("rows_written", "n/a"))
with p3:
    st.metric("Bad skipped (last run)", status.get("bad_rows_skipped", "n/a"))
with p4:
    st.metric("Silver ETag", (meta.get("silver_etag") or "")[:12] + "…")

st.caption(
    f"Remote silver: s3://{S3_BUCKET}/{SILVER_KEY} | "
    f"LastModified: {meta.get('silver_last_modified')} | "
    f"Fetched: {meta.get('fetched_at')} | "
    f"Local mtime: {file_mtime_utc(Path(meta['local_silver_path'])).isoformat()}"
)

with st.expander("Show healer_status.json (from R2)"):
    if status:
        st.json(status)
    else:
        st.write("(status not found yet)")

# -----------------------
# Analytics
# -----------------------
try:
    df, by_region, risk_dist, llm_vs_fallback, top_actions = load_tables(meta["local_silver_path"])
except Exception as e:
    st.error(f"Failed to query silver parquet with DuckDB: {e}")
    st.stop()

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.metric("Total insights", int(len(df)))
with k2:
    high = int((df["pii_leak_risk"] == "high").sum()) if "pii_leak_risk" in df.columns else 0
    st.metric("High risk", high)
with k3:
    llm = int((df["summary_source"] == "llm").sum()) if "summary_source" in df.columns else 0
    st.metric("LLM summaries", llm)
with k4:
    avgq = float(pd.to_numeric(df.get("quality_score"), errors="coerce").mean())
    st.metric("Avg quality", f"{avgq:.3f}" if avgq == avgq else "n/a")

left, right = st.columns(2)
with left:
    st.subheader("Insights by region")
    st.bar_chart(by_region.set_index("region")["insights"] if not by_region.empty else pd.Series(dtype=int))

with right:
    st.subheader("Risk distribution")
    if not risk_dist.empty:
        st.bar_chart(risk_dist.set_index("pii_leak_risk")["insights"])
    else:
        st.write("(no data)")

c5, c6 = st.columns(2)
with c5:
    st.subheader("LLM vs fallback")
    st.dataframe(llm_vs_fallback, use_container_width=True)

with c6:
    st.subheader("Top actions (top 50)")
    st.dataframe(top_actions, use_container_width=True)

st.divider()
st.subheader("Raw sample (Silver)")
st.dataframe(df.head(50), use_container_width=True)