"""Human approval gate -- a real LangGraph interrupt, not a bespoke pending-flag workaround.
Only Critical/High severity reports ever reach this node (see documentation_agent.py's severity
branch); Low/Medium auto-publish and never touch this code at all.

The graph pauses here (see app/graph.py's PostgresSaver-backed checkpointer) until something calls
`graph.invoke(Command(resume=...), config={"configurable": {"thread_id": ...}})` with the SAME
thread_id -- which can happen minutes, hours, or days later, from a completely different process,
since the paused state lives in Postgres, not this process's memory. See
redteam/scripts/approve_report.py for the actual approval action.
"""
from __future__ import annotations

from langgraph.types import interrupt

from app.db import publish_report
from app.schemas import VulnerabilityReport


def human_gate_node(state: dict) -> dict:
    report = VulnerabilityReport(**state["report"])

    decision = interrupt(
        {
            "report_id": report.id,
            "severity": report.severity.value,
            "description": report.description,
            "clinical_impact": report.clinical_impact,
            "reproduction_steps": report.reproduction_steps,
            "remediation_recommendation": report.remediation_recommendation,
            "prompt": "Approve this Critical/High report for publication? (True/False)",
        }
    )

    if decision is True:
        publish_report(report.id)
        updated = dict(state["report"])
        updated["status"] = "published"
        return {"report": updated}

    # Explicit rejection (or anything falsy) -- the report stays pending_approval in the DB, exactly
    # where documentation_agent.py left it. No separate "rejected" state exists yet: a human
    # deciding a Critical/High finding isn't real is rare enough, and important enough, that it's
    # deliberately left as a manual DB/report note today rather than modeled in the schema before
    # there's a real case to model it from.
    return {"report": state["report"]}
