# USERS.md — Target Users & Use Cases

This platform has a different kind of user than the Clinical Co-Pilot it tests (see `Gauntlet/Week
1/USER.md`'s ED resident persona). Its users are the people responsible for the Co-Pilot's *security
posture over time*, not the clinicians who use it day to day.

## Primary User

**The engineer maintaining the Clinical Co-Pilot** — the person who ships changes to `agent/` or
`interface/modules/copilot/` and needs to know, before and after each change, whether it introduced or
reintroduced a vulnerability. This is the same person doing the Week 1-2 build work; the platform exists
because manual, ad hoc testing doesn't scale to "every change, continuously," which is exactly the gap the
assignment's scenario describes (inconsistent behavior surfacing under conditions nobody explicitly tested
for).

## Secondary User

**Whoever has to sign off on the Co-Pilot being trustworthy** — described in the assignment's own framing as
"a hospital CISO deciding whether to trust this platform with continuous security testing." This person
doesn't read code; they read the Documentation Agent's reports and the coverage/trend data the Observability
Layer surfaces, and they're the one a Critical/High report's human-approval gate is actually gating for.

## Why Automation Is the Right Solution Here

Three properties of the underlying problem specifically justify automation over manual pentesting, not just
"automation is generally good":

1. **The target changes continuously, not once.** A one-time pentest report goes stale the moment `agent/`
   or `interface/modules/copilot/` changes. Re-running a fixed manual test list by hand after every change is
   exactly the kind of repetitive, ambient-monitoring task that doesn't scale to a human doing it
   consistently — but a regression suite triggered on every deploy does, cheaply.
2. **Attack techniques evolve, and a static list goes stale too.** The assignment's own framing — "defenses
   built around a small number of known examples rarely hold as attackers adapt" — is why the Red Team Agent
   generates and mutates rather than replaying a fixed payload list. A human writing new attack variants by
   hand every week doesn't keep pace with an adversary who can iterate as fast as the target changes.
3. **Judging attack success requires the same context every time, and humans are inconsistent at that.** The
   whole point of a rubric-based, version-pinned Judge Agent is that "did this attack succeed" gets answered
   the same way today as it will in a month — a real, measured problem, not hypothetical: this project's own
   Week 1-2 eval gate already documented LLM-answer phrasing variance causing flaky pass/fail judgments on
   nominally-identical test cases (see `Gauntlet/STATUS.md`'s `REF-02`/`REF-06` history). A human grader
   re-reading a transcript by hand has the same consistency problem, just less visibly.

What automation is explicitly **not** trusted to do here: decide that a Critical/High finding is safe to
publish without a human looking at it, or apply a fix to the target's own code. Those stay human decisions
by design (see `ARCHITECTURE.md`'s human-gate section) — automation's job is coverage and consistency, not
unsupervised judgment calls with real consequences.

## Use Cases

### UC-1: Continuous regression detection across deploys
"Did the change I just shipped reintroduce a vulnerability we already fixed?" — the regression harness
re-runs every confirmed exploit against the new `target_version`, pinned to the same `rubric_version` so a
pass can't just mean the model drifted (`ARCHITECTURE.md` decision #4). This is the platform's core
reason for existing, not an add-on feature.

### UC-2: Coverage visibility
"Which attack categories are actually tested, and which are still a gap?" — the Observability Layer answers
this from real per-category counts in the Exploit DB, not from assuming the seed suite is comprehensive. Today
(`evals/run_redteam_eval.py`'s first live run) this honestly shows exactly 4 categories tested, 2 (tool misuse,
identity/role exploitation as a distinct case from data exfiltration) still open — a real, current gap, not
a hypothetical one.

### UC-3: Human triage of a Critical/High finding
The engineer or CISO-equivalent reviews a specific, reproducible finding (attack sequence, observed response,
Judge's rationale and evidence quote) and decides whether to publish it — not asked to trust an opaque "AI
says this is bad" claim. This is why `evidence_quote` is a required field on every `JudgeVerdict`, not
optional polish.

### UC-4: New vulnerability discovery beyond the seed set
"What haven't we thought to test yet?" — the Red Team Agent mutates and the Orchestrator (once built)
redirects attention toward under-covered categories, so the platform's value grows past whatever the initial
`seed_attacks.json` happened to include. The first live run already demonstrates this isn't just theoretical:
the confirmed cross-patient data-exfiltration finding came from a seed *hypothesis*, generated into an actual
novel message by the Red Team Agent, not a hand-written static payload.

### UC-5: Cost/scale planning before committing to a bigger testing cadence
"What would running this nightly, or at 10x the volume, actually cost?" — answered from real measured
per-attack cost (Red Team + target + Judge calls), not estimated, extending the same real-data discipline as
`Week 1/COST_ANALYSIS.md`/`Week 2/COST_ANALYSIS.md`. `Gauntlet/Week 3/COST_ANALYSIS.md` (Friday) is this use
case's deliverable.

### UC-6: Honest "we don't know yet" reporting
The platform must never imply full coverage it doesn't have. A category with zero attempts reads as an
explicit, visible gap (UC-2), not silently absent from a report — the same "don't fabricate confidence"
principle the Clinical Co-Pilot itself is held to (`Gauntlet/Week 1/USER.md` UC-6), applied here to security
coverage claims instead of clinical ones.

## Explicitly Out of Scope

- Automated remediation (writing a fix to the target's own code) — always a human action, never this
  platform's job (`ARCHITECTURE.md`'s AI-use disclosure section).
- Attacking anything other than the seeded synthetic patients in the deployed Clinical Co-Pilot — no real
  patient data exists in this system, and no other target is in scope this week.
- Replacing a human security reviewer's judgment on Critical/High findings — the platform produces evidence
  for that judgment, it doesn't make the call itself.
