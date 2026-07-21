"""Pure unit tests for verify_node's own logic (app/graph.py), distinct from test_verifier_unit.py
(which tests verifier.py's verify_claims -- the matching logic itself, not what verify_node does
with its output). No live API.
"""
from __future__ import annotations

from app.graph import _complete_fhir_citation


def test_completes_a_bare_fhir_citation_with_the_resource_s_own_date():
    """Citation Contract's full 5-field shape (grader-flagged fix, Final feedback): a real model
    only ever sends the plain Week 1 {resource_type, resource_id} shape -- real live testing found
    it complies inconsistently when asked to invent the other 3 fields itself, so this completes
    them deterministically in code instead, using the FHIR tool result's own `date` field."""
    source = {"resource_type": "Observation", "resource_id": "obs-1"}
    tool_results = [{"resource_type": "Observation", "id": "obs-1", "date": "2026-07-17", "value": "88"}]

    completed = _complete_fhir_citation(source, tool_results)

    assert completed == {
        "resource_type": "Observation",
        "resource_id": "obs-1",
        "source_type": "fhir",
        "source_id": "obs-1",
        "page_or_section": "2026-07-17",
        "field_or_chunk_id": "n/a",
        "quote_or_value": "n/a",
    }


def test_falls_back_to_n_a_when_the_matching_tool_result_has_no_date():
    source = {"resource_type": "Patient", "resource_id": "pat-1"}
    tool_results = [{"resource_type": "Patient", "id": "pat-1", "date": None, "name": "Maria Gonzalez"}]

    completed = _complete_fhir_citation(source, tool_results)

    assert completed["page_or_section"] == "n/a"


def test_falls_back_to_n_a_when_no_matching_tool_result_is_found():
    """Defensive: a claim citing a resource that isn't (or is no longer) in tool_results_this_turn
    still gets a complete, valid shape rather than crashing on a missing lookup."""
    completed = _complete_fhir_citation({"resource_type": "Condition", "resource_id": "cond-1"}, [])

    assert completed["page_or_section"] == "n/a"


def test_does_not_overwrite_fields_the_model_already_provided():
    """Additive only -- if the model happens to already provide some of the unified fields
    correctly, they're kept, not clobbered with a recomputed value."""
    source = {
        "resource_type": "Observation", "resource_id": "obs-1",
        "source_type": "fhir", "page_or_section": "already-set",
    }
    completed = _complete_fhir_citation(source, [{"resource_type": "Observation", "id": "obs-1", "date": "2026-07-17"}])

    assert completed["page_or_section"] == "already-set"


def test_no_data_marker_passes_through_unchanged():
    """A no_data marker has nothing to cite -- it's a distinct, valid concept (PROVIDE_ANSWER_TOOL's
    own schema draws the same distinction), not a citation to complete."""
    source = {"type": "no_data", "resource_type": "MedicationRequest"}

    assert _complete_fhir_citation(source, []) == source


def test_a_document_or_guideline_sourced_claim_passes_through_unchanged():
    """Only a bare FHIR shape (has resource_type, no source_type of its own) gets completed -- a
    document/guideline claim already has its full shape from the fact it was copied from."""
    source = {
        "source_type": "document", "source_id": "34", "page_or_section": "1",
        "field_or_chunk_id": "results[0]", "quote_or_value": "Glucose 88 mg/dL",
    }

    assert _complete_fhir_citation(source, []) == source
