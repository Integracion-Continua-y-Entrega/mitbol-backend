-- FASE 4: laboratorio PostgreSQL 18 exclusivamente. NO es un script para Neon.
-- No contiene contraseñas. Requiere una base temporal identificada y rol nuevo.
-- Las pruebas validan data_directory antes de permitir ejecutar este archivo.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';

DO $guard$
BEGIN
    IF current_database() !~ '^mitbol_test_auth_[a-f0-9]{32}$'
       OR current_user <> 'mitbol_ci' OR session_user <> 'mitbol_ci'
       OR coalesce(inet_server_addr() NOT IN ('127.0.0.1'::inet, '::1'::inet), true)
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'WRITER_LOCAL_TARGET: sólo PostgreSQL 18 temporal del ejecutor.';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261106_0005', '20261111_0006')) THEN
        RAISE EXCEPTION 'WRITER_SCHEMA: se requiere 20261106_0005 o 20261111_0006.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mitbol_admin_writer') THEN
        RAISE EXCEPTION 'WRITER_EXISTS: revisar, no sobrescribir permisos existentes.';
    END IF;
END
$guard$;

CREATE ROLE mitbol_admin_writer LOGIN PASSWORD NULL
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- Las ACL son aditivas: no basta REVOKE del rol si PUBLIC permite crear objetos.
-- Estos cambios afectan únicamente a la base temporal y a su esquema public.
DO $database$
BEGIN
    EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO mitbol_admin_writer', current_database());
END
$database$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO mitbol_admin_writer;

-- Columnas realmente cargadas por el ORM. No abre acceso a columnas futuras.
GRANT SELECT (product_id, loyverse_id, name, description, is_available, is_active,
              is_featured, is_new, synced_at) ON public."Products" TO mitbol_admin_writer;
GRANT SELECT (product_variant_id, loyverse_id, name, price, is_available,
              is_commercially_available, synced_at, product_id)
    ON public."ProductVariants" TO mitbol_admin_writer;
GRANT SELECT (product_image_id, product_variant_id, image_url, display_order, created_at)
    ON public."ProductImages" TO mitbol_admin_writer;
GRANT SELECT (tag_id, name, created_at) ON public."Tags" TO mitbol_admin_writer;
GRANT SELECT (product_variant_id, tag_id) ON public."ProductTags" TO mitbol_admin_writer;

-- HU-009: lectura administrativa únicamente, sin secuencia ni DML comercial.
DO $discounts$
BEGIN
    IF EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '20261111_0006') THEN
        GRANT SELECT (discount_id, loyverse_id, name, discount_type, value, scope,
                      is_active, synced_at, created_at) ON public."Discounts" TO mitbol_admin_writer;
    END IF;
END
$discounts$;

-- HU-006: únicamente indicadores locales adicionales; sin UPDATE de tabla completa.
GRANT UPDATE (description, is_available, is_featured, is_new) ON public."Products" TO mitbol_admin_writer;
GRANT UPDATE (is_available) ON public."ProductVariants" TO mitbol_admin_writer;
GRANT INSERT (name) ON public."Tags" TO mitbol_admin_writer;
GRANT INSERT (product_variant_id, tag_id), DELETE ON public."ProductTags" TO mitbol_admin_writer;
GRANT INSERT (product_variant_id, image_url, display_order), UPDATE (display_order), DELETE
    ON public."ProductImages" TO mitbol_admin_writer;

-- Identidad: sin altas, borrados, cambios de identidad, activación ni roles.
GRANT SELECT (person_id, name) ON public."Persons" TO mitbol_admin_writer;
GRANT SELECT (account_id, person_id, email, password_hash, is_active, auth_version,
              email_verified_at, last_login_at, created_at, updated_at)
    ON public."PersonalAccounts" TO mitbol_admin_writer;
GRANT UPDATE (password_hash, auth_version, last_login_at, updated_at)
    ON public."PersonalAccounts" TO mitbol_admin_writer;
GRANT SELECT (person_id, employee_code, hired_at, is_active, created_at)
    ON public."Employees" TO mitbol_admin_writer;
GRANT SELECT (role_id, code, name, description) ON public."Roles" TO mitbol_admin_writer;
GRANT SELECT (person_id, role_id) ON public."EmployeeRoles" TO mitbol_admin_writer;

GRANT SELECT (session_id, account_id, token_hash, csrf_token_hash, auth_version,
              created_at, last_activity_at, expires_at, idle_expires_at, revoked_at, revocation_reason),
      INSERT (session_id, account_id, token_hash, csrf_token_hash, auth_version,
              created_at, last_activity_at, expires_at, idle_expires_at, revoked_at, revocation_reason),
      UPDATE (last_activity_at, idle_expires_at, revoked_at, revocation_reason)
    ON public."AccountSessions" TO mitbol_admin_writer;

-- Append-only para el proceso web; sólo SELECT para el RETURNING del ORM.
GRANT INSERT (actor_account_id, action, outcome, resource_type, resource_id, request_id, failure_code),
      SELECT (audit_event_id, created_at)
    ON public."AuditEvents" TO mitbol_admin_writer;

-- SERIAL requiere USAGE, sin setval ni acceso a las secuencias de comercio/altas.
GRANT SELECT (policy_id, fingerprint), INSERT (policy_id, fingerprint)
    ON public."AuthRateLimitPolicy" TO mitbol_admin_writer;
GRANT SELECT (key_hash, started_at, expires_at, attempts),
      INSERT (key_hash, started_at, expires_at, attempts), UPDATE (attempts), DELETE
    ON public."AuthRateLimits" TO mitbol_admin_writer;

GRANT USAGE ON SEQUENCE public."Tags_tag_id_seq", public."ProductImages_product_image_id_seq",
                        public."AuditEvents_audit_event_id_seq" TO mitbol_admin_writer;

-- Sin defaults de escritura: objetos/columnas futuras necesitan revisión explícita.
ALTER ROLE mitbol_admin_writer SET search_path TO pg_catalog, public;
ALTER ROLE mitbol_admin_writer SET statement_timeout TO '15s';
ALTER ROLE mitbol_admin_writer SET lock_timeout TO '5s';
COMMIT;
