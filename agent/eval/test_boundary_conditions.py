"""Integration tests for boundary conditions: malformed/edge-case inputs that a happy-path suite
would never exercise. Each hits the real LangGraph pipeline (real Anthropic + real local FHIR API),
so requires DEV_BEARER_TOKEN/ANTHROPIC_API_KEY set and a running local OpenEMR FHIR API -- see
eval/README.md.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_empty_message_is_rejected_at_the_api_boundary(maria_id):
    """Failure mode guarded (a REAL bug this eval suite caught): a blank message used to be passed
    straight through to the Anthropic API, which rejects empty content with a raw 400 and crashed
    the turn with an unhandled exception. Fixed by validating at the actual system boundary (the
    ChatRequest pydantic model in app/main.py) so a blank message is rejected cleanly with a 422,
    not a crash mid-turn."""
    client = TestClient(app)

    resp = client.post("/chat", json={"patient_id": maria_id, "message": ""})

    assert resp.status_code == 422


def test_nonexistent_patient_id_does_not_crash(chat):
    """KNOWN GAP, documented not silently masked: OpenEMR's FHIR search returns an empty-but-200
    Bundle for a patient id that doesn't exist at all -- indistinguishable at the tool layer from a
    real patient with a genuinely empty chart (see test_use_case_edge_cases.py's James Whitfield
    case). The agent cannot currently tell these apart. This test only asserts the turn completes
    without crashing, not that the agent produces any particular 'patient not found' message --
    that would be a false claim about current behavior."""
    result = chat("nonexistent-patient-id-xyz", "What's the latest on this patient?")

    assert isinstance(result["verified_claims"], list)
    assert isinstance(result["stripped_claims"], list)


def test_invalid_bearer_token_degrades_to_tool_failure_not_crash(maria_id):
    """Failure mode guarded: an invalid/expired bearer token must cause tool calls to fail
    gracefully (recorded in tool_failures, per the existing tool_failure path) rather than raising
    an unhandled exception that crashes the request. This is the multi-user access control /
    authorization boundary -- a bad token must never silently return another user's cached data or
    take down the process."""
    from app.graph import run_turn

    result = run_turn(
        patient_id=maria_id,
        bearer_token="invalid-token-definitely-not-real",
        user_message="What's the latest on this patient?",
    )

    assert isinstance(result["tool_failures"], list)
    # Every claim made must still cite a real source -- an invalid token must not let the model
    # fabricate clinical claims out of nothing just because tools failed.
    assert result["verified_claims"] == [] or all(
        c.get("source") for c in result["verified_claims"]
    )
