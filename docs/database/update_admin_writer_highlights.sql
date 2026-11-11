-- HU-006: incremento/reversión para un escritor EXISTENTE. No crea roles ni cambia datos.
-- Requiere preflight del perfil anterior con LOGIN real y ventana administrativa cerrada.
-- En la MISMA sesión: mitbol.approved_admin_write=hu006-reviewed,
-- mitbol.hu006_direction=upgrade o rollback. En Neon, además TLS verificado externamente.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';

DO $guard$
DECLARE
    direction text := current_setting('mitbol.hu006_direction', true);
    writer oid;
BEGIN
    IF coalesce(current_setting('mitbol.approved_admin_write', true), '') <> 'hu006-reviewed'
       OR coalesce(direction, '') NOT IN ('upgrade', 'rollback')
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'HU006_NOT_APPROVED';
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
        RAISE EXCEPTION 'HU006_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS (
        SELECT 1 FROM public.alembic_version WHERE version_num = '20261106_0005'
    ) THEN
        RAISE EXCEPTION 'HU006_WRONG_SCHEMA';
    END IF;
    SELECT oid INTO writer FROM pg_roles
      WHERE rolname = 'mitbol_admin_writer'
        AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
        AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit;
    IF writer IS NULL OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = writer)
       OR has_table_privilege(writer, 'public."Products"', 'UPDATE') THEN
        RAISE EXCEPTION 'HU006_UNSAFE_WRITER';
    END IF;
    IF direction = 'upgrade' THEN
        GRANT UPDATE (is_featured, is_new) ON public."Products" TO mitbol_admin_writer;
    ELSE
        REVOKE UPDATE (is_featured, is_new) ON public."Products" FROM mitbol_admin_writer;
    END IF;
    IF has_column_privilege(writer, 'public."Products"', 'is_featured', 'UPDATE') <> (direction = 'upgrade')
       OR has_column_privilege(writer, 'public."Products"', 'is_new', 'UPDATE') <> (direction = 'upgrade') THEN
        RAISE EXCEPTION 'HU006_UNEXPECTED_EFFECTIVE_PERMISSIONS';
    END IF;
END
$guard$;
COMMIT;
