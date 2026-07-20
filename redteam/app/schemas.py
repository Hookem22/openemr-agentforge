"""Pydantic mirror of contracts/v1/*.schema.json -- the JSON Schema is the source of truth (see
contracts/README.md); these models exist so redteam/ code gets real type-checking and validation
without hand-parsing JSON Schema at runtime. Field-for-field, these must match the checked-in JSON
Schema exactly -- contract drift between the two is exactly what contracts/tests/
test_contracts_match_schemas.py (added Wednesday, once there's a second version to test drift
against) exists to catch.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AttackCategory(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    DATA_EXFILTRATION = "data_exfiltration"
    STATE_CORRUPTION = "state_corruption"
    TOOL_MISUSE = "tool_misuse"
    DENIAL_OF_SERVICE = "denial_of_service"
    IDENTITY_ROLE_EXPLOITATION = "identity_role_exploitation"


class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    NOT_CONFIRMED = "not_confirmed"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AttackTurn(BaseModel):
    pid: int
    message: str
    conversation_history_override: list[dict[str, Any]] | None = None


class AttackSequence(BaseModel):
    attack_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    attack_category: AttackCategory
    owasp_llm_category: str | None = None
    target_id: str
    turns: list[AttackTurn]
    rationale: str = ""


class ObservedTurn(BaseModel):
    pid: int
    sent_message: str
    status: str  # "ok" | "http_error" | "timeout" | "auth_error" | "csrf_error"
    http_status: int | None = None
    response_text: str | None = None
    response_json: dict[str, Any] | None = None
    latency_ms: float | None = None


class ObservedResponse(BaseModel):
    attack_id: str
    target_id: str
    target_version: str
    turns: list[ObservedTurn]


class JudgeVerdict(BaseModel):
    attack_id: str
    verdict: Verdict
    rubric_version: str
    rationale: str
    severity: Severity | None = None
    evidence_quote: str | None = None


class ExploitRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_id: str
    target_version: str
    attack_category: AttackCategory
    rubric_version: str
    verdict: Verdict
    attack_sequence: AttackSequence
    observed_response: ObservedResponse
    judge_verdict: JudgeVerdict
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    regression_of: str | None = None


class CategoryCounts(BaseModel):
    confirmed: int = 0
    partial: int = 0
    not_confirmed: int = 0


class CoverageState(BaseModel):
    target_id: str
    target_version: str
    categories: dict[str, CategoryCounts]
    open_findings: int = 0


class NextTarget(BaseModel):
    target_id: str
    attack_category: AttackCategory
    escalate: bool = False
    rationale: str
    priority_score: float | None = None


class ReportStatus(str, Enum):
    DRAFT = "draft"
    AUTO_PUBLISHED = "auto_published"
    PENDING_APPROVAL = "pending_approval"
    PUBLISHED = "published"


class VulnerabilityReport(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    exploit_record_id: str
    severity: Severity
    description: str
    clinical_impact: str
    reproduction_steps: list[str]
    observed_behavior: str
    expected_behavior: str
    remediation_recommendation: str
    status: ReportStatus = ReportStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fix_validated_at: datetime | None = None
