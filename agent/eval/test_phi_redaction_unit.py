"""Pure unit tests guarding PHI redaction to Langfuse Cloud (see PHI_AUDIT.md). No LLM calls, no
FHIR calls, no real network -- the Langfuse singleton client's telemetry methods are monkeypatched
to record what would have been sent, instead of actually sending it. Regression guard for the
specific failure mode PHI_AUDIT.md documents: langfuse's `@observe` decorator auto-captures a
wrapped function's arguments/return value by default, and several functions in app/graph.py take a
raw FHIR bearer token and/or full patient chart data as arguments or return values. If any of these
`capture_input=False, capture_output=False` decorator options are ever removed, or a manual
`update_current_*`/`set_current_trace_io` call is changed to pass raw data again, these tests catch
it before it reaches Langfuse Cloud.
"""
from __future__ import annotations

from app import graph as graph_module


class _FakeLangfuseClient:
    """Records calls instead of sending telemetry. Necessary because langfuse's real get_client()
    returns a fresh, unconfigured (public_key-less) instance on every call in this dev/test
    environment -- so monkeypatching a real client's bound method doesn't intercept the instance
    app/graph.py actually calls get_client() to obtain. Patching app.graph.get_client itself to
    always return this one recording stub sidesteps that."""

    def __init__(self):
        self.calls: dict[str, list[dict]] = {}

    def _record(self, method_name):
        def _fn(**kwargs):
            self.calls.setdefault(method_name, []).append(kwargs)
        return _fn

    def __getattr__(self, name):
        return self._record(name)


def _patch_client(monkeypatch, method_name: str) -> list[dict]:
    fake = _FakeLangfuseClient()
    monkeypatch.setattr(graph_module, "get_client", lambda: fake)
    return fake.calls.setdefault(method_name, [])


def test_agent_node_sends_only_counts_not_message_content(monkeypatch):
    """Failure mode guarded: agent_node previously passed the full conversation (state["messages"])
    and the full LLM response content -- both real PHI -- as generation input/output. Only
    message_count, stop_reason, and tool names requested must be sent now."""
    calls = _patch_client(monkeypatch, "update_current_generation")

    class FakeUsage:
        input_tokens = 10
        output_tokens = 5

    class FakeResponse:
        stop_reason = "tool_use"

        def model_dump(self):
            return {
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "get_conditions", "input": {}},
                ]
            }

    FakeResponse.usage = FakeUsage()

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeAnthropicClient:
        messages = FakeMessages()

    monkeypatch.setattr(graph_module, "_anthropic_client", lambda: FakeAnthropicClient())

    state = {
        "patient_id": "p1",
        "bearer_token": "secret-token-must-never-leak",
        "messages": [
            {"role": "user", "content": "This patient, Maria Gonzalez, has atrial fibrillation."}
        ],
        "tool_results_this_turn": [],
        "tool_failures": [],
        "verified_claims": [],
        "stripped_claims": [],
    }

    graph_module.agent_node(state)

    assert calls, "update_current_generation was not called"
    sent = calls[0]
    assert sent["input"] == {"message_count": 1}
    assert sent["output"] == {"stop_reason": "tool_use", "tool_calls_requested": ["get_conditions"]}
    dumped = str(sent)
    assert "Maria Gonzalez" not in dumped
    assert "atrial fibrillation" not in dumped
    assert "secret-token-must-never-leak" not in dumped


def test_call_tool_sends_only_result_count_not_tool_result_content(monkeypatch):
    """Failure mode guarded: _call_tool's tool result (real patient data, e.g. allergy/condition
    text) must never be sent to Langfuse -- only a count of resources returned."""
    span_calls = _patch_client(monkeypatch, "update_current_span")

    def fake_tool(fhir, patient_id, **kwargs):
        return [
            {"resource_type": "AllergyIntolerance", "id": "a1", "allergen": "Penicillin", "reaction": "Rash"},
        ]

    monkeypatch.setitem(graph_module.TOOL_FUNCTIONS, "get_allergies", fake_tool)

    result = graph_module._call_tool(fhir=object(), patient_id="p1", name="get_allergies", tool_input={})

    assert result == [{"resource_type": "AllergyIntolerance", "id": "a1", "allergen": "Penicillin", "reaction": "Rash"}]
    assert len(span_calls) == 2  # one call setting name/input, one call setting output
    dumped = str(span_calls)
    assert "Penicillin" not in dumped
    assert "Rash" not in dumped
    assert span_calls[-1]["output"] == {"result_count": 1}


def test_verify_node_sends_only_claim_counts_not_claim_text(monkeypatch):
    """Failure mode guarded: verify_node previously passed the raw claims list (clinical assertion
    text, e.g. "Patient has newly diagnosed atrial fibrillation") as span input/output. Only
    claim_count / verified_count / stripped_count and the numeric strip_rate must be sent now."""
    span_calls = _patch_client(monkeypatch, "update_current_span")

    state = {
        "patient_id": "p1",
        "bearer_token": "tok",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_answer_1",
                        "name": "provide_answer",
                        "input": {
                            "claims": [
                                {
                                    "text": "Patient has newly diagnosed atrial fibrillation",
                                    "source": {"resource_type": "Condition", "resource_id": "c1"},
                                }
                            ]
                        },
                    }
                ],
            }
        ],
        "tool_results_this_turn": [{"resource_type": "Condition", "id": "c1"}],
        "tool_failures": [],
        "verified_claims": [],
        "stripped_claims": [],
    }

    graph_module.verify_node(state)

    assert span_calls, "update_current_span was not called"
    sent = span_calls[0]
    assert sent["input"] == {"claim_count": 1}
    assert sent["output"] == {"verified_count": 1, "stripped_count": 0}
    assert "atrial fibrillation" not in str(sent)


def test_run_turn_hashes_patient_id_and_redacts_trace_io(monkeypatch):
    """Failure mode guarded: run_turn previously used the raw FHIR patient UUID as the Langfuse
    session_id and the clinician's literal question text as trace input. Session id must be a
    salted hash (not equal to the raw patient_id), and trace input must contain only a message
    length, never the question text."""
    trace_io_calls = _patch_client(monkeypatch, "set_current_trace_io")

    captured_session_id = {}

    def fake_propagate_attributes(**kwargs):
        captured_session_id.update(kwargs)
        from contextlib import nullcontext

        return nullcontext()

    monkeypatch.setattr(graph_module, "propagate_attributes", fake_propagate_attributes)

    def fake_invoke(initial_state):
        return {**initial_state, "verified_claims": [], "stripped_claims": [], "tool_failures": []}

    monkeypatch.setattr(graph_module.COMPILED_GRAPH, "invoke", fake_invoke)

    patient_id = "patient-uuid-1234"
    graph_module.run_turn(patient_id=patient_id, bearer_token="tok", user_message="Does this patient have any allergies?")

    assert captured_session_id["session_id"] != patient_id
    assert len(captured_session_id["session_id"]) == 16

    assert trace_io_calls[0]["input"] == {"message_length": len("Does this patient have any allergies?")}
    dumped = str(trace_io_calls)
    assert "allergies" not in dumped
