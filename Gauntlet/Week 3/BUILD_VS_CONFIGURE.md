# Build-vs-Configure Decision Record — Adversarial AI Security Platform

Written before any `redteam/` code exists, per the assignment's own framing: "part of the challenge is
determining which tools, models, evaluation strategies, and security methodologies are most appropriate."
This records what was actually evaluated and why a custom 4-agent build is justified — and, just as
importantly, which existing tools *aren't* being replaced.

## What was evaluated

| Tool | What it does | Where it fits this assignment | Where it falls short |
|---|---|---|---|
| **Burp Suite** | Interactive/automated web-app proxy scanner — request interception, active/passive scanning, extensible via BApp Store. | Excellent for classic protocol-level web vulns (auth bypass, injection, SSRF) if the Clinical Co-Pilot's HTTP surface (`proxy.php`, `upload.php`) needs deterministic fuzzing. | No concept of an LLM conversation, multi-turn state, or "did the model's *response* leak PHI" — it scans HTTP requests/responses as opaque bytes, not semantic content. Commercial licensing also doesn't fit a CI-triggered, fully automated nightly run without a paid seat. |
| **OWASP ZAP** | Open-source equivalent of Burp — proxy-based scanner, has a real REST API and a Docker image, so it's automatable without a paid seat. | Same protocol-level niche as Burp, but genuinely automatable (Docker daemon + API client) — a real candidate to wrap as an Orchestrator-invokable tool for the OWASP Top 10 (non-LLM) requirement. | Same fundamental gap as Burp: zero understanding of LLM semantics. Cannot generate a multi-turn prompt-injection sequence, cannot judge whether a chat response actually leaked cross-patient data — it only sees HTTP traffic shape. |
| **Semgrep** | Static analysis / pattern-matching across source code (rules-based, AST-aware). | Genuinely useful for a *static* pass over `interface/modules/copilot/` and `agent/app/` to catch known-bad patterns (e.g. missing auth checks, raw SQL, unvalidated input) before any dynamic testing starts. Cheap, fast, zero API cost. | Static-only — cannot exercise the actual running system, cannot discover a *behavioral* vulnerability like the `pid` IDOR (the code path exists and even has a comment about it; the vulnerability is that the check performed is coarser than the comment implies, which only shows up by actually calling the endpoint with someone else's `pid` and observing the response). |
| **Garak** | Pip-installable, open-source LLM vulnerability scanner — a library of known static/single-turn attack techniques (prompt injection variants, jailbreak templates, data leakage probes) run against a target LLM endpoint. | Directly relevant — this is purpose-built for the OWASP LLM Top 10 category. Cheap to run repeatedly as a regression-suite baseline layer, since its probes are static and don't need per-target customization. | Explicitly not multi-turn-aware and not system-specific: it has no concept of "attack sequence that escalates over 3 turns," no way to know the Clinical Co-Pilot's own tool set or FHIR data model, and can't mutate a partially-successful attack into a smarter variant based on what it just observed. It's a known-technique net, not an adaptive attacker. |
| **Commercial red-team platforms** (e.g. hosted LLM red-teaming SaaS) | Managed adversarial testing services, typically API-driven, often with their own judge/scoring layer. | Would remove the need to build Judge-rubric logic from scratch. | Two disqualifying issues for this assignment specifically: (1) sending real, PHI-adjacent clinical chat traffic to a third-party SaaS red-team platform re-opens the exact BAA/data-sharing question Week 1's `AUDIT.md` already worked through for Anthropic — a new vendor means a new compliance review, not a rubber stamp; (2) the assignment's core engineering challenge is explicitly *designing the multi-agent architecture itself* (agent roles, trust boundaries, conflict-of-interest separation) — outsourcing that to a SaaS product would mean not doing the assignment's actual hard problem. |

## The gap none of the above close

Every tool above is either (a) protocol/static-analysis-level and has zero concept of LLM conversation
semantics, or (b) LLM-aware but limited to known, static, single-turn techniques. None of them can:
- Generate a **novel** multi-turn attack sequence targeting this specific system's actual tool set and data
  model (e.g. "ask about Patient A, then in turn 3 reference 'the patient we discussed earlier' while having
  switched `pid` — does the Co-Pilot notice the referent changed?").
- **Mutate** a partially-successful attack into a variant that pushes further, based on what the target's
  response just revealed.
- **Independently judge** whether an attack actually succeeded against this system's specific safe-behavior
  definition (e.g. "did this response cite a FHIR resource it never actually fetched this turn") — that
  requires understanding the Co-Pilot's own verification contract, not a generic jailbreak-detection heuristic.

This is the actual justification for building 4 custom agents rather than configuring existing tools: the
hard part of this assignment — adaptive, multi-turn, system-specific attack generation with independently
verified judging — has no off-the-shelf answer. Static/protocol-level coverage, on the other hand, is a
solved problem with mature free tooling, so it's reused, not reinvented.

## Decision

- **Garak**: wrapped as a real Orchestrator-invokable tool (`redteam/app/garak_tool.py`), feeding its results
  into the same Exploit DB and Documentation Agent report pipeline as the custom agents' findings. Covers the
  known/static half of the OWASP LLM Top 10 cheaply, freeing the custom Red Team Agent to focus on dynamic,
  multi-turn, system-specific attacks.
- **OWASP ZAP**: same treatment, contingent on integration effort — genuinely automatable (Docker + API), so
  it's the second thing wired up if Garak's integration goes smoothly. If time runs short this week, this is
  the one explicitly downgraded to "evaluated here, deferred" rather than shipping a flaky half-integration.
- **Semgrep**: a one-time static pass recommended over `interface/modules/copilot/` and `agent/app/`, feeding
  candidate findings into `THREAT_MODEL.md` as testable hypotheses (the way the `pid`/ACL finding already was)
  — not wired into the live agent loop, since it doesn't run against a live target the way Garak/ZAP do.
- **Burp Suite / commercial red-team SaaS**: not adopted. Burp's licensing model doesn't fit unattended CI
  automation; commercial red-team SaaS would outsource the assignment's actual design problem and reopen a
  vendor/BAA question this project has already worked through once.
- **Custom Red Team / Judge / Orchestrator / Documentation agents**: justified specifically for the gap above
  — adaptive multi-turn attack generation and independent, system-aware verdict judging, which none of the
  evaluated tools do.
