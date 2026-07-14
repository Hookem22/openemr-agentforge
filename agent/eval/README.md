# Eval Suite

Proves the Clinical Co-Pilot agent works, per the assignment's evaluation requirement: covers
boundary conditions, safety-critical invariants, and known regression risks -- not a happy-path-only
demo. Every test's docstring states the specific failure mode it guards against.

**Two tiers** (Gauntlet/Week 2/W2_ARCHITECTURE.md Section 6):
- **Tier 1 -- always run, no live API**: every `test_*_unit.py` file plus `test_ingestion_integration.py`
  and `test_rag_integration.py` (Anthropic/OpenEMR/Voyage calls stubbed out -- guards wiring/plumbing,
  not model quality). Free, near-instant, safe to run on every save.
- **Tier 2 -- the pre-push gate, real API**: `test_golden_set.py`'s 50 cases, run via
  `eval/run_eval_gate.py` (see below). Costs real Anthropic + Voyage tokens and needs a local OpenEMR
  + fresh `DEV_BEARER_TOKEN` for its chat-based cases.

## Prerequisites

- Local OpenEMR running with `rest_api`/`rest_fhir_api` globals enabled, and both seed scripts run:
  `docs/seed-sample-patients.sql` (Maria Gonzalez, James Whitfield) and
  `docs/seed-additional-patients.sql` (Robert Chen, Dorothy Simmons).
- `agent/.env` populated: `ANTHROPIC_API_KEY`, `FHIR_BASE_URL`, `DEV_BEARER_TOKEN` (see
  `agent/README.md` for how to mint one -- it expires in ~1 hour, so regenerate if tests suddenly
  start failing on auth).
- `cd agent && source venv/bin/activate && pip install -r requirements-dev.txt`

## Running

```
pytest -v
```

`pytest.ini` (`testpaths = eval`, `pythonpath = .`) means this works from `agent/` regardless of
invocation details. Run a single file with e.g. `pytest eval/test_verifier_unit.py -v`.

## Cost note

Every `*_unit.py` file, plus `test_ingestion_integration.py` and `test_rag_integration.py`, are free
and near-instant (no LLM/FHIR/Voyage calls -- Tier 1 above). Every other file (`test_boundary_conditions.py`,
`test_clinical_safety.py`, `test_use_case_edge_cases.py`, `test_regression_known_bugs.py`,
`test_golden_set.py`) makes real Anthropic API calls and real local FHIR requests through the full
LangGraph pipeline (deliberately -- this is an eval of the deployed agent's actual behavior, not a
mocked harness), so cost and runtime scale with the number of these tests.

## The eval gate (Tier 2, Stage 4)

```
python eval/run_eval_gate.py                    # run the 50-case golden set, compare to baseline_results.json
python eval/run_eval_gate.py --update-baseline   # (re)write the baseline from this run
```

`../scripts/install-hooks.sh` (run once from the repo root) installs this as a `pre-push` git hook,
so it runs automatically before every push and blocks (non-zero exit) on a per-category regression
>5% or a drop below the 80% floor. Golden-set cases and the checker logic live in
`golden_set.json` / `golden_checks.py` / `test_golden_set.py` -- see W2_ARCHITECTURE.md Section 6 for
the full design (boolean rubrics, categories, why this tier uses real APIs).

## Test files and what they guard

