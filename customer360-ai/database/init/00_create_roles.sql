-- Read-only role for the NL-to-SQL module.
-- Execute once on the managed database (docker-entrypoint-initdb.d runs this
-- automatically in the Docker development setup).
--
-- Generated SQL from NL-to-SQL must ONLY be executed as this role. It cannot
-- INSERT/UPDATE/DELETE/DDL because PostgreSQL grants read-only privileges here.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'customer360_ro') THEN
    CREATE ROLE customer360_ro;
  END IF;
END
$$;

GRANT CONNECT ON DATABASE customer360 TO customer360_ro;
GRANT USAGE ON SCHEMA public TO customer360_ro;

-- Read-only on all current and future tables.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO customer360_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO customer360_ro;

-- No write/execute privileges are granted, so DML/DDL is impossible for this role.

-- Example password (replace in production, provide via DATABASE_URL_RO):
--   ALTER ROLE customer360_ro WITH LOGIN PASSWORD 'change-me-ro-password';