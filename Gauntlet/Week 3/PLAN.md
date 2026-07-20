# Week 3 AgentForge — Full Implementation Plan

*Approved 2026-07-20. Mirror of `/Users/willparks/.claude/plans/i-have-completed-the-dynamic-marble.md`,
stored here for the project's own record per Weeks 1-2's convention of keeping planning artifacts alongside
the rest of that week's documentation.*

## Context

Week 3 requires building an autonomous multi-agent adversarial evaluation platform (Red Team, Judge,
Orchestrator, Documentation agents) that continuously attacks and evaluates the Clinical Co-Pilot built in
Weeks 1-2. MVP is due **tomorrow night (Tuesday 11:59 PM)** — today is kickoff day. Final submission is due
**Friday at Noon**.

A repo-state check confirmed the MVP hard gates are **not actually done yet**: only planning artifacts exist
(`Gauntlet/Week 3/ARCHITECTURE.md` draft, slides, langgraph diagram — all built during architecture-defense
prep this session). `./THREAT_MODEL.md`, `./USERS.md`, `./evals/`, `./contracts/`, and any `redteam/` agent
code do not exist anywhere in the repo, and `Gauntlet/Week 3/` itself isn't committed to git yet. This plan
folds the two remaining MVP hard gates (threat model, evals + one live agent prototype) into Day 1, then
builds out the full 4-agent platform plus as many of the ~15 optional-but-graded engineering deliverables as
realistically fit, through Friday.

Per explicit decisions already made this week: one LangGraph service (`redteam/`, sibling to `agent/`), not
four microservices; a `TargetAdapter` interface + `OpenEMRAdapter` so the platform isn't permanently wired to
OpenEMR; model tiering (Red Team/Orchestrator/Documentation = Haiku, Judge = Sonnet); Exploit DB keyed by
`(target_id, target_version, attack_category)`; human approval gate as a real LangGraph interrupt for
Critical/High severity only; Garak + ZAP as complements to the custom agents, not replacements. Full detail in
`Gauntlet/Week 3/ARCHITECTURE.md` and `Gauntlet/Week 3/langgraph-diagram.mmd`.

**File placement** (confirmed): hard-gate deliverables go at the **repo root** now — `./THREAT_MODEL.md`,
`./ARCHITECTURE.md`, `./USERS.md`, `./evals/`, `./contracts/`, `./redteam/` — matching the assignment's literal
paths and exactly what Week 1 did (root-level docs during the live week, archived into `Gauntlet/Week 1/` only
after that week was fully graded — see commit `e5ed069b`). `Gauntlet/Week 3/` keeps holding the
Architecture-Defense-checkpoint artifacts plus supplementary docs (cost analysis, load test, decision records,
ATO packet) until Week 3 is done, then everything gets archived the same way.

**`Gauntlet/STATUS.md`** continues as the single living status doc across weeks (same file Week 2 used) — a
new "## Week 3" section starts today and gets updated at the end of each day, same format as Week 2's section
(checkpoint table, stage-by-stage `- [x]` build log, grader-feedback sections added as they arrive).

## File layout

```
./THREAT_MODEL.md                 ./USERS.md                    ./contracts/v1/*.schema.json
./ARCHITECTURE.md (finalized)     ./evals/                      ./redteam/app/...
Gauntlet/Week 3/BUILD_VS_CONFIGURE.md, COST_ANALYSIS.md, LOADTEST.md, OBSERVABILITY.md,
                ATO_EVIDENCE_PACKET.md, VULN_SCAN_TRIAGE.md, charts/
Gauntlet/STATUS.md                # updated daily
```

## Day-by-day plan

### Today (Monday) — both MVP hard gates land tonight, not tomorrow

1. **Update `Gauntlet/STATUS.md` first** — new Week 3 section reflecting true state (kickoff today,
   architecture-defense artifacts done, MVP hard gates in progress, plan set for the week).
2. **`Gauntlet/Week 3/BUILD_VS_CONFIGURE.md`** — evaluate Burp Suite/OWASP ZAP/Semgrep/Garak/commercial
   red-team platforms against this assignment's actual needs (multi-turn, system-specific, conflict-of-interest
   separated judging) before justifying the custom build. Cheapest to write before any redteam code exists.
3. **`./THREAT_MODEL.md`** — full attack-surface map, 6 required categories, ~500-word summary. Seed with the
   3 concrete findings already in hand: `proxy.php`'s only auth check is role-level
   (`AclMain::aclCheckCore('patients','med')`), not per-patient, with client-supplied `pid` → cross-patient
   IDOR hypothesis; `conversation_history` is client-echoed and replayed raw → state-corruption surface;
   document-ingestion vision extraction → indirect prompt-injection surface. Fill out tool-misuse, DoS, and
   identity/role categories with equally concrete hypotheses grounded in `agent/app/tools.py` and `proxy.php`.
