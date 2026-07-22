# CLAUDE.md — openemr-base-clean-main (Clinical Co-Pilot target)

This repo is the **target** the AgentForge adversarial platform attacks and evaluates. That
platform — Red Team / Judge / Orchestrator / Documentation agents, plus a web dashboard — lives in a
separate sibling repo, `agentforge-redteam` (assumed to be a sibling directory on the same machine,
e.g. both under `~/Documents/Gauntlet/` — an explicit assumption, not something to silently rely on
if the layout looks different).

## If you're asked to "pick up a vulnerability and fix it"

That means: read a real, documented finding from the redteam platform's `vulnerability_reports`
table via its JSON API, fix the underlying issue in this codebase, and mark the finding
`fixed_by_ai`. A human reviews and confirms the fix afterward — you are not deploying anything, and
your fix does not go live on its own.

### 1. Find the AI-fixer credential

It lives in the sibling repo's `redteam/.env` (gitignored there, not committed) as
`AI_FIXER_USERNAME` / `AI_FIXER_PASSWORD`, alongside the platform's base URL. Locally, the running
platform is usually at `http://localhost:8001`; in production it's
`https://redteam-web-production.up.railway.app`. If you can't find `../agentforge-redteam/redteam/.env`,
stop and ask the user where the platform is running and what the credential is — do not guess or
fabricate one.

### 2. Read a finding

```
GET /api/reports?status=published        # or auto_published / pending_approval — the 3 fixable statuses
GET /api/reports/{id}                     # full detail: report + the exact attack_sequence/observed_response
```

Both require HTTP Basic Auth with the AI-fixer credential. The detail response's `exploit_record`
field has the real attack transcript and the target's actual observed response — read this, not
just the report's prose description, before writing a fix.

### 3. Locate the real root cause

Cross-reference the report's `attack_category` and OWASP tags (`owasp_llm_category`,
`owasp_web_category`) against where the relevant logic actually lives:

- **Agent-logic issues** (prompt injection, PHI leakage, faulty tool-calling, RAG/retrieval bugs,
  verifier gaps) → `agent/app/*.py` (`graph.py`, `tools.py`, `rag.py`, `verifier.py`,
  `fhir_client.py`, `ingestion.py`). See `agent/README.md` and this repo's `ARCHITECTURE.md`.
- **Integration-layer issues** (auth/session handling, IDOR, the OpenEMR-facing proxy, widget
  embedding) → `interface/modules/copilot/*.php` (`proxy.php`, `callback.php`, `config.php`,
  `widget.php`, `start.php`).

`THREAT_MODEL.md` documents the known attack surface and prior hypotheses (e.g. the cross-patient
IDOR surface in `proxy.php`) — check it before assuming a finding is novel.

### 4. Implement a real, minimal fix — on a new branch, do not push or merge

```
git checkout -b fix/<short-description>
# make the actual code change
git add <files>
git commit -m "..."
```

Do **not** push this branch and do **not** merge it into `main`. A human reviews the diff first and
decides if/when it reaches the deployed target. Marking something `fixed_by_ai` is a claim to be
verified, not an automatic deploy.

### 5. Claim the fix

```
POST /api/reports/{id}/mark-fixed
{"fix_description": "what changed and why, in plain language", "fix_reference": "fix/<branch-name>"}
```

Same credential as step 2. This will 409 if the report isn't currently in a fixable status (e.g.
someone already marked it, or a human already rejected the finding).

### 6. Tell the human what to review

State plainly: the branch name, which file(s) changed, and a one-line summary of the fix — so they
know exactly what to look at before running the redteam platform's Re-test action against it (or
testing manually) and closing the report.

## What you must never do here

- Never push or merge the fix branch yourself.
- Never call `approve`, `reject`, `close`, or `reopen` on a report — those are human-only actions on
  the redteam platform's dashboard, and the AI-fixer credential can't reach them anyway.
- Never fabricate a `fix_reference` for a fix you didn't actually make.
