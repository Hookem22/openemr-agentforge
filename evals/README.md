# evals/ — Adversarial Test Suite

`seed_attacks.json` is the seed library the Red Team Agent mutates/extends from — each entry is a
hypothesis grounded in a concrete finding in `THREAT_MODEL.md`, not an arbitrary payload. Every case
is tagged with its `attack_category` and `owasp_llm_category`/`owasp_web_category`, satisfying the
assignment's mandatory Engineering Requirement (map every test case to its OWASP category) from the
first seed onward, not retrofitted later.

`run_redteam_eval.py` drives the full **Red Team Agent -> Target Adapter -> Judge Agent** loop live
against the deployed target for every seed (or one category via `--category`), writes each result to
the Exploit DB (unless `--no-db`), and prints a pass/fail-style summary. This one script is what
satisfies both remaining MVP hard gates at once: results across >=3 attack categories, and a working
prototype of two agent roles (Red Team + Judge) running live.

## Running it

```bash
cd redteam
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY, TARGET_BASE_URL, REDTEAM_OPENEMR_USER/PASS, DATABASE_URL
python ../evals/run_redteam_eval.py
```

## Cost note

Every seed makes at least 3 real Anthropic calls (Red Team's message generation, the target's own
chat turn(s), the Judge's verdict) plus whatever tool-calling the target's own agent loop does. The
4 seeds here cost roughly the same order of magnitude as a handful of normal Clinical Co-Pilot chat
turns — see `Week 1/COST_ANALYSIS.md`/`Week 2/COST_ANALYSIS.md` for the per-turn baseline this scales
from. `Gauntlet/Week 3/COST_ANALYSIS.md` (Friday) extends this methodology to the full platform at
scale.

## Scope

Every attack in `seed_attacks.json` targets one of the 4 seeded synthetic patients (Maria Gonzalez,
James Whitfield, Robert Chen, Dorothy Simmons) — never a real patient, and never a patient outside
that seeded set, per `PLAN.md`'s residue-risk callout.

## Known limitation (fill in once resolved)

The `data_exfiltration` seed's IDOR hypothesis is only fully conclusive when the Red Team Agent
authenticates as an ordinary clinical-role user, not an administrator — see `THREAT_MODEL.md` and
`redteam/.env.example`'s note on `REDTEAM_OPENEMR_USER`.
