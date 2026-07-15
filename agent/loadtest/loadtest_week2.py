"""Load/stress test for Week 2's two new endpoints/flows: /ingest (document upload + extraction)
and RAG-triggering /chat calls (evidence_retriever). Same methodology as loadtest.py (plain
asyncio + httpx against the real deployed production agent), extended for these two new,
much-more-expensive-per-call flows -- concurrency levels are deliberately smaller than
loadtest.py's 1-200 sweep to bound real Anthropic/Voyage spend (each /ingest call alone costs
~$0.025, see Gauntlet/Week 2/COST_ANALYSIS.md).

Usage:
    AGENT_BEARER_TOKEN=... python loadtest_week2.py --base-url https://copilot-agent-production-8af2.up.railway.app \
        --mode ingest --users 1 3 5
    AGENT_BEARER_TOKEN=... python loadtest_week2.py --base-url https://copilot-agent-production-8af2.up.railway.app \
        --mode rag-chat --users 1 5 10
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import statistics
import time

import httpx

MARIA_PATIENT_ID = "a2351838-8fd0-4823-b202-3fd7c4ed9ebe"  # FHIR uuid
MARIA_PID = "1"  # OpenEMR-native int pid

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eval", "fixtures", "maria_gonzalez_lab.pdf")

RAG_PROMPTS = [
    "What's the recommended target A1c for this patient given her diabetes?",
    "What blood pressure target should we be aiming for given her hypertension?",
    "Is there a guideline-based concern with her current medication regimen?",
]


async def one_ingest(client: httpx.AsyncClient, base_url: str, token: str, idx: int) -> dict:
    with open(FIXTURE_PATH, "rb") as f:
        data = f.read()
    start = time.monotonic()
    try:
        resp = await client.post(
            f"{base_url}/ingest",
            headers={"Authorization": f"Bearer {token}"},
            data={"patient_id": MARIA_PID, "doc_type": "lab_pdf", "patient_uuid": MARIA_PATIENT_ID},
            files={"file": (f"loadtest-{idx}.pdf", data, "application/pdf")},
            timeout=120.0,
        )
        elapsed = time.monotonic() - start
        return {"ok": resp.status_code == 200, "status": resp.status_code, "latency": elapsed}
    except Exception as exc:  # noqa: BLE001 -- load test needs to record any failure, not crash the run
        elapsed = time.monotonic() - start
        return {"ok": False, "status": None, "latency": elapsed, "error": str(exc)}


async def one_rag_chat(client: httpx.AsyncClient, base_url: str, token: str, idx: int) -> dict:
    prompt = RAG_PROMPTS[idx % len(RAG_PROMPTS)]
    start = time.monotonic()
    try:
        resp = await client.post(
            f"{base_url}/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"patient_id": MARIA_PATIENT_ID, "message": prompt},
            timeout=90.0,
        )
        elapsed = time.monotonic() - start
        ok = resp.status_code == 200
        routed_to_evidence = False
        if ok:
            body = resp.json()
            routed_to_evidence = any(h.get("to") == "evidence_retriever" for h in body.get("handoff_log", []))
        return {"ok": ok, "status": resp.status_code, "latency": elapsed, "routed_to_evidence": routed_to_evidence}
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - start
        return {"ok": False, "status": None, "latency": elapsed, "error": str(exc)}


async def run_level(base_url: str, token: str, concurrency: int, mode: str) -> list[dict]:
    fn = one_ingest if mode == "ingest" else one_rag_chat
    async with httpx.AsyncClient() as client:
        tasks = [fn(client, base_url, token, i) for i in range(concurrency)]
        return await asyncio.gather(*tasks)


def summarize(label: str, results: list[dict]) -> None:
    latencies = sorted(r["latency"] for r in results)
    errors = [r for r in results if not r["ok"]]
    n = len(latencies)

    def pct(p: float) -> float:
        idx = min(n - 1, int(round(p * (n - 1))))
        return latencies[idx]

    print(f"\n=== {label} ({n} requests) ===")
    print(f"error rate: {len(errors)}/{n} ({100 * len(errors) / n:.1f}%)")
    if errors:
        for e in errors[:5]:
            print(f"  error: status={e['status']} detail={e.get('error', '')[:200]}")
    print(f"p50: {pct(0.50):.2f}s  p95: {pct(0.95):.2f}s  p99: {pct(0.99):.2f}s")
    print(f"min: {latencies[0]:.2f}s  max: {latencies[-1]:.2f}s  mean: {statistics.mean(latencies):.2f}s")
    if results and "routed_to_evidence" in results[0]:
        routed = sum(1 for r in results if r.get("routed_to_evidence"))
        print(f"routed to evidence_retriever: {routed}/{n}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mode", choices=["ingest", "rag-chat"], required=True)
    parser.add_argument("--users", type=int, nargs="+", default=[1, 5])
    args = parser.parse_args()

    token = os.environ.get("AGENT_BEARER_TOKEN")
    if not token:
        raise SystemExit("AGENT_BEARER_TOKEN env var is required")

    for concurrency in args.users:
        results = await run_level(args.base_url, token, concurrency, args.mode)
        summarize(f"{args.mode}: {concurrency} concurrent users", results)


if __name__ == "__main__":
    asyncio.run(main())
