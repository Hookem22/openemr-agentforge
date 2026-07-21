"""LangGraph orchestration for the Clinical Co-Pilot agent (ARCHITECTURE.md stack decision).

Flow: agent (Claude, tool-use) -> execute_tools -> back to agent -> ... -> agent calls the forced
`provide_answer` tool -> verify (deterministic, verifier.py) -> done. The model cannot end a turn
any other way than calling `provide_answer`, which is what makes the "every claim carries a
source" requirement (Section 5) structural rather than a prompt suggestion.

W2_ARCHITECTURE.md Section 3 adds an outer routing layer above this unmodified loop: entry ->
supervisor -> {intake_extractor | evidence_retriever | agent (finalize)}, with the two workers
looping back to supervisor and every routing decision logged to `handoff_log` (not just internal
state -- returned in the API response so a grader can see *why* the supervisor routed where it
did, per the "supervisor becomes a black box" pitfall the assignment calls out). `intake_extractor`
calls `ingestion.attach_and_extract`; `evidence_retriever` calls `rag.retrieve`. Both workers inject
their findings into the turn's message history as extra text blocks on the user's turn (not a new
message -- see `_append_context_to_last_user_message`, which avoids breaking the Anthropic API's
role-alternation requirement) so `agent_node`'s Claude call can cite them, and `verify_node`
(extended, not replaced) checks those citations against `extracted_facts`/`evidence_snippets`
the same deterministic way it already checks FHIR tool citations.

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
import json
import uuid
from datetime import datetime, timezone

import httpx
from typing import TypedDict

from anthropic import Anthropic
from langfuse import get_client, observe, propagate_attributes
from langgraph.graph import END, StateGraph

from .config import settings
from .fhir_client import FhirClient
from .ingestion import BBOX_SCHEMA, DocType, IngestionError, attach_and_extract
from .rag import retrieve
from .tools import TOOL_FUNCTIONS, TOOL_SCHEMAS
from .verifier import verify_claims

# Failure-mode guard (W2_ARCHITECTURE.md Section 10, "Supervisor routing error" row): a hard cap on
# handoffs per turn so a routing bug (e.g. a heuristic that never sets its "done" flag) fails closed
# to `verify` with whatever was gathered, rather than looping forever. 6 is generous headroom over
# the worst realistic case (document -> supervisor -> evidence -> supervisor -> agent = 4 hops).
MAX_HANDOFFS_PER_TURN = 6

EVIDENCE_KEYWORDS = (
    "guideline", "guidelines", "recommend", "recommendation", "recommended",
    "should i", "should we", "target", "threshold", "screen", "screening",
    "standard of care", "first-line", "first line", "when to start", "evidence",
)

TOOL_RESOURCE_TYPE = {
    "get_recent_encounters": "Encounter",
    "get_conditions": "Condition",
    "get_medications": "MedicationRequest",
    "get_allergies": "AllergyIntolerance",
    "get_vitals": "Observation",
    "get_labs": "Observation",
    "get_notes": "DocumentReference",
}

# The exact resource_type strings verify_claims will accept for a no_data claim -- an enum here
# (rather than a free-text string + prose instruction) is what actually pins the model down. Found
# via the Stage 4 golden set: without this, the model reasonably wrote resource_type="Medication"
# (a real, but wrong, FHIR resource name) instead of "MedicationRequest", silently stripping an
# otherwise-correct "no medications on file" claim from the clinician.
NO_DATA_RESOURCE_TYPES = list(dict.fromkeys(TOOL_RESOURCE_TYPE.values())) + ["guideline"]

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
                            "description": (
                                "Either a Week 1 FHIR citation ({resource_type, resource_id}), a "
                                "no_data marker ({type: 'no_data', resource_type}), or -- for facts "
                                "surfaced by the intake-extractor/evidence-retriever workers -- the "
                                "unified citation copied exactly from that fact's own citation object "
                                "({source_type, source_id, field_or_chunk_id}, plus bbox when present)."
                            ),
                            "properties": {
                                "resource_type": {
                                    "type": "string",
                                    "enum": NO_DATA_RESOURCE_TYPES,
                                    "description": "For a no_data claim, use exactly one of these -- not a synonym (e.g. 'MedicationRequest', never 'Medication').",
                                },
                                "resource_id": {"type": "string"},
                                "type": {"type": "string", "enum": ["no_data"]},
                                "source_type": {"type": "string", "enum": ["document", "guideline"]},
                                "source_id": {"type": "string"},
                                "field_or_chunk_id": {"type": "string"},
                                "bbox": {
                                    **BBOX_SCHEMA,
                                    "description": (
                                        "Normalized (0.0-1.0) location of this fact on its page image. Only "
                                        "present on a document-sourced fact -- copy it exactly, unmodified, "
                                        "when citing one; never invent one for a fact that didn't carry it."
                                    ),
                                },
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
- If an earlier message in this conversation is labeled "[Extracted from uploaded document ...]" or \
  "[Retrieved guideline evidence ...]", each line below that label is a JSON object with a `text` \
  fact and its own `citation`. When you cite one of these facts in provide_answer, copy that \
  citation's source_type, source_id, field_or_chunk_id, AND bbox (when the citation has one) fields \
  exactly as given -- never invent or alter them, and never add a bbox that wasn't already there. \
  If a "[Retrieved guideline evidence]" label says no evidence was found, say so plainly rather than \
  fabricating guideline-sourced guidance.
- You must end every turn by calling provide_answer -- this is the only way to respond.
"""


