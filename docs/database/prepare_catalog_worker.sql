-- PROPUESTA HU-008/HU-010. Identidad nueva, independiente de los roles web.
-- Requiere 0007 y mitbol.approved_worker_write=hu010-worker-reviewed.
-- Neon: validar externamente branch/host/base y TLS ANTES de ejecutar.
-- PASSWORD NULL: el operador provisiona la credencial por canal privado después.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';
DO $guard$
BEGIN
    IF coalesce(current_setting('mitbol.approved_worker_write', true), '') <> 'hu010-worker-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'WORKER_NOT_APPROVED';
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
        RAISE EXCEPTION 'WORKER_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num = '20261114_0007') THEN
        RAISE EXCEPTION 'WORKER_WRONG_SCHEMA';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_catalog_worker') THEN
        RAISE EXCEPTION 'WORKER_EXISTS: revisar sin sobrescribir.';
    END IF;
    IF has_schema_privilege('mitbol_render_reader','public','CREATE')
       OR has_database_privilege('mitbol_render_reader',current_database(),'CREATE,TEMPORARY') THEN
        RAISE EXCEPTION 'WORKER_UNSAFE_PUBLIC_BASELINE';
    END IF;
END
$guard$;
CREATE ROLE mitbol_catalog_worker LOGIN PASSWORD NULL
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
DO $database$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO mitbol_catalog_worker', current_database());
END
$database$;
GRANT USAGE ON SCHEMA public TO mitbol_catalog_worker;
GRANT SELECT ON public."Products", public."ProductVariants", public."ProductModifiers",
    public."ProductComplements", public."ProductModifierAssignments", public."ProductImages",
    public."Tags", public."ProductTags", public.alembic_version, public."SyncRuns"
    TO mitbol_catalog_worker;
GRANT INSERT (loyverse_id,name,is_active,synced_at), UPDATE (name,is_active,synced_at)
    ON public."Products" TO mitbol_catalog_worker;
GRANT INSERT (loyverse_id,name,price,product_id,is_available,is_commercially_available,synced_at),
    UPDATE (name,price,is_commercially_available,synced_at)
    ON public."ProductVariants" TO mitbol_catalog_worker;
GRANT INSERT (loyverse_id,name,display_order,synced_at), UPDATE (name,display_order,synced_at)
    ON public."ProductModifiers" TO mitbol_catalog_worker;
GRANT INSERT (loyverse_id,name,price,display_order,product_modifier_id,is_active,synced_at),
    UPDATE (name,price,display_order,is_active,synced_at)
    ON public."ProductComplements" TO mitbol_catalog_worker;
GRANT INSERT (product_variant_id,product_modifier_id), DELETE
    ON public."ProductModifierAssignments" TO mitbol_catalog_worker;
GRANT INSERT (run_id,store_id,source,requested_by),
    UPDATE (status,started_at,finished_at,available_at,attempts,attempt_token,result,error_code)
    ON public."SyncRuns" TO mitbol_catalog_worker;
GRANT USAGE ON SEQUENCE public."Products_product_id_seq", public."ProductVariants_product_variant_id_seq",
    public."ProductModifiers_product_modifier_id_seq", public."ProductComplements_product_complement_id_seq"
    TO mitbol_catalog_worker;
-- Sin UPDATE local, DELETE comercial, DDL, grants, Discounts o autenticación.
-- No se toca el rol lector ni se cambian privilegios de PUBLIC.
ALTER ROLE mitbol_catalog_worker SET search_path TO pg_catalog, public;
ALTER ROLE mitbol_catalog_worker SET lock_timeout TO '5s';
-- El operador coordina los timeouts del proceso con la duración del catálogo.
COMMIT;
