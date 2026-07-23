"""Regression test for the real bottleneck found live (identity_role_exploitation timeout
investigation, 2026-07-23): execute_tools_node ran every requested tool call sequentially. A broad
"give me everything" question can request many independent tools in one round (patient/conditions/
medications/labs/vitals/notes/encounters/allergies all at once) -- a direct un-proxied replay
confirmed this alone took 73s, well past proxy.php's 45s budget. These are independent, read-only
FHIR GETs with no interdependency, so running them concurrently is safe and cuts wall-clock time
roughly proportionally to how many are batched.

No live model, no live FHIR, no network -- TOOL_FUNCTIONS entries replaced with fakes.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from app.graph import execute_tools_node


def _fake_langfuse_client():
    return SimpleNamespace(update_current_span=lambda **kwargs: None)


def _state_with_tool_calls(*calls: tuple[str, str]) -> dict:
    """calls: (tool_name, tool_use_id) pairs."""
    return {
        "patient_id": "patient-uuid-1",
        "bearer_token": "tok",
        "tool_results_this_turn": [],
        "tool_failures": [],
        "agent_tool_iterations": 0,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tid, "name": name, "input": {}} for name, tid in calls
                ],
            }
        ],
    }


def test_execute_tools_node_runs_independent_calls_concurrently_not_sequentially(monkeypatch):
    """The actual fix: 4 tools each artificially delayed 0.2s must complete in roughly one delay's
    worth of wall time, not four -- proving they overlap rather than running one after another."""
    import app.graph as graph_module

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    def _slow_tool(fhir, patient_id, **kwargs):
        time.sleep(0.2)
        return []

    for name in ("get_conditions", "get_medications", "get_labs", "get_vitals"):
        monkeypatch.setitem(graph_module.TOOL_FUNCTIONS, name, _slow_tool)

    state = _state_with_tool_calls(
        ("get_conditions", "t1"), ("get_medications", "t2"), ("get_labs", "t3"), ("get_vitals", "t4"),
    )

    start = time.monotonic()
    execute_tools_node(state)
    elapsed = time.monotonic() - start

    # Sequential would be ~0.8s (4 x 0.2s); concurrent should land well under that -- generous
    # margin (0.5s) to stay robust against CI/scheduling jitter without weakening the actual claim.
    assert elapsed < 0.5, f"expected concurrent execution (~0.2s), took {elapsed:.2f}s -- looks sequential"


def test_execute_tools_node_attributes_each_result_to_the_right_tool_use_id(monkeypatch):
    """Results must map back to the correct tool_use_id regardless of which thread finished first
    -- futures are resolved in submission order (f.result() per future, in order), not completion
    order, so this must hold even though the underlying calls ran concurrently."""
    import app.graph as graph_module

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    monkeypatch.setitem(
        graph_module.TOOL_FUNCTIONS, "get_conditions",
        lambda fhir, patient_id, **kwargs: [{"resource_type": "Condition", "id": "c1"}],
    )
    monkeypatch.setitem(
        graph_module.TOOL_FUNCTIONS, "get_medications",
        lambda fhir, patient_id, **kwargs: [{"resource_type": "MedicationRequest", "id": "m1"}],
    )

    state = _state_with_tool_calls(("get_conditions", "t1"), ("get_medications", "t2"))
    execute_tools_node(state)

    last = state["messages"][-1]
    blocks_by_id = {b["tool_use_id"]: b["content"] for b in last["content"]}
    assert "Condition" in blocks_by_id["t1"] and "c1" in blocks_by_id["t1"]
    assert "MedicationRequest" in blocks_by_id["t2"] and "m1" in blocks_by_id["t2"]
    assert {"resource_type": "Condition", "id": "c1"} in state["tool_results_this_turn"]
    assert {"resource_type": "MedicationRequest", "id": "m1"} in state["tool_results_this_turn"]


def test_execute_tools_node_one_tool_failing_does_not_affect_the_others(monkeypatch):
    import app.graph as graph_module
    import httpx

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    def _raise(fhir, patient_id, **kwargs):
        raise httpx.HTTPError("boom")

    monkeypatch.setitem(graph_module.TOOL_FUNCTIONS, "get_notes", _raise)
    monkeypatch.setitem(
        graph_module.TOOL_FUNCTIONS, "get_allergies",
        lambda fhir, patient_id, **kwargs: [{"resource_type": "AllergyIntolerance", "id": "a1"}],
    )

    state = _state_with_tool_calls(("get_notes", "t1"), ("get_allergies", "t2"))
    execute_tools_node(state)

    assert len(state["tool_failures"]) == 1
    assert state["tool_failures"][0]["tool"] == "get_notes"
    assert {"resource_type": "AllergyIntolerance", "id": "a1"} in state["tool_results_this_turn"]
    last = state["messages"][-1]
    error_block = next(b for b in last["content"] if b["tool_use_id"] == "t1")
    assert error_block["is_error"] is True


def test_execute_tools_node_empty_result_gets_empty_marker(monkeypatch):
    import app.graph as graph_module

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)
    monkeypatch.setitem(graph_module.TOOL_FUNCTIONS, "get_medications", lambda fhir, patient_id, **kwargs: [])

    state = _state_with_tool_calls(("get_medications", "t1"))
    execute_tools_node(state)

    assert {"resource_type": "MedicationRequest", "_empty_marker": True} in state["tool_results_this_turn"]


def test_execute_tools_node_increments_iteration_count_once_per_round(monkeypatch):
    """Regardless of how many tools were batched into this one round, agent_tool_iterations
    increments by exactly 1 -- it counts rounds, not individual tool calls (unchanged by
    parallelizing within a round)."""
    import app.graph as graph_module

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)
    for name in ("get_conditions", "get_medications", "get_labs"):
        monkeypatch.setitem(graph_module.TOOL_FUNCTIONS, name, lambda fhir, patient_id, **kwargs: [])

    state = _state_with_tool_calls(("get_conditions", "t1"), ("get_medications", "t2"), ("get_labs", "t3"))
    execute_tools_node(state)

    assert state["agent_tool_iterations"] == 1
