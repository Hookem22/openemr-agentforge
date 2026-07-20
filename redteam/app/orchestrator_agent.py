"""Orchestrator Agent -- reads coverage state, decides what the Red Team Agent attacks next.

Deliberately NOT one opaque LLM call deciding everything: the assignment's own framing ("part of the
challenge is determining when AI-driven approaches are useful, when deterministic systems are more
reliable") applies directly here. Which category is highest-priority is arithmetic on real counts --
a deterministic, auditable scoring function owns that decision (`_score_categories`); Haiku is used
only to write the human-readable `rationale` string, never to pick the category itself. This also
means the Orchestrator's core decision is unit-testable without any API call.

Trust boundary (ARCHITECTURE.md roster): read-only over the Exploit DB. This module never imports
redteam_agent or judge_agent, and never writes to exploit_records/vulnerability_reports -- it can't
generate or judge an attack even by accident, only decide what's requested next.
"""
from __future__ import annotations

from anthropic import Anthropic

from app.config import settings
from app.db import get_coverage_state
from app.schemas import AttackCategory, CoverageState, NextTarget

# A category gets escalated to the stronger model once it's been tried at least this many times
# with zero confirmed/partial hits -- cheap-model volume gets a fair shot first, matching how real
# red teams triage cheap fuzzing before expensive manual effort (ARCHITECTURE.md's model-tiering
# decision), rather than escalating on the very first not-confirmed result.
ESCALATE_AFTER_N_MISSES = 2


def _score_categories(coverage: CoverageState) -> dict[AttackCategory, float]:
    """Higher score = higher priority to attack next. Purely a function of counts already in the
    DB -- no LLM involved, so this is reproducible and independently checkable by re-running it
    against the same CoverageState."""
    scores: dict[AttackCategory, float] = {}
    for category in AttackCategory:
        counts = coverage.categories.get(category.value)
        if counts is None:
            scores[category] = 1000.0  # never attempted at all -- maximum priority
            continue
        total = counts.confirmed + counts.partial + counts.not_confirmed
        # Heavily favors under-covered categories (few/no attempts); a category that already has a
        # confirmed or partial hit is deprioritized relative to one that's never landed anything,
        # since the immediate coverage goal is breadth across categories, not depth on one.
        coverage_score = 1000.0 / (total + 1)
        hit_penalty = 200.0 * (counts.confirmed + counts.partial)
        scores[category] = max(coverage_score - hit_penalty, 1.0)
    return scores


def _should_escalate(coverage: CoverageState, category: AttackCategory) -> bool:
    counts = coverage.categories.get(category.value)
    if counts is None:
        return False
    return counts.not_confirmed >= ESCALATE_AFTER_N_MISSES and counts.confirmed == 0 and counts.partial == 0


def _client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return Anthropic(api_key=settings.anthropic_api_key)


def _write_rationale(category: AttackCategory, coverage: CoverageState, score: float, escalate: bool) -> str:
    counts = coverage.categories.get(category.value)
    counts_text = (
        f"{counts.confirmed} confirmed, {counts.partial} partial, {counts.not_confirmed} not-confirmed"
        if counts
        else "no prior attempts"
    )
    resp = _client().messages.create(
        model=settings.redteam_model,  # Haiku -- this is a one-line summary, not a judgment call
        max_tokens=120,
        system=(
            "You write a single, short (1-2 sentence) explanation of a security-testing "
            "prioritization decision that has already been made by a scoring function. You do not "
            "re-decide anything -- just explain the given facts plainly."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Category picked: {category.value}. Priority score: {score:.1f}. "
                    f"Prior results for this category: {counts_text}. "
                    f"Escalating to a stronger model: {escalate}. "
                    "Write the rationale."
                ),
            }
        ],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def decide_next_target(target_id: str, target_version: str) -> NextTarget:
    coverage = get_coverage_state(target_id, target_version)
    scores = _score_categories(coverage)
    category = max(scores, key=lambda c: (scores[c], -list(AttackCategory).index(c)))
    escalate = _should_escalate(coverage, category)
    rationale = _write_rationale(category, coverage, scores[category], escalate)
    return NextTarget(
        target_id=target_id,
        attack_category=category,
        escalate=escalate,
        rationale=rationale,
        priority_score=scores[category],
    )
