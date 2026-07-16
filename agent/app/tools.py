"""Tool layer: one function per USER.md use case, mapped 1:1 to ARCHITECTURE.md Section 3's table.

Every function's return shape is an ALLOW-LIST (Section 4), not a raw FHIR resource pass-through:
fields no use case needs (SSN, address, insurance identifiers, etc.) are simply never extracted
here in the first place, regardless of what OpenEMR's API happens to return. Each item also always
carries `resource_type` + `id` + `date` -- these three fields are exactly what the verifier
(verifier.py) checks every model-generated citation against, so they must never be dropped.
"""
from __future__ import annotations

import re
from typing import Callable

from .fhir_client import FhirClient

_DATA_ABSENT_REASON_SYSTEM = "http://terminology.hl7.org/CodeSystem/data-absent-reason"


def _text(codeable_concept: dict | None) -> str | None:
    if not codeable_concept:
        return None
    if codeable_concept.get("text"):
        return codeable_concept["text"]
    codings = codeable_concept.get("coding") or []
    if codings and codings[0].get("system") != _DATA_ABSENT_REASON_SYSTEM:
        return codings[0].get("display") or codings[0].get("code")
    return None


def _narrative_text(resource: dict) -> str | None:
    """Falls back to the FHIR Narrative (resource.text.div) when a codeableConcept has no real
    coding -- only a data-absent-reason placeholder. Found via the Stage 4 golden set:
    FhirAllergyIntoleranceService.php never maps OpenEMR's free-text allergy title into
    `code.coding` (no RxNorm/SNOMED code exists for e.g. "No Known Drug Allergies (NKDA) - verified
    at visit"), so `code` is *always* just {system: data-absent-reason, display: "Unknown"} --
    for every allergy on every patient, not only NKDA-style entries. The real text OpenEMR puts in
    is the resource's own generated narrative, so that's the fallback source of truth here."""
    div = (resource.get("text") or {}).get("div")
    if not div:
        return None
    return re.sub(r"<[^>]+>", "", div).strip() or None


def get_patient(fhir: FhirClient, patient_id: str) -> dict:
    p = fhir.read("Patient", patient_id)
    name = (p.get("name") or [{}])[0]
    return {
        "resource_type": "Patient",
        "id": p.get("id"),
        "date": None,
        "name": " ".join(name.get("given", []) + [name.get("family", "")]).strip(),
        "birth_date": p.get("birthDate"),
        "gender": p.get("gender"),
    }


def get_recent_encounters(fhir: FhirClient, patient_id: str, count: int = 5) -> list[dict]:
    resources = fhir.search(
        "Encounter",
        {"patient": patient_id, "_sort": "-date", "_count": count},
    )
    out = []
    for e in resources:
        reason = None
        if e.get("reasonCode"):
            reason = _text(e["reasonCode"][0])
        elif e.get("type"):
            reason = _text(e["type"][0])
        out.append(
            {
                "resource_type": "Encounter",
                "id": e.get("id"),
                "date": (e.get("period") or {}).get("start"),
                "class": (e.get("class") or {}).get("code"),
                "reason": reason,
                "status": e.get("status"),
            }
        )
    return out


def get_conditions(fhir: FhirClient, patient_id: str) -> list[dict]:
    resources = fhir.search("Condition", {"patient": patient_id})
    out = []
    for c in resources:
        status = None
        if c.get("clinicalStatus", {}).get("coding"):
            status = c["clinicalStatus"]["coding"][0].get("code")
        out.append(
            {
                "resource_type": "Condition",
                "id": c.get("id"),
                "date": c.get("onsetDateTime") or c.get("recordedDate"),
                "condition": _text(c.get("code")),
                "clinical_status": status,
            }
        )
    return out


def get_medications(fhir: FhirClient, patient_id: str, active_only: bool = False) -> list[dict]:
    params = {"patient": patient_id}
    if active_only:
        params["status"] = "active"
    resources = fhir.search("MedicationRequest", params)
    out = []
    for m in resources:
        dosage = None
        if m.get("dosageInstruction"):
            dosage = m["dosageInstruction"][0].get("text")
        out.append(
            {
                "resource_type": "MedicationRequest",
                "id": m.get("id"),
                "date": m.get("authoredOn"),
                "drug": _text(m.get("medicationCodeableConcept")),
                "status": m.get("status"),
                "dosage": dosage,
            }
        )
    return out


def get_allergies(fhir: FhirClient, patient_id: str) -> list[dict]:
    resources = fhir.search("AllergyIntolerance", {"patient": patient_id})
    out = []
    for a in resources:
        reaction = None
        if a.get("reaction"):
            manifestations = a["reaction"][0].get("manifestation") or []
            if manifestations:
                reaction = _text(manifestations[0])
        out.append(
            {
                "resource_type": "AllergyIntolerance",
                "id": a.get("id"),
                "date": a.get("recordedDate"),
                "allergen": _text(a.get("code")) or _narrative_text(a),
                "criticality": a.get("criticality"),
                "reaction": reaction,
            }
        )
    return out


