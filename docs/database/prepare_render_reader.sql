-- PROPUESTA PARA REVISION. No ejecutar hasta autorizar la Etapa 3 correspondiente.
-- Ejecutar en la conexion directa TLS ya verificada, despues de migrar a 0003.
-- No contiene contrasenas. Configurar la del lector despues, con \password
-- mitbol_render_reader en psql (entrada oculta), nunca en este archivo.

BEGIN;

DO $guard$
BEGIN
    IF current_database() <> 'mitbol_cloud_dev' OR current_user <> 'neondb_owner' THEN
        RAISE EXCEPTION 'Destino u operador no autorizado para preparar el lector de Render.';
    END IF;
    IF to_regclass('public.alembic_version') IS NULL THEN
        RAISE EXCEPTION 'Primero deben aplicarse las migraciones autorizadas.';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1
       OR NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '20261102_0003') THEN
        RAISE EXCEPTION 'La base debe estar exactamente en 20261102_0003.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'mitbol_render_reader') THEN
        RAISE EXCEPTION 'El rol ya existe; revisar sus permisos antes de modificarlo.';
    END IF;
END
$guard$;

CREATE ROLE mitbol_render_reader LOGIN PASSWORD NULL
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- PostgreSQL no permite negar a un usuario un permiso heredado de PUBLIC.
-- Estos REVOKE afectan a PUBLIC en esta base/esquema, no a otras bases.
-- El owner conserva sus permisos; otros operadores requeriran grants explicitos.
REVOKE CREATE, TEMPORARY ON DATABASE mitbol_cloud_dev FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT CONNECT ON DATABASE mitbol_cloud_dev TO mitbol_render_reader;
GRANT USAGE ON SCHEMA public TO mitbol_render_reader;

GRANT SELECT ON TABLE
    public."Products", public."ProductVariants", public."ProductModifiers",
    public."ProductComplements", public."ProductModifierAssignments",
    public."ProductImages", public."Tags", public."ProductTags"
TO mitbol_render_reader;

-- No se concede lectura de alembic_version ni de otras tablas.
-- Las tablas futuras requieren GRANT SELECT explicito despues de revisar su uso.
-- Estos defaults corresponden al rol que crea los objetos con Alembic.
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM mitbol_render_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM mitbol_render_reader;

-- Defensa adicional para las conexiones nuevas; no sustituye los privilegios.
ALTER ROLE mitbol_render_reader SET default_transaction_read_only = on;

DO $verify$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'mitbol_render_reader'
        AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit)
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_auth_members m
        JOIN pg_catalog.pg_roles r ON r.oid = m.member
        WHERE r.rolname = 'mitbol_render_reader'
    ) THEN
        RAISE EXCEPTION 'El lector tiene atributos o membresias no autorizados.';
    END IF;
    IF has_database_privilege('mitbol_render_reader', current_database(), 'CREATE,TEMPORARY')
       OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_namespace n
           WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
           AND has_schema_privilege('mitbol_render_reader', n.oid, 'CREATE')
       ) THEN
        RAISE EXCEPTION 'El lector puede crear objetos; revisar los permisos de PUBLIC.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        AND CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
            (has_table_privilege('mitbol_render_reader', c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
             OR has_any_column_privilege('mitbol_render_reader', c.oid, 'INSERT,UPDATE,REFERENCES'))
            ELSE false END
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        AND CASE WHEN c.relkind = 'S' THEN
            has_sequence_privilege('mitbol_render_reader', c.oid, 'USAGE,UPDATE')
            ELSE false END
    ) THEN
        RAISE EXCEPTION 'El lector conserva permisos de escritura; detener la preparacion.';
    END IF;
END
$verify$;

COMMIT;
