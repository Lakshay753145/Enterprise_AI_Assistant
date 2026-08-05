-- ===========================================================================
-- Aerolloy Enterprise AI Assistant - database roles
--
-- Run this ONCE as a Postgres superuser, AFTER `alembic upgrade head`.
--
--   psql -U postgres -d Enterprise_AI -f scripts/setup_db_roles.sql
--
-- What this does and why it matters
-- ---------------------------------
-- The SQL agent lets an LLM write SQL. That is useful and it is dangerous.
-- The mitigation is not "prompt the model not to be naughty" - it is to make
-- damage impossible at the database level:
--
--   * ai_readonly can log in but owns nothing
--   * it has NO privileges on any base table (users, documents, chunks, ...)
--   * it can SELECT from exactly the kb_<dept>_* views, each of which
--     hard-codes its department in a WHERE clause
--   * every one of its transactions is read-only
--
-- So the worst a fully compromised prompt can achieve is reading rows the
-- signed-in user was already entitled to read.
-- ===========================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- 1. The role
-- ---------------------------------------------------------------------------
-- CHANGE THIS PASSWORD and mirror it in SQL_AGENT_DATABASE_URL in your .env
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_readonly') THEN
        CREATE ROLE ai_readonly LOGIN PASSWORD 'CHANGE_ME_READONLY_PASSWORD';
    END IF;
END
$$;

ALTER ROLE ai_readonly SET default_transaction_read_only = on;
ALTER ROLE ai_readonly SET statement_timeout = '15s';
ALTER ROLE ai_readonly NOCREATEDB NOCREATEROLE NOSUPERUSER NOINHERIT;

-- ---------------------------------------------------------------------------
-- 2. Revoke everything, then grant back only what is needed
-- ---------------------------------------------------------------------------
REVOKE ALL ON SCHEMA public FROM ai_readonly;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ai_readonly;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ai_readonly;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM ai_readonly;

GRANT CONNECT ON DATABASE "Enterprise_AI" TO ai_readonly;
GRANT USAGE ON SCHEMA public TO ai_readonly;

-- Only the department-scoped views.
GRANT SELECT ON kb_finance_documents,    kb_finance_chunks    TO ai_readonly;
GRANT SELECT ON kb_hr_documents,         kb_hr_chunks         TO ai_readonly;
GRANT SELECT ON kb_it_documents,         kb_it_chunks         TO ai_readonly;
GRANT SELECT ON kb_production_documents, kb_production_chunks TO ai_readonly;
GRANT SELECT ON kb_purchase_documents,   kb_purchase_chunks   TO ai_readonly;

-- Future tables must not be reachable by default.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM ai_readonly;

-- ---------------------------------------------------------------------------
-- 3. Views must run as their owner
-- ---------------------------------------------------------------------------
-- The base tables have RLS FORCEd on. A view executes with the privileges of
-- its owner, so the owner must be a role that RLS lets through - and the view
-- itself supplies the department predicate. security_barrier (set in the
-- migration) stops the planner from pushing a user-supplied function below the
-- WHERE clause, which is the classic way to leak rows out of a view.
DO $$
DECLARE
    v text;
BEGIN
    FOR v IN
        SELECT table_name FROM information_schema.views
        WHERE table_schema = 'public' AND table_name LIKE 'kb\_%'
    LOOP
        EXECUTE format('ALTER VIEW public.%I OWNER TO postgres', v);
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- 4. Let the application role bypass nothing, but see everything it owns
-- ---------------------------------------------------------------------------
-- The app connects as the table owner. FORCE ROW LEVEL SECURITY (set in the
-- migration) means even the owner is subject to the policies, so the app must
-- set app.current_department on every transaction. That is done by
-- backend.database.apply_rls_context().

-- ---------------------------------------------------------------------------
-- 5. Verification
-- ---------------------------------------------------------------------------
\echo ''
\echo '=== Tables with RLS enabled (expect 4) ==='
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relnamespace = 'public'::regnamespace
  AND relrowsecurity = true
ORDER BY relname;

\echo ''
\echo '=== Isolation policies ==='
SELECT tablename, policyname FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename;

\echo ''
\echo '=== Objects ai_readonly can SELECT (expect ONLY kb_* views) ==='
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'ai_readonly'
ORDER BY table_name;

\echo ''
\echo 'Done. Update SQL_AGENT_DATABASE_URL in .env with the ai_readonly password.'
