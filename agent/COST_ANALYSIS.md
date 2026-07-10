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

### Architectural changes needed at each tier

- **100 users (single hospital department pilot):** current architecture is sufficient largely as-is. The one
  fix already identified by load testing (`LOADTEST.md`): the single-`uvicorn`-worker `copilot-agent` process
  saturates well before 50 concurrent long-running turns, so this tier needs at least 2-3 workers/replicas
  behind Railway's load balancer to keep latency stable during shift-change bursts, not for raw cost reasons.

- **1,000 users (multi-department/small hospital):** the highest-leverage cost change is **Anthropic prompt
  caching**. The system prompt (`agent/app/graph.py`'s `SYSTEM_PROMPT`) and the 9 tool schemas are identical
  on every single LLM call, and the measured data shows ~2 LLM calls per turn — meaning that static ~1,000+
  token block is being paid for as fresh input tokens twice per turn today. Caching it would cut input-token
  cost on the (larger) second call in each turn substantially; this is a code change (mark the system+tools
  block as cacheable in the Anthropic SDK call), not an infra change, and pays for itself almost immediately
  at this volume. This tier is also where the documented compliance debt — Langfuse Cloud receiving full PHI
  trace payloads with no BAA (`ARCHITECTURE.md` Section 8) — stops being deferrable and must move to a
  self-hosted Langfuse instance, which is an infra cost addition, not a reduction.

- **10,000 users (hospital system / multi-site):** at this volume, a meaningful fraction of turns are likely
  near-duplicate lookups (e.g. multiple clinicians pulling the same patient's snapshot within the same shift).
  Adding a short-TTL cache in front of the FHIR read tools (not the LLM call itself, to avoid any risk of
  stale clinical data reaching a claim) reduces tool-call volume and, indirectly, the number of turns that need
  a full multi-round tool-use loop. This is also the tier where a single shared `copilot-agent` deployment
  needs real horizontal autoscaling (not just a fixed 2-3 replicas) and connection pooling to OpenEMR's FHIR
  API, since 10,000 users maps to roughly the concurrency load already shown to cause edge-level 502s at just
  50 simultaneous long-running requests (`LOADTEST.md`).

- **100,000 users:** this scale does not correspond to "one hospital's OpenEMR instance" anymore — a 500-bed
  hospital has on the order of hundreds to low thousands of clinical staff, not 100K (directly relevant to the
  "scaling to a 500-bed hospital / 300 concurrent users" interview-prep question). Reaching 100K users
  necessarily means **multi-tenant deployment across many hospital systems**, which changes the architecture
  qualitatively, not just its capacity: per-tenant data isolation and per-tenant BAAs (each hospital system is
  its own covered entity), a routing/tenancy layer in front of `copilot-agent`, and likely a tiered model
  strategy — e.g. a smaller/cheaper model for simple single-fact lookups (UC-1 "who is this patient") with
  escalation to the full model only for turns that need multi-source reasoning (UC-2 "what changed," UC-3
  interaction flagging) — to keep the highest-volume, lowest-complexity turns cheap. This is the point where
  cost-per-token x n breaks down most visibly: without model-tiering and caching, the naive ~$2.9M/month
  figure above is a real floor, not a ceiling, once added infra (multi-tenant routing, self-hosted Langfuse at
  this scale, autoscaled compute) is included.

## Summary

The measured per-turn cost (~$0.02) is small in absolute terms, but the assignment's own framing is correct:
naive linear scaling reaches meaningful dollar figures by the 10K-user tier, and the architectural response
isn't just "spend more" — prompt caching and model-tiering are cost reductions available in the code itself,
while the compliance (self-hosted Langfuse/BAA) and multi-tenancy changes are cost additions required by scale,
not optional. The load test (`LOADTEST.md`) already shows the current single-worker deployment doesn't hold up
even at 50 concurrent users on the latency/error-rate axis, independent of dollar cost — that ceiling is hit
long before the cost figures above become the binding constraint.
