# PHI Audit: Langfuse Cloud Tracing (Option B — Redaction)

**Purpose of this document**: justify, with evidence, that no PHI reaches Langfuse Cloud from the
Clinical Co-Pilot agent, so the current vendor relationship (Langfuse Cloud, no BAA) is acceptable
without waiting for the full self-host migration (see `LANGFUSE_SELFHOST.md` for that deferred
Option A plan).

## Background: how the leak was possible

Langfuse's Python SDK `@observe` decorator auto-captures a wrapped function's **arguments and
return value** by default — confirmed by reading the SDK source
(`langfuse/_client/observe.py`): `capture_input`/`capture_output` resolve to the
`LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_ENABLED` env var, which defaults to enabled. Every
`@observe`-decorated function in `agent/app/graph.py` takes or returns real PHI (patient
name/DOB/gender, diagnoses, medications, allergies, vitals, labs, notes, full conversation history)
and/or the raw FHIR OAuth2 bearer token, either as a function argument or a return value. Left at
its defaults, every one of these would have been silently captured and sent to Langfuse Cloud.

Several call sites also made this worse with **explicit** manual telemetry calls
(`update_current_generation`, `update_current_span`, `set_current_trace_io`) that passed raw
conversation messages, raw tool results, and raw claim text as `input`/`output` — real PHI, sent on
purpose, not just via the decorator default.

## Fix (Option B, implemented)

1. Every `@observe` decorator in `graph.py` now explicitly sets `capture_input=False,
   capture_output=False`, disabling automatic argument/return-value capture entirely.
2. Every manual `update_current_generation` / `update_current_span` / `set_current_trace_io` call
   was rewritten to send only counts, names, flags, and numeric scores — never PHI content.
