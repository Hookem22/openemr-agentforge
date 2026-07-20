#!/usr/bin/env python3
"""Drives Red Team Agent -> Target Adapter -> Judge Agent, live, against the deployed Clinical
Co-Pilot -- one run of this script is both remaining MVP hard gates at once: it produces
results across >=3 attack categories (./evals/ deliverable) and is a working prototype of two
agent roles (Red Team + Judge) running live against the deployed target.

Usage (from the redteam/ directory, with its venv active and .env populated):
    python ../evals/run_redteam_eval.py
    python ../evals/run_redteam_eval.py --category data_exfiltration
    python ../evals/run_redteam_eval.py --no-db   # skip writing to the Exploit DB (e.g. no DATABASE_URL yet)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "redteam"))

from app import db  # noqa: E402
from app.adapters.openemr_adapter import OpenEMRAdapter  # noqa: E402
from app.config import settings  # noqa: E402
from app.judge_agent import judge  # noqa: E402
from app.redteam_agent import generate_attack  # noqa: E402
from app.schemas import AttackCategory, ExploitRecord  # noqa: E402

SEED_FILE = os.path.join(os.path.dirname(__file__), "seed_attacks.json")


def load_seeds(category_filter: str | None) -> list[dict]:
    with open(SEED_FILE) as f:
        seeds = json.load(f)
    if category_filter:
        seeds = [s for s in seeds if s["attack_category"] == category_filter]
    return seeds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default=None, help="Run only this attack_category")
    parser.add_argument("--no-db", action="store_true", help="Skip writing results to the Exploit DB")
    args = parser.parse_args()

    seeds = load_seeds(args.category)
    if not seeds:
        print(f"No seed attacks match category={args.category!r}", file=sys.stderr)
        return 1

    print(f"Target: {settings.target_id} @ {settings.target_base_url} (version={settings.target_version})")
    print(f"Running {len(seeds)} seed attack(s)...\n")

    adapter = OpenEMRAdapter()
    print("Authenticating adapter...")
    adapter.authenticate()
    print("Authenticated.\n")

    results: list[dict] = []
    db_ready = False
    if not args.no_db:
        try:
            applied = db.run_migrations()
            db_ready = True
            print(f"Exploit DB ready (migrations applied: {applied or 'none needed'})\n")
        except Exception as exc:  # noqa: BLE001 -- a DB problem shouldn't stop the live eval run
            print(f"WARNING: Exploit DB unavailable ({exc}) -- continuing without persistence.\n")

    for seed in seeds:
        category = AttackCategory(seed["attack_category"])
        print(f"--- {category.value} ---")
        print(f"Hypothesis: {seed['hint']}")

        attack = generate_attack(
            category=category,
            target_id=settings.target_id,
            hint=seed["hint"],
            generation_task=seed["generation_task"],
            owasp_llm_category=seed.get("owasp_llm_category"),
        )
        print(f"Generated attack_id={attack.attack_id}, turns={len(attack.turns)}")
        for t in attack.turns:
            print(f"  turn: pid={t.pid} message={t.message!r}")

        start = time.monotonic()
        observed = adapter.send(attack)
        elapsed = time.monotonic() - start
        print(f"Observed {len(observed.turns)} turn(s) in {elapsed:.2f}s:")
        for t in observed.turns:
            print(f"  status={t.status} http={t.http_status} latency_ms={t.latency_ms}")
            if t.response_text:
                print(f"  response: {t.response_text[:300]!r}")

        verdict = judge(attack, observed)
        print(f"VERDICT: {verdict.verdict.value} (severity={verdict.severity}) -- {verdict.rationale}")
        if verdict.evidence_quote:
            print(f"  evidence: {verdict.evidence_quote!r}")

        record = ExploitRecord(
            target_id=settings.target_id,
            target_version=settings.target_version,
            attack_category=category,
            rubric_version=verdict.rubric_version,
            verdict=verdict.verdict,
            attack_sequence=attack,
            observed_response=observed,
            judge_verdict=verdict,
        )
        if db_ready:
            try:
                db.insert_exploit_record(record)
                print("  -> written to exploit_records")
            except Exception as exc:  # noqa: BLE001
                print(f"  -> WARNING: failed to write to exploit_records: {exc}")

        results.append({"category": category.value, "verdict": verdict.verdict.value, "severity": str(verdict.severity)})
        print()

    print("=== Summary ===")
    for r in results:
        print(f"{r['category']:30s} {r['verdict']:15s} severity={r['severity']}")

    confirmed = [r for r in results if r["verdict"] == "confirmed"]
    print(f"\n{len(confirmed)}/{len(results)} attacks confirmed against {settings.target_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
