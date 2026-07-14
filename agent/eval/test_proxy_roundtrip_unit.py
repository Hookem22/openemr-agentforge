"""Pure unit tests for _repair_round_tripped_tool_use_input (app/graph.py) -- no LLM calls, no FHIR
calls, no network. Regression guard for a real production bug: OpenEMR's copilot proxy
(interface/modules/copilot/proxy.php) decodes the client-echoed conversation history with PHP's
json_decode(..., true), which cannot distinguish an empty JSON object `{}` from an empty JSON array
`[]` -- both become an empty PHP array, and re-encode as `[]`. Several tools have no arguments (e.g.
get_patient, get_conditions, get_allergies -- see tools.py's `properties: {}` schemas), so their
tool_use.input is `{}`. Once that round-trips through the proxy, Anthropic rejects the corrupted
history on the very next turn with `tool_use.input: Input should be an object`, breaking every
multi-turn conversation after the first no-argument tool call. This fix repairs that specific shape
before it's replayed to the LLM.
"""
from __future__ import annotations

from app.graph import _repair_round_tripped_tool_use_input, verify_node


def test_corrupted_empty_object_input_is_repaired_to_a_dict():
    """Failure mode guarded: a no-argument tool call's `{}` input, corrupted to `[]` by the PHP
    proxy's json_decode(..., true) round trip, must be repaired back to `{}` before being replayed
    to the Anthropic API -- otherwise every turn after the first no-argument tool call crashes with
    `tool_use.input: Input should be an object`."""
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "get_patient", "input": []}],
        }
    ]

    repaired = _repair_round_tripped_tool_use_input(messages)

    assert repaired[0]["content"][0]["input"] == {}


def test_non_empty_tool_use_input_is_left_untouched():
    """A genuinely non-empty tool_use.input (e.g. get_recent_encounters' {"count": 5}) must survive
    the repair pass unchanged -- this fix must not corrupt normal tool calls with real arguments."""
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "get_recent_encounters", "input": {"count": 5}}],
        }
    ]

    repaired = _repair_round_tripped_tool_use_input(messages)

    assert repaired[0]["content"][0]["input"] == {"count": 5}


def test_non_list_content_and_non_tool_use_blocks_are_left_untouched():
    """Regular text messages (string content, no list of blocks) and non-tool_use blocks (e.g.
    tool_result) must pass through the repair pass unmodified -- it must only ever touch
    tool_use.input, never anything else in the message history."""
    messages = [
        {"role": "user", "content": "Tell me about this patient"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "some result"}]},
    ]

    repaired = _repair_round_tripped_tool_use_input(messages)

    assert repaired == messages


def test_verify_node_appends_tool_result_for_provide_answer():
    """Failure mode guarded: execute_tools_node deliberately never emits a tool_result for
    provide_answer (see its `continue` there), so a turn's final stored message used to be a
    dangling assistant tool_use with no paired tool_result. Once that message list is echoed back
    by the client as next turn's conversation_history and a new plain-text user message is
    appended, Anthropic rejects the whole request with `tool_use ids were found without
    tool_result blocks immediately after` -- breaking every conversation right after its first
    turn (a real production bug, reproduced live). verify_node must append a synthetic tool_result
    for provide_answer's tool_use id so the stored history is always valid to replay."""
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
                        "input": {"claims": []},
                    }
                ],
            }
        ],
        "tool_results_this_turn": [],
        "tool_failures": [],
        "verified_claims": [],
        "stripped_claims": [],
        "extracted_facts": [],
        "evidence_snippets": [],
        "evidence_empty": False,
    }

    result = verify_node(state)

    last_message = result["messages"][-1]
    assert last_message["role"] == "user"
    assert last_message["content"][0]["type"] == "tool_result"
    assert last_message["content"][0]["tool_use_id"] == "toolu_answer_1"
