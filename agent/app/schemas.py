"""Strict extraction schemas for Week 2 document ingestion (W2_ARCHITECTURE.md Sections 2, 2.3, 5).

Raw VLM output never bypasses these -- ingestion.py validates every extraction against the models
below via Pydantic before anything is persisted or shown to the clinician (W2_ARCHITECTURE.md's
"vision extraction without invention" hard problem: the schema, not the model's own stated
confidence, is the safety net). Every extracted fact carries its own `confidence`, `bbox` (for the
required click-to-source visual overlay), and `citation` -- there is no bare/uncited field anywhere
in these schemas.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["lab_pdf", "intake_form"]


class BoundingBox(BaseModel):
    """Normalized (0-1) location of an extracted field on its source page image, for the
    click-to-source PDF overlay. Approximate, not pixel-perfect OCR -- see W2_ARCHITECTURE.md
    Section 12 risk #2."""

    page: int = Field(ge=0)
    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)


class Citation(BaseModel):
    """Unified 5-field citation contract (W2_ARCHITECTURE.md Section 5). Every clinical claim --
    whether sourced from a Week 1 FHIR tool, an extracted document field, or a retrieved guideline
    chunk -- is checked by verifier.py against a citation of this shape."""

    source_type: Literal["fhir", "document", "guideline"]
    source_id: str
    page_or_section: str | None = None
    field_or_chunk_id: str | None = None
    quote_or_value: str | None = None


class _ExtractedField(BaseModel):
    """Shared shape every extracted fact carries in addition to its own value(s): how confident
    the model was, where it came from on the page, and its citation. Never subclassed directly --
    concrete fields below add their own value field(s) on top of this."""

    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BoundingBox | None = None
    citation: Citation


class LabResultField(_ExtractedField):
    """One lab test result. Required fields per the assignment: test name, value, unit, reference
    range, collection date, abnormal flag, source citation."""

    test_name: str
    value: str
    unit: str | None = None
    reference_range: str | None = None
    collection_date: str | None = None  # ISO 8601 date if the model can read one
    abnormal_flag: bool | None = None


class LabPdfExtraction(BaseModel):
    results: list[LabResultField] = Field(default_factory=list)


class Demographics(_ExtractedField):
    name: str | None = None
    date_of_birth: str | None = None
    sex: str | None = None


class ChiefConcern(_ExtractedField):
    text: str


class MedicationEntry(_ExtractedField):
    name: str
    dose: str | None = None
    frequency: str | None = None


class AllergyEntry(_ExtractedField):
    allergen: str
    reaction: str | None = None


class FamilyHistoryEntry(_ExtractedField):
    relation: str
    condition: str


class IntakeFormExtraction(BaseModel):
    """Required fields per the assignment: demographics, chief concern, current medications,
    allergies, family history, source citation (carried per-entry via `_ExtractedField`)."""

    demographics: Demographics | None = None
    chief_concern: ChiefConcern | None = None
    current_medications: list[MedicationEntry] = Field(default_factory=list)
    allergies: list[AllergyEntry] = Field(default_factory=list)
    family_history: list[FamilyHistoryEntry] = Field(default_factory=list)


class GuidelineChunk(BaseModel):
    """One retrieved-and-reranked guideline chunk (W2_ARCHITECTURE.md Section 4) -- the
    evidence-retriever worker's output shape. `citation.source_type` is always "guideline"."""

    chunk_id: str
    text: str
    source_title: str
    source_org: str
    section: str
    citation: Citation
