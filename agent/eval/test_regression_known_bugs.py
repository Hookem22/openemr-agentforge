"""Regression guards for two specific, already-documented upstream OpenEMR bugs
(agent-implementation.md). Neither test asserts the bug is fixed -- both assert the agent degrades
gracefully (tool_failure, not a crashed turn) whether or not it currently is, and are meant to catch
NEW regressions: if either bug's failure mode changes shape (e.g. get_notes starts raising instead
of returning empty), that's a signal worth investigating even though this suite still "passes" on
the crash-safety assertion.
"""
from __future__ import annotations


def test_maria_allergy_lookup_does_not_crash_the_turn(chat, maria_id):
    """Regression guarded: FhirAllergyIntoleranceService.php:175 does `foreach()` over a `reaction`
    field that is sometimes a scalar, not a list -- Maria's seeded Penicillin allergy ("Rash"
    reaction) can trigger this. Either the upstream bug is fixed (allergy data comes back normally)
    or it's still broken (recorded as a tool_failure via the existing FhirClient JSON-decode-error
    path) -- either is acceptable. An unhandled exception that crashes the whole chat turn is not."""
    result = chat(maria_id, "What allergies does this patient have?")

    assert isinstance(result["tool_failures"], list)
    assert isinstance(result["verified_claims"], list)


def test_maria_notes_lookup_returns_empty_not_error(chat, maria_id):
    """Regression guarded: OpenEMR's FHIR DocumentReference resource maps to the `documents` table,
    not the `pnotes` table Maria's seeded clinical note actually lives in -- so get_notes is
    expected to come back empty (not find her note), and that emptiness must be a graceful
    no-data-found result, not a tool_failure. If this ever starts raising a tool_failure instead of
    returning gracefully-empty, that's a NEW, separate regression worth flagging even though this
    test doesn't assert notes are actually found."""
    result = chat(maria_id, "Are there any clinical notes for this patient?")

    note_tool_failures = [
        f for f in result["tool_failures"] if "note" in str(f).lower()
    ]
    assert note_tool_failures == [], (
        f"get_notes raised a tool_failure instead of gracefully returning empty: {note_tool_failures}"
    )
