"""Regression test for a real gap found live (identity_role_exploitation timeout investigation,
2026-07-23): hitting MAX_TOOL_ITERATIONS_PER_TURN previously routed straight to verify_node, which
found the last message's tool_use wasn't provide_answer (its input has no "claims" key) and
finalized with zero claims -- not a partial answer, nothing at all, for a broad-but-reasonable
clinical question. force_answer_node closes this: one final Claude call offering ONLY
provide_answer (so it cannot request more tools, cannot loop, cannot re-trigger the cap) forces a
best-effort synthesis from whatever was already gathered.

No live model, no live FHIR, no network.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.graph import PROVIDE_ANSWER_TOOL, force_answer_node, route_after_agent


def _fake_langfuse_client():
    return SimpleNamespace(
        update_current_generation=lambda **kwargs: None,
    )


def _base_state(**overrides) -> dict:
    state = {
        "patient_id": "patient-uuid-1",
        "bearer_token": "tok",
        "agent_tool_iterations": 3,
        "messages": [
            {"role": "user", "content": "Give me everything on this patient."},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "get_medications", "input": {}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[]"}],
            },
        ],
    }
    state.update(overrides)
    return state


def test_force_answer_node_offers_only_provide_answer(monkeypatch):
    """The whole point: the model must not be able to request another tool here, or this could
    loop right back into the same cap."""
    import app.graph as graph_module

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)
    captured = {}

    def fake_create(**kwargs):
        captured["tools"] = kwargs.get("tools")
        return SimpleNamespace(
            model_dump=lambda: {"content": [{"type": "tool_use", "id": "t2", "name": "provide_answer", "input": {"claims": []}}]},
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )

    monkeypatch.setattr(
        graph_module, "_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    )

    force_answer_node(_base_state())

    assert captured["tools"] == [PROVIDE_ANSWER_TOOL]


def test_force_answer_node_appends_the_models_answer(monkeypatch):
    import app.graph as graph_module

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    def fake_create(**kwargs):
        return SimpleNamespace(
            model_dump=lambda: {
                "content": [
                    {
                        "type": "tool_use", "id": "t2", "name": "provide_answer",
                        "input": {"claims": [{"text": "Best-effort answer from gathered data.", "source": {}}]},
                    }
                ]
            },
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )

    monkeypatch.setattr(
        graph_module, "_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    )

    state = _base_state()
    result = force_answer_node(state)

    last = result["messages"][-1]
    assert last["role"] == "assistant"
    assert last["content"][0]["name"] == "provide_answer"
    # And route_after_agent now correctly sends this straight to verify, same as any normal
    # provide_answer call.
    result["agent_tool_iterations"] = 999  # even far past the cap, provide_answer always wins
    assert route_after_agent(result) == "verify"


def test_force_answer_node_degrades_gracefully_on_api_error(monkeypatch):
    """Same fail-closed contract as agent_node's own APIError handling -- must not crash the turn."""
    import app.graph as graph_module
    from anthropic import APITimeoutError
    import httpx

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    def fake_create(**kwargs):
        raise APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    monkeypatch.setattr(
        graph_module, "_anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    )

    state = _base_state()
    original_messages = list(state["messages"])

    result = force_answer_node(state)

    assert result["messages"] == original_messages
