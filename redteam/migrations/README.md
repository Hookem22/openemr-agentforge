# Exploit DB Migrations

Plain, numbered SQL files (`000N_description.sql`), applied in filename order by
`app.db.run_migrations()` — no Alembic/migration framework, matching this project's existing
preference for plain SQL over an ORM layer (`docs/seed-*.sql`).

## Convention

- Every file uses `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT
  EXISTS`, so re-running the full set is always a safe no-op — there is no separate
  "already-applied" ledger table. This is deliberately the simplest thing that works for a project
  at this scale and lifespan; if the schema needs a real destructive change (renaming or dropping a
  column with existing data) that this pattern can't express safely, that's the trigger to
  introduce an applied-migrations table, not before.
- **Additive-only so far** (`0001_init.sql` created `exploit_records`; `0002_documentation.sql`
  added `vulnerability_reports` with a foreign key to it) — no existing column has been renamed or
  removed yet, so no data-migration/backfill story has been needed for real. When one is, it gets
  documented here as it actually happens, not speculated about in advance.
- Every new table's data-quality constraints (unique keys, required fields via `NOT NULL`, `CHECK`
  enums) are added in the same migration that creates the table — not retrofitted after rows exist,
  since that's the point at which adding a constraint is cheap versus expensive.

## Rollback

No down-migrations exist. Given the additive-only history so far, rolling back means restoring from
a database snapshot (Railway's Postgres addon takes these automatically) rather than running a
generated inverse script — a real down-migration story is only worth building once a migration
actually needs to be rolled back in practice, not before.
