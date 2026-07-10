"""LangGraph orchestration for the Clinical Co-Pilot agent (ARCHITECTURE.md stack decision).

Flow: agent (Claude, tool-use) -> execute_tools -> back to agent -> ... -> agent calls the forced
`provide_answer` tool -> verify (deterministic, verifier.py) -> done. The model cannot end a turn
any other way than calling `provide_answer`, which is what makes the "every claim carries a
source" requirement (Section 5) structural rather than a prompt suggestion.

Observability (agent-implementation.md decision #3): each node below is wrapped with Langfuse's
`@observe` decorator, so a single chat turn produces one trace (`run_turn`) with child spans for
the LLM call, each individual tool call, and the verifier -- giving per-step latency, tool
failures, and token usage exactly as the assignment's observability requirement asks for.

PHI REDACTION (see PHI_AUDIT.md for the full inventory/justification): Langfuse's `@observe`
decorator auto-captures a wrapped function's arguments and return value by default (confirmed via
the SDK source -- `capture_input`/`capture_output` resolve to `LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_
ENABLED`, which defaults to enabled). Every function below carries real PHI (patient name/DOB,
diagnoses, medications, allergies, vitals, labs, notes, full conversation messages) and/or the raw
FHIR bearer token in its arguments or return value, so every `@observe` decorator here explicitly
sets `capture_input=False, capture_output=False` to disable that auto-capture, and any telemetry
we DO want (latency, token counts, tool names, result counts, error flags, strip rate) is sent
manually via `update_current_generation`/`update_current_span`/`set_current_trace_io` using
redacted/summarized values only -- never raw PHI or the bearer token. This is why Langfuse Cloud
(no BAA) is acceptable here: see PHI_AUDIT.md for the call-site-by-call-site justification, and
LANGFUSE_SELFHOST.md for the deferred full self-host plan (Option A) if this redaction approach is
ever judged insufficient.
If LANGFUSE_PUBLIC_KEY/SECRET_KEY are unset (see .env.example), the client logs an "Authentication
error... Client will be disabled" warning per call but does not raise -- the agent still runs.
"""
from __future__ import annotations

import hashlib
import httpx
from typing import TypedDict

from anthropic import Anthropic
from langfuse import get_client, observe, propagate_attributes
from langgraph.graph import END, StateGraph

from .config import settings
from .fhir_client import FhirClient
from .tools import TOOL_FUNCTIONS, TOOL_SCHEMAS
from .verifier import verify_claims

TOOL_RESOURCE_TYPE = {
    "get_recent_encounters": "Encounter",
    "get_conditions": "Condition",
    "get_medications": "MedicationRequest",
    "get_allergies": "AllergyIntolerance",
    "get_vitals": "Observation",
    "get_labs": "Observation",
    "get_notes": "DocumentReference",
}

PROVIDE_ANSWER_TOOL = {
    "name": "provide_answer",
    "description": (
        "Submit the final answer to the clinician. This is the ONLY way to end the turn. "
        "Every clinical claim must be listed here with its source -- never write claims as free "
        "prose anywhere else. If a use case's data was checked and found empty (e.g. no "
        "allergies on file), include that as a claim with source.type='no_data' -- do not just "
        "stay silent about it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "source": {
                            "type": "object",
                            "properties": {
                                "resource_type": {"type": "string"},
                                "resource_id": {"type": "string"},
                                "type": {"type": "string", "enum": ["no_data"]},
                            },
                        },
                    },
                    "required": ["text", "source"],
                },
            }
        },
        "required": ["claims"],
    },
}

SYSTEM_PROMPT = """You are the Clinical Co-Pilot, helping an ED resident prep for a patient visit in \
the ~90 seconds between rooms. You have read-only tools to look up this specific patient's record. \
Rules, no exceptions:
- Never state a clinical fact you did not just retrieve via a tool call this turn.
- Never infer a drug interaction or dosage risk that isn't backed by actual allergy/medication \
  data you retrieved -- but if the data DOES show a conflict (e.g. a prescribed drug in a class the \
  patient is allergic to), you MUST flag it explicitly.
- If a tool returns no results, say so plainly (e.g. "no allergy information on file") rather than \
  staying silent or assuming none exist.
- When explaining why a patient might be here tonight, rank chronic conditions/history by \
  plausible relevance to the presenting complaint -- do not list an unrelated condition (e.g. \
  osteoarthritis for a chest-pain visit) flatly alongside relevant ones; if you mention it at all, \
  explicitly note it's unrelated/lower-priority.
- You must end every turn by calling provide_answer -- this is the only way to respond.
"""


