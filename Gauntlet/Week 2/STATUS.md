# Week 2 Status — Multimodal Evidence Agent

**Last updated:** 2026-07-13
**Overall state:** Architecture designed and documented. MVP build has not started.

## Checkpoints (from the assignment)

| Checkpoint | Deadline | Status |
|---|---|---|
| Architecture Defense | 4 hours from sprint start | **Done** — `W2_ARCHITECTURE.md` + `W2_Architecture_Slides.pptx` |
| MVP | Tuesday @ 11:59PM | **Not started** — this doc is the plan to get there |
| Early Submission | Thursday @ 11:59PM | Not started |
| Final | Sunday @ Noon | Not started |

## What's done

- `W2_ARCHITECTURE.md` — full design covering ingestion flow, worker graph, RAG design, citation contract,
  eval-gate design, data ownership, observability extensions, failure modes, backup/recovery, and risks.
- `W2_Architecture_Slides.pptx` — 3-slide architecture-defense deck (whole-system overview, literal worker-graph
  diagram, eval-gate design).
- Key upfront decisions confirmed (see `W2_ARCHITECTURE.md` §12 for full rationale):
  - Build one new native OpenEMR write path for lab results (`procedure_order`/`report`/`result`, using the
    already-existing but previously-unpopulated `document_id` column) rather than skipping native persistence.
  - Voyage AI for both embeddings (`voyage-3-lite`) and rerank (`voyage-rerank-2`) — one vendor instead of
    adding Cohere.
  - Synthetic fixture documents (lab PDF + intake form) generated for the 4 existing seeded patients, rather
    than sourcing real ones.
  - LangGraph stays the orchestration framework (extends Week 1's graph, doesn't replace it).
  - Eval gate enforced via a local `pre-push` git hook (no GitLab CI runner assumed).

## What's not done — the five MVP stages

None of the following has any code written yet. Ordered roughly as they should be tackled — later stages
depend on earlier ones (the eval gate needs the ingestion/RAG/graph code to exist before it can test it).

### Stage 1 — Document ingestion (lab PDF + intake form)
- [ ] `agent/app/schemas.py` — `Citation`, `LabResultField`, `IntakeFormExtraction` Pydantic models
- [ ] `agent/app/ingestion.py` — `attach_and_extract(patient_id, file_path, doc_type, ...)`: upload via existing
      `POST /api/patient/{pid}/document`, dedup by hash, rasterize PDF pages (`pymupdf`), forced-tool-use Claude
      vision extraction, Pydantic validation, persist, return citations
- [ ] New PHP write path in `src/Services/ProcedureService.php` (or a small new method) inserting
      `procedure_order`/`procedure_report`/`procedure_result` rows with `document_id` set + a dedup key on
      `procedure_order.external_id`
- [ ] Reuse existing `POST /api/patient/{pid}/medication` and `/allergy` endpoints for intake facts
- [ ] Upload affordance in `interface/modules/copilot/widget.php` + new `upload.php` endpoint forwarding to a
      new agent-side `/ingest` FastAPI route
- [ ] Synthetic fixture lab PDF + intake form documents for the 4 seeded patients
- [ ] `agent/eval/test_schemas_unit.py` — schema validation acceptance/rejection tests

### Stage 2 — Hybrid RAG over guideline corpus
- [ ] Source 5–10 public guideline excerpts (ADA/USPSTF/CDC-style) relevant to the seeded patients' conditions
      → `agent/data/guidelines/*.md`
- [ ] `agent/app/rag.py` — chunking, BM25 (`rank_bm25`) + Voyage dense embeddings, reciprocal-rank fusion,
      Voyage rerank, citation-shaped results
- [ ] New deps in `agent/requirements.txt`: `voyageai`, `rank_bm25`, `pymupdf`
- [ ] `VOYAGE_API_KEY` provisioned and added to `.env` / Railway env vars

### Stage 3 — Supervisor + 2 workers
- [ ] Extend `agent/app/graph.py`: new `supervisor`, `intake_extractor`, `evidence_retriever` nodes; new state
      fields (`pending_document`, `extracted_facts`, `evidence_snippets`, `correlation_id`, `handoff_log`)
- [ ] New edges: `entry→supervisor`, `supervisor→{intake_extractor, evidence_retriever, agent}`,
      workers `→supervisor`; existing `agent⇄execute_tools→verify→END` untouched
- [ ] Extend `agent/app/verifier.py` to accept citations from `extracted_facts` and `evidence_snippets`, not
      just FHIR tool results
- [ ] `handoff_log` returned in the API response (not just internal state) so routing is inspectable
- [ ] Correlation ID minted once per request and threaded through every node/span/OpenEMR write

### Stage 4 — Eval gate (50-case golden set + PR-blocking hook)
- [ ] `agent/eval/golden_set.json` — 50 cases across extraction, evidence retrieval, citations, refusals,
      missing-data behavior
- [ ] `agent/eval/test_golden_set.py` — parametrized runner computing the 5 boolean rubrics per case
- [ ] `agent/eval/run_eval_gate.py` — aggregates pass rate per category, compares to
      `agent/eval/baseline_results.json`, fails on >5% regression or floor breach
- [ ] `agent/eval/test_ingestion_integration.py` (+ similar) — fixture/stub-based integration tests, no live API
- [ ] `scripts/install-hooks.sh` installing `.git/hooks/pre-push` to run the gate
- [ ] Reconcile the stale Week 1 eval count in `agent/eval/README.md` (says 22/26/27 in different places)

### Stage 5 — Integrate, deploy, observe
- [ ] Deploy to Railway (`origin` + `railway-deploy` push, as usual)
- [ ] Extend Langfuse spans/dashboard per `W2_ARCHITECTURE.md` §9; add the 3 new alerts
- [ ] Update the Postman/Bruno API collection with the new endpoints
- [ ] Publish an OpenAPI 3.0 spec for the new endpoints + contract tests
- [ ] Update `/ready` to check document storage, vector index, and Voyage API reachability (degraded, not binary)
- [ ] Record demo video; extend `COST_ANALYSIS.md`/`LOADTEST.md` methodology to Week 2 flows

## Immediate next action

Start Stage 1 (`agent/app/schemas.py` + `ingestion.py` + the new PHP write path) — everything else depends on
having real extracted facts and citations to route, retrieve against, and evaluate.
