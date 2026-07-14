"""Tier 1 of the two-tier testing strategy (W2_ARCHITECTURE.md Section 6): fixture-based
integration tests with the Anthropic and OpenEMR HTTP calls stubbed out, so this file always runs
-- no live API, no local OpenEMR server, no cost -- and guards the upload->extract->validate->
persist WIRING independent of real model output quality. Real extraction *accuracy* is the golden
set's job (test_golden_set.py); this file's job is "does the pipeline correctly call the right
endpoints with the right payloads and correctly handle their responses," which a live test can't
isolate from model behavior.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app import ingestion as ingestion_module
from app.ingestion import IngestionError, attach_and_extract

FIXTURE = "eval/fixtures/maria_gonzalez_lab.pdf"


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body
        self.text = json.dumps(json_body)

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("stub error", request=None, response=self)


def _fake_anthropic_client(tool_input: dict):
    """Stubs Anthropic's forced tool-use response shape exactly as extract_with_vision reads it
    (response.model_dump()["content"] -> a tool_use block), plus the `usage`/`stop_reason`
    attributes the observability span in extract_with_vision reads directly off the response."""
    fake_response = SimpleNamespace(
        model_dump=lambda: {"content": [{"type": "tool_use", "id": "toolu_stub", "name": "stub", "input": tool_input}]},
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        stop_reason="tool_use",
    )
    fake_messages = SimpleNamespace(create=lambda **kwargs: fake_response)
    return SimpleNamespace(messages=fake_messages)


LAB_TOOL_INPUT = {
    "results": [
        {
            "test_name": "Hemoglobin A1c", "value": "7.4", "unit": "%", "reference_range": "4.0-5.6",
            "collection_date": "2026-07-12", "abnormal_flag": True, "confidence": 0.95,
            "citation": {"source_type": "document", "source_id": "34", "page_or_section": "1", "quote_or_value": "Hemoglobin A1c 7.4 %"},
        }
    ]
}

INTAKE_TOOL_INPUT = {
    "demographics": {
        "name": "Maria Gonzalez", "date_of_birth": "1985-03-14", "sex": "F", "confidence": 0.9,
        "citation": {"source_type": "document", "source_id": "35", "page_or_section": "1", "quote_or_value": "Maria Gonzalez"},
    },
    "chief_concern": {
        "text": "Increased thirst and fatigue", "confidence": 0.9,
        "citation": {"source_type": "document", "source_id": "35", "page_or_section": "1", "quote_or_value": "Increased thirst and fatigue"},
    },
    "current_medications": [
        {"name": "Lisinopril", "dose": "10mg", "frequency": "daily", "confidence": 0.9,
         "citation": {"source_type": "document", "source_id": "35", "page_or_section": "1", "quote_or_value": "Lisinopril 10mg daily"}}
    ],
    "allergies": [
        {"allergen": "Penicillin", "reaction": "rash", "confidence": 0.9,
         "citation": {"source_type": "document", "source_id": "35", "page_or_section": "1", "quote_or_value": "Penicillin: rash"}}
    ],
    "family_history": [],
}


@pytest.fixture
def stub_upload_and_lookup(monkeypatch):
    """Stubs the two OpenEMR HTTP round trips attach_and_extract makes before extraction:
    document upload (POST) and the post-upload lookup (GET) that resolves the new document's id."""
    def fake_post(url, **kwargs):
        if url.endswith("/document"):
            return _FakeResponse(200, True)  # insertAtPath() returns a bare success boolean
        raise AssertionError(f"unexpected POST in this stub: {url}")

    def fake_get(url, **kwargs):
        if "document_lookup" in url:
            # document_lookup returns the row unwrapped ({"id", "hash"}) -- not nested under "data".
            # Any hash here that doesn't match the fixture's real sha3-512 correctly skips dedup.
            return _FakeResponse(200, {"id": 34, "hash": "stubhash-does-not-match-real-file-hash"})
        raise AssertionError(f"unexpected GET in this stub: {url}")

    monkeypatch.setattr(ingestion_module.httpx, "post", fake_post)
    monkeypatch.setattr(ingestion_module.httpx, "get", fake_get)


