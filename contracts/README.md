# Inter-Agent Message Contracts

Versioned JSON Schema for every message that crosses an agent-to-agent trust boundary in the adversarial
platform (`ARCHITECTURE.md`). `redteam/app/schemas.py` mirrors these 1:1 as Pydantic models — the JSON Schema
here is the source of truth; `schemas.py` is validated against it by `contracts/tests/test_contracts_match_schemas.py`
(added once the Documentation/Orchestrator agents exist and there's a second schema version to actually test
drift against).

## Versioning policy

- Directory-per-version (`v1/`, `v2/`, ...). A schema only gets a new top-level directory on a **breaking**
  change (removing/renaming a required field, changing a field's type, narrowing an enum). Additive changes
  (a new optional field) stay in the current version.
- Every breaking change requires: a new `v{n}/` directory, a migration note (added to this file once the
  first breaking change actually happens — not written speculatively in the abstract), and updated contract
  tests proving both the old and new producer/consumer sides.
- `v1` currently covers the 4 schemas exercised by the Red Team + Judge prototype:
  `attack_sequence`, `observed_response`, `judge_verdict`, `exploit_record`. `next_target` and
  `coverage_state` (Orchestrator's inputs/outputs) are added once the Orchestrator Agent is built — writing
  a schema for an agent that doesn't exist yet risks describing an interface that turns out wrong once the
  agent is actually implemented.

## Schemas (v1)

| Schema | Producer | Consumer(s) | Notes |
|---|---|---|---|
| `attack_sequence.schema.json` | Red Team Agent | Target Adapter | Multi-turn; `turns[].pid` is deliberately part of the contract, since an IDOR attack is exactly a turn whose `pid` doesn't match the authorized patient. |
| `observed_response.schema.json` | Target Adapter | Red Team Agent, Judge Agent | The only thing the Judge ever sees about what happened. |
| `judge_verdict.schema.json` | Judge Agent | Exploit DB, Documentation Agent | No field exists for the Red Team Agent's own reasoning — the isolation is enforced by the Judge's implementation never reading it, not by the schema, but the schema has nowhere to smuggle it in either. |
| `exploit_record.schema.json` | (composed by the graph from the three above) | Exploit DB, Documentation Agent | The versioned regression-store row shape; embeds the other three by reference. |

## Error schemas

Typed error schemas per agent failure mode (target unreachable, budget exceeded, judge timeout, no findings
in window, regression detected) are planned for Thursday per the week's build plan — not yet in this
directory, since the failure modes are clearer once the full 4-agent loop exists.
