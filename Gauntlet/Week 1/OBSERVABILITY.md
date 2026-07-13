# Observability: Dashboard + Alerts

Engineering requirement: a dashboard with >=3 alerts (p95 latency threshold, error rate threshold, tool
failure rate), each documented with meaning + on-call response. Approach: **Langfuse-native** — reuse the
tracing already wired into every chat turn (`agent/app/graph.py`, decision in `ARCHITECTURE.md` Section 8)
rather than standing up a separate metrics stack.

I could not configure these directly: browser automation in this environment is domain-restricted and
Langfuse Cloud isn't reachable from it (same restriction hit earlier on the Railway production domain), and
account-level configuration on a third-party service is outside what I'll do without you driving it. Below
are the exact definitions to enter in the Langfuse UI (Project Settings -> Alerts, or the Dashboards ->
custom widget + threshold flow, depending on your Langfuse Cloud version) plus what each one means
operationally.

## Trace/span structure being alerted on

Every chat turn produces one trace named `copilot_chat_turn` (`agent/app/graph.py:234`) with:
- a `generation`-type observation `agent_llm_call` (model, token usage, input/output)
- one `tool_call` span per tool invocation, named `tool:{tool_name}` (e.g. `tool:get_medications`)
- a `verify_claims` span with `metadata.strip_rate` (fraction of claims stripped by the deterministic verifier)
- trace-level output includes `tool_failures` count (`agent/app/graph.py:256`)

## The 3 required alerts

| # | Alert | Threshold | Query/filter | Meaning | On-call response |
|---|---|---|---|---|---|
| 1 | **p95 latency** | p95 of trace `copilot_chat_turn` duration > 5s over a rolling 15-30 min window | Filter traces by `name = copilot_chat_turn`, latency percentile | The agent is regularly missing its own "seconds, not minutes" budget (ARCHITECTURE.md Section 3 tradeoff). Since the agent has no heavy compute of its own, this is almost never our code. | Check Anthropic API status page first, then the OpenEMR FHIR endpoint's own response times (Railway logs for `openemr-app`) — the two external dependencies on the critical path. |
| 2 | **Error rate** | >5% of `/chat` requests return non-200, or >5% of `copilot_chat_turn` traces end with an exception, over a rolling 15 min window | Filter traces by `name = copilot_chat_turn`, level = ERROR (or filter on HTTP status via the FastAPI access logs if not surfaced as a Langfuse score) | A step in the request path is failing outright (not just slow) — auth, FHIR client, or an uncaught exception in the LangGraph nodes. | Check `copilot-agent` Railway logs for stack traces first; if none appear there, check whether the OAuth client (`COPILOT_CLIENT_ID`/`SECRET`) or the OpenEMR OAuth server itself is the failure point (this is exactly the `invalid_client` bug found and fixed this session — this alert would have caught it automatically instead of waiting for a user report). |
| 3 | **Tool failure rate** | >5% of `tool_call` spans (any `tool:*` name) recorded as failed/errored over a rolling 5 min window | Filter observations by `name` starting with `tool:`, status = ERROR | OpenEMR's FHIR API (or the clinician's OAuth2 token) is failing, not a model problem — matches `execute_tools_node`'s `httpx.HTTPError` handling (`agent/app/graph.py:170-180`), which is the only path that increments `tool_failures`. | Check OpenEMR application logs and token expiry first — a burst of tool failures usually means one session/token lapsed or one FHIR resource type's endpoint is down, not that the whole agent broke. |

## Bonus 4th alert (assignment-specific, not in the base 3 but directly validates the verification layer)

| # | Alert | Threshold | Meaning | On-call response |
|---|---|---|---|---|
| 4 | **Verification strip rate** | `verify_claims` span `metadata.strip_rate` average > 10% over a rolling 1h window | The model is frequently trying to make claims the deterministic verifier can't ground in this turn's tool results — not an outage, but a quality regression signal. | Not paged urgently; triggers a prompt/tool-schema review. A rising strip rate over time means the safety net is being exercised more than expected and the system prompt or tool coverage needs attention. |

## Dashboard

Langfuse Cloud's default trace/observation explorer (filterable by trace name, latency, cost, and score)
already serves as the dashboard — no separate widget-building was necessary beyond configuring the 4 alerts
above against it, since every metric they reference (latency, error status, tool-call name/status, strip
rate) is already emitted per-turn by the existing `@observe` instrumentation.

## Setup steps (to run in the Langfuse UI)

1. Log into the Langfuse Cloud project already receiving `copilot-agent` traces.
2. For each alert row above: create a new alert (Project Settings -> Alerts, or equivalent in your Langfuse
   version) using the threshold and filter in that row, with a notification channel of your choice
   (email/Slack/webhook).
3. Send one real chat turn against the deployed agent to confirm a trace appears, then verify each alert's
   query returns the expected 0-hit baseline (no alert should currently be firing).
