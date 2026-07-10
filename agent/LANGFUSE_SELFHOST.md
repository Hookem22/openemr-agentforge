# Future Plan: Self-Hosted Langfuse (Option A, deferred)

**Status: not implemented.** `PHI_AUDIT.md` documents the redaction (Option B) implemented instead,
which is the current answer to the Langfuse Cloud PHI/BAA compliance question. This document is a
plan for the stronger, infrastructure-level alternative — removing Langfuse Cloud from the loop
entirely — to execute later if redaction is ever judged insufficient (e.g. an audit requirement
that no third party receive trace data regardless of content, not just PHI-scoped content).

## Why this is deferred, not done now

Redaction (Option B) closes the actual PHI/BAA gap with a small, verifiable code change (see
`PHI_AUDIT.md`'s live verification). Self-hosting is a large, separate infrastructure project that
doesn't change what data leaves the agent's own boundary — it changes *whose* infrastructure
receives it. Given Option B already gets non-PHI-only telemetry to a third party, self-hosting's
marginal compliance benefit (no third party at all) is real but not urgent enough to block on right
now.

## Corrected scope: this SDK requires the v3 (OTel-based) self-host architecture

An earlier planning note in this repo's memory assumed a lightweight Langfuse v2 self-host (single
container + Postgres). That assumption is **stale** — `agent/requirements.txt` pins `langfuse>=3`,
and `graph.py` already uses the v3 OTel-based API (`get_client()`, `@observe`, `propagate_attributes`).
Self-hosting this SDK version requires Langfuse's full v3 server stack, not the old single-container
setup. Scope, corrected:

- **Postgres** — transactional data (projects, users, prompts, scores)
- **ClickHouse** — trace/observation/event storage (the actual tracing data volume)
- **Redis / Valkey** — queueing between the web and worker processes
- **S3-compatible blob storage** — large trace payloads/media (MinIO if self-hosted, or a real S3
  bucket)
- **`langfuse/langfuse` (web)** container — UI + API
- **`langfuse/langfuse-worker`** container — async ingestion/processing

That's 6 new backing services beyond the 3 already running in this project's Railway environment
(MySQL, `openemr-app`, `copilot-agent`) — a materially larger footprint than the current setup.

## Plan (4 phases)

### Phase 1: Provision backing stores
Add Postgres, ClickHouse, Redis/Valkey, and an S3-compatible bucket as new Railway services (or
managed equivalents, if Railway's trial/plan resource limits make self-hosting all of this
impractical there — worth checking before starting, since this is 4 new stateful services on top of
the existing MySQL instance).

### Phase 2: Deploy the Langfuse server stack
Add `langfuse/langfuse` (web) and `langfuse/langfuse-worker` as two more Railway services, wired to
the Phase 1 backing stores via env vars (per Langfuse's self-host docs). Confirm the web UI is
reachable and a test project/API key pair can be created.

### Phase 3: Cut the agent over
Point `copilot-agent`'s `LANGFUSE_HOST`/`LANGFUSE_BASE_URL` at the new self-hosted Langfuse URL and
swap in a new public/secret key pair from the self-hosted project. Send a real chat turn and confirm
a trace appears in the self-hosted instance instead of Langfuse Cloud. At this point the redaction
work in `graph.py` is no longer strictly necessary for compliance (nothing leaves our own
infrastructure), but there's no reason to revert it — it costs nothing and is a reasonable minimal-
data-sent practice regardless of host.

### Phase 4: Decommission + document
Delete the Langfuse Cloud project (or at minimum revoke its API keys) once the self-hosted instance
has a few days of real traffic confirming it's stable. Update `ARCHITECTURE.md`, `PHI_AUDIT.md`, and
`.env.example` to reflect self-hosted Langfuse as the new baseline, removing references to Langfuse
Cloud entirely.

## Risks / open questions to resolve before starting

- **ClickHouse operational complexity** — it's the least familiar piece of this stack for a small
  team; expect a learning curve around backup/restore and disk sizing that Postgres/MySQL don't have.
- **New attack surface** — 6 new internet-adjacent services (even if only internally reachable
  within Railway's private network) is a meaningfully larger security surface than today's 3
  services; each needs the same auth/network hardening review the existing services got.
- **No managed backup story** — unlike Railway's managed MySQL, self-hosted ClickHouse/Postgres/
  Redis on Railway have no built-in backup guarantee; a backup plan needs to exist before real
  trace data (even redacted) depends on this stack.
- **Railway plan/resource limits** — confirm the current Railway plan can actually support 6
  additional services (CPU/memory/storage quotas) before committing to this design; if not, this
  may need a different host for the Langfuse stack specifically.
