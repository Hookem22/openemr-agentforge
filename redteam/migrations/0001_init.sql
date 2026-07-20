-- Exploit DB, v1. Follows the project's existing docs/seed-*.sql convention (plain versioned SQL
-- files, no Alembic) -- see redteam/migrations/README.md (added Wednesday, once the schema has
-- genuinely changed more than once) for the numbering/rollback convention.

CREATE TABLE IF NOT EXISTS exploit_records (
    id                 UUID PRIMARY KEY,
    target_id          TEXT NOT NULL,
    target_version     TEXT NOT NULL,
    attack_category     TEXT NOT NULL,
    rubric_version      TEXT NOT NULL,
    verdict             TEXT NOT NULL CHECK (verdict IN ('confirmed', 'partial', 'not_confirmed')),
    attack_sequence     JSONB NOT NULL,
    observed_response   JSONB NOT NULL,
    judge_verdict       JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    regression_of       UUID NULL REFERENCES exploit_records(id),

    -- Data-quality requirement (assignment: "no duplicate entries for the same attack sequence"):
    -- the natural key for "the same attack against the same target version" is this triple plus
    -- the attack's own id (attack_id lives inside attack_sequence, indexed separately below for
    -- query performance, but the uniqueness constraint here is what actually prevents dupes).
    CONSTRAINT exploit_records_natural_key UNIQUE (target_id, target_version, attack_category, id)
);

-- attack_id is nested in JSONB (attack_sequence->>'attack_id'), pulled out as a generated column so
-- it can be indexed/queried directly without a JSONB path expression on every call site.
ALTER TABLE exploit_records
    ADD COLUMN IF NOT EXISTS attack_id TEXT GENERATED ALWAYS AS (attack_sequence ->> 'attack_id') STORED;

CREATE UNIQUE INDEX IF NOT EXISTS exploit_records_attack_id_target_version_uq
    ON exploit_records (attack_id, target_id, target_version);

-- Orchestrator's coverage-gap query (added Tuesday) and Documentation's dedup check both filter on
-- this triple -- added now, at schema-creation time, not retrofitted after rows exist (Wednesday's
-- SQL-indexing step measures before/after on top of this baseline index).
CREATE INDEX IF NOT EXISTS exploit_records_coverage_idx
    ON exploit_records (target_id, target_version, attack_category);

CREATE INDEX IF NOT EXISTS exploit_records_verdict_idx
    ON exploit_records (verdict);
