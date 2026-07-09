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
| 1. Run it locally | Done | Repo runs against local Apache/MySQL; see `README.md` |
| 2. Deploy it | Done | Root `Dockerfile` + `docker/entrypoint.sh` (this fork had no prior Docker setup — built from scratch for Railway) |
| 3. Audit it | Done | `./AUDIT.md` — security + compliance/regulatory audit, ~500-word summary up top |
| 4. Identify users | Done | `./USER.md` — ED resident, overnight intake shift; 6 concrete use cases (UC-1 through UC-6) |
| 5. Plan the agent | Done | `./ARCHITECTURE.md` — integration plan, verification strategy, tradeoffs, ~500-word summary up top |

4 sample patients seeded (locally and on every deploy, idempotently, via `docker/entrypoint.sh`) to exercise
all 6 use cases: Maria Gonzalez (rich chart, new-onset condition, allergy), James Whitfield (empty chart),
Robert Chen (unflagged drug/allergy conflict, unrelated chronic condition), Dorothy Simmons (stale chart,
verified-absent allergy). See `docs/seed-sample-patients.sql` and `docs/seed-additional-patients.sql`.

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
  **Documented compliance debt**: Cloud tracing sends full trace payloads (real PHI) with no BAA in place —
  acceptable for dev/eval only, must migrate to self-hosted before any "production-ready" claim (flagged
  repeatedly in code comments, `.env.example`, and `ARCHITECTURE.md`).
- **Evaluation**: `agent/eval/` — 19 tests across 5 files, covering the verification invariant (unit),
  boundary conditions (empty input, nonexistent patient, invalid auth), a safety-critical invariant (must flag
  an unflagged sulfa-allergy/sulfa-antibiotic conflict), UC-6 edge cases (empty chart vs. verified-absent
  data), and two known upstream OpenEMR bugs (graceful degradation, not silent crash). Not happy-path-only:
  building this suite caught and fixed two real bugs (an unhandled crash on empty input, and a missing UC-5
  relevance-ranking rule in the system prompt). Full results and the failure-mode-per-test table: `agent/eval/README.md`.

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

## Status / what's left

- **Agent service Railway deployment**: done, including production auth-bridge wiring (see Deployed links
  above) — the chat widget works end-to-end on the deployed app, not just locally.
- **Load/stress testing (10 & 50 concurrent users)**: not yet done.
- **Dashboard + 3 alerts (p95 latency, error rate, tool failure rate)**: not yet done.
- **AI cost analysis**: not yet done.
- **Self-hosted Langfuse migration**: intentionally deferred past this submission (documented compliance debt,
  not an oversight) — required before any "production-ready" claim.

## Key documents index

- `AUDIT.md` — security & compliance audit
- `USER.md` — target user & use cases
- `ARCHITECTURE.md` — agent integration plan
- `agent/README.md` — agent setup, dev bearer token instructions
- `agent/eval/README.md` — eval suite results and failure-mode table
- `docs/seed-sample-patients.sql`, `docs/seed-additional-patients.sql` — sample patient data
