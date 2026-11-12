-- PROPUESTA PARA REVISION. No ejecutar hasta autorizar la Etapa 3 correspondiente.
-- Ejecutar en la conexion directa TLS ya verificada, despues de migrar a una revision compatible.
-- No contiene contrasenas. Configurar la del lector despues, con \password
-- mitbol_render_reader en psql (entrada oculta), nunca en este archivo.

BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';

DO $guard$
BEGIN
    IF current_database() <> 'mitbol_cloud_dev' OR current_user <> 'neondb_owner'
       OR session_user <> 'neondb_owner' OR current_setting('transaction_read_only') <> 'off' THEN
        RAISE EXCEPTION 'Destino u operador no autorizado para preparar el lector de Render.';
    END IF;
    IF to_regclass('public.alembic_version') IS NULL THEN
        RAISE EXCEPTION 'Primero deben aplicarse las migraciones autorizadas.';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1
       OR NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num IN
            ('20261102_0003', '20261105_0004', '20261106_0005', '20261111_0006')) THEN
        RAISE EXCEPTION 'La base requiere 20261102_0003 o una revision compatible hasta 20261111_0006.';
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
-- Los grants directos se conservan; revisar operadores que dependan de PUBLIC.
-- neondb y postgres se mantienen intactas, incluido CONNECT/TEMPORARY de PUBLIC.
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
-- Estos REVOKE por esquema no anulan grants globales ni los de PUBLIC.
-- El bloque de verificacion rechaza defaults que expongan futuros objetos.
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM mitbol_render_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM mitbol_render_reader;

-- Defensa adicional para las conexiones nuevas; no sustituye los privilegios.
ALTER ROLE mitbol_render_reader SET default_transaction_read_only = on;

-- Misma politica en preparacion y verificacion; no ejecuta funciones con efectos.
DO $reader_policy$
DECLARE
    reader_oid oid;
    allowed_tables CONSTANT text[] := ARRAY[
        'Products', 'ProductVariants', 'ProductModifiers', 'ProductComplements',
        'ProductModifierAssignments', 'ProductImages', 'Tags', 'ProductTags'
    ];
BEGIN
    SELECT oid INTO reader_oid FROM pg_roles WHERE rolname = 'mitbol_render_reader';
    IF reader_oid IS NULL THEN
        RAISE EXCEPTION 'READER_MISSING';
    END IF;
    IF current_setting('server_version_num')::int < 180000 THEN
        RAISE EXCEPTION 'READER_UNSUPPORTED_SERVER: se requiere PostgreSQL 18 (has_largeobject_privilege).';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE oid = reader_oid AND
        (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls)) THEN
        RAISE EXCEPTION 'READER_ADMIN_ATTRIBUTES';
    END IF;
    -- Ninguna membresia, incluso NOINHERIT/SET: PUBLIC no aparece en esta tabla.
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = reader_oid) THEN
        RAISE EXCEPTION 'READER_MEMBERSHIP';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_db_role_setting s CROSS JOIN LATERAL pg_options_to_table(s.setconfig) o
        WHERE s.setrole = reader_oid AND s.setdatabase = 0
          AND o.option_name = 'default_transaction_read_only' AND o.option_value::boolean
    ) OR EXISTS (
        SELECT 1 FROM pg_db_role_setting s CROSS JOIN LATERAL pg_options_to_table(s.setconfig) o
        WHERE s.setrole = reader_oid
          AND s.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND o.option_name = 'default_transaction_read_only' AND NOT o.option_value::boolean
    ) THEN
        RAISE EXCEPTION 'READER_DEFAULT_CONFIGURATION';
    END IF;
    IF NOT has_database_privilege(reader_oid, current_database(), 'CONNECT')
       OR NOT has_schema_privilege(reader_oid, 'public', 'USAGE') THEN
        RAISE EXCEPTION 'READER_REQUIRED_ACCESS';
    END IF;
    IF has_database_privilege(reader_oid, current_database(), 'CREATE,TEMPORARY')
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE has_schema_privilege(reader_oid, oid, 'CREATE')) THEN
        RAISE EXCEPTION 'READER_CREATE_OR_TEMP';
    END IF;
    -- Incluye bases de la rama (catalogo compartido), pero no objetos de otras bases.
    IF EXISTS (SELECT 1 FROM pg_database WHERE datdba = reader_oid)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = reader_oid)
       OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = reader_oid)
       OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = reader_oid)
       OR EXISTS (SELECT 1 FROM pg_type WHERE typowner = reader_oid)
       OR EXISTS (SELECT 1 FROM pg_extension WHERE extowner = reader_oid)
       OR EXISTS (SELECT 1 FROM pg_largeobject_metadata WHERE lomowner = reader_oid) THEN
        RAISE EXCEPTION 'READER_OBJECT_OWNER';
    END IF;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname::text = ANY(allowed_tables)
          AND has_table_privilege(reader_oid, c.oid, 'SELECT')) <> cardinality(allowed_tables) THEN
        RAISE EXCEPTION 'READER_REQUIRED_SELECT';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
        AND CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
            has_table_privilege(reader_oid, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
            OR has_any_column_privilege(reader_oid, c.oid, 'INSERT,UPDATE,REFERENCES')
            OR (NOT (n.nspname = 'public' AND c.relname::text = ANY(allowed_tables) AND c.relkind = 'r')
                AND has_any_column_privilege(reader_oid, c.oid, 'SELECT'))
            ELSE false END
    ) THEN
        RAISE EXCEPTION 'READER_TABLE_OR_COLUMN_PRIVILEGES';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
        AND CASE WHEN c.relkind = 'S' THEN
            has_sequence_privilege(reader_oid, c.oid, 'SELECT,USAGE,UPDATE') ELSE false END
    ) THEN
        RAISE EXCEPTION 'READER_SEQUENCE_PRIVILEGES';
    END IF;
    -- Sin membresias: una ACL delegable directa seria una concesion innecesaria.
    IF EXISTS (
        SELECT 1 FROM (
            SELECT relacl AS acl FROM pg_class
            UNION ALL SELECT attacl FROM pg_attribute WHERE attnum > 0 AND NOT attisdropped
            UNION ALL SELECT nspacl FROM pg_namespace
            UNION ALL SELECT datacl FROM pg_database
            UNION ALL SELECT proacl FROM pg_proc
            UNION ALL SELECT typacl FROM pg_type
            UNION ALL SELECT lomacl FROM pg_largeobject_metadata
        ) objects CROSS JOIN LATERAL aclexplode(objects.acl) a
        WHERE a.grantee = reader_oid AND a.is_grantable
    ) THEN
        RAISE EXCEPTION 'READER_GRANT_OPTION';
    END IF;
    -- No se cambian defaults de PUBLIC ni de otros creadores: se rechazan si
    -- expondrían objetos futuros. EXECUTE/USAGE normales del sistema se auditan.
    IF EXISTS (
        SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        WHERE a.grantee IN (0, reader_oid)
          AND (d.defaclobjtype IN ('r', 'S', 'L')
               OR (d.defaclobjtype = 'n' AND a.privilege_type = 'CREATE')
               OR a.is_grantable)
    ) THEN
        RAISE EXCEPTION 'READER_UNSAFE_DEFAULT_ACL';
    END IF;
    IF current_setting('lo_compat_privileges') <> 'off'
       OR EXISTS (SELECT 1 FROM pg_largeobject_metadata
                  WHERE has_largeobject_privilege(reader_oid, oid, 'SELECT,UPDATE')) THEN
        RAISE EXCEPTION 'READER_LARGE_OBJECT_ACCESS';
    END IF;
    -- Rutinas propias SECURITY DEFINER no forman parte del menu autorizado.
    -- Las de extensiones/sistema se informan para revision, sin confiar en su
    -- seguridad ni rechazarlas automaticamente por un EXECUTE normal de PUBLIC.
    IF EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
          AND p.prosecdef AND has_schema_privilege(reader_oid, n.oid, 'USAGE')
          AND has_function_privilege(reader_oid, p.oid, 'EXECUTE')
          AND NOT EXISTS (SELECT 1 FROM pg_depend d
                          WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
    ) THEN
        RAISE EXCEPTION 'READER_UNREVIEWED_DEFINER';
    END IF;
