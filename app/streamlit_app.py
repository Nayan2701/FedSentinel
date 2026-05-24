import json
import time
from pathlib import Path
from datetime import datetime, timezone
import os
import duckdb
import pandas as pd
import streamlit as st

INBOX = Path(os.getenv("INBOX_PATH","/data/inbox"))
SILVER = INBOX / "edge_insights_silver.parquet"
STATUS = INBOX / "healer_status.json"
METRICS = INBOX / "healer_metrics.jsonl"
BADROWS = INBOX / "edge_insights_bad_rows.jsonl"


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def file_mtime_utc(path: Path):
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


def tail_jsonl(path: Path, n: int = 10):
    """Read last n JSON objects from a JSONL file (best-effort, small files expected)."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for s in lines[-n:]:
            s = s.strip()
            if not s:
                continue
            try:
                out.append(json.loads(s))
            except Exception:
                out.append({"raw": s})
        return out
    except Exception:
        return []


def count_lines(path: Path, max_bytes: int = 20_000_000):
    """Count lines without blowing up memory; caps reads for safety."""
    if not path.exists():
        return 0
    try:
        # If file is huge, do a streaming count anyway (still OK for most demos).
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


@st.cache_data(ttl=10)
def load_tables(silver_path: str):
    con = duckdb.connect(database=":memory:")
    con.execute("INSTALL parquet; LOAD parquet;")

    df = con.execute(
        "SELECT * FROM read_parquet(?)",
        [silver_path],
    ).fetchdf()

    by_region = con.execute(
        """
        SELECT region,
               count(*) AS insights,
               round(avg(CAST(quality_score AS DOUBLE)), 3) AS avg_quality
        FROM read_parquet(?)
        GROUP BY 1
        ORDER BY insights DESC
        """,
        [silver_path],
    ).fetchdf()

    risk_dist = con.execute(
        """
        SELECT pii_leak_risk, count(*) AS insights
        FROM read_parquet(?)
        GROUP BY 1
        ORDER BY insights DESC
        """,
        [silver_path],
    ).fetchdf()

    llm_vs_fallback = con.execute(
        """
        SELECT summary_source, count(*) AS insights
        FROM read_parquet(?)
        GROUP BY 1
        ORDER BY insights DESC
        """,
        [silver_path],
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
        [silver_path],
    ).fetchdf()

    return df, by_region, risk_dist, llm_vs_fallback, top_actions


st.set_page_config(page_title="FedSentinel Dashboard", layout="wide")
st.title("FedSentinel — Edge Security Insights Dashboard")

# -----------------------
# Controls (auto-refresh)
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

# Auto-refresh mechanism (simple + reliable)
if auto_refresh:
    time.sleep(int(refresh_seconds))
    st.cache_data.clear()
    st.rerun()

# -----------------------
# Self-healing proof
# -----------------------
st.subheader("Self-healing proof (health, rollback, quarantine)")

status = read_json(STATUS) or {}
silver_mtime = file_mtime_utc(SILVER)
bad_count = count_lines(BADROWS) if BADROWS.exists() else 0
recent_metrics = tail_jsonl(METRICS, n=8)

p1, p2, p3, p4 = st.columns(4)
with p1:
    st.metric("Healer status", status.get("status", "unknown"))
with p2:
    st.metric("Bad rows quarantined", bad_count)
with p3:
    st.metric("Rows written (last run)", status.get("rows_written", "n/a"))
with p4:
    st.metric("Bad skipped (last run)", status.get("bad_rows_skipped", "n/a"))

if silver_mtime:
    st.caption(f"Silver Parquet last modified (UTC): {silver_mtime.isoformat()}")
else:
    st.error("Silver Parquet not found. Start the healer: `docker compose up -d healer healer-watchdog`")
    st.stop()

with st.expander("Show healer_status.json (last run)"):
    if status:
        st.json({k: status.get(k) for k in ["run_ts", "status", "reason", "total_lines_seen", "rows_written", "bad_rows_skipped"]})
    else:
        st.write("(missing)")

with st.expander("Show last 8 healer_metrics.jsonl entries"):
    if recent_metrics:
        st.dataframe(pd.DataFrame(recent_metrics), use_container_width=True)
    else:
        st.write("(no metrics yet)")

st.divider()

# -----------------------
# Main analytics
# -----------------------
try:
    df, by_region, risk_dist, llm_vs_fallback, top_actions = load_tables(str(SILVER))
except Exception as e:
    st.error(f"Failed to read Silver Parquet with DuckDB: {e}")
    st.stop()

# KPIs
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
    if not by_region.empty:
        st.bar_chart(by_region.set_index("region")["insights"])
    else:
        st.write("(no data)")

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