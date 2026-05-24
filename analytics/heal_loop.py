import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from r2_io import download_file, upload_file, object_exists

# -----------------------
# Paths (local temp only)
# -----------------------
TMP_DIR = Path("/tmp/fedsentinel")
TMP_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_BRONZE = TMP_DIR / "edge_insights.jsonl"
LOCAL_SILVER_TMP = TMP_DIR / "edge_insights_silver.parquet.tmp"
LOCAL_SILVER = TMP_DIR / "edge_insights_silver.parquet"
LOCAL_BADROWS = TMP_DIR / "edge_insights_bad_rows.jsonl"
LOCAL_STATUS = TMP_DIR / "healer_status.json"

# -----------------------
# Required S3/R2 env vars
# -----------------------
S3_BUCKET = os.environ["SILVER_S3_BUCKET"]

BRONZE_KEY = os.getenv("BRONZE_S3_KEY", "bronze/edge_insights.jsonl")

SILVER_KEY = os.getenv("SILVER_S3_KEY", "silver/edge_insights_silver.parquet")
SILVER_PREV_KEY = os.getenv("SILVER_S3_PREV_KEY", "silver/edge_insights_silver.parquet.prev")

STATUS_KEY = os.getenv("STATUS_S3_KEY", "status/healer_status.json")
BADROWS_KEY = os.getenv("BADROWS_S3_KEY", "quarantine/edge_insights_bad_rows.jsonl")

HEAL_INTERVAL_SECONDS = int(os.getenv("HEAL_INTERVAL_SECONDS", "60"))

# -----------------------
# Helpers
# -----------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_json_loads(s: str):
    return json.loads(s)


