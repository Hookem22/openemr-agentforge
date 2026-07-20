# THREAT_MODEL.md — Clinical Co-Pilot Attack Surface

## Summary

The Clinical Co-Pilot (Weeks 1-2) is a LangGraph agent embedded in OpenEMR via a PHP auth-bridge
(`interface/modules/copilot/proxy.php`, `upload.php`), calling 9 read-only FHIR tools plus a document-ingestion
pipeline that writes extracted lab/intake data back into OpenEMR. This document maps its attack surface across
the 6 required categories, grounded in what the code actually does — not hypothetical — because three of the
findings below are already confirmed by reading the running code, not guessed at.

**Highest-risk finding: cross-patient data exfiltration via a coarse authorization check.**
`proxy.php`'s only authorization gate is `AclMain::aclCheckCore('patients', 'med')` — a role-level capability
check (does this user have general permission to view patient records at all), not a per-patient or
care-team check. The `pid` identifying *which* patient's chart the agent should answer about comes straight
from the client's JSON request body. Any authenticated user holding the `patients`/`med` capability — the
normal case for any clinical role — can very plausibly request any other patient's `pid` and have the agent
answer questions grounded in that patient's real chart data, regardless of whether they're on that patient's
care team. This is the single highest-priority hypothesis for the initial attack suite.

**Second-highest: state corruption via client-echoed conversation history.** `conversation_history` is
accepted from the client, stored nowhere server-side between turns, and replayed raw into the next turn's
Anthropic call. A client that can modify this array (a compromised browser session, a malicious extension, or
simply a modified request) can inject a fake prior tool result — e.g. a spoofed "no allergies on file" —
that the model will treat as established fact on the current turn, potentially suppressing a real safety
warning. No integrity check exists on replayed history today.

**Third: indirect prompt injection via document ingestion.** Uploaded lab PDFs and intake forms go through
Claude-vision extraction (`agent/app/ingestion.py`) before being schema-validated and written into OpenEMR.
Schema validation is a real, existing defense against arbitrary field injection, but it doesn't rule out
crafted document content influencing which values get extracted, or smuggling instruction-like text into a
field that later gets surfaced back to the clinician as "extracted" fact.

**Fourth: an already-confirmed denial-of-service / cost-amplification gap.** `route_after_agent`
(`agent/app/graph.py:455`) has no cap on the number of `agent ⇄ execute_tools` iterations within a single
turn — only `provide_answer` ends the loop. `MAX_HANDOFFS_PER_TURN = 6` caps the *supervisor's* worker
routing, but not this inner tool-calling loop. A model influenced (via prompt injection or a crafted question)
to keep choosing tool calls over `provide_answer` can drive unbounded Anthropic API cost per turn, limited
only by each call's own 2048-token cap, not by call count.

**Fifth: coarse identity/role scoping**, the same root cause as the first finding, viewed from the
authorization-model side rather than the data-exfiltration side.

**Sixth: tool misuse is comparatively well-defended today** — `patient_id` is injected server-side from the
resolved `pid`, never taken from the model, so the model itself cannot redirect a tool call to a different
patient. The remaining tool-misuse surface is chaining/volume-based, not parameter tampering.

Priority for the initial attack suite: data exfiltration (IDOR) first, since it's the most concrete and
highest-impact; state corruption second; DoS third, since it's already confirmed in code and cheap to verify
live.

## 1. Prompt Injection — direct, indirect, multi-turn

- **Attack surface**: the `message` field sent to `/chat` (direct); uploaded document content processed by
  Claude vision in `ingestion.py` (indirect); `conversation_history` replayed across turns (multi-turn).
- **Potential impact**: model deviates from its grounded-answer-only behavior; fabricates clinical claims;
  follows injected instructions embedded in a document or prior turn instead of the clinician's actual
  question.
- **Difficulty**: direct injection — low effort, but the existing verifier (`verify_claims`) strips uncited
  claims, a real mitigating control. Indirect (document) injection — moderate effort, requires crafting a
  document that survives vision extraction while carrying injected content. Multi-turn — moderate, requires
  understanding the client-echoed history mechanism (confirmed below in State Corruption).
- **Existing defenses**: `verify_claims`'s citation-existence check (fail-closed) is real and already proven
  to strip uncited claims live. It does not, however, verify that a *tool result itself* wasn't influenced by
  injected document content before the claim citing it was made.

## 2. Data Exfiltration — PHI leakage, cross-patient exposure, authorization bypass

- **Attack surface**: `proxy.php`'s `pid` parameter (client-supplied, resolved server-side to a FHIR uuid with
  no per-patient authorization check beyond the coarse role capability above).
- **Potential impact**: full chart access (demographics, conditions, medications, allergies, vitals, labs,
  notes — all 9 tool categories) for any patient, by any authenticated clinical-role user, regardless of care
  team assignment. This is the most severe category in this threat model.
- **Difficulty**: low — requires only a valid session with the `patients`/`med` capability (the normal case)
  and knowledge of another patient's `pid` (a small integer, plausibly guessable or enumerable).
- **Existing defenses**: CSRF token check and OAuth2/SMART bearer-token scoping protect the *session*, not the
  *patient selection* within that session. `patient_id` being server-injected into tool calls (see Tool Misuse)
  prevents the *model* from redirecting to another patient, but does nothing once `proxy.php` has already
  resolved a client-chosen `pid` before the agent is ever invoked.

