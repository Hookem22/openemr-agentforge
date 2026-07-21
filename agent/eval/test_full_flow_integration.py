"""Tier 1 of the two-tier testing strategy (W2_ARCHITECTURE.md Section 6): one true end-to-end
integration test chaining the FULL pipeline -- document upload -> vision extraction -> OpenEMR
persistence -> supervisor routing -> guideline evidence retrieval -> FHIR tool call -> a grounded
final answer with citations -- through the real compiled LangGraph (`run_turn`), with every
external boundary (Anthropic, OpenEMR HTTP, Voyage) stubbed. No live API, no network, no cost.

Distinct from test_ingestion_integration.py (ingestion pipeline alone, called directly) and
test_rag_integration.py (retrieval alone, called directly): neither exercises the two chained
together through the actual supervisor/worker graph in one turn, so a wiring break at the boundary
between them -- e.g. a worker's output shape the *other* worker or the final answer step doesn't
actually consume correctly -- wouldn't be caught by either file alone. This test drives one turn
with both a pending document AND an evidence-needing question, matching the multi-handoff scenario
test_handoff_index_unit.py already proves the routing/span side of; this file's job is proving the
*data* produced at each stage is what the final answer step actually receives and cites.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app import graph as graph_module
from app import ingestion as ingestion_module
from app import rag as rag_module

FIXTURE = "eval/fixtures/maria_gonzalez_lab.pdf"


class _FakeResponse:
    def __init__(self, status_code: int, json_body):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body

    def raise_for_status(self):
        pass


LAB_EXTRACTION_TOOL_INPUT = {
    "results": [
        {
            "test_name": "Fasting Glucose", "value": "142", "unit": "mg/dL", "reference_range": "70-99",
            "collection_date": "2026-07-17", "abnormal_flag": True, "confidence": 0.95,
            "bbox": {"page": 0, "x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.25},
            "citation": {"source_type": "document", "source_id": "34", "page_or_section": "1", "quote_or_value": "Fasting Glucose 142 mg/dL"},
        }
    ]
}


class _FakeVoyageClient:
    """Same deterministic stand-in as test_rag_integration.py's -- distinct-but-reproducible
    embeddings and a rerank that reliably surfaces at least one real corpus chunk above
    MIN_RELEVANCE_SCORE, without depending on real embedding/rerank quality."""

    def embed(self, texts, model, input_type):
        return SimpleNamespace(embeddings=[[float(len(t) % 7), float(len(t) % 5)] for t in texts])

    def rerank(self, query, documents, model, top_k):
        order = list(reversed(range(len(documents))))[:top_k]
        results = [SimpleNamespace(index=i, relevance_score=0.9 - 0.01 * rank) for rank, i in enumerate(order)]
        return SimpleNamespace(results=results)


def _extract_injected_facts(messages: list[dict], label_prefix: str) -> list[dict]:
    """Parses the exact wire format _facts_to_context_message writes (one instruction line, then
    one JSON {"text", "citation"} line per fact) back out of the conversation's injected context
    blocks -- so this test's fake Anthropic client builds its provide_answer claims from whatever
    citations were *actually* produced by the real ingestion/retrieval stubs this turn, not
    hardcoded values that could silently drift from what the pipeline really emits. Searches every
    message, not just the latest one: by the time provide_answer is called, execute_tools_node has
    already appended a newer user message (the tool_result), so the injected-context message is no
    longer the most recent user turn."""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            text = block.get("text", "") if isinstance(block, dict) else ""
            if text.startswith(f"[{label_prefix}"):
                lines = text.split("\n")[1:]  # skip the instruction line
                return [json.loads(line) for line in lines]
    return []


def test_full_pipeline_upload_to_grounded_answer(monkeypatch, tmp_path):
    # -- Stage 1: ingestion (document upload -> vision extraction -> persistence), stubbed exactly
    # as test_ingestion_integration.py does.
    monkeypatch.setattr(ingestion_module, "_anthropic_client", lambda: SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
            model_dump=lambda: {"content": [{"type": "tool_use", "id": "t1", "name": "stub", "input": LAB_EXTRACTION_TOOL_INPUT}]},
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            stop_reason="tool_use",
        ))
    ))

    def fake_post(url, **kwargs):
        assert url.endswith("/document")
        return _FakeResponse(200, True)

    def fake_get(url, **kwargs):
        assert "document_lookup" in url
        return _FakeResponse(200, {"id": 34, "hash": "stub-hash"})

    monkeypatch.setattr(ingestion_module.httpx, "post", fake_post)
    monkeypatch.setattr(ingestion_module.httpx, "get", fake_get)

    persist_calls = []

    def fake_persist_lab_results(bearer_token, patient_id, document_id, extraction, correlation_id):
        persist_calls.append({"patient_id": patient_id, "document_id": document_id})
        return {"procedure_order_id": 99, "result_ids": [1]}

    monkeypatch.setattr(ingestion_module, "persist_lab_results", fake_persist_lab_results)

    # -- Stage 2: hybrid RAG, stubbed Voyage but the real checked-in corpus / BM25 / RRF / rerank
    # orchestration, exactly as test_rag_integration.py drives it.
    monkeypatch.setattr(rag_module, "_voyage_client", lambda: _FakeVoyageClient())
    rag_module.reset_index()

    # -- Stage 3: the FHIR tool layer -- stubbed at the TOOL_FUNCTIONS boundary (not FhirClient/httpx)
    # since this test's job is the graph's wiring, not FhirClient's own HTTP behavior (that's Week 1's
    # live-server suite's job).
    monkeypatch.setitem(
        graph_module.TOOL_FUNCTIONS, "get_labs",
        lambda fhir, patient_id, **kwargs: [{"resource_type": "Observation", "id": "obs-glucose-live-1", "value": "88 mg/dL"}],
    )

    # -- Stage 4: the main chat-loop Anthropic client -- a stateful fake scripting a realistic two-
    # call turn: first a FHIR tool call, then (once its result is appended) provide_answer citing
    # all three source types this turn actually produced -- FHIR, document, and guideline.
    call_count = {"n": 0}

    def fake_agent_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return SimpleNamespace(
                model_dump=lambda: {"content": [{"type": "tool_use", "id": "tu1", "name": "get_labs", "input": {}}]},
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="tool_use",
            )
        document_facts = _extract_injected_facts(kwargs["messages"], "Extracted from uploaded document")
        guideline_facts = _extract_injected_facts(kwargs["messages"], "Retrieved guideline evidence")
        assert document_facts, "expected the intake-extractor's fact to have been injected as context by now"
        assert guideline_facts, "expected the evidence-retriever's snippet to have been injected as context by now"

        claims = [
            {"text": "Fasting glucose 88 mg/dL on file.", "source": {"resource_type": "Observation", "resource_id": "obs-glucose-live-1"}},
            {"text": document_facts[0]["text"], "source": document_facts[0]["citation"]},
            {"text": guideline_facts[0]["text"], "source": guideline_facts[0]["citation"]},
        ]
        return SimpleNamespace(
            model_dump=lambda: {"content": [{"type": "tool_use", "id": "tu2", "name": "provide_answer", "input": {"claims": claims}}]},
            usage=SimpleNamespace(input_tokens=20, output_tokens=15),
            stop_reason="tool_use",
        )

    monkeypatch.setattr(
        graph_module, "_anthropic_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=fake_agent_create)),
    )

    with open(FIXTURE, "rb") as f:
        data = f.read()

    result = graph_module.run_turn(
        patient_id="fhir-uuid-1",
        bearer_token="tok",
        user_message="Summarize this lab result and tell me the recommended target for glucose control.",
        patient_pid="1",
        pending_document={"data": data, "filename": "lab.pdf", "doc_type": "lab_pdf", "mimetype": "application/pdf"},
    )

    # Ingestion actually persisted through the real (stubbed) OpenEMR write path.
    assert len(persist_calls) == 1
    assert persist_calls[0]["patient_id"] == "1"
    assert persist_calls[0]["document_id"] == 34

    # Both workers ran (in priority order: document first, then evidence) before finalizing.
    assert [h["to"] for h in result["handoff_log"]] == ["intake_extractor", "evidence_retriever", "agent"]
    assert result["document_processed"] is True
    assert result["evidence_fetched"] is True
    assert len(result["extracted_facts"]) == 1
    assert len(result["evidence_snippets"]) >= 1

    # The final answer is genuinely grounded: all 3 claims survived verification (none stripped),
    # one from each source type this turn produced -- proving the full chain actually reaches a
    # cited, verified answer, not just that no stage crashed.
    assert result["stripped_claims"] == []
    assert len(result["verified_claims"]) == 3
    source_types = {
        c["source"].get("source_type") or c["source"].get("resource_type") for c in result["verified_claims"]
    }
    assert source_types == {"Observation", "document", "guideline"}

    # Citation Contract's required click-to-source visual overlay: the bbox on the extracted lab
    # result must survive the entire chain -- extraction -> _flatten_extracted_facts -> injected
    # context -> the model copying it into provide_answer -> verify_claims passing it through
    # unmodified -- all the way into this turn's final response, not just as far as extracted_facts.
    document_claim = next(c for c in result["verified_claims"] if c["source"].get("source_type") == "document")
    assert document_claim["source"]["bbox"] == {"page": 0, "x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.25}