def test_lab_pdf_pipeline_persists_with_the_stubbed_extraction(monkeypatch, stub_upload_and_lookup):
    """Wiring check: attach_and_extract for doc_type=lab_pdf calls upload -> rasterize -> extract
    -> validate -> persist_lab_results in order, and the persistence POST fires with the validated
    payload -- independent of whether Claude's real extraction would be accurate."""
    monkeypatch.setattr(ingestion_module, "_anthropic_client", lambda: _fake_anthropic_client(LAB_TOOL_INPUT))

    persist_calls = []

    def fake_persist(bearer_token, patient_id, document_id, extraction):
        persist_calls.append((patient_id, document_id, extraction))
        return {"procedure_order_id": 99, "result_ids": [1]}

    monkeypatch.setattr(ingestion_module, "persist_lab_results", fake_persist)

    with open(FIXTURE, "rb") as f:
        data = f.read()

    result = attach_and_extract(patient_id="1", data=data, filename="lab.pdf", doc_type="lab_pdf", bearer_token="tok")

    assert result["document_id"] == 34
    assert result["extraction"]["results"][0]["test_name"] == "Hemoglobin A1c"
    assert len(persist_calls) == 1
    assert persist_calls[0][0] == "1"
    assert persist_calls[0][1] == 34


def test_intake_form_pipeline_requires_patient_uuid(monkeypatch, stub_upload_and_lookup):
    """Wiring check: the intake_form path must not silently proceed without patient_uuid (needed
    by persist_intake_facts's allergy call) -- guards the exact bug class Stage 1 found live
    (allergy endpoint keyed on FHIR uuid, medication endpoint keyed on int pid)."""
    monkeypatch.setattr(ingestion_module, "_anthropic_client", lambda: _fake_anthropic_client(INTAKE_TOOL_INPUT))

    with open(FIXTURE, "rb") as f:
        data = f.read()

    with pytest.raises(IngestionError, match="patient_uuid"):
        attach_and_extract(patient_id="1", data=data, filename="intake.pdf", doc_type="intake_form", bearer_token="tok")


def test_intake_form_pipeline_persists_medications_and_allergies_separately(monkeypatch, stub_upload_and_lookup):
    """Wiring check: medications go to the int-pid-keyed endpoint, allergies to the uuid-keyed
    endpoint -- both are called with the correct identifier for their own endpoint, not the same one."""
    monkeypatch.setattr(ingestion_module, "_anthropic_client", lambda: _fake_anthropic_client(INTAKE_TOOL_INPUT))

    calls = []

    def fake_persist(bearer_token, patient_id, patient_uuid, extraction):
        calls.append((patient_id, patient_uuid))
        return {"medications": [{"ok": True}], "allergies": [{"ok": True}]}

    monkeypatch.setattr(ingestion_module, "persist_intake_facts", fake_persist)

    with open(FIXTURE, "rb") as f:
        data = f.read()

    result = attach_and_extract(
        patient_id="1", data=data, filename="intake.pdf", doc_type="intake_form",
        bearer_token="tok", patient_uuid="fhir-uuid-1",
    )

    assert result["extraction"]["current_medications"][0]["name"] == "Lisinopril"
    assert result["extraction"]["allergies"][0]["allergen"] == "Penicillin"
    assert calls == [("1", "fhir-uuid-1")]


def test_extraction_failing_schema_validation_raises_ingestion_error_not_a_bare_exception(monkeypatch, stub_upload_and_lookup):
    """Wiring check: a malformed tool_use input (e.g. missing a required field) must surface as the
    caller-facing IngestionError (which main.py maps to a clean 422), not an unhandled
    pydantic.ValidationError leaking a raw 500 to the widget."""
    monkeypatch.setattr(ingestion_module, "_anthropic_client", lambda: _fake_anthropic_client({"results": [{"value": "7.4"}]}))  # missing test_name, confidence, citation

    with open(FIXTURE, "rb") as f:
        data = f.read()

    with pytest.raises(IngestionError, match="schema validation"):
        attach_and_extract(patient_id="1", data=data, filename="lab.pdf", doc_type="lab_pdf", bearer_token="tok")


def test_document_upload_failure_raises_ingestion_error(monkeypatch):
    """Wiring check: insertAtPath() returning anything other than a bare `true` (its actual
    success/failure signal, per _upload_document) must surface as IngestionError before any
    extraction is even attempted (no point rasterizing/calling Claude for a document that was never
    actually stored)."""
    monkeypatch.setattr(ingestion_module.httpx, "post", lambda url, **kwargs: _FakeResponse(200, False))
    monkeypatch.setattr(ingestion_module.httpx, "get", lambda url, **kwargs: _FakeResponse(404, {}))

    with open(FIXTURE, "rb") as f:
        data = f.read()

    with pytest.raises(IngestionError, match="rejected the document upload"):
        attach_and_extract(patient_id="1", data=data, filename="lab.pdf", doc_type="lab_pdf", bearer_token="tok")
