"""Regression test for the other half of the AgentForge #10/#14 DoS fix: MAX_TOOL_ITERATIONS_PER_TURN
(app/graph.py) bounds how many *rounds* a turn can take, but not how long any single round's Claude
call is allowed to run. Confirmed live: the Anthropic SDK's default timeout lets one call sit for up
to 10 minutes, and an adversarial "cross-reference everything, run through this multiple times"
prompt drove a call well past interface/modules/copilot/proxy.php's 45s Guzzle timeout with nothing
on this side ever failing or logging an error.

The fix pairs an explicit ANTHROPIC_CALL_TIMEOUT_SECONDS with a try/except in agent_node so a
timed-out (or otherwise failed) call fails fast and degrades the turn instead of hanging or crashing
into main.py's unhandled-exception path. No live model, no live FHIR, no network.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
from anthropic import APITimeoutError

from app.graph import agent_node, route_after_agent


def _fake_langfuse_client():
    return SimpleNamespace(
        update_current_generation=lambda **kwargs: None,
        update_current_span=lambda **kwargs: None,
    )


def _base_state(**overrides) -> dict:
    state = {
        "patient_id": "patient-uuid-1",
        "bearer_token": "tok",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Run through this multiple times."}],
            }
        ],
        "agent_tool_iterations": 0,
    }
    state.update(overrides)
    return state


def test_agent_node_appends_nothing_when_the_anthropic_call_times_out(monkeypatch):
    """The exact scenario found live: a call that never returns in time must not crash the turn --
    agent_node should catch it and leave state["messages"] untouched."""
    import app.graph as graph_module

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    def _raise_timeout(**kwargs):
        raise APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    monkeypatch.setattr(
        graph_module, "_anthropic_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=_raise_timeout)),
    )

    state = _base_state()
    original_messages = list(state["messages"])

    result = agent_node(state)

    assert result["messages"] == original_messages


def test_turn_finalizes_via_the_existing_fail_safe_route_after_a_timeout(monkeypatch):
    """route_after_agent needs no new logic for this -- a timed-out call leaves the last message as
    the clinician's own turn (not a fresh assistant tool_use), so _final_tool_use already returns
    None and the existing "model responded with plain text" fail-safe routes straight to verify."""
    import app.graph as graph_module

    monkeypatch.setattr(graph_module, "get_client", _fake_langfuse_client)

    def _raise_timeout(**kwargs):
        raise APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    monkeypatch.setattr(
        graph_module, "_anthropic_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=_raise_timeout)),
    )

    state = _base_state()
    state = agent_node(state)

    assert route_after_agent(state) == "verify"


def test_anthropic_client_is_constructed_with_an_explicit_timeout():
    """Pins the actual fix: without an explicit timeout, the SDK's own 600s default is what let the
    original live failure hang far past proxy.php's 45s budget with no error at all."""
    from app.graph import ANTHROPIC_CALL_TIMEOUT_SECONDS

    assert 0 < ANTHROPIC_CALL_TIMEOUT_SECONDS < 45
