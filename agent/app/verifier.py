"""Deterministic (non-LLM) verification layer -- ARCHITECTURE.md Section 5.

The model is forced (via the `provide_answer` tool, see graph.py) to emit every claim as
`{text, source}`, never as free prose. This module then checks each claim's source against what
this turn's tool calls *actually* returned -- a code-level check against ground truth, not a
second model call grading the first. Claims that fail are stripped before the user ever sees them
(fail closed). This is deliberately boring, dumb code: the entire point is that it cannot be argued
into accepting an ungrounded claim the way a second LLM call could be.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VerificationResult:
    verified_claims: list[dict] = field(default_factory=list)
    stripped_claims: list[dict] = field(default_factory=list)

    @property
    def strip_rate(self) -> float:
        total = len(self.verified_claims) + len(self.stripped_claims)
        return (len(self.stripped_claims) / total) if total else 0.0


def verify_claims(claims: list[dict], tool_results_this_turn: list[dict]) -> VerificationResult:
    """
    claims: [{"text": str, "source": {"resource_type": str, "resource_id": str}}
                        | {"text": str, "source": {"type": "no_data", "resource_type": str}}]
    tool_results_this_turn: every item returned by every tool call made this turn (each already
        carrying resource_type/id/date -- see tools.py's allow-list shape).
    """
    available_citations = {
        (item["resource_type"], item["id"]) for item in tool_results_this_turn if item.get("id")
    }
    # A resource_type counts as "confirmed empty this turn" only if at least one tool call for it
    # happened and returned nothing to work with here -- callers pass this in explicitly (see
    # graph.py, which tracks which tools were actually called and what they returned).
    empty_resource_types = {
        item["resource_type"] for item in tool_results_this_turn if item.get("_empty_marker")
    }

    result = VerificationResult()
    for claim in claims:
        source = claim.get("source") or {}
        text = claim.get("text", "")

        if source.get("type") == "no_data":
            if source.get("resource_type") in empty_resource_types:
                result.verified_claims.append(claim)
            else:
                result.stripped_claims.append(
                    {"text": text, "reason": "claimed absence of data not confirmed by an actual empty tool result this turn"}
                )
            continue

        key = (source.get("resource_type"), source.get("resource_id"))
        if key[0] and key[1] and key in available_citations:
            result.verified_claims.append(claim)
        else:
            result.stripped_claims.append(
                {"text": text, "reason": "citation does not match any resource actually fetched this turn"}
            )

    return result
