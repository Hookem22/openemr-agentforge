# Load/Stress Test Results — Week 2 Extension (`/ingest` + RAG-triggering `/chat`)

Extends `Week 1/LOADTEST.md` (the `/chat`-only sweep, still fully valid and unchanged) with the two
new flows Week 2 adds: document ingestion (`/ingest`) and guideline-evidence retrieval (a `/chat`
call that routes through `evidence_retriever`). Same standard as Week 1: real requests against the
**deployed production** `copilot-agent` service, real network path, real Anthropic + Voyage spend.

## Method

- Script: `agent/loadtest/loadtest_week2.py` (same `asyncio` + `httpx` pattern as `loadtest.py`,
  extended with `--mode ingest|rag-chat`).
- Target: `https://copilot-agent-production-8af2.up.railway.app`, not local.
- **Concurrency levels are deliberately smaller than Week 1's 1-200 sweep**: `/ingest` alone costs
  ~$0.025 and ~11-16s per call (`Gauntlet/Week 2/COST_ANALYSIS.md`), so a wide sweep would have real,
  disproportionate cost for the marginal information gained at this stage. `ingest`: 1/3/5 concurrent.
  `rag-chat`: 1/5/10 concurrent (cheaper — Voyage's contribution is near-free, see COST_ANALYSIS.md —
  so a slightly wider range was affordable).
- Auth: same time-boxed pattern as Week 1 — a temporary password-grant OAuth2 client, registered,
  used for exactly this run, then immediately disabled, with `oauth_password_grant` reverted to
  disabled afterward. **Unlike Week 1's original run, this was done only after explicit, in-session
  user confirmation** (not assumed from precedent) — see `Gauntlet/Week 2/STATUS.md`'s CI-setup
  section for the reasoning on why standing/security-sensitive changes get their own confirmation
  even when a prior session already used the same pattern.
- `rag-chat` requests use guideline-style prompts against Maria Gonzalez (diabetic, real chart data)
  specifically chosen to trigger the `evidence_retriever` routing path — confirmed via each response's
  own `handoff_log` (returned in the `/chat` API response itself, not inferred).
- CPU/memory/HTTP metrics pulled via `railway metrics --service copilot-agent --cpu --memory --http
  --json --since 20m`, covering the exact window of both test runs (25 total requests, matching the
  Railway-reported total exactly).

## Results — document ingestion (`/ingest`)

| Concurrent users | Requests | Error rate | p50 | p95 | p99 | min / max / mean |
|---|---|---|---|---|---|---|
| 1 | 1 | 0/1 (0.0%) | 15.90s | 15.90s | 15.90s | 15.90s / 15.90s / 15.90s |
| 3 | 3 | 0/3 (0.0%) | 30.30s | 44.22s | 44.22s | 15.06s / 44.22s / 29.86s |
| 5 | 5 | 0/5 (0.0%) | 71.68s | 71.68s | 71.68s | 15.75s / 71.68s / 60.50s |

**Latency grows steeply, not flatly, with concurrency** — mean latency roughly quadruples from 1 to 5
concurrent users (15.9s → 60.5s), unlike `/chat`'s much flatter curve in Week 1's sweep (8-17s mean
across the entire 1-50 range). Zero errors at every level, though — every request eventually
succeeded, just slower.

## Results — RAG-triggering `/chat`

| Concurrent users | Requests | Error rate | p50 | p95 | p99 | min / max / mean | Routed to `evidence_retriever` |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 0/1 (0.0%) | 24.31s | 24.31s | 24.31s | 24.31s / 24.31s / 24.31s | 1/1 |
| 5 | 5 | 0/5 (0.0%) | 15.24s | 25.34s | 25.34s | 12.01s / 25.34s / 17.43s | 5/5 |
| 10 | 10 | 0/10 (0.0%) | 18.41s | 25.56s | 25.56s | 13.20s / 25.56s / 19.20s | 10/10 |

