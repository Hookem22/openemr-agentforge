# Eval Suite

Proves the Clinical Co-Pilot agent works, per the assignment's evaluation requirement: covers
boundary conditions, safety-critical invariants, and known regression risks -- not a happy-path-only
demo. Every test's docstring states the specific failure mode it guards against.

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

`test_verifier_unit.py` is pure unit tests (no LLM/FHIR calls) -- free and near-instant. Every other
file makes real Anthropic API calls and real local FHIR requests through the full LangGraph
pipeline (deliberately -- this is an eval of the deployed agent's actual behavior, not a mocked
harness), so cost and runtime scale with the number of integration tests. Run
`test_verifier_unit.py` alone for a free, fast smoke check.

## Test files and what they guard

| File | Guards against |
|---|---|
| `test_verifier_unit.py` | The core verification invariant (claims only pass if their cited source was actually fetched this turn) at the unit level -- hallucinated citations, malformed sources, unconfirmed no_data claims, resource_type/id cross-matching. |
| `test_boundary_conditions.py` | Empty message input; nonexistent patient_id (documents a known gap -- OpenEMR's FHIR API can't distinguish a nonexistent patient from a real empty chart); invalid bearer token degrading to `tool_failures` instead of crashing. |
| `test_clinical_safety.py` | The single highest-stakes case: Robert Chen's unflagged sulfa-allergy/sulfa-antibiotic conflict must be surfaced by cross-referencing structured data, cite a real source even though it's safety-critical, and deprioritize (not blend in) his unrelated knee history for a cardiac complaint. |
| `test_use_case_edge_cases.py` | James Whitfield (truly empty chart -- every non-demographic claim must be `no_data`) vs. Dorothy Simmons (an explicit verified-absent NKDA entry -- must NOT be reported as `no_data`, since that would erase the fact someone actually checked). |
| `test_regression_known_bugs.py` | Two documented upstream OpenEMR bugs: `FhirAllergyIntoleranceService.php`'s scalar/list `reaction` bug (Maria's allergy), and DocumentReference-vs-pnotes table mismatch (Maria's note). Guards graceful degradation, not that the bugs are fixed. |

## Results

Last run: **19/19 passed** (`pytest -v`, local OpenEMR + live Anthropic API, ~110s wall time).

Two real issues were found and fixed by this suite while it was being built (not a happy-path-only
result):
1. **Real crash bug fixed**: an empty `message` was passed straight through to the Anthropic API,
   which rejects empty content and raised an unhandled 400 mid-turn. Fixed with a pydantic
   validator on `ChatRequest` in `app/main.py` (system-boundary validation -> clean 422 instead of
   a crash).
2. **Real gap fixed**: the system prompt had no instruction to rank chronic-condition relevance
   against a presenting complaint (UC-5) -- Robert Chen's unrelated knee osteoarthritis was
   sometimes listed flatly alongside his cardiac findings instead of being deprioritized. Added an
   explicit relevance-ranking rule to `SYSTEM_PROMPT` in `app/graph.py`.

**Known, documented, unresolved gap** (`test_nonexistent_patient_id_does_not_crash`): OpenEMR's FHIR
search API returns an empty-but-200 Bundle for a `patient_id` that doesn't exist at all --
indistinguishable at the tool layer from a real patient with a genuinely empty chart. The test only
asserts the turn doesn't crash; it does not assert (because it isn't true yet) that the agent can
tell these two cases apart.
