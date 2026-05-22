import asyncio
import json
import os
import time
from datetime import datetime, timezone

from nats.aio.client import Client as NATS

NATS_URL = os.getenv("NATS_URL", "nats://fedsentinel-nats:4222")
SUBJECT = os.getenv("NATS_SUBJECT", "fedsentinel.edge.insights")
INBOX_PATH = os.getenv("INBOX_PATH","/data/inbox/edge_insights.jsonl")

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

async def main():
    nc= NATS()
    await nc.connect(servers=[NATS_URL])

    os.makedirs(os.path.dirname(INBOX_PATH), exist_ok=True)
    print(f"[central-ingestor] connected to NATS: {NATS_URL}")
    print(f"[central-ingestor] subscribing: {SUBJECT}")
    print(f"[central-ingestor] writing inbox: {INBOX_PATH}")

    async def handler(msg):
        raw = msg.data.decode("utf-8", errors="replace").strip()
        try:
            obj = json.loads(raw)
        except Exception:
            obj = {
                "ingest_ts": utc_now_iso(),
                "parse_error": True,
                "raw": raw,
            }
        obj.setdefault("ingest_ts", utc_now_iso())
        
        line = json.dumps(obj,ensure_ascii= False)
        with open(INBOX_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        print(f"[central-ingestor] wrote 1 event @ {obj.get('ingest_ts')}")
    await nc.subscribe(SUBJECT, cb=handler)

    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
