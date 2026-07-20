"""Narrow, per-agent state slices -- NOT one shared state object passed to every node. Each agent
function below only receives the fields it actually needs; this is what makes the Judge/Red Team
isolation (ARCHITECTURE.md decision #2) real in code, not just a convention someone could
accidentally violate by passing the whole graph state through. redteam/app/graph.py (added Tuesday,
once the Orchestrator exists) wires these into a real LangGraph StateGraph -- until then,
evals/run_redteam_eval.py calls the agent functions directly in sequence.
"""
from __future__ import annotations

from typing import TypedDict

from app.schemas import AttackSequence, JudgeVerdict, ObservedResponse


class RedTeamState(TypedDict):
    """Everything the Red Team Agent's own generation logic gets. Notably absent: any prior Judge
    verdict detail beyond a bare pass/fail count -- the Red Team Agent knows a category is
    under-covered, not *why* a specific past attempt failed in the Judge's own words, so it can't
    reverse-engineer the rubric from Judge feedback."""

    attack_category: str
    owasp_llm_category: str | None
    target_id: str
    mutation_hint: str  # e.g. "first attempt" | "prior attempt was too obvious, be more indirect"
    prior_attempt_count: int


class JudgeState(TypedDict):
    """Everything the Judge Agent gets -- and ONLY this. No field here can ever hold the Red Team
    Agent's own reasoning/prompt/system message; enforced by judge_agent.judge() only accepting an
    AttackSequence + ObservedResponse as parameters, never a RedTeamState."""

    attack: AttackSequence
    observed: ObservedResponse
    rubric_version: str


class ExploitState(TypedDict):
    """What's left after a confirmed verdict, handed to the Documentation Agent (added Tuesday)."""

    attack: AttackSequence
    observed: ObservedResponse
    verdict: JudgeVerdict
