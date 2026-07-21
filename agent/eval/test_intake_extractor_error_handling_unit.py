"""Regression test for a real bug found live (2026-07-16, via the new Full Week 2 Flow Bruno
request): intake_extractor_node's except clause only caught IngestionError, so an upstream OpenEMR
HTTP failure inside attach_and_extract (401/5xx/timeout -- httpx.HTTPStatusError/HTTPError, not
IngestionError) propagated uncaught through the whole LangGraph invoke, crashing the *entire* chat
turn with a raw 500 instead of degrading just this worker's outcome. Same bug class main.py's
standalone /ingest route already had fixed (test_ingestion_integration.py's
test_ingest_endpoint_returns_clean_json_on_an_upstream_401_not_a_raw_500), but this chat-embedded
path didn't. No live API.
"""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import httpx

from app import graph as graph_module


def _fake_langfuse_client():
    # start_as_current_observation (real parent/child span nesting, grader-flagged fix, Final
    # feedback) replaces the plain @observe decorator intake_extractor_node used to have --
    # nullcontext(...) yields a stand-in span with just enough shape (.update()) to satisfy it.
    return SimpleNamespace(
        update_current_span=lambda **_: None,
        start_as_current_observation=lambda **_: nullcontext(SimpleNamespace(update=lambda **_: None)),
    )


def _base_state(**overrides) -> dict:
    state = {
        "patient_id": "patient-uuid-1",
        "patient_pid": "1",
        "bearer_token": "tok",
        "messages": [{"role": "user", "content": "upload this"}],
        "pending_document": {"data": b"x", "filename": "f.pdf", "doc_type": "lab_pdf", "mimetype": "application/pdf"},
        "document_processed": False,
        "extracted_facts": [],
        "correlation_id": "corr-1",
        "handoff_log": [{"from": "supervisor", "to": "intake_extractor", "reason": "test", "timestamp": "now"}],
        "handoff_span_context": None,
    }
    state.update(overrides)
    return state


def test_intake_extractor_node_degrades_gracefully_on_an_upstream_http_status_error(monkeypatch):
    request = httpx.Request("GET", "https://openemr.example/apis/default/api/patient/1/document_lookup")
    response = httpx.Response(401, request=request)

    def raise_401(**kwargs):
        raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    monkeypatch.setattr(graph_module, "attach_and_extract", raise_401)
    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    state = _base_state()

    result = graph_module.intake_extractor_node(state)  # must not raise

    assert result["document_processed"] is True
    assert result["extracted_facts"] == []
    last_message = result["messages"][-1]
    content = last_message["content"][-1]["text"] if isinstance(last_message["content"], list) else last_message["content"]
    assert "processing failed" in content


def test_intake_extractor_node_degrades_gracefully_on_a_connection_error(monkeypatch):
    """Same guard, different transient failure mode -- a timeout/connection error mid-extraction
    (e.g. OpenEMR or Anthropic briefly unreachable) must degrade the same way, not crash."""
    def raise_connect_error(**kwargs):
        raise httpx.ConnectError("connection refused", request=httpx.Request("GET", "https://openemr.example/x"))

    monkeypatch.setattr(graph_module, "attach_and_extract", raise_connect_error)
    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    state = _base_state()

    result = graph_module.intake_extractor_node(state)  # must not raise

    assert result["document_processed"] is True
