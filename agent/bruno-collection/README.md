# Clinical Co-Pilot Agent -- Bruno collection

Open this folder in [Bruno](https://www.usebruno.com/) (or run headless via `npx @usebruno/cli run`).
Covers all 4 agent endpoints: `Health`, `Ready`, `Chat`, `Ingest`.

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
pipeline; `Chat` runs a full LangGraph turn.
