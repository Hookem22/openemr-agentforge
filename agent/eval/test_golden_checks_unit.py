"""Pure unit tests for golden_checks.py's own aggregation logic (Engineering Requirements: every
test needs a documented failure mode it guards against) -- no live API. Distinct from
test_golden_set.py, which drives the full case-runner functions against real live cases (Tier 2);
this file is Tier 1 coverage of the citation-shape check itself, which previously had none at all.
"""
from __future__ import annotations

from eval.golden_checks import _citation_is_complete

FULL_DOCUMENT_CITATION = {
    "source_type": "document", "source_id": "34", "page_or_section": "1",
    "field_or_chunk_id": "results[0]", "quote_or_value": "Glucose 88 mg/dL",
}


def test_full_document_citation_is_complete():
    assert _citation_is_complete(FULL_DOCUMENT_CITATION) is True


def test_citation_missing_page_or_section_is_incomplete():
    """Grader-flagged fix (Final feedback): the old check only verified source_id truthiness --
    this is exactly the gap that let a citation missing page_or_section/quote_or_value/etc. through
    silently, since the contract was never actually proven complete either way."""
    incomplete = {**FULL_DOCUMENT_CITATION, "page_or_section": None}
    assert _citation_is_complete(incomplete) is False


def test_citation_with_empty_string_field_is_incomplete():
    """An empty string is falsy-as-a-citation-value even though it's technically 'present' as a
    key -- a naive `key in source` check would wrongly accept this."""
    incomplete = {**FULL_DOCUMENT_CITATION, "quote_or_value": ""}
    assert _citation_is_complete(incomplete) is False


def test_fhir_citation_needs_the_full_shape_too():
    """The rubric's literal wording ('every clinical claim') applies to FHIR-sourced claims too,
    not just document/guideline ones -- a bare {resource_type, resource_id} (the old Week 1 shape)
    is no longer sufficient on its own."""
    bare_fhir = {"resource_type": "Observation", "resource_id": "obs-1"}
    assert _citation_is_complete(bare_fhir) is False

    full_fhir = {
        **bare_fhir, "source_type": "fhir", "source_id": "obs-1", "page_or_section": "n/a",
        "field_or_chunk_id": "n/a", "quote_or_value": "n/a",
    }
    assert _citation_is_complete(full_fhir) is True


def test_no_data_marker_is_exempt_from_the_full_shape():
    """A no_data marker is a distinct, valid concept (nothing was found, so there's nothing to
    cite) -- PROVIDE_ANSWER_TOOL's own schema draws this same distinction, so it must not be held
    to the 5-field citation shape."""
    no_data = {"type": "no_data", "resource_type": "MedicationRequest"}
    assert _citation_is_complete(no_data) is True


def test_no_data_marker_without_a_resource_type_is_still_incomplete():
    no_data = {"type": "no_data"}
    assert _citation_is_complete(no_data) is False


def test_empty_source_is_incomplete():
    assert _citation_is_complete({}) is False