class AgentState(TypedDict):
    patient_id: str
    bearer_token: str
    messages: list[dict]
    tool_results_this_turn: list[dict]
    tool_failures: list[dict]
    verified_claims: list[dict]
    stripped_claims: list[dict]


def _anthropic_client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return Anthropic(api_key=settings.anthropic_api_key)


@observe(as_type="generation", name="agent_llm_call", capture_input=False, capture_output=False)
def agent_node(state: AgentState) -> AgentState:
    client = _anthropic_client()
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=TOOL_SCHEMAS + [PROVIDE_ANSWER_TOOL],
        messages=state["messages"],
    )
    assistant_content = response.model_dump()["content"]
    # Redacted: no message text/content (PHI) sent to Langfuse -- only counts and tool names, which
    # are never patient data (see PHI_AUDIT.md).
    tool_calls_requested = [b["name"] for b in assistant_content if b.get("type") == "tool_use"]
    get_client().update_current_generation(
        model=settings.anthropic_model,
        input={"message_count": len(state["messages"])},
        output={"stop_reason": response.stop_reason, "tool_calls_requested": tool_calls_requested},
        usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
    )
    state["messages"].append({"role": "assistant", "content": assistant_content})
    return state


def _final_tool_use(state: AgentState) -> dict | None:
    last = state["messages"][-1]
    if last["role"] != "assistant":
        return None
    for block in last["content"]:
        if block.get("type") == "tool_use":
            return block
    return None


def route_after_agent(state: AgentState) -> str:
    tool_use = _final_tool_use(state)
    if tool_use is None:
        return "verify"  # model responded with plain text -- shouldn't happen, fail safe to verify (0 claims)
    if tool_use["name"] == "provide_answer":
        return "verify"
    return "execute_tools"


@observe(name="tool_call", as_type="tool", capture_input=False, capture_output=False)
def _call_tool(fhir: FhirClient, patient_id: str, name: str, tool_input: dict):
    """Thin wrapper so each individual tool call gets its own Langfuse span (name, input, latency,
    and -- if it raises -- the error, since @observe auto-records exceptions raised through it),
    rather than only seeing the aggregate execute_tools span.

    Redacted: `fhir` (holds the bearer token) and `patient_id` are excluded from auto-capture via
    capture_input=False. `tool_input` (e.g. {"count": 5}) has no PHI per tools.py's schemas, so it's
    still logged explicitly. The tool's return value (real PHI) is never logged -- only its count.
    """
    get_client().update_current_span(name=f"tool:{name}", input=tool_input)
    result = TOOL_FUNCTIONS[name](fhir, patient_id, **tool_input)
    get_client().update_current_span(output={"result_count": len(result) if isinstance(result, list) else 1})
    return result


@observe(name="execute_tools", capture_input=False, capture_output=False)
def execute_tools_node(state: AgentState) -> AgentState:
    # Redacted: `state` (arg and return value) holds the bearer token and every tool result this
    # turn -- capture_input/output=False above stops @observe from auto-logging it. A safe summary
    # (counts only) is logged manually below instead.
    last = state["messages"][-1]
    fhir = FhirClient(state["bearer_token"])
    tool_result_blocks = []

    for block in last["content"]:
        if block.get("type") != "tool_use" or block["name"] == "provide_answer":
            continue
        name, tool_use_id, tool_input = block["name"], block["id"], block.get("input", {})
        try:
            result = _call_tool(fhir, state["patient_id"], name, tool_input)
        except httpx.HTTPError as exc:
            state["tool_failures"].append({"tool": name, "error": str(exc)})
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"ERROR: could not retrieve this data right now ({exc}).",
                    "is_error": True,
                }
            )
            continue

        if isinstance(result, list):
            if not result:
                resource_type = TOOL_RESOURCE_TYPE.get(name)
                if resource_type:
                    state["tool_results_this_turn"].append({"resource_type": resource_type, "_empty_marker": True})
            else:
                state["tool_results_this_turn"].extend(result)
        elif isinstance(result, dict):
            if "resource_type" in result:
                state["tool_results_this_turn"].append(result)
            else:
                # diff_encounters-shaped: nested lists of already resource_type/id-tagged items.
                for value in result.values():
                    if isinstance(value, list):
                        state["tool_results_this_turn"].extend(v for v in value if isinstance(v, dict) and "resource_type" in v)

        tool_result_blocks.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)})

    state["messages"].append({"role": "user", "content": tool_result_blocks})
    get_client().update_current_span(
        input={"tool_calls_requested": len(tool_result_blocks)},
        output={
            "tool_failures": len(state["tool_failures"]),
            "results_collected": len(state["tool_results_this_turn"]),
        },
    )
    return state


