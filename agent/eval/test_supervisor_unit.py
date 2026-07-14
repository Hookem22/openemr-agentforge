"""Pure unit tests for the Stage 3 supervisor/worker routing logic (app/graph.py) -- no LLM calls,
no FHIR calls, no Voyage calls, no network. Covers the parts of W2_ARCHITECTURE.md Section 3 that
don't require a live model: routing decisions, the handoff-count failure-mode guard, fact
flattening, and message-injection shape.
"""
from __future__ import annotations

from app.graph import (
    MAX_HANDOFFS_PER_TURN,
    _append_context_to_last_user_message,
    _evidence_needed,
    _facts_to_context_message,
    _flatten_extracted_facts,
    _latest_user_text,
    _route_decision,
    route_after_supervisor,
)


def _base_state(**overrides) -> dict:
    state = {
        "messages": [{"role": "user", "content": "What's going on with this patient?"}],
        "pending_document": None,
        "document_processed": False,
        "evidence_fetched": False,
        "handoff_log": [],
    }
    state.update(overrides)
    return state


def test_pending_unprocessed_document_routes_to_intake_extractor():
    state = _base_state(pending_document={"data": b"x", "filename": "lab.pdf", "doc_type": "lab_pdf"})

    to, reason = _route_decision(state)

    assert to == "intake_extractor"
    assert "pending" in reason


def test_processed_document_does_not_re_route_to_intake_extractor():
    """Boundary: once document_processed is True this turn, the same pending_document must not
    trigger a second extraction pass (would double-persist / infinite loop)."""
    state = _base_state(pending_document={"data": b"x", "filename": "lab.pdf", "doc_type": "lab_pdf"}, document_processed=True)

    to, _ = _route_decision(state)

    assert to != "intake_extractor"


def test_guideline_style_question_routes_to_evidence_retriever():
    state = _base_state(messages=[{"role": "user", "content": "What's the recommended target A1c for this patient?"}])

    to, reason = _route_decision(state)

    assert to == "evidence_retriever"
    assert "evidence" in reason


def test_already_fetched_evidence_does_not_re_route():
    state = _base_state(
        messages=[{"role": "user", "content": "What's the recommended target A1c?"}],
        evidence_fetched=True,
    )

    to, _ = _route_decision(state)

    assert to != "evidence_retriever"


def test_plain_chart_question_routes_straight_to_agent():
    """No pending document, no guideline-style keywords -- ready to finalize immediately, same as
    every Week 1 turn before Stage 3 existed."""
    state = _base_state(messages=[{"role": "user", "content": "What are this patient's current medications?"}])

    to, reason = _route_decision(state)

    assert to == "agent"


def test_document_takes_priority_over_evidence_need():
    """Ordering: if both a pending document and a guideline-style question are present, extraction
    runs first (facts from the document may be what the evidence query should be about)."""
    state = _base_state(
        messages=[{"role": "user", "content": "What's the recommended target for this lab result?"}],
        pending_document={"data": b"x", "filename": "lab.pdf", "doc_type": "lab_pdf"},
    )

    to, _ = _route_decision(state)

    assert to == "intake_extractor"


def test_handoff_cap_forces_route_to_agent():
    """Failure-mode guard (W2_ARCHITECTURE.md Section 10 'Supervisor routing error' row): once the
    handoff cap is hit, route to agent regardless of what would otherwise be decided, so a routing
    bug fails closed instead of looping forever."""
    state = _base_state(
        pending_document={"data": b"x", "filename": "lab.pdf", "doc_type": "lab_pdf"},
        handoff_log=[{"to": "intake_extractor"}] * MAX_HANDOFFS_PER_TURN,
    )

    to, reason = _route_decision(state)

    assert to == "agent"
    assert "cap" in reason


def test_route_after_supervisor_reads_the_latest_handoff_log_entry():
    state = _base_state(handoff_log=[{"to": "intake_extractor"}, {"to": "evidence_retriever"}])

    assert route_after_supervisor(state) == "evidence_retriever"


def test_evidence_needed_matches_guideline_style_keywords():
    assert _evidence_needed(_base_state(messages=[{"role": "user", "content": "What's the screening threshold here?"}]))
    assert not _evidence_needed(_base_state(messages=[{"role": "user", "content": "What are their vitals right now?"}]))


