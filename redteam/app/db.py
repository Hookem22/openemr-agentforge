"""Exploit DB access -- Postgres via DATABASE_URL. Deliberately thin: no ORM, just psycopg2 +
parameterized SQL, matching this project's existing preference for plain SQL over an ORM layer
(see docs/seed-*.sql). Access-control model (per-agent Postgres roles/grants) is added Wednesday,
once there's more than one agent writing to this table -- see ARCHITECTURE.md.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

from app.config import settings
from app.schemas import AttackCategory, CategoryCounts, CoverageState, ExploitRecord, Verdict, VulnerabilityReport

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


@contextmanager
def get_connection() -> Iterator["psycopg2.extensions.connection"]:
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set -- redteam/.env needs the Postgres connection string "
            "(see redteam/.env.example)."
        )
    conn = psycopg2.connect(settings.database_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migrations() -> list[str]:
    """Applies every migrations/*.sql file in filename order. No migration-tracking table yet
    (single-migration schema so far) -- each file uses CREATE TABLE IF NOT EXISTS / ADD COLUMN IF
    NOT EXISTS, so re-running is a safe no-op. A real applied-migrations ledger is added Wednesday
    per redteam/migrations/README.md, once the schema has changed more than once."""
    applied = []
    for filename in sorted(os.listdir(_MIGRATIONS_DIR)):
        if not filename.endswith(".sql"):
            continue
        path = os.path.join(_MIGRATIONS_DIR, filename)
        with open(path) as f:
            sql = f.read()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        applied.append(filename)
    return applied


def insert_exploit_record(record: ExploitRecord) -> None:
    """Insert-or-ignore on the natural key (attack_id, target_id, target_version) -- a duplicate
    insert of the exact same attempt is silently a no-op, not an error, since a retried eval run
    shouldn't create a second row for the same attack (data-quality: no-dupes requirement)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exploit_records
                    (id, target_id, target_version, attack_category, rubric_version, verdict,
                     attack_sequence, observed_response, judge_verdict, created_at, regression_of)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (attack_id, target_id, target_version) DO NOTHING
                """,
                (
                    record.id,
                    record.target_id,
                    record.target_version,
                    record.attack_category.value,
                    record.rubric_version,
                    record.verdict.value,
                    json.dumps(record.attack_sequence.model_dump(mode="json")),
                    json.dumps(record.observed_response.model_dump(mode="json")),
                    json.dumps(record.judge_verdict.model_dump(mode="json")),
                    record.created_at,
                    record.regression_of,
                ),
            )


def coverage_by_category(target_id: str, target_version: str) -> dict[str, dict[str, int]]:
    """Per-category counts of confirmed/partial/not_confirmed rows -- what the Orchestrator Agent
    reads to decide which category is under-covered. Returns every enum category, including ones
    with zero rows, so a category with no attempts yet reads as a clear coverage gap rather than
    silently missing from the result."""
    counts: dict[str, dict[str, int]] = {
        cat.value: {"confirmed": 0, "partial": 0, "not_confirmed": 0} for cat in AttackCategory
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT attack_category, verdict, count(*)
                FROM exploit_records
                WHERE target_id = %s AND target_version = %s
                GROUP BY attack_category, verdict
                """,
                (target_id, target_version),
            )
            for category, verdict, count in cur.fetchall():
                counts.setdefault(category, {"confirmed": 0, "partial": 0, "not_confirmed": 0})
                counts[category][verdict] = count
    return counts


def count_open_findings(target_id: str, target_version: str) -> int:
    """Confirmed/partial exploit_records with no vulnerability_report row yet -- the Orchestrator's
    'unresolved findings' signal, distinct from coverage-by-category (which counts attempts, not
    resolution state)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM exploit_records er
                WHERE er.target_id = %s AND er.target_version = %s
                  AND er.verdict IN ('confirmed', 'partial')
                  AND NOT EXISTS (
                      SELECT 1 FROM vulnerability_reports vr WHERE vr.exploit_record_id = er.id
                  )
                """,
                (target_id, target_version),
            )
            (count,) = cur.fetchone()
    return count


def get_coverage_state(target_id: str, target_version: str) -> CoverageState:
    """Composes coverage_by_category + count_open_findings into the single CoverageState object
    the Orchestrator Agent actually reads (contracts/v1/coverage_state.schema.json) -- one place
    that knows how to assemble this, rather than every caller re-deriving it from two functions."""
    raw_categories = coverage_by_category(target_id, target_version)
    return CoverageState(
        target_id=target_id,
        target_version=target_version,
        categories={cat: CategoryCounts(**counts) for cat, counts in raw_categories.items()},
        open_findings=count_open_findings(target_id, target_version),
    )


def report_exists_for_exploit(exploit_record_id: str) -> bool:
    """Data-quality pre-check the Documentation Agent runs before writing -- the real guarantee is
    the DB's own UNIQUE constraint (migrations/0002_documentation.sql), this just lets the agent
    fail fast with a clear reason instead of a raw constraint-violation exception."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM vulnerability_reports WHERE exploit_record_id = %s", (exploit_record_id,)
            )
            return cur.fetchone() is not None


def insert_vulnerability_report(report: VulnerabilityReport) -> None:
    """Insert-or-ignore on exploit_record_id -- matches insert_exploit_record's dedup discipline.
    Required-field presence and severity/status enum validity are already enforced by
    VulnerabilityReport's own Pydantic validation before this is ever called (schemas.py), so this
    function's only remaining job is the no-duplicate-per-exploit guarantee."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vulnerability_reports
                    (id, exploit_record_id, severity, description, clinical_impact,
                     reproduction_steps, observed_behavior, expected_behavior,
                     remediation_recommendation, status, created_at, fix_validated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (exploit_record_id) DO NOTHING
                """,
                (
                    report.id,
                    report.exploit_record_id,
                    report.severity.value,
                    report.description,
                    report.clinical_impact,
                    json.dumps(report.reproduction_steps),
                    report.observed_behavior,
                    report.expected_behavior,
                    report.remediation_recommendation,
                    report.status.value,
                    report.created_at,
                    report.fix_validated_at,
                ),
            )