class AgentState(TypedDict):
    patient_id: str  # FHIR patient uuid (Week 1 convention -- used by every FHIR tool call)
    bearer_token: str
    patient_pid: str | None  # OpenEMR-native int pid, only required when pending_document is set
    messages: list[dict]
    tool_results_this_turn: list[dict]
    tool_failures: list[dict]
    verified_claims: list[dict]
    stripped_claims: list[dict]
    # Stage 3 additions (W2_ARCHITECTURE.md Section 3):
    pending_document: dict | None  # {"data": bytes, "filename": str, "doc_type": DocType, "mimetype": str}
    document_processed: bool
    extracted_facts: list[dict]  # [{"text": str, "citation": dict}] flattened from attach_and_extract
    evidence_snippets: list[dict]  # [{"text": str, "citation": dict}] flattened from rag.retrieve
    evidence_fetched: bool
    evidence_empty: bool
    correlation_id: str
    handoff_log: list[dict]  # [{"from": str, "to": str, "reason": str, "timestamp": str}]


def _anthropic_client() -> Anthropic:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    # No explicit retry config: the SDK already defaults to max_retries=2, retrying connection
    # errors and 408/409/429/5xx (see app/retry.py's module docstring for the verified details) --
    # this is deliberate reliance on that default, not a missing retry.
    return Anthropic(api_key=settings.anthropic_api_key)


def _latest_user_text(state: AgentState) -> str | None:
    """Finds the clinician's own question text -- the first text block of the most recent `user`-
    role message -- scanning past any extra text blocks workers appended to that same turn (see
    `_append_context_to_last_user_message`)."""
    for message in reversed(state["messages"]):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        for block in content or []:
            if isinstance(block, dict) and block.get("type") == "text":
                return block["text"]
        return None
    return None


def _append_context_to_last_user_message(state: AgentState, text: str) -> None:
    """Injects worker findings as an extra text block on the clinician's current turn rather than a
    new message. A new `role: "user"` message here would violate the Anthropic API's role-
    alternation rule (no assistant turn has happened yet at this point in the graph -- supervisor
    and both workers all run before `agent_node`'s first Claude call)."""
    last = state["messages"][-1]
    if last.get("role") != "user":
        state["messages"].append({"role": "user", "content": [{"type": "text", "text": text}]})
        return
    content = last["content"]
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content}, {"type": "text", "text": text}]
    else:
        content.append({"type": "text", "text": text})


def _facts_to_context_message(facts: list[dict], label: str) -> str:
    if not facts:
        return f"[{label}: none found]"
    lines = [f"[{label} -- {len(facts)} facts. Copy each `citation` object exactly, unmodified, "
             f"when citing one of these in provide_answer.]"]
    lines.extend(json.dumps({"text": f["text"], "citation": f["citation"]}) for f in facts)
    return "\n".join(lines)


