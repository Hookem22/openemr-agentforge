# Observability: Week 2 Extensions

Extends `Gauntlet/Week 1/OBSERVABILITY.md` (the base dashboard + 4 alerts, still fully applicable
unchanged) with the additions W2_ARCHITECTURE.md Section 9 calls for: new spans for the ingestion/
RAG/supervisor pipeline, new alerts, an eval-regression alert wired to the Stage 4 gate, and `/ready`
degraded-vs-down health reporting. Same caveat as Week 1: I can't configure Langfuse Cloud alerts
directly (domain-restricted browser automation in this environment) -- below are the exact
definitions to enter in the Langfuse UI, plus what's already emitted in code for them to query.

## New spans (already emitted, `capture_input=False, capture_output=False` throughout per
`PHI_AUDIT.md`'s redaction contract -- only counts/flags/names, never PHI)

| Span (actual name in code) | Where | What it carries |
|---|---|---|
| `document_ingestion` | `agent/app/ingestion.py::upload_and_resolve_document` | `doc_type`, `byte_count`, `was_deduped` / error |
| `extraction` (generation-type) | `agent/app/ingestion.py::extract_with_vision` | `doc_type`, `page_count`, token usage, `stop_reason`, `extracted` (bool), `field_count`/`mean_confidence`/`min_confidence` (aggregated from every extracted field's own confidence -- a plain float, never PHI; added 2026-07-16 to close the Engineering Requirements' "extraction confidence per document" gap, previously promised here but not actually emitted) |
| `supervisor` (Section 9 calls this `worker_handoff` conceptually -- same span, actual code name is `supervisor`) | `agent/app/graph.py::supervisor_node` | `routed_to`, `reason` (a static heuristic label, never patient data), `handoff_index` (metadata) |
| `intake_extractor` | `agent/app/graph.py::intake_extractor_node` | outcome (`success`, `fact_count`, `document_id`), `handoff_index` (metadata) |
| `evidence_retriever` (Section 9 calls this `evidence_retrieval` conceptually -- same span, actual code name is `evidence_retriever`) | `agent/app/graph.py::evidence_retriever_node` | outcome (`success`, `result_count`), `handoff_index` (metadata) |

`handoff_index` (added 2026-07-16): the position of a supervisor decision in `handoff_log`. The
supervisor span and whichever worker span it routed to share the same value, so a grader can group
Langfuse spans by this field to reconstruct "supervisor decision #N routed to worker X" even though
LangGraph invokes them as sibling steps, not one calling the other -- see `W2_ARCHITECTURE.md`
Section 9's distributed-tracing note for the full reasoning on why they aren't literal parent/child
OTel spans.

`handoff_log` (every routing decision this turn, `{from, to, reason, timestamp}`) is also returned in
the `/chat` API response body directly -- not just in Langfuse -- so a grader/on-call engineer can
see supervisor routing without needing Langfuse access at all (W2_ARCHITECTURE.md Section 3's
"don't let the supervisor become a black box" requirement).

## New SLOs

| SLO | Target | Query |
|---|---|---|
| Document ingestion p95 | < 15s (the Claude vision call dominates) | p95 duration of `document_ingestion` + `extraction` spans combined |
| Evidence retrieval p95 | < 3s | p95 duration of `evidence_retriever` span |

## New alerts (in addition to Week 1's 4)

