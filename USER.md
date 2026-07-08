# USER.md — Target User & Use Cases

## Target User

**ED resident, overnight intake shift.**

A resident physician working an overnight shift in the emergency department, seeing a steady stream of
walk-in and ambulance-arrival patients, most of whom are new to them and many of whom have an existing chart
in the system from prior visits (their own ED visits, or PCP/specialist visits if the ED shares the same
OpenEMR instance). Between rooms, the resident has **about 90 seconds** to look up a patient before walking
in: not enough time to page through years of encounter history, but enough time to ask a focused question and
get a sourced answer.

This differs from a PCP's continuity-of-care workflow (long-term relationship, scheduled visits, chronic
disease management) and from a hospitalist's workflow (multi-day inpatient admission, rounding on the same
patient repeatedly). It fits OpenEMR's actual data model well: OpenEMR represents care as discrete encounters
(`form_encounter`, with FHIR class code `EMER` for "Emergency Dept" already defined in
`sql/database.sql:5767`), not continuous multi-day inpatient stays — which matches how an ED resident actually
experiences a patient (one fresh, self-contained episode at a time), unlike a hospitalist who needs continuity
across a single admission spanning many days that OpenEMR doesn't model natively.

**Known limitation, stated up front:** if this patient is a true walk-in with no prior chart, there is no
record for the agent to ground answers in. The agent must say so plainly rather than inventing a summary —
this is itself a use case (see UC-6).

## Core Pain Point

**Pre-visit chart prep.** Before walking into the room, the resident needs the answer to: *"Who is this
patient, why might they be here, what's changed, what's on file, and what should I watch out for?"* — pulled
from the actual record, in seconds, not from re-reading the whole chart.

## Use Cases

Each use case below is scoped to data that already exists in OpenEMR's schema/services, so every one is
buildable against the real record — no capability here requires data OpenEMR doesn't already store.

### UC-1: Quick patient snapshot
"Who is this patient?" — demographics, last encounter date/reason, active problem list. Grounded in
`PatientService`, `EncounterService`, and the `lists` table (problems). Answer must cite the specific
encounter(s)/list entries it draws from.

### UC-2: What's changed since last visit
"What's new since I last saw them / since their last ED visit?" — diff between the two most recent encounters:
new diagnoses, new/discontinued medications, new lab results, new allergies. Grounded in `EncounterService`,
`ConditionService`, prescriptions, `AllergyIntoleranceService`, `ObservationLabService`. This is the highest-
value, highest-risk use case: every "changed" claim must point to the specific record (old value vs. new
value, with dates) rather than a paraphrased summary.

### UC-3: Current medications & allergies check
"What are they currently on, and what are they allergic to?" — active medication list + active allergy list,
surfaced together so the resident can immediately cross-check against whatever they're about to order/give.
Grounded in prescriptions/`MedicationRequest` data and `AllergyIntoleranceService`. Per the assignment's
"clinical constraints" requirement, this is where dosage-threshold and interaction-flag checks matter most —
the agent must flag rather than silently pass through a dangerous combination if that data exists in the
record, and must not infer an interaction that isn't backed by a flag/entry in the data.

### UC-4: Relevant labs & vitals at a glance
"What labs/vitals are on file, and are any abnormal?" — most recent vitals (`form_vitals`: BP, temp, pulse,
respiration, O2 sat, weight) and most recent lab results (`ObservationLabService`), with abnormal-flagged
values surfaced first. Grounded directly in stored flags/reference ranges, not the model's own judgment of
what counts as "abnormal."

### UC-5: Why might they be here tonight
"Given their history, what should I be thinking about for tonight's visit?" — surfaces relevant chronic
conditions, prior ED visit reasons, and recent notes (`pnotes`/documents) that plausibly relate to a
presenting complaint the resident enters (e.g., chest pain → surfaces cardiac history, not unrelated ortho
history). This is a retrieval/ranking use case, not a diagnostic one: the agent surfaces relevant existing
record entries, it does not suggest a diagnosis.

### UC-6: Honest "nothing here" / partial-data response
"What's on file for this patient?" when the chart is thin, empty, or a true first-time walk-in with no prior
OpenEMR record. The agent must explicitly state what's missing (e.g., "no prior encounters in this system,"
"no allergy information on file — not verified absent, just not recorded") rather than producing a
confident-sounding summary from nothing. This is a direct requirement of the assignment's Verification & Trust
constraint and is treated as a first-class use case, not an edge case bolted on later.

## Explicitly Out of Scope (for this persona/pain point)

- Multi-day inpatient rounding/continuity (hospitalist-style) — not pursued given OpenEMR's discrete-encounter
  model; noted above as the reason ED resident was chosen over hospitalist.
- In-visit real-time Q&A during the physical exam itself — pain point is pre-visit prep, not an in-room
  assistant.
- Diagnostic suggestion or treatment recommendation — the agent retrieves and surfaces record data; it does
  not practice medicine.
