-- HU-008/HU-002: delta de privilegios, sin crear roles ni cambiar credenciales.
-- Antes: identificar rama/host/base y TLS. Después: LOGIN nuevo y verificadores.
BEGIN;
SET LOCAL search_path=pg_catalog,public;
SET LOCAL statement_timeout='15s';
SET LOCAL lock_timeout='5s';
DO $guard$
BEGIN
    IF coalesce(current_setting('mitbol.approved_category_write',true),'') <> 'hu008-categories-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'CATEGORIES_NOT_APPROVED';
    END IF;
    IF NOT (
        (current_database() ~ '^mitbol_test_auth_[a-f0-9]{32}$'
         AND current_user='mitbol_ci' AND session_user='mitbol_ci'
         AND coalesce(inet_server_addr() IN ('127.0.0.1'::inet,'::1'::inet),false))
        OR (current_database()='mitbol_cloud_dev'
            AND current_user='neondb_owner' AND session_user='neondb_owner'
            AND coalesce(current_setting('mitbol.external_tls_verified',true),'')='verify-full-channel-binding')
    ) THEN
        RAISE EXCEPTION 'CATEGORIES_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version)<>1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012','20261120_0013','20261121_0014','20261124_0015','20261124_0016','20261124_0017'))
       OR to_regclass('public."Categories"') IS NULL THEN
        RAISE EXCEPTION 'CATEGORIES_WRONG_SCHEMA';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_render_reader')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_admin_writer') THEN
        RAISE EXCEPTION 'CATEGORIES_WEB_ROLES_REQUIRED';
    END IF;
END
$guard$;
GRANT SELECT (category_id,name,color,is_active) ON public."Categories" TO mitbol_render_reader;
GRANT SELECT (category_id) ON public."Products" TO mitbol_admin_writer;
DO $worker$
BEGIN
    -- Si todavía no existe, prepare_catalog_worker.sql crea luego el perfil 0008.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_catalog_worker') THEN
        GRANT SELECT ON public."Categories" TO mitbol_catalog_worker;
        GRANT INSERT (loyverse_id,name,color,is_active,synced_at), UPDATE (name,color,is_active,synced_at)
            ON public."Categories" TO mitbol_catalog_worker;
        GRANT SELECT (category_id), INSERT (category_id), UPDATE (category_id)
            ON public."Products" TO mitbol_catalog_worker;
        GRANT USAGE ON SEQUENCE public."Categories_category_id_seq" TO mitbol_catalog_worker;
    END IF;
END
$worker$;
COMMIT;
