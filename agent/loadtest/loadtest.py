"""Load/stress test for the deployed copilot-agent /chat endpoint (assignment engineering requirement:
10 & 50 concurrent users, p50/p95/p99 latency + error rate at each level).

Hits the real production agent service directly (bypasses the OpenEMR PHP proxy) with a bearer token
supplied via the AGENT_BEARER_TOKEN env var. Each simulated user sends exactly one chat turn (real
Anthropic API spend per request -- kept to one turn per user to bound cost).

Usage:
    AGENT_BEARER_TOKEN=... python loadtest.py --base-url https://copilot-agent-production-8af2.up.railway.app --users 10
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time

import httpx

# The 4 seeded sample patients (docs/seed-sample-patients.sql / seed-additional-patients.sql),
# fetched live from the deployed FHIR API -- see memory/railway-deployment.md for how these were seeded.
PATIENT_IDS = [
    "a2351838-8fd0-4823-b202-3fd7c4ed9ebe",  # Maria Gonzalez -- rich chart
    "a2351838-8fd9-408c-9811-decbbfe0488a",  # James Whitfield -- empty chart
    "a236cb84-4e5b-4fd6-89c8-700de739d977",  # Robert Chen -- unflagged drug/allergy conflict
    "a236cb84-4e62-478e-8066-2b81837016d0",  # Dorothy Simmons -- stale chart, verified-absent allergy
]

PROMPTS = [
    "Tell me about this patient",
    "What changed since the last visit?",
    "What medications and allergies are on file?",
    "Any abnormal labs I should know about?",
]


async def one_user(client: httpx.AsyncClient, base_url: str, token: str, idx: int) -> dict:
    patient_id = PATIENT_IDS[idx % len(PATIENT_IDS)]
    message = PROMPTS[idx % len(PROMPTS)]
    start = time.monotonic()
    try:
        resp = await client.post(
            f"{base_url}/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"patient_id": patient_id, "message": message},
            timeout=90.0,
        )
        elapsed = time.monotonic() - start
        return {"ok": resp.status_code == 200, "status": resp.status_code, "latency": elapsed}
    except Exception as exc:  # noqa: BLE001 -- load test needs to record any failure, not crash the run
        elapsed = time.monotonic() - start
        return {"ok": False, "status": None, "latency": elapsed, "error": str(exc)}


async def run_level(base_url: str, token: str, concurrency: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        tasks = [one_user(client, base_url, token, i) for i in range(concurrency)]
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


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--users", type=int, nargs="+", default=[10, 50])
    args = parser.parse_args()

    token = os.environ.get("AGENT_BEARER_TOKEN")
    if not token:
        raise SystemExit("AGENT_BEARER_TOKEN env var is required")

    for concurrency in args.users:
        results = await run_level(args.base_url, token, concurrency)
        summarize(f"{concurrency} concurrent users", results)


if __name__ == "__main__":
    asyncio.run(main())
