"""Pure unit tests for the Week 2 extraction schemas (app/schemas.py) -- no LLM calls, no network.
Guards the "vision extraction without invention" hard problem: raw VLM output must never bypass
these Pydantic models, so a malformed or out-of-range extraction has to fail validation here, not
be silently coerced or passed through to persistence/the clinician.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    AllergyEntry,
    BoundingBox,
    Citation,
    LabPdfExtraction,
    LabResultField,
    MedicationEntry,
)


def _citation(**overrides) -> dict:
    base = {
        "source_type": "document",
        "source_id": "doc-123",
        "page_or_section": "1",
        "field_or_chunk_id": "test_name",
        "quote_or_value": "Hemoglobin A1c",
    }
    base.update(overrides)
    return base


def test_valid_lab_result_field_is_accepted():
    """Happy path: a fully-formed lab result with all required fields validates cleanly."""
    field = LabResultField(
        test_name="Hemoglobin A1c",
        value="7.2",
        unit="%",
        reference_range="4.0-5.6",
        collection_date="2026-07-01",
        abnormal_flag=True,
        confidence=0.94,
        citation=_citation(),
    )
    assert field.test_name == "Hemoglobin A1c"
    assert field.citation.source_type == "document"


def test_lab_result_field_missing_required_test_name_is_rejected():
    """Failure mode guarded: a field missing the required `test_name` must fail validation, not
    silently default to an empty/None value that could later render as a blank clinical fact."""
    with pytest.raises(ValidationError):
        LabResultField(
            value="7.2",
            confidence=0.94,
            citation=_citation(),
        )


def test_lab_result_field_missing_citation_is_rejected():
    """Failure mode guarded: `citation` is required on every extracted field -- there is no
    schema-valid way to produce an uncited clinical fact (W2_ARCHITECTURE.md Section 5)."""
    with pytest.raises(ValidationError):
        LabResultField(test_name="Glucose", value="110", confidence=0.9)


@pytest.mark.parametrize("bad_confidence", [-0.1, 1.5, 2.0])
def test_confidence_out_of_range_is_rejected(bad_confidence):
    """Failure mode guarded: confidence must be a real [0,1] probability -- a raw model output like
    a stray '95' (percent, not fraction) or a negative number must not be accepted silently."""
    with pytest.raises(ValidationError):
        LabResultField(
            test_name="Glucose",
            value="110",
            confidence=bad_confidence,
            citation=_citation(),
        )


def test_invalid_citation_source_type_is_rejected():
    """Failure mode guarded: `source_type` is a closed set (fhir/document/guideline) -- an
    unexpected value must fail validation rather than silently pass through as an untyped citation."""
    with pytest.raises(ValidationError):
        LabResultField(
            test_name="Glucose",
            value="110",
            confidence=0.9,
            citation=_citation(source_type="made_up_source"),
        )


def test_bounding_box_coordinates_out_of_normalized_range_are_rejected():
    """Failure mode guarded: bbox coordinates are normalized [0,1] fractions of the page image --
    a value outside that range (e.g. raw pixel coordinates passed by mistake) must fail validation."""
    with pytest.raises(ValidationError):
        BoundingBox(page=0, x0=0.1, y0=0.1, x1=1.4, y1=0.9)


def test_lab_pdf_extraction_defaults_to_empty_results_list():
    """Boundary: an extraction with zero readable results (e.g. a blank/unreadable scan) must
    validate as an empty list, not raise or require a placeholder result."""
    extraction = LabPdfExtraction()
    assert extraction.results == []


def test_lab_pdf_extraction_with_multiple_results_validates():
    extraction = LabPdfExtraction(
        results=[
            LabResultField(test_name="Glucose", value="110", unit="mg/dL", confidence=0.92, citation=_citation()),
            LabResultField(test_name="Potassium", value="4.1", unit="mmol/L", confidence=0.88, citation=_citation(field_or_chunk_id="value")),
        ]
    )
    assert len(extraction.results) == 2


def test_medication_entry_requires_name_but_dose_and_frequency_are_optional():
    """Boundary: a scanned intake form may legibly show a drug name but not its dose/frequency --
    those are optional, but the drug name itself is not."""
    entry = MedicationEntry(name="Metformin", confidence=0.85, citation=_citation())
    assert entry.dose is None
    with pytest.raises(ValidationError):
        MedicationEntry(confidence=0.85, citation=_citation())


def test_allergy_entry_requires_allergen():
    with pytest.raises(ValidationError):
        AllergyEntry(reaction="hives", confidence=0.8, citation=_citation())


def test_citation_optional_fields_default_to_none_not_required():
    """Boundary: `page_or_section`/`field_or_chunk_id`/`quote_or_value` are useful but not every
    source (e.g. a bare FHIR resource reference) can always populate all three -- only `source_type`
    and `source_id` are strictly required."""
    citation = Citation(source_type="fhir", source_id="cond-1")
    assert citation.page_or_section is None
    assert citation.quote_or_value is None