3. The Langfuse `session_id` (used to group a patient's turns together in the Langfuse UI) is now a
   salted SHA-256 hash of the FHIR patient UUID (`_hashed_session_id` in `graph.py`), truncated to
   16 hex chars — Langfuse never sees the raw patient identifier.

## Call-site-by-call-site inventory

| Function | What could leak (pre-fix) | What is sent now | Redaction mechanism |
|---|---|---|---|
| `agent_node` (`agent_llm_call` generation) | `state["messages"]` (full conversation, PHI) as input; full LLM response `content` (PHI) as output | `{"message_count": N}` as input; `{"stop_reason": ..., "tool_calls_requested": [tool names]}` as output; token counts (already safe) | `capture_input/output=False` on decorator + rewritten `update_current_generation` call |
| `_call_tool` (`tool:{name}` span) | `fhir` object (holds bearer token) and `patient_id` as auto-captured args; full tool return value (diagnosis/medication/allergy/vital/lab/note content) as auto-captured output | `tool_input` (e.g. `{"count": 5}` or `{"active_only": true}` — never PHI per `tools.py`'s schemas) as input; `{"result_count": N}` as output | `capture_input/output=False` on decorator (stops auto-capture of `fhir`/`patient_id`/return value); manual `update_current_span` sends only `tool_input` + a count |
| `execute_tools_node` (`execute_tools` span) | Full `state` dict (bearer token + all messages + all tool results) as auto-captured arg and return value — this function had **zero** manual overrides before, so it was the most exposed call site | `{"tool_calls_requested": N}` as input; `{"tool_failures": N, "results_collected": N}` as output | `capture_input/output=False` on decorator + new manual `update_current_span` call (previously absent) |
| `verify_node` (`verify_claims` span) | Full `state` dict as auto-captured arg/return; claim `text` (clinical assertions, e.g. "Patient has newly diagnosed atrial fibrillation") and verified/stripped claim contents as explicit input/output | `{"claim_count": N}` as input; `{"verified_count": N, "stripped_count": N}` as output; `strip_rate` (a ratio, already safe) as metadata/score | `capture_input/output=False` on decorator + rewritten `update_current_span` call |
| `run_turn` (`copilot_chat_turn` trace) | `patient_id`, `bearer_token`, `user_message` (clinician's literal question), `prior_messages` (full conversation) as auto-captured args; full `AgentState` result as auto-captured return value; raw patient UUID used as `session_id` | `{"message_length": N}` as trace input; `{"verified_claims": N, "stripped_claims": N, "tool_failures": N}` as trace output; `session_id` = salted hash of `patient_id` | `capture_input/output=False` on decorator + rewritten `set_current_trace_io` calls + `_hashed_session_id()` |

## What tools.py's data actually contains (why it's PHI)

Read in full for this audit. Every tool function returns clinically-identifying content:
`get_patient` (name, birth date, gender — direct identifiers), `get_conditions` (diagnosis text),
`get_medications` (drug name, dosage), `get_allergies` (allergen, reaction), `get_vitals`/`get_labs`
(observation name, value, interpretation), `get_notes` (note title), `diff_encounters` (aggregates
of the above). None of this is ever sent to Langfuse post-fix — only the *count* of resources a
tool call returned.

## What's left after redaction (confirmed non-PHI)

- Trace/span/generation **names** (`copilot_chat_turn`, `agent_llm_call`, `tool:get_medications`, etc.)
- **Durations** (Langfuse computes these itself from span start/end, not from any payload we send)
- **Counts**: message count, claim count, verified/stripped/failure counts, tool result counts
- **Flags**: `stop_reason`, tool names requested (a tool name like `get_allergies` describes *capability
  used*, not patient data)
- **Token usage** (input/output token counts — model billing telemetry, not content)
- **`strip_rate`** (a ratio 0.0–1.0, already safe, used for the 4th observability alert)
- **Hashed session ID** (one-way SHA-256 of patient UUID + server-side salt — not reversible by
  Langfuse, not the patient's actual identifier)

None of these require PHI or a BAA to justify sending to a third-party vendor.

## Live verification (this session)

Ran two real chat turns against the local agent (real Anthropic + FHIR calls, real Langfuse Cloud
export) for Maria Gonzalez (a seeded sample patient with real allergy/medication data), then queried
the Langfuse Cloud public API (`GET /api/public/traces`, `GET /api/public/observations`) directly to
inspect the exact payload Langfuse received. Confirmed for both the trace and every child
observation (`agent_llm_call`, `tool:get_medications`, `execute_tools`, `verify_claims`): `input`/
`output` fields contained only counts, names, and flags as described above — no patient name, no
diagnosis/medication/allergy text, no bearer token, and `sessionId` was a 16-character hash, not the
patient's real FHIR UUID.

## Regression coverage

`agent/eval/test_phi_redaction_unit.py` — 4 pure unit tests (no network, no LLM calls), one per
redacted call site (`agent_node`, `_call_tool`, `verify_node`, `run_turn`), each asserting the exact
counts-only shape sent to a mocked Langfuse client and asserting specific PHI/credential strings
(e.g. a patient name, a diagnosis, a fake bearer token) never appear in what would be sent. These
catch a regression if a future change removes `capture_input=False`/`capture_output=False` or
reintroduces a raw-data argument to one of the manual telemetry calls.

## Residual risk / what this does NOT cover

- **Anthropic still receives full PHI** in the actual LLM `messages.create()` call — this is
  covered separately by Anthropic's BAA (ARCHITECTURE.md), not by this redaction, which is scoped
  only to what leaves the agent toward Langfuse.
- **Exception messages**: if a tool call raises (`httpx.HTTPError`), `str(exc)` is stored in
  `state["tool_failures"]`, which is never sent to Langfuse (only its *count* is, per the table
  above) — but this is worth re-checking if `tool_failures` content is ever added to any future
  telemetry call, since an FHIR error message could theoretically echo back request data.
- **OTel resource/scope metadata** (SDK version, `public_key`, `service.instance.id`) is sent by the
  SDK itself, not by our code — this is operational metadata, not PHI.
- **This is a code-level control, not an infrastructure one.** Langfuse Cloud, as a vendor, still
  technically *could* receive PHI if a future code change reintroduces it — the guarantee here is
  "our code doesn't send it today, and tests catch regressions," not "Langfuse's infrastructure is
  incapable of receiving PHI." `LANGFUSE_SELFHOST.md` documents the stronger, infrastructure-level
  guarantee (no third party in the loop at all) as a deferred future hardening step.
