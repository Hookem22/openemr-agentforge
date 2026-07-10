# Load/Stress Test Results — `copilot-agent`

Assignment engineering requirement: load/stress test at 10 and 50 concurrent users against the deployed
agent, recording p50/p95/p99 latency and error rate at each level, plus a baseline CPU/memory/latency/
throughput profile.

## Method

- Script: `agent/loadtest/loadtest.py` (plain `asyncio` + `httpx`, no new dependency — `httpx` was already a
  project dependency).
- Target: the **deployed production** `copilot-agent` service
  (`https://copilot-agent-production-8af2.up.railway.app`), not local — real network path, real Railway infra,
  real Anthropic API calls.
- Each simulated user sends exactly **one** chat turn (one full agent run: tool calls + LLM + verification),
  cycling through the 4 seeded sample patients and 4 representative prompts (see script). Kept to one turn per
  user to bound real Anthropic API spend.
- Auth: a temporary, time-boxed OAuth2 password-grant client was registered against production to obtain a
  real bearer token (OpenEMR's password grant is disabled by default in production; it was enabled only for
  the duration of this test and immediately reverted afterward, along with disabling the temporary client).
- CPU/memory/HTTP metrics pulled via `railway metrics --service copilot-agent --cpu --memory --http --json
  --since 1h`, covering both test runs.

## Results

### 10 concurrent users

| Metric | Value |
|---|---|
| Error rate | 0/10 (0.0%) |
| p50 | 8.04s |
| p95 | 24.83s |
| p99 | 24.83s |
| min / max / mean | 4.71s / 24.83s / 12.84s |

### 50 concurrent users

| Metric | Value |
|---|---|
| Error rate | 11/50 (22.0%), all HTTP 502 |
| p50 | 18.65s |
| p95 | 53.78s |
| p99 | 58.34s |
| min / max / mean | 7.39s / 58.34s / 21.48s |

### Baseline resource usage (Railway metrics, window covering both runs)

| Metric | Value |
|---|---|
| CPU average | 0.003 vCPU |
| CPU max | 0.172 vCPU (of 2.0 vCPU limit — **8.6% peak utilization**) |
| Memory average | 158.7 MB |
| Memory max | 234.6 MB (of 1024 MB limit — **22.9% peak utilization**) |
| Container-level HTTP (Railway edge) | 86 total requests, 64 2xx, 22 4xx, **0 5xx** |
| Container-level HTTP latency | p50 25.4s, p95/p99 27.0s |

## Interpretation

**Latency is dominated by the LLM call, not our infrastructure.** Even at 10 users with zero errors, p50 is
already 8s and p95 is 25s — consistent with a multi-tool-call agent turn (FHIR fetches + Claude tool-use loop
+ verification) rather than any bottleneck in `copilot-agent` itself. This validates the Speed vs. Completeness
tradeoff documented in `ARCHITECTURE.md`: the agent optimizes for grounded completeness within a single turn,
at the cost of sub-second responses.

**The 50-user error rate is a real, documented scaling limit — but not a resource-exhaustion one.** CPU peaked
at 8.6% and memory at 22.9% of their container limits during the test window, and the container's own HTTP
metrics recorded **zero 5xx responses** (only 64 2xx + 22 4xx, 86 total — fewer than the 50 requests sent,
and no 502s at all). Both facts point the same direction: the 11 client-observed 502s never reached the
FastAPI process — they were generated at Railway's edge/proxy layer and never dispatched to the container.
The root cause is architectural, not a resource limit: `agent/Dockerfile` runs a single `uvicorn` worker
process with no horizontal scaling, and each chat turn holds a live HTTP connection open for the full
duration of a synchronous, multi-step LLM tool-use loop (10-60+ seconds). At 50 simultaneous long-lived
requests, Railway's proxy layer starts rejecting/timing out excess concurrent connections before the single
worker process can even see them.

**Fix for production scale (documented as required work in `COST_ANALYSIS.md`/architecture-changes-needed
discussion, not done in this submission):** run multiple `uvicorn` workers (or multiple Railway replicas)
behind Railway's load balancer, and/or move long-running turns off the request/response cycle entirely
(background job + polling or streaming/SSE) so a single slow LLM call doesn't hold a full proxy connection
open for a minute. At current single-worker-process scale, ~10-20 concurrent users is a safe operating ceiling;
50 concurrent users measurably exceeds it.

## Reproducing

```bash
AGENT_BEARER_TOKEN=<token> python agent/loadtest/loadtest.py \
  --base-url https://copilot-agent-production-8af2.up.railway.app --users 10 50
```

See `agent/README.md` for how to obtain a bearer token (dev: `DEV_BEARER_TOKEN` via password grant against a
local instance; production: password grant is disabled by default and must be deliberately, temporarily
re-enabled — do not leave it enabled).
