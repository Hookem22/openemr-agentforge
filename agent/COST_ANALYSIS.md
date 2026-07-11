# AI Cost Analysis

## Actual dev spend (measured, not estimated)

Pulled directly from Langfuse's per-trace cost tracking via its public API (`/api/public/traces`), which has
been wired into every chat turn since early development (`agent/app/graph.py`'s `@observe` instrumentation):

| Metric | Value |
|---|---|
| Total chat-turn traces recorded | 123 |
| Total Anthropic spend across all traces | **$2.63** |
| Mean cost per turn | $0.0214 |
| Median cost per turn | $0.0184 |
| Model | `claude-sonnet-4-6` |
| Mean input tokens per LLM call | ~1,976 |
| Mean output tokens per LLM call | ~519 |
| LLM calls per turn (avg) | ~2.0 (matches the tool-use loop: one call that requests tools, one that calls `provide_answer`) |

This $2.63 figure covers local development testing, the `agent/eval` suite runs, and the load test in
`LOADTEST.md` (123 traces total) — i.e. it's the real, complete cost of building and validating this agent so
far, not a cherry-picked sample. It is a **lower bound** on total dev spend: any manual testing done before
Langfuse tracing was wired in, or direct Anthropic API calls outside the `/chat` code path, wouldn't appear
here. Cross-check against the Anthropic Console's usage dashboard for the exact account-wide total if a
precise figure is needed.

## Projected cost at scale

**Assumption (stated explicitly, not hidden in the math):** one "user" = one ED resident running one
overnight shift's worth of usage per day. Per `USER.md`, a shift involves a steady stream of patients; assuming
~15 patients/shift and ~3 agent turns/patient (covering UC-1 through UC-6's prompts — patient snapshot,
what's changed, meds/allergies, labs) gives **~45 turns/user/day**. At the measured mean cost of $0.0214/turn:

| Users | Turns/day | Naive cost/day (turns x $0.0214) | Naive cost/month |
|---|---|---|---|
| 100 | 4,500 | $96 | ~$2,900 |
| 1,000 | 45,000 | $963 | ~$29,000 |
| 10,000 | 450,000 | $9,630 | ~$289,000 |
| 100,000 | 4,500,000 | $96,300 | ~$2,890,000 |

**This naive linear extrapolation is exactly what the assignment warns against.** It assumes the architecture
and per-turn token cost stay fixed as usage scales, which is false in both directions — some costs will fall
faster than linear, others require real infrastructure spend not captured by "cost per token x n" at all.

### Architectural changes needed at each tier — with quantified estimates

Each mitigation below is stated as an explicit, named assumption first, so the number can be checked and
changed independently of the prose. Every "adjusted cost" figure is *cumulative*: a tier's adjusted cost
applies its own new mitigation on top of every mitigation already adopted at a lower tier (this models a real
system evolving as it scales, not four unrelated architectures).

#### Assumptions used below (change any of these and recompute the tables that follow)

| # | Parameter | Assumed value | How to change it |
|---|---|---|---|
| 1 | Turns/user/day | 45 (~15 patients/shift x 3 turns/patient) | Unchanged from the naive model above — see `USER.md` for the shift/patient-count basis |
| 2 | Anthropic Sonnet per-token price | $3 / M input tokens, $15 / M output tokens | Anthropic's published pricing at time of writing; re-check `console.anthropic.com` if pricing has changed since |
| 3 | Cacheable static block size | ~1,200 of the ~1,976 mean input tokens/call | Rough token count of `SYSTEM_PROMPT` + the 9 tool JSON schemas in `agent/app/graph.py` — not separately measured; re-count if the prompt/tool set grows materially |
| 4 | Prompt-cache write cost | 1.25x normal input-token price | Anthropic's published prompt-caching pricing (5-minute-TTL cache writes) |
| 5 | Prompt-cache hit cost | 0.1x normal input-token price | Anthropic's published prompt-caching pricing (a 90% discount on cache reads) |
| 6 | → **derived**: per-turn cost reduction from caching | **~9%** | Computed from #2-#5 against the measured ~2-calls/turn pattern (math below) — change #3-#5 and this recomputes |
| 7 | FHIR read-cache hit rate (10K+ tier) | ~20% of turns hit a same-shift cache | Estimate, not measured — no cache exists yet; modeled conservatively as a 5% cost reduction (see note below) |
| 8 | Model-tiering eligible turns (100K tier) | ~30% of turns are single-fact UC-1 lookups | Estimate from `USER.md`'s use-case mix; adjust if the real "who is this patient"-type share differs |
| 9 | Cheaper-model cost ratio | **~0.31x the cost of `claude-sonnet-4-6`** (measured, not estimated) | `claude-haiku-4-5-20251001` measured directly: 27/27 eval-suite tests pass (no accuracy regression on this project's safety-critical checks), mean cost/turn $0.00617 vs. Sonnet's $0.0197 across 1,066 real traces. See `MODEL_TRADEOFF.md` for the full comparison, caveats, and reproduction steps. Supersedes an earlier ~1/10th placeholder guess. |
| 10 | Self-hosted Langfuse infra (100K tier) | ~$800/month | Placeholder compute estimate for the 6-service stack in `LANGFUSE_SELFHOST.md` (Postgres, ClickHouse, Redis, S3-compatible storage, web, worker) — not a quote |
| 11 | Multi-tenant routing/ops overhead (100K tier) | ~$2,000/month flat | Placeholder for a tenancy/routing layer's compute cost only — excludes per-tenant legal/BAA administrative cost, which isn't a dollar-per-token figure |
| 12 | Extra replicas for latency (100-user tier) | 2-3 replicas @ ~$10-15/month each | Rough Railway compute pricing for a small `uvicorn` worker process; not a formal quote |
| 13 | Autoscaling compute (10K-user tier) | ~$120/month | Rough estimate for sustained horizontal scaling beyond the fixed 100-user-tier replica count |

**How the ~9% caching reduction (#6) was derived:** per turn (2 LLM calls, ~1,976 input tokens each): the
first call pays the cache-write price on the static block (#3 x #4) plus the normal price on the rest; the
second call pays the cache-hit price on the static block (#3 x #5) plus the normal price on the rest. Doing
this arithmetic and comparing to the uncached baseline gives roughly a 20% reduction in *input-token* cost per
turn. Input tokens are only part of total per-turn cost, though — output tokens (#2's $15/M rate, not
cacheable) make up the rest. Applying the 20% input-only reduction against the input/output cost split implied
by #2 and the measured mean token counts yields an overall **~9% reduction in total per-turn cost**. Changing
#3, #4, or #5 changes this number; the calculation isn't hardcoded elsewhere in this document.

**Note on FHIR read caching (#7):** this mitigation's main effect is on *latency and throughput*
(`LOADTEST.md` already shows the current deployment struggling well before 50 concurrent long-running
requests), not dollar cost — OpenEMR's FHIR API isn't billed per call the way Anthropic tokens are. The 5%
cost figure used below is a conservative, secondary effect: fewer duplicate lookups means a smaller share of
turns need the full 2-call tool-use loop. Treat this as the softest, most speculative number in this document.

#### 100 users (single hospital department pilot)

Current architecture is sufficient largely as-is. The one fix already identified by load testing
(`LOADTEST.md`): the single-`uvicorn`-worker `copilot-agent` process saturates well before 50 concurrent
long-running turns, so this tier needs at least 2-3 workers/replicas behind Railway's load balancer to keep
latency stable during shift-change bursts — a reliability fix, not a cost-saving one. No LLM-cost mitigation
is prioritized yet at this volume (prompt caching's absolute savings here would be small, per assumption #6
applied to $2,900/month — about $260/month — not worth the engineering effort before 1,000 users).

**Adjusted cost:** $2,900/month (naive) + ~$30/month (assumption #12, 2-3 replicas) ≈ **$2,930/month** —
slightly *higher* than the naive figure, since this tier's only architectural change is a reliability cost
addition, not a reduction. That's an intentional, honest result of the model: not every tier makes cost go
down.

#### 1,000 users (multi-department/small hospital)

The highest-leverage cost change here is **Anthropic prompt caching** (assumptions #3-#6): the system prompt
and 9 tool schemas are identical on every call, and this is the first tier where the absolute dollar savings
(~9% of $29,000/month, roughly $2,600/month) clearly justify the code change (mark the system+tools block as
cacheable in the Anthropic SDK call).

Note on compliance framing (corrected from an earlier draft of this document): this tier does **not** require
moving to a self-hosted Langfuse instance for compliance reasons. `PHI_AUDIT.md` documents that PHI redaction
(Option B) was already implemented and verified live against Langfuse Cloud's public API — no raw patient data
reaches Langfuse Cloud today regardless of scale. Self-hosting (`LANGFUSE_SELFHOST.md`'s Option A) remains a
deferred, optional infra project, not a requirement at this tier; it isn't included as a cost here.

**Adjusted cost:** $29,000/month x (1 - 0.09) ≈ **$26,400/month** (~9% below naive).

#### 10,000 users (hospital system / multi-site)

At this volume a meaningful share of turns are likely near-duplicate lookups (multiple clinicians pulling the
same patient's snapshot within a shift). A short-TTL cache in front of the FHIR read tools (not the LLM call
itself, to avoid any risk of stale clinical data reaching a claim) reduces tool-call volume and, per assumption
#7, a modest share of per-turn LLM cost. This tier also needs real horizontal autoscaling (assumption #13) and
connection pooling to OpenEMR's FHIR API, since 10,000 users maps to roughly the concurrency load already shown
to cause edge-level 502s at just 50 simultaneous long-running requests (`LOADTEST.md`) — again, primarily a
reliability fix rather than a cost one.

**Adjusted cost:** $289,000/month x (1 - 0.09 caching) x (1 - 0.05 FHIR cache) + ~$120/month (autoscaling) ≈
**$250,000/month** (~14% below naive).

#### 100,000 users

This scale does not correspond to "one hospital's OpenEMR instance" anymore — a 500-bed hospital has on the
order of hundreds to low thousands of clinical staff, not 100K (directly relevant to the "scaling to a 500-bed
hospital / 300 concurrent users" interview-prep question). Reaching 100K users necessarily means
**multi-tenant deployment across many hospital systems**, which changes the architecture qualitatively, not
just its capacity: per-tenant data isolation and per-tenant BAAs (each hospital system is its own covered
entity), a routing/tenancy layer in front of `copilot-agent` (assumption #11), and a tiered model strategy
(assumptions #8-#9) — routing simple single-fact lookups (UC-1) to a cheaper model, escalating to the full
model only for turns needing multi-source reasoning (UC-2 "what changed," UC-3 interaction flagging). At this
scale, Langfuse Cloud's own usage-based pricing on ~4.5M turns/month of trace volume would likely exceed the
cost of self-hosting (assumption #10), so self-hosted Langfuse is included here as a **cost-driven** choice,
not a compliance-driven one (compliance was already resolved at any scale by `PHI_AUDIT.md`'s redaction).

**Adjusted cost:** $2,890,000/month x (1 - 0.09 caching) x (1 - 0.05 FHIR cache) x (1 - 0.21 model tiering, from
30% of turns at the measured 0.31x Haiku cost ratio — see `MODEL_TRADEOFF.md`) + $800/month (self-hosted
Langfuse) + $2,000/month (routing/tenancy) ≈ **$1,985,000/month** (~31% below naive — the largest relative
reduction of any tier, driven mostly by model-tiering, assumptions #8-#9). This revises an earlier draft's
~37%/$1,827,000 figure, which used a since-corrected 1/10th-cost placeholder for assumption #9 — the real
measured Haiku ratio is less favorable than that guess, so the tiering saving is smaller than first estimated.

### Naive vs. adjusted cost, side by side

| Users | Naive cost/month | Adjusted cost/month (with architecture) | Change |
|---|---|---|---|
| 100 | $2,900 | ~$2,930 | +1% (infra reliability cost, no LLM reduction yet) |
| 1,000 | $29,000 | ~$26,400 | -9% (prompt caching) |
| 10,000 | $289,000 | ~$250,000 | -14% (caching + FHIR cache + autoscaling infra) |
| 100,000 | $2,890,000 | ~$1,985,000 | -31% (+ model tiering + self-hosted Langfuse + multi-tenant routing) |

## Summary

The measured per-turn cost (~$0.02) is small in absolute terms, but the assignment's own framing is correct:
naive linear scaling reaches meaningful dollar figures by the 10K-user tier. The architectural response isn't
just "spend more" or "spend less" uniformly — it's a mix: prompt caching and model-tiering are genuine cost
reductions available in the code itself (worth ~9% and ~21% respectively, per the assumptions above — the
model-tiering figure is now backed by a real measured Haiku-vs-Sonnet comparison, see `MODEL_TRADEOFF.md`,
rather than an estimate), while
reliability infra (extra replicas, autoscaling) and, at 100K users, multi-tenant routing are cost *additions*
required by scale, not optional. Compliance (PHI reaching Langfuse Cloud) was resolved separately by redaction
(`PHI_AUDIT.md`) and doesn't require self-hosting at any of these tiers, though self-hosting becomes
cost-justified in its own right once trace volume is high enough (100K-user tier). Every multiplier used above
is listed in the assumptions table and can be changed independently — this is a model to be argued with, not a
final number. Separately, the load test (`LOADTEST.md`) already shows the current single-worker deployment
doesn't hold up even at 50 concurrent users on the latency/error-rate axis, independent of dollar cost — that
ceiling is hit long before the cost figures above become the binding constraint.