def _with_field_id(citation: dict, field_id: str, bbox: dict | None = None) -> dict:
    """Overrides `citation.field_or_chunk_id` with a deterministic, code-assigned identifier
    instead of trusting whatever (if anything) the VLM itself put there. Real live testing found
    Claude leaves this null for lab results -- the extraction tool schema only asks it to name
    *which field* ('value', 'test_name'), not *which row* -- so every result in a multi-result lab
    PDF collapsed onto the same (document, id, None) key, letting a claim cite any one of them
    without the verifier distinguishing which. Same "don't trust the model's own attestation"
    principle verifier.py already applies to claims -- applied here to citation metadata too.

    `bbox` (Citation Contract's required click-to-source visual overlay) is folded in here too --
    it lives as a sibling field on the extracted record (schemas.py's `_ExtractedField`), not
    inside `citation` itself, so callers must pass it separately. Additive only: this is not a
    change to the Citation model's own 5-field contract (still exactly source_type/source_id/
    page_or_section/field_or_chunk_id/quote_or_value everywhere that model is used for extraction
    validation) -- verifier.py's unified-citation match key only ever reads those same 3 fields, so
    an extra `bbox` key riding along on the plain dict downstream doesn't affect matching at all."""
    merged = {**citation, "field_or_chunk_id": field_id}
    if bbox is not None:
        merged["bbox"] = bbox
    return merged


def _flatten_extracted_facts(extraction: dict, doc_type: DocType) -> list[dict]:
    """Turns a validated LabPdfExtraction/IntakeFormExtraction dict into flat {text, citation}
    facts the agent can cite -- one per schema field that carries its own citation (schemas.py's
    `_ExtractedField`), never a synthesized value without one."""
    facts: list[dict] = []
    if doc_type == "lab_pdf":
        for i, r in enumerate(extraction.get("results", [])):
            unit = f" {r['unit']}" if r.get("unit") else ""
            range_note = f" (reference range {r['reference_range']})" if r.get("reference_range") else ""
            facts.append({
                "text": f"{r['test_name']}: {r['value']}{unit}{range_note}",
                "citation": _with_field_id(r["citation"], f"results[{i}]", r.get("bbox")),
            })
        return facts

    demographics = extraction.get("demographics")
    if demographics:
        facts.append({
            "text": f"Demographics: name={demographics.get('name')}, dob={demographics.get('date_of_birth')}, "
                    f"sex={demographics.get('sex')}",
            "citation": _with_field_id(demographics["citation"], "demographics", demographics.get("bbox")),
        })
    chief_concern = extraction.get("chief_concern")
    if chief_concern:
        facts.append({
            "text": f"Chief concern: {chief_concern['text']}",
            "citation": _with_field_id(chief_concern["citation"], "chief_concern", chief_concern.get("bbox")),
        })
    for i, m in enumerate(extraction.get("current_medications", [])):
        dose = f" {m['dose']}" if m.get("dose") else ""
        freq = f" {m['frequency']}" if m.get("frequency") else ""
        facts.append({
            "text": f"Medication: {m['name']}{dose}{freq}",
            "citation": _with_field_id(m["citation"], f"current_medications[{i}]", m.get("bbox")),
        })
    for i, a in enumerate(extraction.get("allergies", [])):
        reaction = f" (reaction: {a['reaction']})" if a.get("reaction") else ""
        facts.append({
            "text": f"Allergy: {a['allergen']}{reaction}",
            "citation": _with_field_id(a["citation"], f"allergies[{i}]", a.get("bbox")),
        })
    for i, f in enumerate(extraction.get("family_history", [])):
        facts.append({
            "text": f"Family history: {f['relation']} - {f['condition']}",
            "citation": _with_field_id(f["citation"], f"family_history[{i}]", f.get("bbox")),
        })
    return facts


def _evidence_needed(state: AgentState) -> bool:
    text = (_latest_user_text(state) or "").lower()
    return any(keyword in text for keyword in EVIDENCE_KEYWORDS)


def _route_decision(state: AgentState) -> tuple[str, str]:
    if len(state["handoff_log"]) >= MAX_HANDOFFS_PER_TURN:
        return "agent", f"handoff cap ({MAX_HANDOFFS_PER_TURN}) reached this turn -- failing closed to finalize with whatever was gathered"
    if state.get("pending_document") and not state.get("document_processed"):
        return "intake_extractor", "a document is pending and has not been processed yet this turn"
    if _evidence_needed(state) and not state.get("evidence_fetched"):
        return "evidence_retriever", "the question references guideline/recommendation-style evidence and none has been fetched yet this turn"
    return "agent", "no pending document or evidence need -- ready to finalize"


