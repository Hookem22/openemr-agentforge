"""TargetAdapter -- the one interface every other agent talks through to reach the actual system
under test. See ARCHITECTURE.md's "Target Adapter Layer" section: everything OpenEMR-specific (auth
flow, request shape, PHI-field classification) lives behind this interface in a concrete adapter
(openemr_adapter.py), so exporting the platform to a different target means writing one new adapter,
not touching the Orchestrator/Red Team/Judge/Documentation agents at all.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.schemas import AttackSequence, ObservedResponse


@dataclass
class TargetProfile:
    """What an adapter tells the rest of the platform about the system it fronts -- endpoints, auth
    method, sensitive-field classification, rate limits. Read from target_profile.yaml, not
    hardcoded, so a new target only needs a new profile + adapter, not a code change to the agents
    that consume it."""

    target_id: str
    endpoints: dict[str, str]
    auth_method: str
    sensitive_fields: list[str] = field(default_factory=list)
    rate_limits: dict[str, float] = field(default_factory=dict)


class TargetAdapter(ABC):
    """Abstract interface. The ONLY component allowed to make a network call to the actual target --
    every other agent (Red Team, Judge, Orchestrator, Documentation) only ever sees AttackSequence/
    ObservedResponse, never the target's real request/response shape."""

    @abstractmethod
    def authenticate(self) -> None:
        """Establish (or refresh) whatever credential/session the adapter needs. Idempotent --
        safe to call again if a prior session expired."""

    @abstractmethod
    def send(self, attack: AttackSequence) -> ObservedResponse:
        """Deliver every turn in `attack` to the target, in order, and return what actually
        happened. Must not raise on a target-side failure (timeout, 4xx, 5xx) -- those are
        legitimate ObservedResponse statuses (see contracts/v1/observed_response.schema.json),
        since 'the target errored under this attack' is itself a finding, not a bug in the adapter."""

    @abstractmethod
    def describe(self) -> TargetProfile:
        """Return this adapter's TargetProfile -- read once at startup by the Orchestrator/Red Team
        agents to know what they're attacking, not re-fetched per attack."""
