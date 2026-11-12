-- HU-009: solo lectura de Discounts para un escritor web EXISTENTE.
-- Primero migrar a 0006. No concede permisos al lector público ni de sincronización.
-- En la misma sesión: mitbol.approved_admin_write=hu009-reviewed.
-- Cloud requiere TLS verificado externamente; no ejecutado en una base real.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';
DO $guard$
DECLARE
    writer oid;
BEGIN
    IF coalesce(current_setting('mitbol.approved_admin_write', true), '') <> 'hu009-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'HU009_NOT_APPROVED';
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
        (current_database() = 'mitbol_dev' AND current_user = session_user
         AND coalesce(inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet), false)
         AND EXISTS (SELECT 1 FROM pg_class WHERE oid = 'public."Discounts"'::regclass
                     AND pg_has_role(current_user, relowner, 'USAGE')))
    ) THEN
        RAISE EXCEPTION 'HU009_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS (
        SELECT 1 FROM public.alembic_version WHERE version_num = '20261111_0006'
    ) THEN
        RAISE EXCEPTION 'HU009_WRONG_SCHEMA';
    END IF;
    SELECT oid INTO writer FROM pg_roles WHERE rolname = 'mitbol_admin_writer'
      AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
      AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit;
    IF writer IS NULL OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = writer)
       OR has_any_column_privilege(writer, 'public."Discounts"', 'INSERT,UPDATE,REFERENCES')
       OR has_table_privilege(writer, 'public."Discounts"', 'DELETE,TRUNCATE,TRIGGER,MAINTAIN')
       OR has_sequence_privilege(writer, 'public."Discounts_discount_id_seq"', 'USAGE,SELECT,UPDATE') THEN
        RAISE EXCEPTION 'HU009_UNSAFE_WRITER';
    END IF;
    GRANT SELECT (discount_id, loyverse_id, name, discount_type, value, scope,
                  is_active, synced_at, created_at) ON public."Discounts" TO mitbol_admin_writer;
END
$guard$;
COMMIT;
