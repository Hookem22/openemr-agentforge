"""Deterministic (non-LLM) verification layer -- ARCHITECTURE.md Section 5.

The model is forced (via the `provide_answer` tool, see graph.py) to emit every claim as
`{text, source}`, never as free prose. This module then checks each claim's source against what
this turn's tool calls *actually* returned -- a code-level check against ground truth, not a
second model call grading the first. Claims that fail are stripped before the user ever sees them
(fail closed). This is deliberately boring, dumb code: the entire point is that it cannot be argued
into accepting an ungrounded claim the way a second LLM call could be.

W2_ARCHITECTURE.md Section 5's migration note: this is a backward-compatible additive extension,
not a rewrite. The original `(resource_type, resource_id)` FHIR check is untouched; claims sourced
from the Week 2 intake-extractor/evidence-retriever workers instead carry the unified citation shape
(`source_type`/`source_id`/`field_or_chunk_id`, schemas.py's `Citation`) and are checked against
whatever `extracted_facts`/`evidence_snippets` this turn's workers actually produced -- the same
"only what was actually fetched this turn" ground-truth check, just against a second data source.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# AgentForge vulnerability report #15 (state_corruption): resource types where a stripped
# "no_data" claim means the model asserted an absence of safety-critical data it did NOT actually
# confirm this turn -- e.g. relying on a fabricated/stale get_allergies result replayed via
# client-echoed conversation_history instead of a real tool call this turn. Per-claim stripping
# alone doesn't stop the same false premise from surviving unstripped inside a SEPARATE,
# legitimately-sourced claim (report #15: a guideline claim citing a real, fetched-this-turn
# guideline chunk whose own text ALSO asserted "in this patient NO sulfa allergy is documented" --
# verify_claims only checked the citation, never the rest of the claim's text, so it passed).
SAFETY_CRITICAL_ABSENCE_TYPES = {"AllergyIntolerance", "MedicationRequest"}


@dataclass
class VerificationResult:
    verified_claims: list[dict] = field(default_factory=list)
    stripped_claims: list[dict] = field(default_factory=list)

    @property
    def strip_rate(self) -> float:
        total = len(self.verified_claims) + len(self.stripped_claims)
        return (len(self.stripped_claims) / total) if total else 0.0


def verify_claims(
    claims: list[dict],
    tool_results_this_turn: list[dict],
    extracted_facts: list[dict] | None = None,
    evidence_snippets: list[dict] | None = None,
    empty_evidence: bool = False,
) -> VerificationResult:
    """
    claims: [{"text": str, "source": {"resource_type": str, "resource_id": str}}
                        | {"text": str, "source": {"type": "no_data", "resource_type": str}}
                        | {"text": str, "source": {"source_type": "document"|"guideline",
                                                    "source_id": str, "field_or_chunk_id": str}}]
    tool_results_this_turn: every item returned by every tool call made this turn (each already
        carrying resource_type/id/date -- see tools.py's allow-list shape).
    extracted_facts / evidence_snippets: [{"text": str, "citation": Citation-shaped dict}] this
        turn's intake_extractor/evidence_retriever workers actually produced (graph.py).
    empty_evidence: True if evidence_retriever ran this turn and found zero guideline chunks --
        lets the model make a verified "no guideline evidence found" no_data claim instead of being
        forced to either fabricate guidance or leave the gap unstated (W2_ARCHITECTURE.md Section 10).
    """
    extracted_facts = extracted_facts or []
    evidence_snippets = evidence_snippets or []

    available_citations = {
        (item["resource_type"], item["id"]) for item in tool_results_this_turn if item.get("id")
    }
    # A resource_type counts as "confirmed empty this turn" only if at least one tool call for it
    # happened and returned nothing to work with here -- callers pass this in explicitly (see
    # graph.py, which tracks which tools were actually called and what they returned).
    empty_resource_types = {
        item["resource_type"] for item in tool_results_this_turn if item.get("_empty_marker")
    }
    if empty_evidence:
        empty_resource_types.add("guideline")

    # Unified citation shape (schemas.py's Citation): keyed on (source_type, source_id,
    # field_or_chunk_id) so a claim must match the exact fact/chunk a worker actually produced this
    # turn, not just any fact from the same document/guideline.
    available_unified_citations = {
        (fact["citation"]["source_type"], fact["citation"]["source_id"], fact["citation"].get("field_or_chunk_id"))
        for fact in (extracted_facts + evidence_snippets)
    }

    result = VerificationResult()
    unverifiable_safety_critical_absence = False
    for claim in claims:
        source = claim.get("source") or {}
        text = claim.get("text", "")

        if source.get("type") == "no_data":
            resource_type = source.get("resource_type")
            if resource_type in empty_resource_types:
                result.verified_claims.append(claim)
            else:
                result.stripped_claims.append(
                    {"text": text, "reason": "claimed absence of data not confirmed by an actual empty tool result this turn"}
                )
                if resource_type in SAFETY_CRITICAL_ABSENCE_TYPES:
                    unverifiable_safety_critical_absence = True
            continue

        if source.get("source_type") in ("document", "guideline"):
            unified_key = (source.get("source_type"), source.get("source_id"), source.get("field_or_chunk_id"))
            if unified_key in available_unified_citations:
                result.verified_claims.append(claim)
            else:
                result.stripped_claims.append(
                    {"text": text, "reason": "citation does not match any extracted fact or evidence snippet fetched this turn"}
                )
            continue

        key = (source.get("resource_type"), source.get("resource_id"))
        if key[0] and key[1] and key in available_citations:
            result.verified_claims.append(claim)
        else:
            result.stripped_claims.append(
                {"text": text, "reason": "citation does not match any resource actually fetched this turn"}
            )

    if unverifiable_safety_critical_absence:
        # Report #15: don't let an otherwise-legitimately-cited claim (e.g. a real guideline chunk
        # fetched this turn) carry the same unverified premise through unstripped just because ITS
        # citation checked out -- the citation check never inspects the rest of that claim's text.
        # Fail the whole answer closed instead of a partial one that might still be unsafe.
        for claim in result.verified_claims:
            result.stripped_claims.append(
                {
                    "text": claim.get("text", ""),
                    "reason": (
                        "withheld: this turn also contained an unverifiable safety-critical absence "
                        "claim (see the other stripped claim above) -- failing the entire answer "
                        "closed rather than risk this claim resting on the same unverified premise"
                    ),
                }
            )
        result.verified_claims = []

    return result