**Every request correctly routed through the evidence-retrieval worker** (confirmed via each
response's own `handoff_log`, not assumed), and latency stayed in the same 12-26s band across the
whole 1-10 range — much closer to Week 1's flat `/chat` curve than to `/ingest`'s steep one. Voyage's
own contribution (embed + rerank) is ~0.35s mean per call (`COST_ANALYSIS.md`), so essentially all of
this latency is the Claude call(s) reasoning over chart data plus retrieved guideline text, not the
retrieval mechanism itself.

## Baseline resource usage (Railway metrics, window covering both runs, 25 total requests)

| Metric | Value |
|---|---|
| CPU average | 0.0029 vCPU |
| CPU max | 0.0288 vCPU (of 2.0 vCPU limit — **1.4% peak utilization**) |
| Memory average | 147.2 MB |
| Memory max | 188.9 MB (of 1024 MB limit — **18.4% peak utilization**) |
| Container-level HTTP (Railway edge) | 25 total requests, 25 2xx, 0 4xx, **0 5xx** |
| Container-level HTTP latency | p50/p90/p95/p99 all report 30,000ms (metrics endpoint clamps its reported percentiles at a 30s ceiling — `/ingest`'s real max of 71.68s exceeds this reporting cap; see Week 1's identical caveat) |

CPU and memory both stayed nowhere near their limits (1.4% / 18.4% peak) — same conclusion as Week 1:
**the growing `/ingest` latency is not resource exhaustion.** The container has ample CPU/memory
headroom; the bottleneck is architectural.

## Interpretation

**`/ingest`'s steep latency growth is the single-`uvicorn`-worker architecture (Week 1's already-
documented root cause) hitting a much more severe case.** A `/chat` turn's slow part is 1-2 Claude
tool-use calls (~5-15s combined); an `/ingest` call additionally rasterizes a PDF page to an image
and sends it through a Claude *vision* call, which both costs more (`COST_ANALYSIS.md`: ~$0.025/call,
more than an entire chat turn) and appears to take meaningfully longer per-call *and* to serialize
more severely under concurrency, since the single worker process handles these heavier requests one
at a time with no parallelism. At 5 concurrent `/ingest` calls, the slowest request (71.68s) is
already approaching typical client-side timeout budgets — a real, more urgent version of the same
gap Week 1 flagged as needing multiple workers/replicas, since `/ingest` amplifies the effect far more
than `/chat` does at the same concurrency level.

**RAG-triggering `/chat` calls behave like ordinary `/chat` calls, not like `/ingest`.** The extra
`evidence_retriever` hop adds real latency (Voyage's own call: ~0.35s mean) but doesn't change the
qualitative shape of the concurrency curve — it stays flat across 1-10 users, consistent with Week 1's
finding that `/chat`'s latency is dominated by the LLM round-trip, not by anything specific to this
worker addition.

**Practical takeaway, extending Week 1's fix recommendation**: the multi-worker/multi-replica fix
Week 1 already identified as needed is *more* urgent for `/ingest` specifically than for `/chat` —
document upload during a busy shift (multiple clinicians uploading labs/intake forms around the same
time) would degrade far faster than concurrent chart-lookup chat traffic would, at the current
single-worker deployment. This wasn't visible in Week 1's `/chat`-only sweep since `/ingest` didn't
exist yet.

## Reproducing

```bash
AGENT_BEARER_TOKEN=<token> python agent/loadtest/loadtest_week2.py \
  --base-url https://copilot-agent-production-8af2.up.railway.app --mode ingest --users 1 3 5

AGENT_BEARER_TOKEN=<token> python agent/loadtest/loadtest_week2.py \
  --base-url https://copilot-agent-production-8af2.up.railway.app --mode rag-chat --users 1 5 10
```

See `agent/README.md` for how to obtain a bearer token; production password grant is disabled by
default and must be deliberately, temporarily re-enabled with explicit confirmation before running
this — do not leave it enabled, and do not assume prior confirmation carries over to a later session.
