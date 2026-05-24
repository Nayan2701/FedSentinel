import os
import time
import json
from pathlib import Path
from datetime import datetime, timezone
from seed_bronze import seed_if_missing_or_empty

import pandas as pd

INTERVAL = int(os.getenv("HEAL_INTERVAL_SECONDS", "60"))

BRONZE_PATH = Path(os.getenv("BRONZE_PATH", "/data/inbox/edge_insights.jsonl"))
SILVER_PATH = Path(os.getenv("SILVER_PATH", "/data/inbox/edge_insights_silver.parquet"))
BADROWS_PATH = Path(os.getenv("BADROWS_PATH", "/data/inbox/edge_insights_bad_rows.jsonl"))
METRICS_PATH = Path(os.getenv("METRICS_PATH", "/data/inbox/healer_metrics.jsonl"))
STATUS_PATH = Path(os.getenv("STATUS_PATH", "/data/inbox/healer_status.json"))

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def heal_once() -> dict:
    rows = []
    bad_rows = []

    total = 0
    bad = 0
    seed_if_missing_or_empty(BRONZE_PATH, Path(__file__).with_name("sample_bronze.jsonl"))


    if not BRONZE_PATH.exists():
        metrics = {"run_ts": utc_now(), "status": "bronze_missing", "bronze_path": str(BRONZE_PATH)}
        _append_metrics(metrics)
        return metrics

    with BRONZE_PATH.open("r", encoding="utf-8", errors="replace") as fin:
        for line in fin:
            total += 1
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)

                payload_raw = obj.get("payload")
                if not isinstance(payload_raw, str):
                    raise ValueError("payload not a string")

                payload = json.loads(payload_raw)
                if not isinstance(payload, dict):
                    raise ValueError("payload not an object")

                meta = payload.get("meta") or {}
                quality_meta = obj.get("quality_meta") or {}

                rows.append(
                    {
                        "node_id": obj.get("node_id"),
                        "region": obj.get("region"),
                        "model": obj.get("model"),
                        "quality_score": obj.get("quality_score"),
                        "event_ts": obj.get("event_ts"),
                        "ingest_ts": obj.get("ingest_ts"),
                        "summary_source": quality_meta.get("summary_source"),
                        "pii_leak_risk": payload.get("pii_leak_risk"),
                        "summary": payload.get("summary"),
                        "top_actions": payload.get("top_actions"),
                        "events": meta.get("events"),
                        "avg_latency_ms": meta.get("avg_latency_ms"),
                        "top_ip_class": meta.get("top_ip_class"),
                    }
                )
            except Exception as e:
                bad += 1
                bad_rows.append({"ts": utc_now(), "error": str(e), "raw": s[:5000]})

    df = pd.DataFrame(rows)

    # Atomic + rollback-capable publish
    tmp = SILVER_PATH.with_suffix(SILVER_PATH.suffix + ".tmp")
    prev = SILVER_PATH.with_suffix(SILVER_PATH.suffix + ".prev")
    SILVER_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 1) write tmp
        df.to_parquet(tmp, index=False)

        # 2) validate tmp is readable and (if bronze had lines) non-empty
        check = pd.read_parquet(tmp)
        if total > 0 and len(check) == 0:
            raise ValueError("validation failed: empty parquet after non-empty bronze")

        # 3) rotate last-known-good
        if SILVER_PATH.exists():
            try:
                SILVER_PATH.replace(prev)
            except Exception:
                # best-effort rotate; don't block publish
                pass

        # 4) promote tmp -> silver
        tmp.replace(SILVER_PATH)

        status = "ok"
        reason = ""
    except Exception as e:
        # Leave last known good in place; cleanup tmp
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        status = "degraded"
        reason = f"publish_failed: {e}"

    # quarantine bad rows (best-effort)
    if bad_rows:
        try:
            with BADROWS_PATH.open("a", encoding="utf-8") as f:
                for r in bad_rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception:
            pass

    metrics = {
        "run_ts": utc_now(),
        "status": status,
        "reason": reason,
        "bronze_path": str(BRONZE_PATH),
        "silver_path": str(SILVER_PATH),
        "total_lines_seen": total,
        "rows_written": int(len(df)),
        "bad_rows_skipped": bad,
    }
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(metrics), encoding="utf-8")
    except Exception:
        pass
    _append_metrics(metrics)
    return metrics

def _append_metrics(metrics: dict) -> None:
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with METRICS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(metrics) + "\n")

def main():
    print(
        f"[healer] starting; interval={INTERVAL}s bronze={BRONZE_PATH} silver={SILVER_PATH}",
        flush=True,
    )
    while True:
        try:
            m = heal_once()
            print(f"[healer] {m}", flush=True)
        except Exception as e:
            print(f"[healer] ERROR: {e}", flush=True)
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()