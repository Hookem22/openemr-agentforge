#!/usr/bin/env python3
"""Runs ONE campaign through the real compiled LangGraph (app/graph.py) live against the deployed
target: orchestrator picks a category from real coverage state -> red_team generates an attack ->
target_adapter delivers it -> judge scores it -> documentation writes a report if confirmed/partial,
or loops back to the orchestrator (capped) if not.

This is the Orchestrator-driven counterpart to evals/run_redteam_eval.py's fixed-seed-list runner --
that script is still the right tool for "run all known categories once and check results"; this one
demonstrates the actual autonomous decision loop (ARCHITECTURE.md's core claim: "which agent decides
what to test next" is a real, running answer, not just a diagram).

Usage:
    cd redteam && source venv/bin/activate && python scripts/run_campaign.py
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.graph import build_graph  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Reuse an existing (e.g. previously-paused) campaign thread instead of starting a new one.",
    )
    args = parser.parse_args()

    print(f"Target: {settings.target_id} @ {settings.target_base_url} (version={settings.target_version})")
    graph = build_graph()

    thread_id = args.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    print(f"Campaign thread_id: {thread_id}  (needed to approve a paused Critical/High report later)")

    initial_state = {
        "target_id": settings.target_id,
        "target_version": settings.target_version,
        "next_target": None,
        "attack": None,
        "observed": None,
        "verdict": None,
        "exploit_record": None,
        "report": None,
        "report_error": None,
        "iterations": 0,
    }

    for step in graph.stream(initial_state, config=config):
        for node_name, node_output in step.items():
            if node_name == "__interrupt__":
                interrupt = node_output[0]
                print("\n=== HUMAN GATE -- paused, waiting for approval ===")
                print(f"  report_id={interrupt.value['report_id']} severity={interrupt.value['severity']}")
                print(f"  {interrupt.value['description']}")
                print(
                    f"  To approve: python scripts/approve_report.py {thread_id} approve\n"
                    f"  To reject:  python scripts/approve_report.py {thread_id} reject"
                )
                continue
            print(f"\n=== {node_name} ===")
            if node_name == "orchestrator" and node_output.get("next_target"):
                nt = node_output["next_target"]
                print(f"  category={nt['attack_category']} escalate={nt['escalate']} score={nt['priority_score']:.1f}")
                print(f"  rationale: {nt['rationale']}")
            elif node_name == "red_team" and node_output.get("attack"):
                a = node_output["attack"]
                print(f"  attack_id={a['attack_id']}")
                for t in a["turns"]:
                    print(f"  turn: pid={t['pid']} message={t['message']!r}")
            elif node_name == "target_adapter" and node_output.get("observed"):
                for t in node_output["observed"]["turns"]:
                    print(f"  status={t['status']} http={t['http_status']}")
                    if t.get("response_text"):
                        print(f"  response: {t['response_text'][:300]!r}")
            elif node_name == "judge" and node_output.get("verdict"):
                v = node_output["verdict"]
                print(f"  VERDICT: {v['verdict']} (severity={v.get('severity')}) -- {v['rationale']}")
            elif node_name == "documentation" and node_output.get("report"):
                r = node_output["report"]
                print(f"  Report {r['id']} written (status={r['status']}): {r['description'][:200]}")
            elif node_name == "documentation" and node_output.get("report_error"):
                print(f"  Documentation FAILED (exploit already safely recorded): {node_output['report_error']}")
            elif node_name == "human_gate" and node_output.get("report"):
                r = node_output["report"]
                print(f"  Report {r['id']} resumed with status={r['status']}")

    print("\n=== Campaign complete (or paused -- see HUMAN GATE output above) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