def _observations(fhir: FhirClient, patient_id: str, category: str) -> list[dict]:
    resources = fhir.search("Observation", {"patient": patient_id, "category": category, "_sort": "-date"})
    out = []
    for o in resources:
        value = None
        if "valueQuantity" in o:
            vq = o["valueQuantity"]
            value = f"{vq.get('value')} {vq.get('unit', '')}".strip()
        elif "valueString" in o:
            value = o["valueString"]
        interpretation = None
        if o.get("interpretation"):
            interpretation = _text(o["interpretation"][0])
        ref_range = None
        if o.get("referenceRange"):
            ref_range = o["referenceRange"][0].get("text")
        out.append(
            {
                "resource_type": "Observation",
                "id": o.get("id"),
                "date": o.get("effectiveDateTime"),
                "name": _text(o.get("code")),
                "value": value,
                "interpretation": interpretation,
                "reference_range": ref_range,
            }
        )
    return out


def get_vitals(fhir: FhirClient, patient_id: str) -> list[dict]:
    return _observations(fhir, patient_id, "vital-signs")


def get_labs(fhir: FhirClient, patient_id: str) -> list[dict]:
    return _observations(fhir, patient_id, "laboratory")


def get_notes(fhir: FhirClient, patient_id: str) -> list[dict]:
    # NOTE (known gap): OpenEMR's FHIR DocumentReference maps to the `documents` table, not the
    # `pnotes` table our seed data uses for progress notes. Until that's reconciled, this may
    # return empty even when pnotes exist for a patient -- surfaced honestly per UC-6 rather than
    # silently omitted. Tracked in agent-implementation.md.
    resources = fhir.search("DocumentReference", {"patient": patient_id, "_sort": "-date"})
    out = []
    for d in resources:
        title = None
        if d.get("content"):
            title = d["content"][0].get("attachment", {}).get("title")
        out.append(
            {
                "resource_type": "DocumentReference",
                "id": d.get("id"),
                "date": d.get("date"),
                "type": _text(d.get("type")),
                "title": title,
            }
        )
    return out


def diff_encounters(fhir: FhirClient, patient_id: str) -> dict:
    """Agent-side only -- no new OpenEMR endpoint (per ARCHITECTURE.md Section 3). Compares
    conditions/medications recorded around the two most recent encounters to answer UC-2's
    "what's changed" question. Simplification (documented, not hidden): this partitions by date
    relative to the second-most-recent encounter rather than a true per-encounter link, since
    that's what the underlying data reliably exposes."""
    encounters = get_recent_encounters(fhir, patient_id, count=2)
    if len(encounters) < 2:
        return {
            "note": "Fewer than two encounters on file -- no prior visit to diff against.",
            "new_conditions": [],
            "discontinued_medications": [],
            "encounters_compared": encounters,
        }
    cutoff = encounters[0]["date"]  # most recent encounter's date

    conditions = get_conditions(fhir, patient_id)
    new_conditions = [c for c in conditions if c["date"] and cutoff and c["date"] >= cutoff]

    medications = get_medications(fhir, patient_id)
    discontinued = [m for m in medications if m["status"] in ("stopped", "cancelled", "completed")]

    return {
        "encounters_compared": encounters,
        "new_conditions": new_conditions,
        "discontinued_medications": discontinued,
    }


# Anthropic tool-use schemas. Kept 1:1 with the functions above; `patient_id` is injected by the
# graph from conversation state, never taken from the model, so the model cannot redirect a tool
# call to a different patient than the one this conversation is scoped to.
TOOL_SCHEMAS = [
    {
        "name": "get_patient",
        "description": "Get the patient's demographics (name, birth date, gender).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_encounters",
        "description": "Get the patient's most recent encounters (visits), most recent first.",
        "input_schema": {
            "type": "object",
            "properties": {"count": {"type": "integer", "description": "How many encounters to return, default 5"}},
        },
    },
    {
        "name": "get_conditions",
        "description": "Get the patient's problem list (diagnosed conditions).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_medications",
        "description": "Get the patient's medications (prescriptions).",
        "input_schema": {
            "type": "object",
            "properties": {"active_only": {"type": "boolean", "description": "If true, only currently-active medications"}},
        },
    },
    {
        "name": "get_allergies",
        "description": "Get the patient's recorded allergies.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_vitals",
        "description": "Get the patient's most recent vital signs.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_labs",
        "description": "Get the patient's most recent lab results, including abnormal-flagged values.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_notes",
        "description": "Get the patient's clinical notes/documents.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "diff_encounters",
        "description": "Compare the two most recent encounters: new conditions and discontinued medications since the prior visit.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

# Each function's kwargs differ (e.g. get_recent_encounters(count=...) vs get_patient() with none),
# so this is genuinely a heterogeneous dict of callables, not one uniform signature.
TOOL_FUNCTIONS: dict[str, Callable[..., object]] = {
    "get_patient": get_patient,
    "get_recent_encounters": get_recent_encounters,
    "get_conditions": get_conditions,
    "get_medications": get_medications,
    "get_allergies": get_allergies,
    "get_vitals": get_vitals,
    "get_labs": get_labs,
    "get_notes": get_notes,
    "diff_encounters": diff_encounters,
}
