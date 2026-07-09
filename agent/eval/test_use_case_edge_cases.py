"""UC-6 ('honest nothing-here response') edge cases, split across two seeded patients that look
similar at a glance but must be handled differently:

- James Whitfield: TRULY unrecorded data (zero rows at all) -- every claim must be no_data-sourced.
- Dorothy Simmons: an EXPLICIT verified-absent finding ("No Known Drug Allergies - verified at
  visit") -- a real, positive AllergyIntolerance-shaped resource, not an absence of rows. Reporting
  this as a generic no_data claim would be equivalent to erasing the fact that someone actually
  checked and documented it, which is clinically meaningful and different from "never asked."
"""
from __future__ import annotations


def test_empty_chart_produces_only_no_data_claims(chat, james_id):
    """Failure mode guarded: James has zero encounters/clinical history at all -- the only real
    data on file for him is his demographics (the Patient resource itself genuinely exists and is
    legitimately citable). A model that fabricates a plausible-sounding CLINICAL summary anyway
    (the single most dangerous failure mode for an empty chart) must be caught -- every non-Patient
    claim about him should be sourced as no_data, not a fabricated presence claim that slipped past
    the verifier."""
    result = chat(james_id, "Give me a quick snapshot of this patient.")

    for claim in result["verified_claims"]:
        source = claim.get("source", {})
        if source.get("resource_type") == "Patient":
            continue  # demographics are real, legitimately-sourced data, not a fabrication
        assert source.get("type") == "no_data", (
            f"Expected only no_data claims beyond demographics for an empty chart, got: {claim}"
        )


def test_empty_chart_does_not_silently_drop_to_zero_claims(chat, james_id):
    """Boundary guarded: the honest response for an empty chart is 'nothing on file', not silence.
    If the agent asks about James and gets zero verified claims AND zero stripped claims, that's
    indistinguishable from a broken/no-op turn -- there must be at least the no_data acknowledgment
    claims, not nothing at all."""
    result = chat(james_id, "Give me a quick snapshot of this patient.")

    assert len(result["verified_claims"]) > 0


def test_verified_absent_allergy_is_not_reported_as_no_data(chat, dorothy_id):
    """Failure mode guarded: Dorothy has an explicit 'No Known Drug Allergies - verified at visit'
    list entry -- a real AllergyIntolerance-shaped resource recorded by a clinician, distinct from
    James's total absence of any allergy row. Reporting this as a no_data claim (implying nobody
    ever checked) discards clinically meaningful information -- someone explicitly verified and
    documented the absence. The claim about her allergies must cite the actual resource, not a
    no_data marker."""
    result = chat(dorothy_id, "Does this patient have any known drug allergies?")

    allergy_claims = [
        c for c in result["verified_claims"]
        if "allerg" in c["text"].lower()
    ]
    assert allergy_claims, "Expected at least one verified claim about Dorothy's allergy status"
    assert all(c["source"].get("type") != "no_data" for c in allergy_claims), (
        "Dorothy's verified-absent NKDA entry was reported as no_data (unrecorded) instead of "
        f"citing the real resource: {allergy_claims}"
    )
