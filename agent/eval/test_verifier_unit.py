"""Pure unit tests for the deterministic verifier (app/verifier.py) -- no LLM calls, no FHIR
calls, no network. These are the cheapest, fastest, most reliable tests in the suite and cover the
core invariant the whole verification layer exists to guarantee: a claim only reaches the
clinician if its cited source actually corresponds to data fetched THIS turn.
"""
from __future__ import annotations

from app.verifier import verify_claims


def test_claim_with_matching_source_is_verified():
    """Happy path baseline: a claim whose source exactly matches a resource fetched this turn."""
    tool_results = [{"resource_type": "Condition", "id": "cond-1"}]
    claims = [{"text": "Patient has diabetes.", "source": {"resource_type": "Condition", "resource_id": "cond-1"}}]

    result = verify_claims(claims, tool_results)

    assert result.verified_claims == claims
    assert result.stripped_claims == []


def test_claim_with_unresolved_source_is_stripped():
    """Failure mode guarded: the model cites a resource_id that was never actually fetched this
    turn (a hallucinated citation). Must be stripped, not trusted just because it looks well-formed."""
    tool_results = [{"resource_type": "Condition", "id": "cond-1"}]
    claims = [{"text": "Patient is allergic to X.", "source": {"resource_type": "AllergyIntolerance", "resource_id": "allergy-999"}}]

    result = verify_claims(claims, tool_results)

    assert result.verified_claims == []
    assert len(result.stripped_claims) == 1
    assert "does not match any resource actually fetched" in result.stripped_claims[0]["reason"]


def test_claim_missing_source_entirely_is_stripped():
    """Failure mode guarded: malformed/absent source dict. Must fail closed (stripped), not be
    treated as automatically trustworthy just because a KeyError didn't occur."""
    claims = [{"text": "Patient is fine.", "source": {}}]

    result = verify_claims(claims, [])

    assert result.verified_claims == []
    assert len(result.stripped_claims) == 1


def test_no_data_claim_confirmed_by_empty_marker_is_verified():
    """UC-6 invariant: a 'nothing on file' claim is only trusted if a tool call this turn actually
    returned empty for that resource type -- not merely because the model asserts it."""
    tool_results = [{"resource_type": "AllergyIntolerance", "_empty_marker": True}]
    claims = [{"text": "No allergy information on file.", "source": {"type": "no_data", "resource_type": "AllergyIntolerance"}}]

    result = verify_claims(claims, tool_results)

    assert result.verified_claims == claims
    assert result.stripped_claims == []


def test_no_data_claim_without_empty_marker_is_stripped():
    """Failure mode guarded: model claims absence of data (e.g. 'no medications on file') for a
    resource type it never actually checked this turn (no get_medications call happened). Must be
    stripped -- an untested absence claim is exactly as ungrounded as a fabricated presence claim."""
    claims = [{"text": "No medications on file.", "source": {"type": "no_data", "resource_type": "MedicationRequest"}}]

    result = verify_claims(claims, [])

    assert result.verified_claims == []
    assert len(result.stripped_claims) == 1
    assert "not confirmed by an actual empty tool result" in result.stripped_claims[0]["reason"]


def test_empty_claims_list_does_not_crash_and_has_zero_strip_rate():
    """Boundary: zero claims (e.g. a malformed/truncated provide_answer call) must not raise a
    ZeroDivisionError on strip_rate, and must not fabricate a verified/stripped claim out of nothing."""
    result = verify_claims([], [])

    assert result.verified_claims == []
    assert result.stripped_claims == []
    assert result.strip_rate == 0.0


def test_multiple_claims_citing_the_same_resource_all_verify():
    """Boundary: more than one claim legitimately citing the same fetched resource (e.g. two
    sentences both referencing the same Condition) is not a one-claim-per-resource limit."""
    tool_results = [{"resource_type": "Condition", "id": "cond-1"}]
    claims = [
        {"text": "Claim A about the condition.", "source": {"resource_type": "Condition", "resource_id": "cond-1"}},
        {"text": "Claim B about the same condition.", "source": {"resource_type": "Condition", "resource_id": "cond-1"}},
    ]

    result = verify_claims(claims, tool_results)

    assert len(result.verified_claims) == 2
    assert result.stripped_claims == []