4. **Target Adapter Layer** (build before any agent — Red Team can't run live without it):
   `redteam/app/adapters/target_adapter.py` (the ABC: `send`/`authenticate`/`describe`),
   `redteam/app/adapters/openemr_adapter.py` (attacks through `proxy.php` specifically, since that's where the
   IDOR finding lives — not straight against `/chat`, which would skip that surface entirely; auth via a
   dedicated disposable OAuth2 client, same pattern as the existing CI service-account client),
   `redteam/app/target_profile.yaml`.
5. **Contracts v1** (only the schemas today's build actually exercises — defer `next_target`/`coverage_state`
   until Orchestrator exists): `contracts/v1/attack_sequence.schema.json`, `observed_response.schema.json`,
   `judge_verdict.schema.json`, `exploit_record.schema.json`, `redteam/app/schemas.py` (Pydantic mirror),
   `contracts/README.md` (versioning policy).
6. **Exploit DB, schema only**: provision a Railway Postgres addon (confirm with me before creating any billed
   resource), `redteam/app/db.py`, one `exploit_records` table with a unique constraint on the natural key from
   day one, `redteam/migrations/0001_init.sql`.
7. **Red Team + Judge agents, live** — `redteam/app/state.py` (narrow per-agent state slices, not one shared
   object), `redteam/app/redteam_agent.py` (Haiku, seed library across ≥3 categories, circuit-breaker for
   harmful-independent-of-target content built in from the start), `redteam/app/judge_agent.py` (Sonnet, a
   fresh `messages.create` call with only the transcript — never imports or receives Red Team's own reasoning,
   enforced by construction, not convention).
8. **`./evals/` v1** — `evals/seed_attacks.json` (each case tagged with attack_category + OWASP LLM Top 10
   mapping, satisfying the mandatory Engineering Requirement from day one),
   `evals/run_redteam_eval.py` (drives Red Team → Target Adapter → Judge **live against the deployed target**,
   writes verdicts to `exploit_records`), `evals/README.md`. Running this tonight against the real deployed
   OpenEMR instance satisfies both remaining MVP hard gates in one artifact.
9. **`./ARCHITECTURE.md` finalized at root** (expand the draft with an AI-use disclosure section; update "Open
   items" to reflect what actually got built today) + **`./USERS.md`** first draft.
10. **`Gauntlet/STATUS.md`** — end-of-day update with real findings (expect at least one real bug from the
    first live adapter run).

### Tuesday — buffer day: verify and submit MVP early, then pull Wednesday's work forward

1. Morning: re-run `evals/run_redteam_eval.py` fresh, confirm all 3 categories still pass live, final read of
   THREAT_MODEL.md/ARCHITECTURE.md/USERS.md. **Submit MVP once green — target mid-morning, not 11:59 PM.**
2. **Orchestrator Agent** (`redteam/app/orchestrator_agent.py`) — now real, since it needs actual coverage
   state to read: queries `exploit_records` for gaps, decides next target, Haiku→Sonnet escalation signal.
   Adds `contracts/v1/next_target.schema.json` + `coverage_state.schema.json`.
3. **Documentation Agent v1** (`redteam/app/documentation_agent.py`) — Haiku, confirmed exploit → structured
   report (no severity-gate branching yet, that's Wednesday). `redteam/migrations/0002_documentation.sql` adds
   `vulnerability_reports`. **Data-quality checks (unique IDs, required fields, no dupes) built here, at the
   point this agent is built** — not deferred to a later pass.
4. `redteam/app/graph.py` wires `orchestrator → red_team → target_adapter → judge →(confirmed) documentation`,
   `judge →(not confirmed) orchestrator`.
5. `Gauntlet/STATUS.md` update — MVP marked done with today's date.

### Wednesday — human gate, full 4-agent loop, DB hardening (highest-complexity day, front-load the risk)

1. **Spike the LangGraph interrupt + Postgres-checkpointer mechanism in isolation first** — a throwaway script
   proving `interrupt()`/resume with a `thread_id` actually works in this LangGraph version, before touching
   the real graph. This is the single highest-complexity unknown this week (never used in this project before).
   Fallback if it doesn't pan out: a `status="pending_approval"` row + manual re-trigger endpoint — less
   architecturally clean but shippable.
2. `redteam/app/human_gate.py` — real interrupt node. Documentation Agent gets its severity branch: Low/Medium
   → auto-publish (END), Critical/High → `human_gate` interrupt → approved → resume → END.
3. `redteam/app/graph.py` — final topology, matching `langgraph-diagram.mmd` exactly.
4. Real schema-migration story (`redteam/migrations/README.md`) now that the schema has genuinely changed
   twice. Access-control model — Postgres roles/grants per agent (Orchestrator read-only, Judge insert-only,
   Documentation read+write, human_gate publish-status-only) — enforced via real `GRANT`s, not just prose.
5. SQL indexing on `(target_id, target_version, attack_category)` + the dedup key, measured before/after.
6. Regression-run SLO verified in CI (`.github/workflows/redteam-tier2-scheduled.yml`, mirroring the existing
   `agent-tier2-scheduled.yml` pattern) + contract tests validating `schemas.py` against checked-in JSON Schema.
7. `Gauntlet/STATUS.md` update.

### Thursday — traditional tooling, ATO packet, reports, deploy as its own service

1. **Garak wrapper** (`redteam/app/garak_tool.py`, low-risk, pure-Python CLI) wired as a real Orchestrator tool
   feeding `exploit_records`. **ZAP only if Garak went smoothly and time allows** — otherwise explicitly
   downgrade to "evaluated and deferred" in `BUILD_VS_CONFIGURE.md` rather than ship a flaky half-integration.
2. Simulated vuln-scan triage exercise (`Gauntlet/Week 3/VULN_SCAN_TRIAGE.md`, ≥10 findings) using
   Garak's/ZAP's real raw output, feeding genuine findings through the same Documentation Agent pipeline.
3. ATO-style evidence packet (`Gauntlet/Week 3/ATO_EVIDENCE_PACKET.md`) — arch/data-flow diagrams, auth model,
   dependency list, scan results, a real sample incident/postmortem from whatever bug this week actually found.
4. Typed error schemas per agent failure mode (`redteam/app/error_schemas.py`), rate-limit/auth docs for every
   external API (Anthropic, OpenEMR OAuth2, Garak/ZAP).
5. **Deploy `redteam/` as its own Railway service** (new Dockerfile mirroring `agent/Dockerfile`'s
   repo-root-relative COPY pattern, new service, env vars set) — required before Friday's live-test gate.
6. `./USERS.md` finalized, **≥3 vulnerability reports** generated from real, live, Judge-confirmed exploits.
7. `Gauntlet/STATUS.md` update.

### Friday morning (Noon deadline)

1. **Load/stress test** — small dry run (5-10 cases) first for a real cost/latency baseline, then the full
   100-case run against the live deployed target. `Gauntlet/Week 3/LOADTEST.md` — baseline CPU/mem/latency/
   throughput, bottleneck identification.
2. **AI cost analysis at 100/1K/10K/100K tiers** (`Gauntlet/Week 3/COST_ANALYSIS.md`), built from this
   morning's real load-test data — per-case cost = Red Team + target turn + Judge, not naive cost-per-token×n.
3. Demo video (3-5 min): full loop live, a confirmed exploit, the human-gate firing on Critical/High, Langfuse
   traces. Social media post. Final `STATUS.md` pass + smoke-check both deployed services. Submit.

## Critical files

- `redteam/app/graph.py` — the compiled LangGraph, final topology
- `redteam/app/adapters/target_adapter.py`, `redteam/app/adapters/openemr_adapter.py`
- `redteam/app/schemas.py` + `contracts/v1/*.schema.json`
- `redteam/app/db.py` + `redteam/migrations/*.sql`
- `evals/run_redteam_eval.py`
- `./THREAT_MODEL.md`, `./ARCHITECTURE.md`, `./USERS.md`
- `Gauntlet/STATUS.md`

Existing files to mirror patterns from, not modify: `agent/app/graph.py` (LangGraph structure, `@observe`),
`agent/app/config.py` (env-driven Settings), `agent/Dockerfile` (Railway deploy pattern),
`interface/modules/copilot/proxy.php` (the actual attack surface), `agent/eval/README.md` (Tier 1/2 framing).

## Risk callouts

- **LangGraph interrupt + Postgres checkpointer**: never used in this project before — Wednesday's isolated
  spike is the mitigation, with a documented fallback if it fails.
- **Shared Anthropic rate limits**: every attack case makes 3 real calls (Red Team, target, Judge) plus the
  target's own tool-use loop; `agent/` and `redteam/` likely share org-level limits. Run Friday's load test
  off-peak, treat a 429 as a real DoS-category finding, not a silently-retried annoyance.
- **Live attacks against the live target risk leaving residue** in seeded patient data if an attack actually
  succeeds (e.g. IDOR). Scope every attack to existing seeded synthetic patients only; verify-then-clean after
  any run that scores a confirmed exploit.
- **Documentation Agent's severity branch depends on Judge emitting a real severity field**, not just
  pass/fail — confirm this is in Tuesday's Judge rubric output before Wednesday's human-gate work depends on it.

## Verification

- Each day ends with `evals/run_redteam_eval.py` run live against the deployed target, results checked into
  `Gauntlet/STATUS.md`'s daily update.
- Contract tests (`contracts/tests/test_contracts_match_schemas.py`, added Wednesday) run in CI, validating
  `redteam/app/schemas.py` against checked-in JSON Schema on every change.
- The regression-suite SLO check (`.github/workflows/redteam-tier2-scheduled.yml`, added Wednesday) is the
  ongoing live verification that the full loop still works end-to-end against the deployed target, nightly.
- Friday's load test is the final end-to-end verification: 100 real attack cases, live target, real Judge
  verdicts, real reports — the same standard Week 1-2's `LOADTEST.md` used.
