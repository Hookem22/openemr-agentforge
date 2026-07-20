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
from app.schemas import AttackCategory, ExploitRecord, Verdict

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")


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
