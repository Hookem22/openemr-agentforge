"""Regression test for the confirmed DoS finding (THREAT_MODEL.md; AgentForge vulnerability
reports #10/#14): the agent<->execute_tools loop (app/graph.py's route_after_agent) previously had
no cap at all -- a prompt explicitly asking for repeated/simultaneous cross-referencing passes
("run through this multiple times", "cross-reference everything") could drive unbounded real tool
calls (and unbounded Anthropic API cost) within a single turn. MAX_HANDOFFS_PER_TURN caps a
different loop (supervisor's worker routing) and never bounded this one.

No live model, no live FHIR, no network -- a fake Anthropic client that always requests another
tool call (never provide_answer) proves the loop terminates on its own rather than running forever
or requiring an external timeout to end the test.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.graph import MAX_TOOL_ITERATIONS_PER_TURN, route_after_agent


def _tool_use_state(iterations: int, tool_name: str = "get_medications") -> dict:
    return {
        "agent_tool_iterations": iterations,
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": tool_name, "input": {}}],
            }
        ],
    }


def test_route_after_agent_allows_execute_tools_below_the_cap():
    state = _tool_use_state(MAX_TOOL_ITERATIONS_PER_TURN - 1)
    assert route_after_agent(state) == "execute_tools"


def test_route_after_agent_forces_verify_at_the_cap():
    """The exact failure mode a "run through this multiple times" prompt was exploiting: without
    this cap, route_after_agent would return "execute_tools" forever."""
    state = _tool_use_state(MAX_TOOL_ITERATIONS_PER_TURN)
    assert route_after_agent(state) == "verify"


def test_route_after_agent_still_forces_verify_well_past_the_cap():
    state = _tool_use_state(MAX_TOOL_ITERATIONS_PER_TURN + 50)
    assert route_after_agent(state) == "verify"


def test_provide_answer_always_routes_to_verify_regardless_of_iteration_count():
    """The cap must never block a real answer -- only more tool calls."""
    state = {
        "agent_tool_iterations": MAX_TOOL_ITERATIONS_PER_TURN + 5,
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "provide_answer", "input": {"claims": []}}],
            }
        ],
    }
    assert route_after_agent(state) == "verify"


def test_full_turn_with_a_model_that_never_stops_requesting_tools_still_terminates(monkeypatch):
    """End-to-end proof, not just the routing function in isolation: a fake Anthropic client
    scripted to behave exactly like the adversarial prompt described in reports #10/#14 (always
    request another tool call, never call provide_answer) must still produce a finished turn in a
    bounded number of real tool-call rounds, not hang or loop indefinitely."""
    import app.graph as graph_module

    call_count = {"n": 0}

    def fake_agent_create(**kwargs):
        call_count["n"] += 1
        return SimpleNamespace(
            model_dump=lambda: {"content": [{"type": "tool_use", "id": f"t{call_count['n']}", "name": "get_medications", "input": {}}]},
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )

    monkeypatch.setattr(
        graph_module, "_anthropic_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=fake_agent_create)),
    )
    monkeypatch.setitem(graph_module.TOOL_FUNCTIONS, "get_medications", lambda fhir, patient_id, **kwargs: [])

    result = graph_module.run_turn(
        patient_id="fhir-uuid-1", bearer_token="tok", user_message="Run through this multiple times.",
    )

    # Exactly MAX_TOOL_ITERATIONS_PER_TURN rounds of real tool execution happened -- not one more,
    # not unbounded -- before the cap forced finalization with whatever was gathered.
    assert result["agent_tool_iterations"] == MAX_TOOL_ITERATIONS_PER_TURN
    # The turn genuinely finished (reached verify_node, not stuck mid-loop) -- an empty answer here
    # is expected and correct (the model never called provide_answer), not a crash.
    assert result["verified_claims"] == []
