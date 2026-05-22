import asyncio
import json
import os
import random
import re
from collections import Counter
from datetime import datetime, timezone

import requests
from nats.aio.client import Client as NATS

NODE_ID = os.getenv("NODE_ID", "edge-1")
REGION = os.getenv("REGION", "us-east")
MODEL = os.getenv("OLLAMA_MODEL", "tinyllama:latest")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
NATS_URL = os.getenv("NATS_URL", "nats://fedsentinel-nats:4222")
SUBJECT = os.getenv("NATS_SUBJECT", "fedsentinel.edge.insights")

# Default slower interval for laptop stability
INTERVAL_SEC = float(os.getenv("INTERVAL_SEC", "30"))
REQUEST_TIMEOUT_SEC = float(os.getenv("OLLAMA_TIMEOUT_SEC", "60"))


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def generate_local_telemetry() -> str:
    users = ["alex", "sam", "riley", "taylor", "jordan"]
    actions = ["login", "logout", "file_access", "password_reset", "api_error", "suspicious_ip"]
    email = f"{random.choice(users)}@example.com"
    ip = f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
    action = random.choice(actions)
    status = random.choice(["ok", "fail"])
    latency_ms = random.randint(5, 900)

    # includes PII-like field (email) locally to prove we do NOT send raw upstream
    return (
        f"[{utc_now_iso()}] region={REGION} node={NODE_ID} user_email={email} "
        f"src_ip={ip} action={action} status={status} latency_ms={latency_ms}"
    )


def parse_event(line: str) -> dict:
    out = {}
    for key in ["action", "status", "latency_ms", "src_ip", "user_email"]:
        m = re.search(rf"{key}=([^\s]+)", line)
        if m:
            out[key] = m.group(1)

    if "latency_ms" in out:
        try:
            out["latency_ms"] = int(out["latency_ms"])
        except Exception:
            pass

    return out


def redact_ip(ip: str) -> str:
    # 10.12.34.56 -> 10.x.x.x
    if not ip or "." not in ip:
        return "unknown"
    first = ip.split(".")[0]
    return f"{first}.x.x.x"


def compute_structured_insight(raw_events: list[str]) -> dict:
    parsed = [parse_event(e) for e in raw_events]

    actions = [p.get("action", "unknown") for p in parsed]
    statuses = [p.get("status", "unknown") for p in parsed]
    latencies = [p.get("latency_ms", 0) for p in parsed if isinstance(p.get("latency_ms"), int)]

    top_actions = [a for a, _ in Counter(actions).most_common(3)]

    # simple local-only risk heuristic
    risk = "low"
    if "suspicious_ip" in actions:
        risk = "high"
    elif "password_reset" in actions and "fail" in statuses:
        risk = "medium"
    elif "api_error" in actions and "fail" in statuses:
        risk = "medium"

    counts = {"low": 0, "medium": 0, "high": 0}
    counts[risk] = len(raw_events)

    avg_latency = int(sum(latencies) / max(1, len(latencies))) if latencies else 0
    fallback_summary = f"Risk={risk}; Top={','.join(top_actions)}; Events={len(raw_events)}; AvgLatencyMs={avg_latency}"

    ip_classes = [redact_ip(p.get("src_ip")) for p in parsed if p.get("src_ip")]
    top_ip_class = Counter(ip_classes).most_common(1)[0][0] if ip_classes else "unknown"

    structured = {
        "insight_type": "edge_security_summary",
        "summary": fallback_summary,
        "counts": counts,
        "pii_leak_risk": risk,
        "top_actions": top_actions,
        "meta": {
            "top_ip_class": top_ip_class,
            "events": len(raw_events),
            "avg_latency_ms": avg_latency,
        },
    }
    return structured


def build_llm_prompt(structured: dict) -> str:
    # Force the model into a tiny, copyable format and discourage instruction echoing.
    risk = structured.get("pii_leak_risk")
    top_actions = structured.get("top_actions", [])
    events = structured.get("meta", {}).get("events", 0)

    return f"""
Write ONE short dashboard sentence using this template exactly:
Risk={risk}; Top={",".join(top_actions)}; Events={events}.

Rules:
- Do NOT mention these rules.
- Do NOT mention PII, emails, user IDs, or IPs.
- Do NOT add quotes or markdown.
- Output plain text only.
- Max 20 words.
""".strip()


def ollama_summarize(prompt: str) -> str:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 30,
            "num_ctx": 512,
            "stop": ["\n", "```"],
        },
    }
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=REQUEST_TIMEOUT_SEC)
    r.raise_for_status()
    data = r.json()

    text = (data.get("response") or "").strip()
    if not text:
        return ""

    # hard sanitize: keep it one line, short
    one_line = text.splitlines()[0].strip()
    if len(one_line) > 200:
        one_line = one_line[:200]

    # guardrail: if model echoed instructions, discard and fall back
    bad_markers = ["rule", "rules", "pii", "output", "template", "dashboard", "do not"]
    if any(m in one_line.lower() for m in bad_markers):
        return ""

    return one_line


async def main():
    nc = NATS()
    await nc.connect(servers=[NATS_URL])
    print(f"[{NODE_ID}] connected to NATS: {NATS_URL}")
    print(f"[{NODE_ID}] using Ollama: {OLLAMA_URL} model={MODEL}")

    while True:
        raw_events = [generate_local_telemetry() for _ in range(3)]
        structured = compute_structured_insight(raw_events)

        print(f"[{NODE_ID}] generating summary...")

        summary_source = "fallback"
        try:
            llm_summary = ollama_summarize(build_llm_prompt(structured))
            if llm_summary:
                structured["summary"] = llm_summary
                summary_source = "llm"
        except Exception as e:
            # keep fallback summary; just annotate meta
            summary_source = f"fallback_error:{type(e).__name__}"

        insight_obj = {
            "node_id": NODE_ID,
            "event_ts": utc_now_iso(),
            "insight_type": "edge_security_summary",
            "quality_score": 0.95 if summary_source == "llm" else 0.75,
            # payload is ALWAYS valid JSON (LLM never controls structure)
            "payload": json.dumps(structured, ensure_ascii=False),
            "quality_meta": {"reason": "ok", "summary_source": summary_source},
            "region": REGION,
            "model": MODEL,
        }

        await nc.publish(SUBJECT, json.dumps(insight_obj).encode("utf-8"))
        print(f"[{NODE_ID}] published insight meta={insight_obj['quality_meta']}")

        await asyncio.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main())