"""Pure unit tests for extraction-confidence telemetry (Engineering Requirements: "extraction
confidence per document"). No live API. Regression guard for a real gap found by auditing the
code against that requirement: per-field confidence existed in the extraction schema (for
citations) but was never aggregated or logged as a span metric, so nothing implemented the
dashboard metric W2_ARCHITECTURE.md Section 9 already promised.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import ingestion as ingestion_module
from app.ingestion import _collect_confidences, extract_with_vision


def _fake_anthropic_client(tool_input: dict):
    fake_response = SimpleNamespace(
        model_dump=lambda: {"content": [{"type": "tool_use", "id": "toolu_1", "name": "stub", "input": tool_input}]},
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        stop_reason="tool_use",
    )
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: fake_response))


def _capturing_langfuse_client():
    calls: list[dict] = []
    return SimpleNamespace(update_current_generation=lambda **kwargs: calls.append(kwargs)), calls


def test_collect_confidences_lab_pdf():
    raw = {"results": [{"confidence": 0.9}, {"confidence": 0.7}, {"confidence": 0.5}]}
    assert _collect_confidences(raw, "lab_pdf") == [0.9, 0.7, 0.5]


def test_collect_confidences_intake_form_across_all_sections():
    raw = {
        "demographics": {"confidence": 0.9},
        "chief_concern": {"confidence": 0.8},
        "current_medications": [{"confidence": 0.7}, {"confidence": 0.6}],
        "allergies": [{"confidence": 0.95}],
        "family_history": [],
    }
    assert _collect_confidences(raw, "intake_form") == [0.9, 0.8, 0.7, 0.6, 0.95]


def test_collect_confidences_ignores_missing_or_malformed_confidence():
    """A malformed/missing confidence must not crash telemetry collection -- schema validation in
    attach_and_extract is the real safety net for a bad extraction, not this."""
    raw = {"results": [{"confidence": 0.9}, {}, {"confidence": "not-a-number"}, {"confidence": None}]}
    assert _collect_confidences(raw, "lab_pdf") == [0.9]


def test_collect_confidences_empty_extraction_returns_empty_list():
    assert _collect_confidences({"results": []}, "lab_pdf") == []
    assert _collect_confidences({}, "intake_form") == []


def test_extract_with_vision_logs_mean_and_min_confidence(monkeypatch):
    tool_input = {"results": [{"confidence": 0.9}, {"confidence": 0.5}, {"confidence": 0.7}]}
    monkeypatch.setattr(ingestion_module, "_anthropic_client", lambda: _fake_anthropic_client(tool_input))
    fake_client, calls = _capturing_langfuse_client()
    monkeypatch.setattr(ingestion_module, "get_client", lambda: fake_client)

    result = extract_with_vision("lab_pdf", page_images=[b"fake-page"], document_id=1)

    assert result == tool_input
    output = calls[-1]["output"]
    assert output["field_count"] == 3
    assert output["mean_confidence"] == pytest.approx((0.9 + 0.5 + 0.7) / 3)
    assert output["min_confidence"] == 0.5


def test_extract_with_vision_handles_zero_confidence_fields_without_crashing(monkeypatch):
    """An extraction with no fields at all (e.g. a totally illegible page) must still log clean
    telemetry -- None, not a ZeroDivisionError or crash."""
    tool_input = {"results": []}
    monkeypatch.setattr(ingestion_module, "_anthropic_client", lambda: _fake_anthropic_client(tool_input))
    fake_client, calls = _capturing_langfuse_client()
    monkeypatch.setattr(ingestion_module, "get_client", lambda: fake_client)

    extract_with_vision("lab_pdf", page_images=[b"fake-page"], document_id=1)

    output = calls[-1]["output"]
    assert output["field_count"] == 0
    assert output["mean_confidence"] is None
    assert output["min_confidence"] is None
