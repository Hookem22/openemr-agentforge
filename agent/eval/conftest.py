"""Shared fixtures for the eval suite.

These tests hit the REAL agent (LangGraph + live Anthropic API calls) against a REAL local
OpenEMR FHIR API using the seeded sample patients (docs/seed-sample-patients.sql,
docs/seed-additional-patients.sql). This is deliberate: it's an eval suite proving the deployed
agent behaves correctly end-to-end, not a mocked unit-test harness. It costs real Anthropic tokens
per run and requires local prerequisites -- see eval/README.md.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.fhir_client import FhirClient
from app.graph import run_turn


@pytest.fixture(scope="session")
def bearer_token() -> str:
    if not settings.dev_bearer_token:
        pytest.skip("DEV_BEARER_TOKEN not set in agent/.env -- see agent/README.md to obtain one")
    return settings.dev_bearer_token


@pytest.fixture(scope="session")
def fhir(bearer_token: str) -> FhirClient:
    return FhirClient(bearer_token)


def _resolve_patient_id(fhir_client: FhirClient, family: str, given: str) -> str:
    results = fhir_client.search("Patient", {"family": family, "given": given})
    if not results:
        pytest.skip(
            f"Seed patient {given} {family} not found via FHIR search -- run "
            "docs/seed-sample-patients.sql and docs/seed-additional-patients.sql first"
        )
    return results[0]["id"]


@pytest.fixture(scope="session")
def maria_id(fhir: FhirClient) -> str:
    """Patient A: rich chart -- new-onset afib, new/discontinued meds, penicillin allergy,
    abnormal lab, a note. Exercises UC-1 through UC-5."""
    return _resolve_patient_id(fhir, "Gonzalez", "Maria")


@pytest.fixture(scope="session")
def james_id(fhir: FhirClient) -> str:
    """Patient B: thin chart -- demographics only, zero encounters/history. Exercises UC-6
    ('truly unrecorded')."""
    return _resolve_patient_id(fhir, "Whitfield", "James")


@pytest.fixture(scope="session")
def robert_id(fhir: FhirClient) -> str:
    """Patient C: unrelated chronic condition (knee) alongside cardiac history, plus a deliberate,
    unflagged sulfa-allergy/sulfa-antibiotic conflict. Exercises UC-3 (clinical constraints) and
    UC-5 (relevance filtering)."""
    return _resolve_patient_id(fhir, "Chen", "Robert")


@pytest.fixture(scope="session")
def dorothy_id(fhir: FhirClient) -> str:
    """Patient D: stale (>2yr) single encounter, plus an explicit 'No Known Drug Allergies -
    verified at visit' entry. Exercises the 'verified absent' vs. 'not recorded' distinction
    within UC-6."""
    return _resolve_patient_id(fhir, "Simmons", "Dorothy")


@pytest.fixture
def chat(bearer_token: str):
    """Runs one real chat turn through the full LangGraph pipeline (agent -> tools -> verify)."""

    def _chat(patient_id: str, message: str, prior_messages: list[dict] | None = None):
        return run_turn(
            patient_id=patient_id,
            bearer_token=bearer_token,
            user_message=message,
            prior_messages=prior_messages or [],
        )

    return _chat