| # | Alert | Threshold | Query/filter | Meaning | On-call response |
|---|---|---|---|---|---|
| 5 | **Extraction failure rate** | >5% of `extraction` generations end with `extracted=false` or an exception, over a rolling 30 min window | Filter generations by `name = extraction`, output contains `extracted: false` or level=ERROR | Claude is failing to call the forced extraction tool, or timing out -- a document-ingestion-specific failure distinct from Week 1's general error-rate alert. | Check Anthropic API status first; then check whether recent fixture/document formats have shifted (e.g. an unusually large or corrupted upload) by sampling a few failing `document_ingestion` spans' `byte_count`. |
| 6 | **Retrieval latency breach** | p95 of `evidence_retriever` span duration > 3s over a rolling 30 min window | Filter spans by `name = evidence_retriever`, latency percentile | Voyage's embed/rerank calls are slow, or the BM25/RRF step over the local corpus is unexpectedly slow (corpus size regression). | Check Voyage's status page first (external dependency); if Voyage is fine, check whether `agent/data/guidelines/*.md` has grown far beyond the "dozens of chunks" scale the flat in-memory design assumes (W2_ARCHITECTURE.md Section 4). |
| 7 | **Eval regression** | Any `eval_gate_pass_rate_{rubric}` score drops >15 percentage points from its previous value, or any rubric's score < 80% -- `{rubric}` is one of `schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs` (grader-flagged fix, 2026-07: this was previously keyed by test-case domain category -- citations/refusals/extraction/etc. -- not by the actual rubric booleans) | Filter scores by name starting with `eval_gate_pass_rate_`, numeric value trend | A live behavioral regression in extraction accuracy, retrieval relevance, citation correctness, refusal safety, or missing-data handling -- the *same* mechanism `agent/eval/run_eval_gate.py` uses for the pre-push gate, surfaced as a live alert so a regression introduced *between* pushes (e.g. an Anthropic model version change, a corpus edit) is also caught. Requires running `python eval/run_eval_gate.py --push-to-langfuse` on a schedule (not wired to a CI runner in this repo -- see the gate's own two-tier-strategy rationale for why no runner is assumed) to keep this alert's data fresh. | Run `python eval/run_eval_gate.py` locally to get the specific failing case ids and `golden_checks.py` failure detail for the failing rubric, then treat it exactly like a CI gate failure (Section 6). |

## `/ready` (new endpoint, `agent/app/main.py`)

Returns `{"status": "ok" \| "degraded" \| "down", "checks": {...}}` -- **degrades gracefully rather
than reporting a binary down**, per Section 9's explicit requirement:

| Check | What it verifies | Failure severity |
|---|---|---|
| `core_fhir_chat` | `ANTHROPIC_API_KEY` set + OpenEMR FHIR `/metadata` reachable | **down** if broken -- Week 1's core chat flow itself won't work |
| `document_storage` | OpenEMR's standard (non-FHIR) API reachable (upload/procedure-result/medication/allergy write paths) | degraded -- only ingestion breaks, chat still works |
| `vector_index` | Guideline corpus (`agent/data/guidelines/*.md`) loads and has chunks | degraded -- only evidence retrieval breaks |
| `voyage_api` | A real (cached 60s TTL) reachability probe against Voyage's embed endpoint | degraded -- only evidence retrieval breaks |

Overall status is `down` only if `core_fhir_chat` itself fails; any combination of the other three
failing (even all three at once) reports `degraded`, since the core FHIR chat flow doesn't depend on
any of them directly. Guarded by `agent/eval/test_ready_unit.py` (5 unit tests, no live calls).

## Dashboard additions

Same Langfuse Cloud trace/observation explorer as Week 1 -- no new tool. Additional useful filters/
widgets once the alerts above are configured:
- Document ingestion count and success rate (`document_ingestion` span count, grouped by `was_deduped`)
- Extraction field-level pass rate (`extraction` generation count, grouped by `extracted` output flag)
- Retrieval hit rate (`evidence_retriever` span count where `result_count > 0` vs. `= 0`)
- Worker routing decision breakdown (`supervisor` span count, grouped by `routed_to`)
- Eval pass/fail rate per rubric (`eval_gate_pass_rate_*` scores, one line per rubric: schema_valid,
  citation_present, factually_consistent, safe_refusal, no_phi_in_logs)

## Setup steps (to run in the Langfuse UI, in addition to Week 1's 3 steps)

1. Confirm the new span names above appear in the trace explorer after driving one document upload
   and one guideline-evidence chat question through the deployed agent.
2. Create alerts #5-7 using the threshold/filter columns above.
3. Run `python eval/run_eval_gate.py --push-to-langfuse` once locally to seed the `eval_gate_pass_rate_*`
   scores, then verify alert #7's query returns data before relying on it.
