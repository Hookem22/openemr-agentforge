"""Pure unit tests for handoff_index span tagging (Engineering Requirements: "each worker
invocation must be a child span of the supervisor span"). No live API.

Real limitation this works around, not fixes outright: LangGraph invokes supervisor/
intake_extractor/evidence_retriever as separate graph steps, not one calling the other, so their
@observe spans land as siblings under the trace root rather than literal parent/child of the
supervisor span. Restructuring the graph to force real OTel nesting would mean supervisor calling
workers directly instead of returning a routing decision to the graph executor -- a much bigger,
riskier change to an already-verified-live graph than tagging spans. These tests guard the
pragmatic fix instead: every span in one handoff (the supervisor decision plus whichever worker it
routed to) carries the same `handoff_index` metadata, so the logical parent/child relationship is
reconstructable from Langfuse span data even though the span tree itself is flat.
"""
from __future__ import annotations

from types import SimpleNamespace

from app import graph as graph_module


def _capturing_langfuse_client():
    calls: list[dict] = []
    return SimpleNamespace(update_current_span=lambda **kwargs: calls.append(kwargs)), calls


def _base_state(**overrides) -> dict:
    state = {
        "patient_id": "patient-uuid-1",
        "patient_pid": "1",
        "bearer_token": "tok",
        "messages": [{"role": "user", "content": "upload this"}],
        "pending_document": None,
        "document_processed": False,
        "extracted_facts": [],
        "evidence_snippets": [],
        "evidence_fetched": False,
        "evidence_empty": False,
        "correlation_id": "corr-1",
        "handoff_log": [],
    }
    state.update(overrides)
    return state


def test_supervisor_and_intake_extractor_share_the_same_handoff_index(monkeypatch):
    fake_client, calls = _capturing_langfuse_client()
    monkeypatch.setattr(graph_module, "get_client", lambda: fake_client)
    monkeypatch.setattr(
        graph_module, "attach_and_extract",
        lambda **kwargs: {"extraction": {"results": []}, "document_id": 1},
    )

    state = _base_state(pending_document={"data": b"x", "filename": "f.pdf", "doc_type": "lab_pdf", "mimetype": "application/pdf"})

    graph_module.supervisor_node(state)
    graph_module.intake_extractor_node(state)

    supervisor_call, intake_call = calls
    assert supervisor_call["metadata"]["handoff_index"] == 0
    assert intake_call["metadata"]["handoff_index"] == 0
    assert state["handoff_log"][0]["to"] == "intake_extractor"


def test_supervisor_and_evidence_retriever_share_the_same_handoff_index(monkeypatch):
    fake_client, calls = _capturing_langfuse_client()
    monkeypatch.setattr(graph_module, "get_client", lambda: fake_client)
    monkeypatch.setattr(graph_module, "retrieve", lambda query: [])

    state = _base_state(messages=[{"role": "user", "content": "What's the recommended target for this?"}])

    graph_module.supervisor_node(state)
    graph_module.evidence_retriever_node(state)

    supervisor_call, retriever_call = calls
    assert supervisor_call["metadata"]["handoff_index"] == 0
    assert retriever_call["metadata"]["handoff_index"] == 0
    assert state["handoff_log"][0]["to"] == "evidence_retriever"


def test_a_second_handoff_in_the_same_turn_gets_a_different_index(monkeypatch):
    """Confirms the index actually advances across multiple handoffs in one turn, not just a
    hardcoded 0 -- e.g. a document upload followed by a guideline question in the same message."""
    fake_client, calls = _capturing_langfuse_client()
    monkeypatch.setattr(graph_module, "get_client", lambda: fake_client)
    monkeypatch.setattr(
        graph_module, "attach_and_extract",
        lambda **kwargs: {"extraction": {"results": []}, "document_id": 1},
    )
    monkeypatch.setattr(graph_module, "retrieve", lambda query: [])

    state = _base_state(
        pending_document={"data": b"x", "filename": "f.pdf", "doc_type": "lab_pdf", "mimetype": "application/pdf"},
        messages=[{"role": "user", "content": "Summarize this and tell me the recommended target"}],
    )

    graph_module.supervisor_node(state)  # handoff #0 -> intake_extractor (pending doc takes priority)
    graph_module.intake_extractor_node(state)
    graph_module.supervisor_node(state)  # handoff #1 -> evidence_retriever
    graph_module.evidence_retriever_node(state)

    first_supervisor, first_worker, second_supervisor, second_worker = calls
    assert first_supervisor["metadata"]["handoff_index"] == 0
    assert first_worker["metadata"]["handoff_index"] == 0
    assert second_supervisor["metadata"]["handoff_index"] == 1
    assert second_worker["metadata"]["handoff_index"] == 1
