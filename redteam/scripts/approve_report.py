#!/usr/bin/env python3
"""Resumes a campaign paused at the human_gate interrupt (see app/human_gate.py) -- the actual
human-approval action for a Critical/High severity report. Can be run minutes or days after the
campaign paused, from a totally different process: the paused state lives in Postgres via the
graph's checkpointer, not in the memory of whatever process ran scripts/run_campaign.py.

Usage:
    python scripts/approve_report.py <thread_id> approve
    python scripts/approve_report.py <thread_id> reject
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langgraph.types import Command  # noqa: E402

from app.graph import build_graph  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in ("approve", "reject"):
        print("Usage: python scripts/approve_report.py <thread_id> approve|reject", file=sys.stderr)
        return 1

    thread_id, decision_word = sys.argv[1], sys.argv[2]
    decision = decision_word == "approve"

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(Command(resume=decision), config=config)

    report = result.get("report")
    if report is None:
        print(f"No report found on thread {thread_id} -- was this thread actually paused at human_gate?")
        return 1
    print(f"Report {report['id']}: status is now {report['status']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