| File | Guards against |
|---|---|
| `test_verifier_unit.py` | The core verification invariant (claims only pass if their cited source was actually fetched this turn) at the unit level -- hallucinated citations, malformed sources, unconfirmed no_data claims, resource_type/id cross-matching, plus the Stage 3 unified-citation extension (document/guideline sources, empty-evidence no_data). |
| `test_proxy_roundtrip_unit.py` | A real production bug: the OpenEMR-side proxy's PHP `json_decode(..., true)` can't distinguish an empty JSON object `{}` from an empty array `[]`, so a no-argument tool call's `input: {}` comes back as `input: []` after round-tripping through conversation history -- breaking every turn after the first no-argument tool call with `tool_use.input: Input should be an object`. Guards the repair function that fixes this before replay. |
| `test_schemas_unit.py` | Stage 1 extraction schemas (`Citation`, `LabPdfExtraction`, `IntakeFormExtraction`, etc.) -- acceptance/rejection boundaries, no bare uncited field. |
| `test_rag_unit.py` | Stage 2 corpus loading/chunking/frontmatter parsing, tokenization, reciprocal rank fusion -- the offline-testable half of `rag.py`. |
| `test_rag_integration.py` | Stage 2's retrieval *orchestration* (BM25 -> dense -> RRF -> rerank, in order; the MIN_RELEVANCE_SCORE filter; the disk embedding cache) with Voyage stubbed out -- wiring, not embedding/rerank quality. |
| `test_supervisor_unit.py` | Stage 3 supervisor routing decisions, the handoff-count failure-mode guard, fact flattening, and the message-injection shape that avoids breaking the Anthropic API's role-alternation rule. |
| `test_ingestion_integration.py` | Stage 1's upload -> extract -> validate -> persist wiring with Anthropic/OpenEMR HTTP calls stubbed out -- including the `patient_id` (int pid) vs `patient_uuid` (FHIR uuid) mixup class of bug and schema-validation-failure handling. |
| `test_boundary_conditions.py` | Empty message input; nonexistent patient_id (documents a known gap -- OpenEMR's FHIR API can't distinguish a nonexistent patient from a real empty chart); invalid bearer token degrading to `tool_failures` instead of crashing. |
| `test_clinical_safety.py` | The single highest-stakes case: Robert Chen's unflagged sulfa-allergy/sulfa-antibiotic conflict must be surfaced by cross-referencing structured data, cite a real source even though it's safety-critical, and deprioritize (not blend in) his unrelated knee history for a cardiac complaint. |
| `test_use_case_edge_cases.py` | James Whitfield (truly empty chart -- every non-demographic claim must be `no_data`) vs. Dorothy Simmons (an explicit verified-absent NKDA entry -- must NOT be reported as `no_data`, since that would erase the fact someone actually checked). |
| `test_regression_known_bugs.py` | Two documented upstream OpenEMR bugs: `FhirAllergyIntoleranceService.php`'s scalar/list `reaction` bug (Maria's allergy), and DocumentReference-vs-pnotes table mismatch (Maria's note). Guards graceful degradation, not that the bugs are fixed. |
| `test_golden_set.py` | The Stage 4 gate: 50 cases across extraction, evidence retrieval, citations, refusals, and missing-data, each checked against 5 boolean rubrics (`golden_checks.py`). This is what `run_eval_gate.py` runs. |

## Results

**Current: 132 tests** -- 71 offline/Tier-1 (`*_unit.py` + `test_ingestion_integration.py` +
`test_rag_integration.py`) always passing, plus 11 existing Week 1 live-API tests and the 50-case
Tier-2 golden set (both real APIs, 61 total). The
previous "22/22" figure only counted Week 1's original suite before Week 2's ingestion/RAG/supervisor
additions -- reconciled here as of the Stage 4 build (Gauntlet/Week 2/STATUS.md has the full,
up-to-date breakdown per stage).

Real issues found and fixed while building/using this agent (not a happy-path-only result) -- Week 1's
original three, plus five more found via Stage 4's live golden-set run:

1. **Real crash bug fixed** (Week 1): an empty `message` was passed straight through to the Anthropic
   API, which rejects empty content and raised an unhandled 400 mid-turn. Fixed with a pydantic
   validator on `ChatRequest` in `app/main.py`.
2. **Real gap fixed** (Week 1): the system prompt had no instruction to rank chronic-condition
   relevance against a presenting complaint (UC-5). Added an explicit relevance-ranking rule to
   `SYSTEM_PROMPT` in `app/graph.py`.
3. **Real multi-turn crash bug fixed** (Week 1): `interface/modules/copilot/proxy.php`'s PHP JSON
   round trip silently turned a no-argument tool call's `input: {}` into `input: []`. Fixed in
   `app/graph.py`'s `_repair_round_tripped_tool_use_input`.
4. **Real bug fixed** (Stage 4): the model wrote `resource_type="Medication"` (a real but wrong FHIR
   name) instead of the exact `"MedicationRequest"` string `verify_claims` checks against, silently
   stripping an otherwise-correct "no medications on file" claim. Fixed by constraining
   `PROVIDE_ANSWER_TOOL`'s `resource_type` field to an explicit enum (`NO_DATA_RESOURCE_TYPES` in
   `app/graph.py`) instead of relying on prose alone.
5. **Real bug fixed** (Stage 4): `tools.py`'s `get_allergies` read only `code.coding[0].display`,
   which OpenEMR's FHIR mapping sets to a generic `"Unknown"` data-absent-reason placeholder for
   *every* free-text allergy title (not just NKDA-style entries) -- the real text only exists in the
   resource's FHIR narrative (`text.div`). Fixed with a narrative-text fallback (`_narrative_text` in
   `app/tools.py`), applied only when the coded value resolves to nothing real.
6. **Real bug fixed** (Stage 4, OpenEMR core): `FhirObservationLaboratoryService.php` discarded a
   lab result's human-readable test name entirely whenever no LOINC code was available -- true for
   every Week 2 Claude-vision-extracted lab result, which reads a plain-text name off a document
   rather than looking up a coded identifier. Fixed to build a text-only `CodeableConcept` (valid
   FHIR) in that case instead of falling through to `NullFlavorUnknown`.
7. **Real environment-contamination bug caught and cleaned up** (Stage 4): an earlier Stage 3 live
   test had uploaded a real lab PDF into James Whitfield's chart (the dedicated "empty chart" test
   patient), silently breaking both a golden-set case and Week 1's own `test_use_case_edge_cases.py`
   assumption that his chart is empty. Cleaned up via scoped, SELECT-verified `DELETE`s on the exact
   rows created (`procedure_order`/`procedure_report`/`procedure_result`/`documents` by id) -- a
   reminder that shared seed-patient state needs the same discipline as production data.
8. **Test-design fixes, not system bugs** (Stage 4): several golden-set cases initially failed
   because of case-design flaws, not real regressions -- an off-corpus RAG control query that
   happened to score just over the relevance threshold via incidental lexical overlap (picked a
   clearer control query instead of loosening the threshold), refusal-keyword lists too narrow to
   catch equally-valid alternate phrasing ("No X on file" vs. "X not on file"), and one case
   (`REF-03`) that was wrongly expecting a refusal when the system's actual (correct) behavior was to
   ground its answer in real retrieved guideline evidence instead of either refusing or fabricating.

**Known, documented, unresolved gap** (`test_nonexistent_patient_id_does_not_crash`, `MSD-10`):
OpenEMR's FHIR search API returns an empty-but-200 Bundle for a `patient_id` that doesn't exist at
all -- indistinguishable at the tool layer from a real patient with a genuinely empty chart. Both
tests only assert the turn doesn't crash; neither asserts (because it isn't true yet) that the agent
can tell these two cases apart.
