# SUBMISSION.md — AgentForge: Clinical Co-Pilot

High-level summary of work completed for the assignment. This is an index/overview — see the linked
documents for full detail on any given area.

## Deployed links

- **OpenEMR fork (public app)**: https://openemr-app-production-ded9.up.railway.app/ (login: `admin`/`pass`)
- **Agent service**: https://copilot-agent-production-8af2.up.railway.app/ (`/health` returns `{"status":"ok"}`;
  same Railway project as the OpenEMR app, new git-connected service built from `agent/Dockerfile`). The chat
  widget embedded in the deployed OpenEMR app is fully wired to it end-to-end: a production OAuth2 client is
  registered against the deployed instance, and `interface/modules/copilot/config.php` is generated on every
  container boot from Railway env vars (`docker/entrypoint.sh`) since the file itself is gitignored.
- **GitHub repo**: https://github.com/Hookem22/openemr-agentforge (Railway-git-connected; every push to
  `main` auto-triggers a build+deploy)

## MVP deliverables (Stages 1-5)

| Stage | Status | Where |
|---|---|---|
| 1. Run it locally | Done | Repo runs against local Apache/MySQL; see `../README.md` |
| 2. Deploy it | Done | Root `Dockerfile` + `docker/entrypoint.sh` (this fork had no prior Docker setup — built from scratch for Railway) |
| 3. Audit it | Done | `Week 1/AUDIT.md` — security + compliance/regulatory audit, ~500-word summary up top |
| 4. Identify users | Done | `Week 1/USER.md` — ED resident, overnight intake shift; 6 concrete use cases (UC-1 through UC-6) |
| 5. Plan the agent | Done | `Week 1/ARCHITECTURE.md` — integration plan, verification strategy, tradeoffs, ~500-word summary up top |

4 sample patients seeded (locally and on every deploy, idempotently, via `docker/entrypoint.sh`) to exercise
all 6 use cases: Maria Gonzalez (rich chart, new-onset condition, allergy), James Whitfield (empty chart),
Robert Chen (unflagged drug/allergy conflict, unrelated chronic condition), Dorothy Simmons (stale chart,
verified-absent allergy). See `../docs/seed-sample-patients.sql` and `../docs/seed-additional-patients.sql`.

## Agent implementation (`agent/`)

Python service — FastAPI + LangGraph + Anthropic Claude tool-use + a deterministic verification layer —
embedded in OpenEMR's patient chart view.

- **Agentic chatbot**: multi-turn, tool-calling agent with 9 tools mapped directly to `USER.md`'s use cases
  (patient snapshot, encounter diff, conditions, medications, allergies, vitals, labs, notes). No speculative
  surface area beyond what a use case calls for.
- **Auth**: real OAuth2 authorization_code+PKCE session-bridge (`interface/modules/copilot/`), not a shared
  static token — the chat panel runs inside the clinician's own logged-in OpenEMR session and exchanges a
  short-lived, patient-scoped token per request. Multi-user access control is enforced by OpenEMR's own OAuth2
  server, not reimplemented.
- **Verification system**: every model claim must cite a specific resource actually fetched *this turn*
  (`app/verifier.py`). Unsourced or mismatched claims are stripped before the clinician sees them — this is a
  deterministic, non-LLM check, not another model call grading itself.
- **Observability**: Langfuse Cloud tracing wired into every LangGraph node — one trace per chat turn, with
  child spans for the LLM call (model, tokens, input/output), each individual tool invocation (so per-tool
  failures are visible, not just aggregated), and the verifier (strip rate, verified/stripped claims).
  **PHI redacted in code before it reaches Langfuse Cloud**: every `@observe` decorator disables Langfuse's
  default auto-capture of function args/return values, and every manual telemetry call sends only counts,
  names, flags, and numeric scores — never PHI or the bearer token; session grouping uses a salted hash of
  the patient ID, not the raw FHIR UUID. Full inventory + live verification against the Langfuse Cloud public
  API: `Week 1/PHI_AUDIT.md`. Full self-hosting remains a documented, deferred future option
  (`Week 1/LANGFUSE_SELFHOST.md`), not required given this redaction.
- **Evaluation**: `../agent/eval/` — 22 tests across 6 files, covering the verification invariant (unit),
  boundary conditions (empty input, nonexistent patient, invalid auth), a safety-critical invariant (must flag
  an unflagged sulfa-allergy/sulfa-antibiotic conflict), UC-6 edge cases (empty chart vs. verified-absent
  data), a real multi-turn conversation-history corruption bug (unit), and two known upstream OpenEMR bugs
  (graceful degradation, not silent crash). Not happy-path-only: building/using this suite caught and fixed
  three real bugs (an unhandled crash on empty input, a missing UC-5 relevance-ranking rule in the system
  prompt, and the conversation-history corruption bug below). Full results and the failure-mode-per-test
  table: `../agent/eval/README.md`.

## Notable bugs found and fixed along the way

Several were caught via live testing (not just eval), each documented in detail in memory/`agent-implementation.md`-style
notes kept alongside the code:
- OpenEMR's `FhirAllergyIntoleranceService.php` raising a raw PHP warning (breaking JSON parsing) on a
  scalar-vs-list `reaction` field — handled defensively in `agent/app/fhir_client.py`.
- A session-churn race clobbering OAuth2 state/PKCE data mid-flow during the auth-bridge popup login — fixed
  by signing the round-trip data into the `state` param itself instead of depending on the shared session.
