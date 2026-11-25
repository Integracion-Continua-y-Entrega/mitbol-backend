-- PROPUESTA HU-010: actualizar un escritor EXISTENTE, sólo después de 0007.
-- No crea roles, no cambia datos ni configura servicios.
-- La misma sesión debe tener mitbol.approved_admin_write=hu010-reviewed.
-- En Neon: destino/branch aprobados y TLS comprobado por el cliente antes del SQL.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';
DO $guard$
DECLARE
    writer oid;
BEGIN
    IF coalesce(current_setting('mitbol.approved_admin_write', true), '') <> 'hu010-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'HU010_NOT_APPROVED';
    END IF;
    IF NOT (
        (current_database() ~ '^mitbol_test_auth_[a-f0-9]{32}$'
         AND current_user = 'mitbol_ci' AND session_user = 'mitbol_ci'
         AND coalesce(inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet), false))
        OR
        (current_database() = 'mitbol_cloud_dev'
         AND current_user = 'neondb_owner' AND session_user = 'neondb_owner'
         AND coalesce(current_setting('mitbol.external_tls_verified', true), '') = 'verify-full-channel-binding')
    ) THEN
        RAISE EXCEPTION 'HU010_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261114_0007', '20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012','20261120_0013','20261121_0014','20261124_0015','20261124_0016')) THEN
        RAISE EXCEPTION 'HU010_WRONG_SCHEMA';
    END IF;
    SELECT oid INTO writer FROM pg_roles WHERE rolname = 'mitbol_admin_writer'
      AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
      AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit;
    IF writer IS NULL OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = writer)
       OR has_any_column_privilege(writer, 'public."SyncRuns"', 'UPDATE,REFERENCES')
       OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid='public."SyncRuns"'::regclass
           AND a.attnum>0 AND NOT a.attisdropped
           AND a.attname NOT IN ('run_id','store_id','source','requested_by')
           AND has_column_privilege(writer,a.attrelid,a.attnum,'INSERT'))
       OR has_table_privilege(writer, 'public."SyncRuns"', 'INSERT,DELETE,TRUNCATE,TRIGGER,MAINTAIN') THEN
        RAISE EXCEPTION 'HU010_UNSAFE_WRITER';
    END IF;
    GRANT SELECT ON public."SyncRuns" TO mitbol_admin_writer;
    GRANT INSERT (run_id, store_id, source, requested_by) ON public."SyncRuns" TO mitbol_admin_writer;
    GRANT SELECT (version_num) ON public.alembic_version TO mitbol_admin_writer;
    GRANT SELECT (synced_at) ON public."ProductModifiers", public."ProductComplements" TO mitbol_admin_writer;
END
$guard$;
COMMIT;
