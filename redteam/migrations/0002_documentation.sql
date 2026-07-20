-- Adds vulnerability_reports, the Documentation Agent's output table. Second real schema change --
-- see redteam/migrations/README.md (added alongside this, since there are now two migrations to
-- have a real convention for) for the numbering/rollback story.

CREATE TABLE IF NOT EXISTS vulnerability_reports (
    id                          UUID PRIMARY KEY,
    exploit_record_id           UUID NOT NULL REFERENCES exploit_records(id),
    severity                    TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    description                 TEXT NOT NULL,
    clinical_impact             TEXT NOT NULL,
    reproduction_steps          JSONB NOT NULL,
    observed_behavior           TEXT NOT NULL,
    expected_behavior           TEXT NOT NULL,
    remediation_recommendation  TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'draft'
                                    CHECK (status IN ('draft', 'auto_published', 'pending_approval', 'published')),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    fix_validated_at            TIMESTAMPTZ NULL,

    -- Data-quality requirement: one report per exploit -- a re-run of the Documentation Agent on
    -- the same exploit_record must not create a second report (checked in code before insert too,
    -- see documentation_agent.py's validate_report(), but the DB constraint is the real guarantee).
    CONSTRAINT vulnerability_reports_one_per_exploit UNIQUE (exploit_record_id)
);

-- Coverage-dashboard / Orchestrator's "open findings" query (confirmed/partial exploits with no
-- report yet, or a report not yet published) filters by status -- added now, at schema-creation
-- time, same discipline as 0001_init.sql's coverage index.
CREATE INDEX IF NOT EXISTS vulnerability_reports_status_idx ON vulnerability_reports (status);
CREATE INDEX IF NOT EXISTS vulnerability_reports_severity_idx ON vulnerability_reports (severity);
