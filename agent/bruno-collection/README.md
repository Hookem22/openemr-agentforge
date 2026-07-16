# Clinical Co-Pilot Agent -- Bruno collection

Open this folder in [Bruno](https://www.usebruno.com/) (or run headless via `npx @usebruno/cli run`).
Covers all 4 agent endpoints (`Health`, `Ready`, `Chat`, `Ingest`) plus a 5th request, `Full Week 2
Flow`, demonstrating the complete multi-worker graph in a single call.

## Setup

The `local` environment's `bearer_token` reads from a `DEV_BEARER_TOKEN` runtime variable rather than
a hardcoded value (never commit a real token) -- set it before running:

```bash
# GUI: Bruno's env editor, or a Bruno "runtime variable"
# CLI:
npx @usebruno/cli run chat.bru --env local --env-var DEV_BEARER_TOKEN=<your token>
```

See `agent/README.md` for how to mint a `DEV_BEARER_TOKEN` against a local OpenEMR instance.

## Verified

Every request in this collection was run against a real local agent + OpenEMR instance while
building it -- not just written to look plausible. `Ingest` uploads a real fixture
(`../eval/fixtures/maria_gonzalez_lab.pdf`) and exercises the full upload -> extract -> persist
pipeline; `Chat` runs a full LangGraph turn; `Full Week 2 Flow` drives both workers
(`intake_extractor` + `evidence_retriever`) in one `/chat` call, using James Whitfield's fixture lab
PDF and a guideline-triggering question so the routing sequence is genuinely exercised, not just
plausible-looking.

**Real finding while building `Full Week 2 Flow`**: a pre-request script reading a fixture file
directly (the way a natural first attempt would, mirroring `Ingest`'s `@file(...)` multipart syntax)
does not work -- Bruno's script sandbox (via the CLI) is a restricted QuickJS runtime with no
`fs`/`path` module access, confirmed by testing it directly (`Error: Cannot find module fs`), not
assumed. `Chat`'s JSON body has no multipart-style file-reference syntax either, so the fixture is
instead pre-encoded once as a `james_lab_pdf_base64` environment variable.