def test_resource_type_mismatch_with_matching_id_is_stripped():
    """Failure mode guarded: a claim citing the right resource_id but the wrong resource_type
    (e.g. citing a Condition's id as if it were a MedicationRequest) must not verify just because
    the id string happens to match -- both fields are part of the citation key."""
    tool_results = [{"resource_type": "Condition", "id": "shared-id-1"}]
    claims = [{"text": "Patient takes a drug for this.", "source": {"resource_type": "MedicationRequest", "resource_id": "shared-id-1"}}]

    result = verify_claims(claims, tool_results)

    assert result.verified_claims == []
    assert len(result.stripped_claims) == 1


# --- W2_ARCHITECTURE.md Section 5 migration: unified (document/guideline) citation shape ---


def test_document_sourced_claim_matching_an_extracted_fact_is_verified():
    """Stage 3 happy path: a claim citing a fact the intake-extractor worker actually produced this
    turn (source_type='document') is verified the same way an FHIR claim is."""
    extracted_facts = [{"text": "A1c: 8.2%", "citation": {"source_type": "document", "source_id": "doc-1", "field_or_chunk_id": "results[0]"}}]
    claims = [{"text": "The patient's A1c is 8.2%.", "source": {"source_type": "document", "source_id": "doc-1", "field_or_chunk_id": "results[0]"}}]

    result = verify_claims(claims, [], extracted_facts=extracted_facts)

    assert result.verified_claims == claims
    assert result.stripped_claims == []


def test_guideline_sourced_claim_matching_an_evidence_snippet_is_verified():
    """Same happy path for the evidence-retriever worker's output (source_type='guideline')."""
    evidence_snippets = [{"text": "Target A1c below 7%.", "citation": {"source_type": "guideline", "source_id": "ada_diabetes_standards", "field_or_chunk_id": "ada#glycemic-targets"}}]
    claims = [{"text": "Guideline target A1c is below 7%.", "source": {"source_type": "guideline", "source_id": "ada_diabetes_standards", "field_or_chunk_id": "ada#glycemic-targets"}}]

    result = verify_claims(claims, [], evidence_snippets=evidence_snippets)

    assert result.verified_claims == claims
    assert result.stripped_claims == []


def test_document_sourced_claim_with_hallucinated_field_id_is_stripped():
    """Failure mode guarded: the model cites a real document but a field/chunk id that was never
    actually extracted this turn -- must not verify just because source_id matches."""
    extracted_facts = [{"text": "A1c: 8.2%", "citation": {"source_type": "document", "source_id": "doc-1", "field_or_chunk_id": "results[0]"}}]
    claims = [{"text": "The patient's LDL is 190.", "source": {"source_type": "document", "source_id": "doc-1", "field_or_chunk_id": "results[99]"}}]

    result = verify_claims(claims, [], extracted_facts=extracted_facts)

    assert result.verified_claims == []
    assert len(result.stripped_claims) == 1
    assert "does not match any extracted fact or evidence snippet" in result.stripped_claims[0]["reason"]


def test_no_guideline_evidence_found_is_a_verifiable_no_data_claim():
    """W2_ARCHITECTURE.md Section 10: when evidence_retriever finds zero chunks, the model must be
    able to make a verified 'no guideline evidence found' claim rather than fabricating guidance or
    the claim being stripped for lacking a resource citation."""
    claims = [{"text": "No guideline evidence found for this query.", "source": {"type": "no_data", "resource_type": "guideline"}}]

    result = verify_claims(claims, [], empty_evidence=True)

    assert result.verified_claims == claims
    assert result.stripped_claims == []


def test_no_data_guideline_claim_without_empty_evidence_flag_is_stripped():
    """Failure mode guarded: the model claims 'no evidence found' without evidence_retriever having
    actually run and confirmed an empty result this turn -- same fail-closed pattern as any other
    no_data claim."""
    claims = [{"text": "No guideline evidence found.", "source": {"type": "no_data", "resource_type": "guideline"}}]

    result = verify_claims(claims, [], empty_evidence=False)

    assert result.verified_claims == []
    assert len(result.stripped_claims) == 1


def test_fhir_verification_path_is_unaffected_by_new_optional_arguments():
    """Backward-compatibility check for the Section 5 migration note: existing FHIR-citation
    behavior must be byte-for-byte identical whether or not the new extracted_facts/
    evidence_snippets arguments are passed."""
    tool_results = [{"resource_type": "Condition", "id": "cond-1"}]
    claims = [{"text": "Patient has diabetes.", "source": {"resource_type": "Condition", "resource_id": "cond-1"}}]

    with_new_args = verify_claims(claims, tool_results, extracted_facts=[], evidence_snippets=[], empty_evidence=False)
    without_new_args = verify_claims(claims, tool_results)

    assert with_new_args.verified_claims == without_new_args.verified_claims == claims