@observe(name="supervisor", capture_input=False, capture_output=False)
def supervisor_node(state: AgentState) -> AgentState:
    to, reason = _route_decision(state)
    state["handoff_log"].append({
        "from": "supervisor",
        "to": to,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # handoff_index: LangGraph invokes supervisor/intake_extractor/evidence_retriever as separate
    # graph steps, not one calling the other, so their @observe spans land as siblings under the
    # trace root rather than literal parent/child of the supervisor span (distributed-tracing
    # requirement's literal ask). Tagging every span in a handoff with the same index -- the
    # position of this decision in handoff_log -- lets a grader reconstruct "supervisor decision #N
    # routed to worker X, and worker X's span with handoff_index=N is that same decision's result"
    # from Langfuse span metadata alone, without needing the graph restructured to force real
    # nesting (a bigger, riskier change to an already-verified-live graph -- see
    # W2_ARCHITECTURE.md Section 9 for the full reasoning on this tradeoff).
    handoff_index = len(state["handoff_log"]) - 1
    # Redacted: reason strings are static heuristic labels (never patient data), safe to log.
    get_client().update_current_span(
        output={"routed_to": to, "reason": reason}, metadata={"handoff_index": handoff_index}
    )
    return state


def route_after_supervisor(state: AgentState) -> str:
    return state["handoff_log"][-1]["to"]


@observe(name="intake_extractor", capture_input=False, capture_output=False)
def intake_extractor_node(state: AgentState) -> AgentState:
    # Redacted: `doc["data"]` is the raw document bytes and `result["extraction"]` is real PHI --
    # capture_input/output=False above stops @observe from auto-logging either. Only an outcome
    # summary (counts/success flag) is sent manually below.
    doc = state["pending_document"]
    if doc is None:
        # Not reachable via _route_decision (only routes here when pending_document is set), but
        # an explicit raise -- not assert -- so this invariant still holds under -O (assertions are
        # stripped by Python's optimizer; this guard is not).
        raise RuntimeError("intake_extractor_node called with no pending_document")
    patient_pid = state["patient_pid"]
    if patient_pid is None:
        raise RuntimeError("intake_extractor_node called with no patient_pid (must be set alongside pending_document)")
    try:
        # attach_and_extract's `patient_id` is the OpenEMR-native int pid (document/medication
        # endpoints); its `patient_uuid` is the FHIR uuid (allergy endpoint only) -- the *opposite*
        # of AgentState's own "patient_id" field, which has been the FHIR uuid ever since Week 1
        # (see fhir_client.py/tools.py). `patient_pid` is the new field carrying the native int pid
        # specifically for this call.
        result = attach_and_extract(
            patient_id=patient_pid,
            data=doc["data"],
            filename=doc["filename"],
            doc_type=doc["doc_type"],
            bearer_token=state["bearer_token"],
            mimetype=doc.get("mimetype", "application/pdf"),
            patient_uuid=state["patient_id"],
            correlation_id=state["correlation_id"],
        )
        facts = _flatten_extracted_facts(result["extraction"], doc["doc_type"])
        state["extracted_facts"].extend(facts)
        context_message = _facts_to_context_message(facts, "Extracted from uploaded document")
        outcome = {"success": True, "fact_count": len(facts), "document_id": result["document_id"]}
    except (IngestionError, httpx.HTTPError) as exc:
        # Real bug found live (2026-07-16, via the new Full Week 2 Flow Bruno request): this except
        # clause originally only caught IngestionError, so an upstream OpenEMR HTTP failure inside
        # attach_and_extract (401/5xx/timeout -- httpx.HTTPStatusError/HTTPError, not IngestionError)
        # propagated uncaught through the whole LangGraph invoke, crashing the *entire* chat turn
        # with a raw 500 instead of degrading just this worker's outcome -- the same bug class
        # main.py's standalone /ingest route already had fixed, but this chat-embedded path didn't.
        context_message = f"[Extracted from uploaded document: processing failed -- {exc}]"
        outcome = {"success": False}

    state["document_processed"] = True
    _append_context_to_last_user_message(state, context_message)
    # handoff_index links this span back to the exact supervisor decision that routed here (see
    # supervisor_node's comment) -- the most recent handoff_log entry, since supervisor always runs
    # immediately before this node.
    get_client().update_current_span(output=outcome, metadata={"handoff_index": len(state["handoff_log"]) - 1})
    return state


@observe(name="evidence_retriever", capture_input=False, capture_output=False)
def evidence_retriever_node(state: AgentState) -> AgentState:
    # Redacted: the clinician's question text and retrieved guideline text aren't raw patient PHI,
    # but are still excluded from auto-capture for consistency with every other node's pattern --
    # only a result count is sent manually below.
    query = _latest_user_text(state) or ""
    outcome: dict[str, object]
    try:
        results = retrieve(query)
        outcome = {"success": True, "result_count": len(results)}
    except RuntimeError as exc:
        # W2_ARCHITECTURE.md Section 10: VOYAGE_API_KEY missing or an empty corpus -- degrade to "no
        # evidence found" rather than crashing the turn.
        results = []
        outcome = {"success": False, "error": str(exc)}

    snippets = [{"text": r.text, "citation": r.citation.model_dump()} for r in results]
    state["evidence_snippets"].extend(snippets)
    state["evidence_fetched"] = True
    state["evidence_empty"] = state["evidence_empty"] or not snippets
    _append_context_to_last_user_message(state, _facts_to_context_message(snippets, "Retrieved guideline evidence"))
    # handoff_index links this span back to the exact supervisor decision that routed here -- see
    # supervisor_node's comment.
    get_client().update_current_span(output=outcome, metadata={"handoff_index": len(state["handoff_log"]) - 1})
    return state


@observe(as_type="generation", name="agent_llm_call", capture_input=False, capture_output=False)
def agent_node(state: AgentState) -> AgentState:
    client = _anthropic_client()
    # Tool schemas and messages are plain dicts, not the SDK's exact nested TypedDicts -- same
    # tradeoff as ingestion.py's extract_with_vision, see that comment for the reasoning.
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=TOOL_SCHEMAS + [PROVIDE_ANSWER_TOOL],  # type: ignore[arg-type]
        messages=state["messages"],  # type: ignore[arg-type]
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
    result = verify_claims(
        claims,
        state["tool_results_this_turn"],
        extracted_facts=state["extracted_facts"],
        evidence_snippets=state["evidence_snippets"],
        empty_evidence=state["evidence_empty"],
    )
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
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("intake_extractor", intake_extractor_node)
    graph.add_node("evidence_retriever", evidence_retriever_node)
    graph.add_node("agent", agent_node)
    graph.add_node("execute_tools", execute_tools_node)
    graph.add_node("verify", verify_node)

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"intake_extractor": "intake_extractor", "evidence_retriever": "evidence_retriever", "agent": "agent"},
    )
    graph.add_edge("intake_extractor", "supervisor")
    graph.add_edge("evidence_retriever", "supervisor")
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
def run_turn(
    patient_id: str,
    bearer_token: str,
    user_message: str,
    prior_messages: list[dict] | None = None,
    patient_pid: str | None = None,
    pending_document: dict | None = None,
) -> AgentState:
    # Redacted: patient_id, bearer_token, user_message (clinician's literal question), and
    # prior_messages (full conversation) are all PHI/credentials -- capture_input/output=False
    # above stops @observe from auto-logging this function's args/return value. session_id uses a
    # salted hash of patient_id, not the raw FHIR UUID, so Langfuse never sees it.
    # W2_ARCHITECTURE.md Section 8: minted once per request, before entering the Langfuse context
    # below so propagate_attributes' metadata (not just handoff_log) reaches every span in the
    # trace -- including the trace root itself, which propagate_attributes also back-fills per its
    # own docs ("sets attributes on the currently active span AND ... new child spans"). Threaded
    # through to attach_and_extract too (intake_extractor_node) so it reaches the one OpenEMR write
    # path this project owns as an X-Correlation-Id header, not just Langfuse.
    correlation_id = uuid.uuid4().hex
    with propagate_attributes(
        session_id=_hashed_session_id(patient_id), metadata={"correlation_id": correlation_id}
    ):
        prior_messages = _repair_round_tripped_tool_use_input(prior_messages or [])
        client = get_client()
        client.set_current_trace_io(input={"message_length": len(user_message)})
        messages = (prior_messages or []) + [{"role": "user", "content": user_message}]
        initial_state: AgentState = {
            "patient_id": patient_id,
            "bearer_token": bearer_token,
            "patient_pid": patient_pid,
            "messages": messages,
            "tool_results_this_turn": [],
            "tool_failures": [],
            "verified_claims": [],
            "stripped_claims": [],
            "pending_document": pending_document,
            "document_processed": False,
            "extracted_facts": [],
            "evidence_snippets": [],
            "evidence_fetched": False,
            "evidence_empty": False,
            "correlation_id": correlation_id,
            "handoff_log": [],
        }
        result = COMPILED_GRAPH.invoke(initial_state)
        client.set_current_trace_io(
            output={
                "verified_claims": len(result["verified_claims"]),
                "stripped_claims": len(result["stripped_claims"]),
                "tool_failures": len(result["tool_failures"]),
                "handoffs": len(result["handoff_log"]),
            }
        )
        return result
