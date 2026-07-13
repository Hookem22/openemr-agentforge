# Model Trade-off: Sonnet vs. Haiku

Evaluates swapping `copilot-agent`'s LLM from `claude-sonnet-4-6` (current default,
`agent/app/config.py`) to `claude-haiku-4-5-20251001` — no code change required, since the model is
already env-var driven (`ANTHROPIC_MODEL` in `.env`). Two questions: does accuracy regress, and what's
the real cost/latency trade-off (as opposed to the ~1/10th-cost guess used as a placeholder in
`COST_ANALYSIS.md` assumption #9).

## Method

- Same eval suite, same seeded patients, same prompts — only `ANTHROPIC_MODEL` changed.
- Ran `pytest -v` from `agent/` with `ANTHROPIC_MODEL=claude-haiku-4-5-20251001` and a freshly-minted
  local `DEV_BEARER_TOKEN` (the existing one had expired).
- Cost/latency/token data pulled from Langfuse's public API (`/api/public/traces` and
  `/api/public/observations`, paginated, 100/page, with 429 retry-backoff honoring
  `retryAfterSeconds`) — same source used for `COST_ANALYSIS.md`'s "actual dev spend" numbers, so this
  is real measured data, not estimated.
- Sonnet baseline is every Sonnet trace recorded to date (1,066 traces — a much larger, more durable
  sample than `COST_ANALYSIS.md`'s original 123-trace/$2.63 figure, since local dev + eval runs +
  both `LOADTEST.md` sweeps have since added volume). Haiku sample is the 10 traces from this one eval
  run (22 generation calls, ~2/turn, matching Sonnet's pattern).

## Accuracy result: no regression

**27/27 eval tests passed with Haiku** — identical to the existing Sonnet baseline (27/27, per
`eval/README.md`/prior runs). Critically, this includes every safety-critical and edge-case test, not
just the easy majority:

| Test | What it guards | Haiku result |
|---|---|---|
| `test_sulfa_allergy_conflict_is_flagged_when_asked_about_medications` | Cross-referencing Robert Chen's unflagged sulfa-allergy/sulfa-antibiotic conflict | PASS |
| `test_sulfa_conflict_claim_cites_a_real_source` | Even a safety-critical claim must still be grounded, not asserted on "sounds right" | PASS |
| `test_unrelated_chronic_condition_is_deprioritized_for_cardiac_complaint` | Not blending an unrelated chronic condition into a cardiac presentation | PASS |
| `test_empty_chart_produces_only_no_data_claims` | James's truly empty chart — no fabricated clinical summary | PASS |
| `test_verified_absent_allergy_is_not_reported_as_no_data` | Dorothy's explicit verified-absent NKDA must not collapse into "unrecorded" | PASS |
| `test_maria_allergy_lookup_does_not_crash_the_turn` / `..._notes_lookup...` | Graceful degradation on two known upstream OpenEMR bugs | PASS |
| `test_nonexistent_patient_id_does_not_crash` / `test_invalid_bearer_token_degrades_to_tool_failure_not_crash` | Boundary conditions don't crash the turn | PASS |

**Caveat on what this does and doesn't prove:** the suite's assertions are keyword/structure checks
(e.g. "does the response mention sulfa/allergy/conflict", "does every claim have a source"), not a full
semantic grade of answer quality. A model could pass every test here while still being less articulate,
less complete, or subtly worse in ways the suite doesn't check for. 27/27 is strong evidence Haiku
doesn't regress on the *specific failure modes this project has identified as safety-critical* — it is
not proof of equivalent quality on the open-ended "explain this patient" case in general. Manual
side-by-side review of a few real transcripts (not just pass/fail) would be the natural next step before
fully committing to a switch.

## Cost result: ~3x cheaper, not ~10x

| Metric | Sonnet (n=1,066 traces) | Haiku (n=10 traces) | Ratio (Haiku/Sonnet) |
|---|---|---|---|
| Mean cost/turn | $0.0197 | $0.00617 | **0.31x** (~3.2x cheaper) |
| Median cost/turn | $0.01825 | $0.00581 | 0.32x |
| Mean input tokens/call | 1,782 | 1,764 | ~1.0x (same prompt/tools) |
| Mean output tokens/call | 269 | 208 | 0.77x (Haiku's answers run ~23% shorter) |
| LLM calls/turn | ~2.0 | ~2.0 | same tool-use-loop shape |

**This corrects `COST_ANALYSIS.md` assumption #9**, which guessed "~1/10th the cost of Sonnet" as a
placeholder for the model-tiering projection at the 100K-user tier. The real measured ratio is **~0.31x
(roughly 1/3, not 1/10)** — driven mostly by similar input-token counts (same system prompt + tool
schemas dominate input either way) and only a modest reduction in output length, not a 10x per-token
price gap alone. `COST_ANALYSIS.md` has been updated to use this real number (see below).

## Latency result: ~2.3x faster, with a caveat

| Metric | Sonnet (n=1,066) | Haiku (n=10) |
|---|---|---|
| Mean trace latency | 11.50s | 5.10s |
| Median trace latency | 9.33s | 4.80s |

Haiku's mean latency is ~44% of Sonnet's on this data. **Caveat: not a clean apples-to-apples
comparison.** The Sonnet sample spans everything recorded to date, including both `LOADTEST.md` sweeps
(1-200 concurrent users), where per-request latency is inflated by queueing behind a single `uvicorn`
worker (documented in `LOADTEST.md`'s cliff-finding sweep). The Haiku sample is 10 sequential,
uncontended local pytest calls with no concurrency at all. Some of Haiku's latency edge is real
(smaller/faster model), but part of the gap is comparing a contended, high-concurrency population
against an uncontended one. A fair re-test would run the same `agent/loadtest/loadtest.py` sweep against
a Haiku-configured deployment. Not done here — out of scope for this comparison, flagging it as the
correct next step if the latency number needs to be defensible on its own.

## Sample-size caveat

Haiku's n=10 traces (from one eval run) is a small sample next to Sonnet's n=1,066. The eval-suite
accuracy result (27/27, deterministic pass/fail per test) doesn't suffer from this — it's a full run of
every test, not a statistical sample. The cost/latency numbers, though, are a thinner slice and could
shift with more Haiku usage; treat the 0.31x cost ratio as a good working estimate, not a precise
long-run constant.

## Recommendation

No accuracy regression was found on this project's own safety-critical test suite, and the real cost
(~1/3) and latency (~2x, with the caveat above) advantages are substantial. Given the stakes of a
clinical tool, the recommendation is **not** an unconditional full switch based on this suite alone, but:

1. Use Haiku's real 0.31x cost ratio (not the old 1/10th guess) in `COST_ANALYSIS.md`'s model-tiering
   projections going forward — done.
2. Before a production default switch, do a manual side-by-side read of full response transcripts for
   the six use cases in `USER.md` (not just the keyword-assertion pass/fail), since the eval suite's
   checks are necessary but not sufficient evidence of equivalent answer quality.
3. The tiering idea already in `COST_ANALYSIS.md` (route simple single-fact lookups to a cheaper model,
   keep Sonnet for multi-source reasoning like UC-2/UC-3) remains a reasonable middle ground independent
   of whether a full switch is eventually adopted.

## Reproducing

```bash
cd agent
# in .env: ANTHROPIC_MODEL=claude-haiku-4-5-20251001
source venv/bin/activate && pytest -v
```
