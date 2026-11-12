-- HU-010: lectura de dos columnas para el estado agregado, sin DML comercial.
-- Requiere perfil anterior revisado y mitbol.approved_admin_write=hu010-status-reviewed.
-- No ejecutado en una base real. En cloud comprobar TLS externamente primero.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';
DO $guard$
DECLARE
    writer oid;
    relation text;
BEGIN
    IF coalesce(current_setting('mitbol.approved_admin_write', true), '') <> 'hu010-status-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'HU010_STATUS_NOT_APPROVED';
    END IF;
    IF NOT (
        (current_database() ~ '^mitbol_test_auth_[a-f0-9]{32}$'
         AND current_user = 'mitbol_ci' AND session_user = 'mitbol_ci'
         AND coalesce(inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet), false))
        OR
        (current_database() = 'mitbol_cloud_dev'
         AND current_user = 'neondb_owner' AND session_user = 'neondb_owner'
         AND coalesce(current_setting('mitbol.external_tls_verified', true), '') = 'verify-full-channel-binding')
        OR
        (current_database() IN ('mitbol_dev', 'mitbol_auth_local') AND current_user = session_user
         AND coalesce(inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet), false)
         AND EXISTS (SELECT 1 FROM pg_class WHERE oid = 'public."Products"'::regclass
                     AND pg_has_role(current_user, relowner, 'USAGE')))
    ) THEN
        RAISE EXCEPTION 'HU010_STATUS_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS (
        SELECT 1 FROM public.alembic_version WHERE version_num = '20261111_0006'
    ) THEN
        RAISE EXCEPTION 'HU010_STATUS_WRONG_SCHEMA';
    END IF;
    SELECT oid INTO writer FROM pg_roles WHERE rolname = 'mitbol_admin_writer'
      AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
      AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit;
    IF writer IS NULL OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = writer) THEN
        RAISE EXCEPTION 'HU010_STATUS_UNSAFE_WRITER';
    END IF;
    FOREACH relation IN ARRAY ARRAY['public."ProductModifiers"', 'public."ProductComplements"'] LOOP
        IF has_any_column_privilege(writer, relation, 'INSERT,UPDATE,REFERENCES')
           OR has_table_privilege(writer, relation, 'DELETE,TRUNCATE,TRIGGER,MAINTAIN') THEN
            RAISE EXCEPTION 'HU010_STATUS_UNSAFE_COMMERCIAL_PERMISSIONS';
        END IF;
        EXECUTE format('GRANT SELECT (synced_at) ON %s TO mitbol_admin_writer', relation);
    END LOOP;
END
$guard$;
COMMIT;