@observe(name="verify_claims", capture_input=False, capture_output=False)
def verify_node(state: AgentState) -> AgentState:
    tool_use = _final_tool_use(state)
    claims = tool_use["input"].get("claims", []) if tool_use else []
    result = verify_claims(claims, state["tool_results_this_turn"])
    state["verified_claims"] = result.verified_claims
    state["stripped_claims"] = result.stripped_claims
    if tool_use is not None:
        # Anthropic requires every tool_use block to be immediately followed by its tool_result in
        # the next message. execute_tools_node deliberately never does this for provide_answer (it's
        # not a real data-fetch tool, see its `continue` there), so without this the turn's stored
        # messages -- which become next turn's client-echoed conversation_history -- end with a
        # dangling tool_use. Replaying that plus a new plain-text user message on turn 2 gets
        # rejected by the API with `tool_use ids were found without tool_result blocks immediately
        # after`, breaking every multi-turn conversation right after the first turn. Append a
        # synthetic result so the history stays valid to replay.
        state["messages"].append(
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_use["id"], "content": "Answer received."}],
            }
        )
    client = get_client()
    # Redacted: claim text and verified/stripped claim contents are real clinical claims (PHI) --
    # only counts and the strip_rate (a ratio, not patient data) are sent to Langfuse.
    client.update_current_span(
        input={"claim_count": len(claims)},
        output={"verified_count": len(result.verified_claims), "stripped_count": len(result.stripped_claims)},
        metadata={"strip_rate": result.strip_rate},
    )
    # Also emit as a numeric Score (not just span metadata) so it's selectable under the Langfuse
    # Monitor UI's "Scores (numeric)" view -- metadata fields aren't directly alertable there.
    client.score_current_trace(name="strip_rate", value=result.strip_rate, data_type="NUMERIC")
    return state


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("execute_tools", execute_tools_node)
    graph.add_node("verify", verify_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route_after_agent, {"execute_tools": "execute_tools", "verify": "verify"})
    graph.add_edge("execute_tools", "agent")
    graph.add_edge("verify", END)
    return graph.compile()


COMPILED_GRAPH = build_graph()


def _repair_round_tripped_tool_use_input(messages: list[dict]) -> list[dict]:
    """Defensive fix for a lossy round trip, not a design choice: the OpenEMR-side proxy
    (interface/modules/copilot/proxy.php) decodes the client-echoed conversation history with PHP's
    json_decode(..., true), which turns every JSON object into a PHP associative array -- an empty
    JSON object `{}` (e.g. a no-argument tool call's input, see tools.py's several
    `properties: {}` schemas) is then indistinguishable from an empty array and re-encodes as `[]`.
    Anthropic's API requires tool_use.input to always be a JSON object, so repair that specific
    shape before it's replayed -- without this, any turn after the first tool call with no
    arguments (get_patient, get_conditions, get_allergies, get_vitals, get_labs, get_notes,
    diff_encounters) breaks every subsequent turn in the conversation.
    """
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("input") == []:
                block["input"] = {}
    return messages


def _hashed_session_id(patient_id: str) -> str:
    """Groups a patient's chat turns into one Langfuse session view without sending Langfuse the
    raw FHIR patient UUID (see PHI_AUDIT.md). Salted, one-way hash -- not reversible by Langfuse."""
    salted = f"{settings.langfuse_session_salt}:{patient_id}".encode()
    return hashlib.sha256(salted).hexdigest()[:16]


@observe(name="copilot_chat_turn", capture_input=False, capture_output=False)
def run_turn(patient_id: str, bearer_token: str, user_message: str, prior_messages: list[dict] | None = None) -> AgentState:
    # Redacted: patient_id, bearer_token, user_message (clinician's literal question), and
    # prior_messages (full conversation) are all PHI/credentials -- capture_input/output=False
    # above stops @observe from auto-logging this function's args/return value. session_id uses a
    # salted hash of patient_id, not the raw FHIR UUID, so Langfuse never sees it.
    with propagate_attributes(session_id=_hashed_session_id(patient_id)):
        prior_messages = _repair_round_tripped_tool_use_input(prior_messages or [])
        client = get_client()
        client.set_current_trace_io(input={"message_length": len(user_message)})
        messages = (prior_messages or []) + [{"role": "user", "content": user_message}]
        initial_state: AgentState = {
            "patient_id": patient_id,
            "bearer_token": bearer_token,
            "messages": messages,
            "tool_results_this_turn": [],
            "tool_failures": [],
            "verified_claims": [],
            "stripped_claims": [],
        }
        result = COMPILED_GRAPH.invoke(initial_state)
        client.set_current_trace_io(
            output={
                "verified_claims": len(result["verified_claims"]),
                "stripped_claims": len(result["stripped_claims"]),
                "tool_failures": len(result["tool_failures"]),
            }
        )
        return result