def test_latest_user_text_finds_original_question_past_injected_context_blocks():
    """Boundary: after a worker injects extra text blocks onto the same user turn (see
    _append_context_to_last_user_message), the clinician's original question must still be the one
    returned -- not the injected fact dump -- so a second worker's routing/query logic isn't
    confused by the first worker's output."""
    state = _base_state(messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "What's the target A1c for this patient?"},
            {"type": "text", "text": "[Extracted from uploaded document -- 1 facts. ...]"},
        ],
    }])

    assert _latest_user_text(state) == "What's the target A1c for this patient?"


def test_append_context_converts_plain_string_content_to_block_list():
    state = _base_state(messages=[{"role": "user", "content": "Original question"}])

    _append_context_to_last_user_message(state, "[Retrieved guideline evidence -- 2 facts. ...]")

    content = state["messages"][-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "Original question"}
    assert content[1]["text"] == "[Retrieved guideline evidence -- 2 facts. ...]"


def test_append_context_appends_to_existing_block_list_without_new_message():
    """Anthropic API role-alternation guard: two workers running in the same turn must extend the
    same user message, never create a second consecutive user-role message (which the API rejects)."""
    state = _base_state(messages=[{"role": "user", "content": [{"type": "text", "text": "Original"}]}])

    _append_context_to_last_user_message(state, "second worker's findings")

    assert len(state["messages"]) == 1
    assert len(state["messages"][-1]["content"]) == 2


def test_facts_to_context_message_reports_none_found_for_empty_list():
    assert _facts_to_context_message([], "Retrieved guideline evidence") == "[Retrieved guideline evidence: none found]"


def test_facts_to_context_message_includes_exact_citation_json():
    facts = [{"text": "A1c: 8.2%", "citation": {"source_type": "document", "source_id": "doc-1", "field_or_chunk_id": "results[0]"}}]

    message = _facts_to_context_message(facts, "Extracted from uploaded document")

    assert '"source_id": "doc-1"' in message
    assert "A1c: 8.2%" in message


def test_flatten_lab_pdf_extraction_includes_one_fact_per_result_with_its_own_citation():
    extraction = {
        "results": [
            {"test_name": "HbA1c", "value": "8.2", "unit": "%", "reference_range": "4-6", "citation": {"source_type": "document", "source_id": "doc-1", "field_or_chunk_id": "results[0]"}},
            {"test_name": "LDL", "value": "190", "unit": "mg/dL", "reference_range": None, "citation": {"source_type": "document", "source_id": "doc-1", "field_or_chunk_id": "results[1]"}},
        ]
    }

    facts = _flatten_extracted_facts(extraction, "lab_pdf")

    assert len(facts) == 2
    assert "HbA1c: 8.2 %" in facts[0]["text"]
    assert facts[0]["citation"]["field_or_chunk_id"] == "results[0]"


def test_flatten_intake_form_extraction_covers_every_field_type_with_a_citation():
    extraction = {
        "demographics": {"name": "Robert Chen", "date_of_birth": "1960-01-01", "sex": "M", "citation": {"source_type": "document", "source_id": "doc-2", "field_or_chunk_id": "demographics"}},
        "chief_concern": {"text": "chest pain", "citation": {"source_type": "document", "source_id": "doc-2", "field_or_chunk_id": "chief_concern"}},
        "current_medications": [{"name": "Sulfamethoxazole", "dose": "800mg", "frequency": "BID", "citation": {"source_type": "document", "source_id": "doc-2", "field_or_chunk_id": "current_medications[0]"}}],
        "allergies": [{"allergen": "Sulfa", "reaction": "hives", "citation": {"source_type": "document", "source_id": "doc-2", "field_or_chunk_id": "allergies[0]"}}],
        "family_history": [{"relation": "Father", "condition": "MI", "citation": {"source_type": "document", "source_id": "doc-2", "field_or_chunk_id": "family_history[0]"}}],
    }

    facts = _flatten_extracted_facts(extraction, "intake_form")

    assert len(facts) == 5
    texts = [f["text"] for f in facts]
    assert any("Robert Chen" in t for t in texts)
    assert any("chest pain" in t for t in texts)
    assert any("Sulfamethoxazole" in t for t in texts)
    assert any("Sulfa" in t for t in texts)
    assert any("Father" in t and "MI" in t for t in texts)


def test_flatten_intake_form_extraction_omits_missing_optional_fields():
    """Boundary: demographics/chief_concern are Optional in schemas.py -- absence must not produce
    a fact with fabricated content, and must not crash."""
    facts = _flatten_extracted_facts({"current_medications": [], "allergies": [], "family_history": []}, "intake_form")

    assert facts == []
