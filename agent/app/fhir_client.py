"""Thin HTTP client for OpenEMR's FHIR R4 API.

Deliberately dumb: it does not know or care how the bearer token was obtained. Per
ARCHITECTURE.md's "authorization inheritance, not reimplementation" decision, every call rides
whatever token this client was constructed with straight through OpenEMR's existing
AuthorizationListener / BearerTokenAuthorizationStrategy enforcement — this class has no
knowledge of scopes, patients, or users beyond passing the token along.
"""
from __future__ import annotations

import json

import httpx

from .config import settings
from .retry import retry_idempotent_http


def _parse_json(resp: httpx.Response) -> dict:
    """OpenEMR occasionally emits a PHP warning as raw HTML ahead of the JSON body on certain
    malformed records (observed: FhirAllergyIntoleranceService.php foreach() on a non-array
    `reaction` field for some legacy rows) -- that's an upstream data/rendering bug, not something
    this client can fix. Treat "200 but not parseable JSON" as a tool failure (raises httpx.HTTPError
    so graph.py's existing tool_failures handling picks it up) rather than crashing the whole turn."""
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise httpx.HTTPError(f"non-JSON response from FHIR server (likely an upstream rendering bug): {exc}") from exc


class FhirClient:
    def __init__(self, bearer_token: str):
        if not bearer_token:
            raise ValueError("bearer_token is required (no anonymous/service-account access — see ARCHITECTURE.md)")
        self.bearer_token = bearer_token

    @retry_idempotent_http
    def search(self, resource_type: str, params: dict | None = None) -> list[dict]:
        """Runs a FHIR search and returns the raw list of resources (unwrapped from the Bundle).
        Returns an empty list for zero matches -- callers must treat that as a real, first-class
        case (UC-6), not an error. Retried on transient errors (retry.py) -- a GET, always safe."""
        resp = httpx.get(
            f"{settings.fhir_base_url}/{resource_type}",
            params=params or {},
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        bundle = _parse_json(resp)
        return [entry["resource"] for entry in bundle.get("entry", [])]

    @retry_idempotent_http
    def read(self, resource_type: str, resource_id: str) -> dict:
        resp = httpx.get(
            f"{settings.fhir_base_url}/{resource_type}/{resource_id}",
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return _parse_json(resp)