END
$reader_policy$;

-- Riesgos residuales: permisos normales de PostgreSQL no son fallos automaticos.
-- Solo se consultan catalogos y ACL; nunca se invocan las rutinas listadas.
-- EXECUTE observado != efecto confirmado: puede haber comprobaciones internas.
WITH candidates AS (
    SELECT p.oid, p.pronamespace, p.proname, p.prosecdef
    FROM pg_proc p WHERE p.pronamespace = 'pg_catalog'::regnamespace
      AND p.proname IN (
        'lo_create', 'lo_creat', 'lo_from_bytea', 'lo_open', 'lo_put', 'lowrite',
        'lo_unlink', 'lo_truncate', 'lo_truncate64', 'lo_import', 'lo_export',
        'pg_read_file', 'pg_read_binary_file', 'pg_write_file', 'pg_logical_emit_message',
        'pg_create_logical_replication_slot', 'pg_create_physical_replication_slot',
        'pg_drop_replication_slot', 'pg_replication_origin_create', 'pg_replication_origin_drop',
        'pg_cancel_backend', 'pg_terminate_backend', 'pg_reload_conf', 'set_config'
      )
)
SELECT p.oid::regprocedure AS routine, p.prosecdef,
       has_function_privilege('public', p.oid, 'EXECUTE') AS public_execute_observed,
       r.rolname AS evaluated_role,
       has_schema_privilege(r.oid, p.pronamespace, 'USAGE')
         AND has_function_privilege(r.oid, p.oid, 'EXECUTE') AS effective_execute_observed,
       'POTENCIAL_NO_EJECUTADO' AS risk_state
FROM candidates p CROSS JOIN pg_roles r
WHERE r.rolcanlogin AND (r.rolname = current_user OR r.rolname = 'mitbol_render_reader'
                        OR NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb))
ORDER BY 1, 4;

-- Rutinas de usuario de extensiones o propias, y SECURITY DEFINER del sistema.
SELECT p.oid::regprocedure AS routine, n.nspname, p.prosecdef, p.provolatile,
       e.extname AS extension, r.rolname AS evaluated_role,
       has_function_privilege('public', p.oid, 'EXECUTE') AS public_execute_observed,
       has_schema_privilege(r.oid, n.oid, 'USAGE')
         AND has_function_privilege(r.oid, p.oid, 'EXECUTE') AS effective_execute_observed,
       'REQUIERE_REVISION_NO_EJECUTADO' AS risk_state
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
LEFT JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
LEFT JOIN pg_extension e ON d.refclassid = 'pg_extension'::regclass AND e.oid = d.refobjid
CROSS JOIN pg_roles r
WHERE (p.prosecdef OR (n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND p.provolatile = 'v'))
  AND r.rolcanlogin AND (r.rolname = current_user OR r.rolname = 'mitbol_render_reader'
                        OR NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb))
ORDER BY 1, 6;

COMMIT;