## 3. State Corruption — conversation history manipulation, context poisoning

- **Attack surface**: `conversation_history` in the `/chat` request body — accepted as-is, no server-side
  session-bound copy to diff against, replayed directly into the next Anthropic call.
- **Potential impact**: a client can inject a fabricated prior turn (e.g. a fake tool result claiming "no
  allergies found") that the model treats as already-established fact, potentially suppressing a real
  safety-relevant answer on a later turn, or priming the model toward a false premise.
- **Difficulty**: low for a client that controls its own request (the widget's JS already round-trips this
  array unmodified); higher for a genuinely external attacker who'd need to intercept or otherwise influence
  the browser's outgoing request.
- **Existing defenses**: none specific to history integrity. The known message-round-trip bugs fixed in
  Weeks 1-2 (`agent/eval/test_proxy_roundtrip_unit.py`) addressed *format* corruption (JSON structure), not
  *content* integrity — a well-formed but fabricated history entry passes through untouched today.

## 4. Tool Misuse — unintended invocation, parameter tampering, recursive tool calls

- **Attack surface**: the 9 FHIR tool schemas (`agent/app/tools.py`) and the `agent ⇄ execute_tools` loop.
- **Potential impact**: broad, low-friction chart enumeration within one turn (all 9 tools have no
  cross-tool rate limit); resource exhaustion from unbounded recursive tool-calling (see DoS below, same
  root cause, different lens).
- **Difficulty**: parameter tampering — effectively closed, `patient_id` is injected server-side
  (`tools.py`'s own comment: "the model cannot redirect a tool call to a different patient than the one this
  conversation is scoped to"), confirmed by reading the tool schemas directly, all of which omit
  `patient_id` from the model-visible `input_schema`. Recursive/volume-based misuse — low effort, no
  existing cap.
- **Existing defenses**: server-side `patient_id` injection is a real, confirmed, effective defense against
  the most severe form of tool misuse (redirecting to another patient via tool parameters). No defense exists
  against a single turn making many tool calls in sequence.

## 5. Denial of Service — token exhaustion, infinite loops, cost amplification

- **Attack surface**: `route_after_agent` (`agent/app/graph.py:455`) — routes to `execute_tools` for any
  non-`provide_answer` tool call, with **no iteration cap on this specific loop**, confirmed by reading the
  routing function directly. `MAX_HANDOFFS_PER_TURN = 6` caps the *separate* supervisor-to-worker routing
  loop (Stage 3), not this one.
- **Potential impact**: a single crafted turn could drive many sequential Claude + FHIR calls before ever
  reaching `provide_answer`, amplifying cost and latency well beyond a normal turn, bounded only by each
  individual call's 2048-token cap and Apache/PHP's layered timeouts (`proxy.php`'s `set_time_limit(75)`,
  Guzzle's 45s per-request timeout) — which fail the request eventually, but don't prevent the cost from
  being incurred first.
- **Difficulty**: low to moderate — requires influencing the model (via question phrasing or injected content)
  to keep preferring tool calls over answering.
- **Existing defenses**: the layered timeouts documented in `agent-implementation.md` prevent a single runaway
  turn from hanging indefinitely, but they're a request-level safety net, not a cost-control mechanism — they
  fire only after cost has already been incurred, and don't stop a client from immediately retrying.

## 6. Identity and Role Exploitation — privilege escalation, persona hijacking, trust boundary violations

- **Attack surface**: same root cause as Data Exfiltration (#2) — `aclCheckCore('patients', 'med')` is the
  *only* authorization check anywhere in the auth-bridge, confirmed by reading both `proxy.php` and
  `upload.php` directly (identical single-line check in both). No care-team, ownership, or per-patient scoping
  exists anywhere in this code path.
- **Potential impact**: any user with the standard clinical capability effectively has platform-wide patient
  access through the Co-Pilot, an implicit privilege escalation relative to whatever finer-grained access
  control OpenEMR's own UI may enforce elsewhere (not audited as part of this document — the Co-Pilot's *own*
  bypass of that finer control, if any exists, is the finding here).
- **Difficulty**: low — no special technique needed beyond having a normal clinical login and knowing/guessing
  another patient's `pid`.
- **Existing defenses**: none beyond the coarse capability check already described. The session-bound token
  race condition already documented as a known limitation (`agent-implementation.md`) is a related but
  distinct availability/reliability issue, not itself an authorization bypass.

## Coverage prioritization for the initial attack suite

1. **Data exfiltration (cross-patient IDOR)** — highest impact, low effort, directly testable against the
   live deployed target today.
2. **State corruption (history poisoning)** — second priority, real and concrete, moderate effort to craft.
3. **Denial of service (unbounded tool-call loop)** — already confirmed in code; cheap to verify live with a
   bounded, cost-controlled test rather than a full-scale attack.

Remaining categories (prompt injection via documents, tool misuse volume, broader identity/role scenarios)
are documented above with concrete hypotheses and will be built out as the attack suite and Red Team Agent
mature through the week — this is a living document, not a one-time snapshot, per the assignment's framing.
