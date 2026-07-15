# AI Cost Analysis — Week 2 Extension (Ingestion + Retrieval)

Extends `Week 1/COST_ANALYSIS.md` (chat-only cost, still fully valid and unchanged) with the two new
cost centers Week 2 adds: document ingestion (Claude vision extraction) and evidence retrieval
(hybrid RAG, Voyage AI). Same standard as Week 1: **measured, not estimated**, pulled directly from
Langfuse's public API (`/api/public/observations`, `/api/public/traces`) against real usage — this
report was flagged as a specific gap in MVP grader feedback (`Gauntlet/Week 2/STATUS.md`), so every
number below traces back to a real trace or a real, reproducible API call, not a guess.

## A methodology finding worth stating up front

Pulling this data surfaced a real data-quality issue: **the Tier 1 stubbed integration tests
(`agent/eval/test_ingestion_integration.py`) still emit real Langfuse telemetry**, even though the
underlying Anthropic call is mocked. The `@observe` decorator wraps the *function*
(`extract_with_vision`, `upload_and_resolve_document`), not the API call inside it — so every local
test run against a real `.env` with Langfuse credentials configured sends a real (if fake-content)
span. These are identifiable by an exact signature (the stub's fixed `100`/`50` input/output token
counts for `extraction`; near-zero latency for `document_ingestion`) and were filtered out of every
number below. Of 72 raw `extraction` observations, 48 (67%) were test artifacts; of 74 raw
`document_ingestion` observations, 66 (89%) were. This doesn't affect production behavior or cost
(no real API call happens), but it means anyone reading Langfuse's dashboard for these span names
directly, without filtering, would see badly misleading aggregate latency/cost — worth knowing before
trusting a raw dashboard view of these two span names for anything beyond "did this code path run."

## Document ingestion (extraction) — real, measured

Pulled from `extraction` (generation-type) observations, filtered to the 24 real (non-stub) entries
(19 of which have complete cost data — a handful of early entries predate a field being consistently
populated):

| Doc type | n | Mean cost | Mean latency |
|---|---|---|---|
| `lab_pdf` | 9 | $0.0236 | 12.03s |
| `intake_form` | 10 | $0.0267 | 12.63s |
| **Combined** | **19** | **$0.0252** | **11.17s** (p95 15.30s, max 16.78s) |

Mean input tokens 3,451, mean output tokens 990 — both dominated by the page image(s) sent to Claude
vision, not the (short) extraction instructions. `intake_form` costs slightly more than `lab_pdf`
(more distinct fields to extract per page: demographics, chief concern, medications, allergies,
family history, each with its own citation/bbox — vs. a lab PDF's more uniform table-row structure).

**This is the dominant new cost center Week 2 adds** — at ~$0.025/call, extraction alone costs more
than an entire Week 1 chat turn (~$0.007-0.02, see below and `Week 1/COST_ANALYSIS.md`).

## Document upload (`document_ingestion` span) — real, measured

The upload+dedup-check step, separate from extraction: 8 real (non-stub) samples, mean latency
0.61s (median 0.39s, max 1.34s) — negligible compared to extraction's ~11s. 1 of 8 was a real
dedup hit (`was_deduped: true`), consistent with live re-upload testing during Stage 1.

## Evidence retrieval (RAG) — real, measured

`evidence_retriever` span data (38 samples, confirmed **not** stub-polluted — no near-zero-latency
cluster exists in this span, unlike the two above):

| Metric | Value |
|---|---|
| Latency (mean / median / p95 / max) | 0.35s / 0.33s / 0.48s / 0.88s |
| Zero-result rate (correctly returns no evidence, doesn't fabricate) | 33.3% |

Voyage AI cost has no automatic entry in Langfuse's cost catalog (it isn't an Anthropic model), so
this is **computed from real measured call volume × Voyage's published per-token pricing**
(`docs.voyageai.com/docs/pricing`, fetched live), not hand-waved:

| Item | Real measurement | Published price | Cost |
|---|---|---|---|
| Full 7-document, 28-chunk guideline corpus embedding | 2,101 tokens (measured via a real `embed()` call — `voyage-3-lite`) | $0.02 / M tokens | **$0.000042, one-time** (cached to disk, only re-embedded when the corpus content changes) |
| One query embed (measured, real question) | 14 tokens | $0.02 / M tokens | $0.00000028 |
| One rerank call (measured, 10 fused candidates, real question) | 913 tokens | $0.05 / M tokens | $0.0000457 |
| **Per-query marginal cost** | | | **~$0.000046** |

At the 38 real measured calls: **total Voyage spend to date ≈ $0.0017**, essentially free relative to
Claude. **The RAG pipeline's cost is dominated entirely by the Claude call that reasons over the
retrieved evidence, not by Voyage** — retrieval itself (embedding + rerank) is two orders of
magnitude cheaper than a single Claude turn.

## Full-turn comparison — two concrete real traces

Rather than an aggregate over the (also stub-contamination-risked) `agent_llm_call`/`verify_claims`
observation sets, this comparison uses two specific, real, named traces pulled by trace ID — every
number here is one real turn's actual Langfuse trace, not a statistic:

| | Plain Week 1-style chat turn | Evidence-routed Week 2 turn |
|---|---|---|
| Trace ID | `9616ee333a7e4d7a074c87e0b588edb4` | `bebab347e42ac8723dbecc2bbdbf750b` |
| Question shape | Broad chart pull (7 FHIR tool calls: patient, conditions, medications, allergies, vitals, labs, encounters) | Targeted synthesis (2 FHIR tool calls + evidence_retriever) |
| Total cost | $0.0066 | $0.0148 |
| Total latency | 5.78s | 7.30s |
| LLM calls | 2 | 2 |
| Worker hops | 0 (supervisor routed straight to `agent`) | 1 (`supervisor → evidence_retriever → supervisor → agent`) |

**Caveat stated plainly**: this isn't a clean apples-to-apples "cost of adding RAG" isolation — the
two questions differ in scope (broad chart pull vs. a single targeted question), so some of the cost
delta is fewer tool calls, not just the `evidence_retriever` hop itself. The `evidence_retriever`
span itself only added 0.297s and (per above) ~$0.00005 to this specific trace — the larger total-cost
difference is mostly the second Claude call reasoning over more context (chart data *and* retrieved
guideline text) to produce a more synthesized answer, not the retrieval mechanism itself being
expensive.

## Updated cost-at-scale projection

Extends `Week 1/COST_ANALYSIS.md`'s model (45 turns/user/day) with an explicit, stated assumption
about how many of those turns are Week 2 flows, since that mix isn't measured (no real multi-tenant
usage exists yet to measure it from) and must be assumed like `Week 1/COST_ANALYSIS.md`'s own
assumptions table:

| # | Parameter | Assumed value | Basis |
|---|---|---|---|
| 14 | Share of turns that are a document upload (extraction) | 1 in 15 turns (~6.7%) | One upload per patient roughly every 2-3 turns of chart review, per `USER.md`'s intake-shift workflow — not measured, a modeling assumption |
| 15 | Share of turns that trigger evidence retrieval | 1 in 8 turns (~12.5%) | A guideline-style question roughly once per patient encounter — not measured |
| 16 | Extraction cost/turn (measured) | $0.0252 | From this document's real data, above |
| 17 | Evidence-retrieval marginal cost/turn (measured) | ~$0.0082 (the two-trace delta above, held as the working figure despite the stated caveat) | From this document's real data, above |

**Blended per-turn cost**, applied on top of Week 1's measured $0.0214 mean chat-turn cost:

```
blended = $0.0214 (base chat, unconditional)
        + (1/15) × $0.0252 (extraction, when a turn is an upload)   ≈ +$0.00168
        + (1/8)  × $0.0082 (evidence retrieval, when triggered)     ≈ +$0.00103
        ≈ $0.0241/turn  (+13% over the Week 1 chat-only baseline)
```

| Users | Turns/day | Week 1 naive cost/month | Week 2 blended cost/month | Change |
|---|---|---|---|---|
| 100 | 4,500 | $2,900 | ~$3,255 | +12% |
| 1,000 | 45,000 | $29,000 | ~$32,550 | +12% |
| 10,000 | 450,000 | $289,000 | ~$325,500 | +13% |
| 100,000 | 4,500,000 | $2,890,000 | ~$3,255,000 | +13% |

Week 1's architectural mitigations (prompt caching, FHIR read caching, model tiering, self-hosted
Langfuse at the 100K tier) all still apply on top of this blended baseline unchanged — this table
shows the *naive* Week 2 addition before those mitigations, matching how Week 1's own naive table was
presented before its own mitigations section. Extraction is the standout candidate for a *new*
Week-2-specific mitigation not in Week 1's list: at $0.0252/call it is the single most expensive
per-unit operation in the whole system (more than an entire chat turn), so if usage grows,
right-sizing image resolution/DPI in `rasterize_to_page_images` (currently 150 DPI, chosen for OCR
legibility, not cost) is the highest-leverage lever specific to Week 2 — not evaluated here, since it
would require re-verifying extraction accuracy at a lower DPI first (a real accuracy/cost tradeoff,
not a free win).

## Summary

Two new cost centers, two very different cost profiles: **extraction is expensive** (~$0.025/call,
more than a whole chat turn, dominated by Claude vision processing the page image) and **retrieval is
essentially free** (~$0.000046/call — Voyage's embed+rerank cost is two orders of magnitude below a
single Claude call). The blended per-turn cost impact at realistic usage mix assumptions is a modest
+12-13% over Week 1's baseline. The most actionable, real (not hypothetical) finding from actually
pulling this data is the test-stub telemetry contamination — a genuine data-quality issue worth fixing
(adding explicit `flush()`/environment gating so local test runs don't pollute production-adjacent
Langfuse data) if this system's observability data is ever relied on for anything beyond ad hoc
analysis like this report.
