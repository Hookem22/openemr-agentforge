"""The compiled Red Team LangGraph -- orchestrator -> red_team -> target_adapter -> judge ->
(confirmed/partial -> documentation, not_confirmed -> orchestrator, capped). Mirrors
Gauntlet/Week 3/langgraph-diagram.mmd's "NEW / PROPOSED" graph.

One campaign = one graph.invoke() = one bounded unit of work, same execution model as the existing
Week 1-2 chat graph (agent/app/graph.py): triggered by something external (today: a manual script;
later: a schedule/webhook), runs start-to-finish, then the process goes idle again. See the earlier
discussion in this project's history on "what causes the Orchestrator to run" for the full reasoning.

MAX_ITERATIONS_PER_CAMPAIGN caps the not_confirmed -> orchestrator retry loop -- deliberately
mirroring agent/app/graph.py's MAX_HANDOFFS_PER_TURN pattern, and deliberately NOT the mistake this
platform's own THREAT_MODEL.md confirmed in the target (route_after_agent's uncapped tool-call loop).
An uncapped Orchestrator retry loop would be the exact same class of bug in our own code.
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.adapters.openemr_adapter import OpenEMRAdapter
from app.attack_templates import get_template
from app.db import insert_exploit_record, run_migrations
from app.documentation_agent import document
from app.judge_agent import judge
from app.orchestrator_agent import decide_next_target
from app.redteam_agent import generate_attack
from app.schemas import AttackSequence, ExploitRecord, JudgeVerdict, NextTarget, ObservedResponse, Verdict

MAX_ITERATIONS_PER_CAMPAIGN = 3


class RedTeamGraphState(TypedDict):
    target_id: str
    target_version: str
    next_target: dict | None
    attack: dict | None
    observed: dict | None
    verdict: dict | None
    exploit_record: dict | None
    report: dict | None
    iterations: int


_adapter_instance: OpenEMRAdapter | None = None


def _adapter() -> OpenEMRAdapter:
    """One authenticated adapter per process, not per node call -- re-authenticating (a full OAuth2
    login + consent round trip) on every attack would be needlessly slow and load-bearing on the
    target's own login rate limits."""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = OpenEMRAdapter()
        _adapter_instance.authenticate()
    return _adapter_instance


def orchestrator_node(state: RedTeamGraphState) -> dict:
    next_target = decide_next_target(state["target_id"], state["target_version"])
    return {
        "next_target": next_target.model_dump(mode="json"),
        "iterations": state.get("iterations", 0) + 1,
    }


def red_team_node(state: RedTeamGraphState) -> dict:
    next_target = NextTarget(**state["next_target"])
    template = get_template(next_target.attack_category)
    attack = generate_attack(
        category=next_target.attack_category,
        target_id=next_target.target_id,
        hint=template["hint"],
        generation_task=template["generation_task"],
        owasp_llm_category=template.get("owasp_llm_category"),
        escalate=next_target.escalate,
    )
    return {"attack": attack.model_dump(mode="json")}


def target_adapter_node(state: RedTeamGraphState) -> dict:
    attack = AttackSequence(**state["attack"])
    observed = _adapter().send(attack)
    return {"observed": observed.model_dump(mode="json")}


def judge_node(state: RedTeamGraphState) -> dict:
    # Isolation, enforced by construction: only attack + observed cross this boundary -- nothing
    # about the Red Team Agent's own generation call (its prompt, its model, its reasoning) is even
    # representable in RedTeamGraphState, so there's nothing here that COULD leak to the Judge.
    attack = AttackSequence(**state["attack"])
    observed = ObservedResponse(**state["observed"])
    verdict = judge(attack, observed)

    # Every verdict gets a row here, not just confirmed/partial ones -- this is what the
    # Orchestrator's coverage scoring reads (app/db.py's get_coverage_state). An earlier version of
    # this graph only inserted inside documentation_node (confirmed/partial only), which made every
    # not_confirmed attempt invisible to coverage tracking -- the Orchestrator would have kept
    # re-picking the same "under-covered" category forever, never learning it had actually been
    # tried. Caught by noticing exploit_records had no tool_misuse rows after 3 real attempts.
    record = ExploitRecord(
        target_id=state["target_id"],
        target_version=state["target_version"],
        attack_category=attack.attack_category,
        rubric_version=verdict.rubric_version,
        verdict=verdict.verdict,
        attack_sequence=attack,
        observed_response=observed,
        judge_verdict=verdict,
    )
    insert_exploit_record(record)
    return {"verdict": verdict.model_dump(mode="json"), "exploit_record": record.model_dump(mode="json")}


def route_after_judge(state: RedTeamGraphState) -> str:
    verdict = JudgeVerdict(**state["verdict"])
    if verdict.verdict in (Verdict.CONFIRMED, Verdict.PARTIAL):
        return "documentation"
    if state["iterations"] >= MAX_ITERATIONS_PER_CAMPAIGN:
        return "end"
    return "orchestrator"


def documentation_node(state: RedTeamGraphState) -> dict:
    record = ExploitRecord(**state["exploit_record"])
    report = document(record)
    return {"report": report.model_dump(mode="json")}


def build_graph():
    run_migrations()  # idempotent -- safe to call on every process start, same as a normal app boot

    graph = StateGraph(RedTeamGraphState)
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("red_team", red_team_node)
    graph.add_node("target_adapter", target_adapter_node)
    graph.add_node("judge", judge_node)
    graph.add_node("documentation", documentation_node)

    graph.set_entry_point("orchestrator")
    graph.add_edge("orchestrator", "red_team")
    graph.add_edge("red_team", "target_adapter")
    graph.add_edge("target_adapter", "judge")
    graph.add_conditional_edges(
        "judge",
        route_after_judge,
        {"documentation": "documentation", "orchestrator": "orchestrator", "end": END},
    )
    graph.add_edge("documentation", END)
    return graph.compile()