def parse_bronze_line(line: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Returns (record, payload_obj). payload_obj is parsed from record["payload"] which is JSON string.
    """
    rec = safe_json_loads(line)
    payload_raw = rec.get("payload", "{}")
    if isinstance(payload_raw, dict):
        payload_obj = payload_raw
    else:
        payload_obj = safe_json_loads(payload_raw)
    return rec, payload_obj


def normalize_row(rec: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    # Keep the columns your analytics expects (adjust if your schema differs)
    quality_meta = rec.get("quality_meta") or {}
    top_actions = payload.get("top_actions") or []

    return {
        "node_id": rec.get("node_id"),
        "region": rec.get("region"),
        "model": rec.get("model"),
        "quality_score": rec.get("quality_score"),
        "event_ts": rec.get("event_ts"),
        "ingest_ts": rec.get("ingest_ts"),
        "summary_source": quality_meta.get("summary_source") or rec.get("summary_source") or payload.get("summary_source"),
        "pii_leak_risk": payload.get("pii_leak_risk"),
        "summary": payload.get("summary"),
        "top_actions": top_actions,
        # Optional nested meta as JSON string (keeps parquet simple)
        "payload_meta_json": json.dumps(payload.get("meta", {}), ensure_ascii=False),
    }


def write_parquet_atomic(df: pd.DataFrame, out_tmp: Path, out_final: Path):
    # Ensure list column has correct dtype
    if "top_actions" in df.columns:
        df["top_actions"] = df["top_actions"].apply(lambda x: x if isinstance(x, list) else ([] if x is None else [str(x)]))

    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, out_tmp)

    # Validate readable
    _ = pq.read_table(out_tmp, columns=["node_id"]).to_pandas()

    # Promote
    out_final.write_bytes(out_tmp.read_bytes())


def download_bronze_from_r2() -> bool:
    return download_file(S3_BUCKET, BRONZE_KEY, LOCAL_BRONZE)


def append_badrows(bad_rows: List[Dict[str, Any]]):
    if not bad_rows:
        return
    with LOCAL_BADROWS.open("a", encoding="utf-8") as f:
        for row in bad_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def upload_status(status: Dict[str, Any]):
    LOCAL_STATUS.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    upload_file(LOCAL_STATUS, S3_BUCKET, STATUS_KEY)


def upload_badrows_if_any():
    if LOCAL_BADROWS.exists() and LOCAL_BADROWS.stat().st_size > 0:
        upload_file(LOCAL_BADROWS, S3_BUCKET, BADROWS_KEY)


def rotate_prev_and_upload_new_silver():
    # If current silver exists remotely, copy it to prev by downloading then uploading
    if object_exists(S3_BUCKET, SILVER_KEY):
        if download_file(S3_BUCKET, SILVER_KEY, TMP_DIR / "remote_current.parquet"):
            upload_file(TMP_DIR / "remote_current.parquet", S3_BUCKET, SILVER_PREV_KEY)

    # Upload new silver
    upload_file(LOCAL_SILVER, S3_BUCKET, SILVER_KEY)


def heal_once() -> Dict[str, Any]:
    run_ts = utc_now_iso()

    # Ensure local temp files are clean for this run
    for p in [LOCAL_BRONZE, LOCAL_SILVER_TMP, LOCAL_SILVER]:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    bronze_ok = download_bronze_from_r2()
    if not bronze_ok or (not LOCAL_BRONZE.exists()) or LOCAL_BRONZE.stat().st_size == 0:
        status = {
            "run_ts": run_ts,
            "status": "bronze_missing",
            "reason": f"Could not download or bronze empty: s3://{S3_BUCKET}/{BRONZE_KEY}",
            "bronze_bucket": S3_BUCKET,
            "bronze_key": BRONZE_KEY,
        }
        upload_status(status)
        return status

    total_lines = 0
    rows: List[Dict[str, Any]] = []
    bad: List[Dict[str, Any]] = []

    with LOCAL_BRONZE.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                rec, payload = parse_bronze_line(line)
                rows.append(normalize_row(rec, payload))
            except Exception as e:
                bad.append(
                    {
                        "run_ts": run_ts,
                        "error": str(e),
                        "raw": line[:5000],
                    }
                )

    append_badrows(bad)

    if not rows:
        status = {
            "run_ts": run_ts,
            "status": "degraded",
            "reason": "No valid rows parsed from bronze",
            "total_lines_seen": total_lines,
            "rows_written": 0,
            "bad_rows_skipped": len(bad),
            "bronze_bucket": S3_BUCKET,
            "bronze_key": BRONZE_KEY,
        }
        upload_status(status)
        upload_badrows_if_any()
        return status

    df = pd.DataFrame(rows)

    try:
        write_parquet_atomic(df, LOCAL_SILVER_TMP, LOCAL_SILVER)
        rotate_prev_and_upload_new_silver()

        status = {
            "run_ts": run_ts,
            "status": "ok",
            "reason": "published_to_r2",
            "total_lines_seen": total_lines,
            "rows_written": int(len(df)),
            "bad_rows_skipped": int(len(bad)),
            "silver_bucket": S3_BUCKET,
            "silver_key": SILVER_KEY,
            "silver_prev_key": SILVER_PREV_KEY,
            "badrows_key": BADROWS_KEY,
        }
        upload_status(status)
        upload_badrows_if_any()
        return status

    except Exception as e:
        status = {
            "run_ts": run_ts,
            "status": "degraded",
            "reason": f"publish_failed: {e}",
            "total_lines_seen": total_lines,
            "rows_written": int(len(df)),
            "bad_rows_skipped": int(len(bad)),
            "silver_bucket": S3_BUCKET,
            "silver_key": SILVER_KEY,
        }
        upload_status(status)
        upload_badrows_if_any()
        return status


def main():
    print(f"[healer] started (R2 mode) interval={HEAL_INTERVAL_SECONDS}s bronze=s3://{S3_BUCKET}/{BRONZE_KEY} silver=s3://{S3_BUCKET}/{SILVER_KEY}", flush=True)
    while True:
        status = heal_once()
        print(f"[healer] {status.get('status')} run_ts={status.get('run_ts')} rows_written={status.get('rows_written')} bad_rows_skipped={status.get('bad_rows_skipped')} reason={status.get('reason')}", flush=True)
        time.sleep(HEAL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()