- PHP's `max_execution_time` / Apache's `Timeout` / the proxy's HTTP client timeout being misaligned, causing
  raw fatal-error HTML (not JSON) on slow agent turns — fixed by ordering all three timeout layers correctly.
- An empty chat message crashing with a raw 400 from the Anthropic API — fixed with request-boundary
  validation (found by the eval suite).
- **Every multi-turn conversation broke after the first no-argument tool call** (`tool_use.input: Input
  should be an object` from the Anthropic API): the OpenEMR-side proxy's PHP `json_decode(..., true)` can't
  distinguish an empty JSON object `{}` from an empty array `[]`, so a no-argument tool call's `input: {}`
  silently became `input: []` after round-tripping through the client-echoed conversation history. Fixed by
  repairing that specific shape before replay (`app/graph.py`'s `_repair_round_tripped_tool_use_input`),
  guarded by `agent/eval/test_proxy_roundtrip_unit.py`.

## Status / what's left

- **Agent service Railway deployment**: done, including production auth-bridge wiring (see Deployed links
  above) — the chat widget works end-to-end on the deployed app, re-verified after fixing a production-only
  OAuth2 `invalid_client` regression (a persistent-volume attach rotated OpenEMR's on-disk encryption key,
  invalidating the previously-registered client's stored secret; fixed by re-registering a new client and
  updating Railway env vars).
- **Load/stress testing (10 & 50 concurrent users)**: done — `Week 1/LOADTEST.md`. Real run against the deployed
  agent: 10 users, 0% errors, p50 8.04s/p95 24.83s; 50 users, 22% errors (all HTTP 502 at Railway's proxy
  layer, not the app — CPU/memory stayed under 23% utilization throughout), p50 18.65s/p95 53.78s. Documents a
  genuine scaling limit: the current single-`uvicorn`-worker deployment needs multiple workers/replicas before
  it can safely handle 50+ concurrent long-running turns.
- **Dashboard + 3 alerts (p95 latency, error rate, tool failure rate)**: definitions written in
  `Week 1/OBSERVABILITY.md` (plus a 4th, assignment-specific verification-strip-rate alert), each with meaning +
  on-call response, built against the trace/span structure already wired into every turn. Configuring the
  actual alerts in the Langfuse Cloud UI is a manual click-through step against the account (not something
  automatable from this repo).
- **AI cost analysis**: done — `Week 1/COST_ANALYSIS.md`. Actual dev spend pulled from Langfuse's per-trace cost
  data (123 traces, $2.63 total, ~$0.02/turn mean), plus projected cost at 100/1K/10K/100K users with the
  specific architectural changes needed at each tier (prompt caching, FHIR read caching, multi-tenant routing,
  model tiering) rather than a flat cost-per-token extrapolation.
- **Langfuse Cloud PHI compliance**: done via code-level redaction — `Week 1/PHI_AUDIT.md`. Full self-hosting
  remains intentionally deferred (`Week 1/LANGFUSE_SELFHOST.md`), since it's no longer required for compliance,
  just a stronger infrastructure-level guarantee if ever needed.
- **Demo video + social media post**: not yet done.
- **Week 2 (multimodal evidence agent)**: MVP complete and deployed live — document ingestion (lab PDF +
  intake form via Claude vision), hybrid RAG over a 7-document guideline corpus, a supervisor + 2-worker
  LangGraph extension, and a 50-case eval gate (both a fast offline tier and a real-API golden-set tier)
  all built, tested, and running against the deployed instance. Full detail, including real bugs found via
  live production testing and the plan for the rest of the week: `Week 2/STATUS.md`. Architecture:
  `Week 2/W2_ARCHITECTURE.md` / `Week 2/W2_Architecture_Slides.pptx`. Observability extensions:
  `Week 2/OBSERVABILITY.md`. Still open: extending `Week 1/COST_ANALYSIS.md`/`LOADTEST.md` to Week 2 flows,
  and the demo video.

## Key documents index

- `Week 1/AUDIT.md` — security & compliance audit
- `Week 1/USER.md` — target user & use cases
- `Week 1/ARCHITECTURE.md` — agent integration plan
- `../agent/README.md` — agent setup, dev bearer token instructions
- `../agent/eval/README.md` — eval suite results and failure-mode table
- `Week 1/LOADTEST.md` — load/stress test results (10 & 50 concurrent users) + baseline CPU/memory profile
- `Week 1/OBSERVABILITY.md` — dashboard + alert definitions (p95 latency, error rate, tool failure rate, strip rate)
- `Week 1/COST_ANALYSIS.md` — actual dev spend + projected cost at scale
- `Week 1/PHI_AUDIT.md` — PHI redaction inventory + justification for Langfuse Cloud (no BAA) being acceptable
- `Week 1/LANGFUSE_SELFHOST.md` — deferred future plan for full Langfuse self-hosting (Option A)
- `../docs/seed-sample-patients.sql`, `../docs/seed-additional-patients.sql` — sample patient data
- `Week 2/W2_ARCHITECTURE.md` — Week 2 multimodal evidence agent architecture
- `Week 2/W2_Architecture_Slides.pptx` — Week 2 architecture defense slides
- `Week 2/STATUS.md` — Week 2 build status, real bugs found/fixed, and the plan for the rest of the week
- `Week 2/OBSERVABILITY.md` — Week 2 observability extensions (new spans, alerts, `/ready` endpoint)
- `../agent/eval/README.md` — updated for Week 2: 132 offline tests + the 50-case golden-set gate
- `../agent/bruno-collection/` — verified API collection (Health/Ready/Chat/Ingest)
