-- Solo lectura, DESPUES de la creacion autorizada y desde un login NUEVO del lector.
\set ON_ERROR_STOP on
\pset pager off
SHOW default_transaction_read_only;
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL search_path = pg_catalog, public;

DO $guard$
BEGIN
    IF current_database() <> 'mitbol_cloud_dev'
       OR current_user <> 'mitbol_render_reader' OR session_user <> 'mitbol_render_reader' THEN
        RAISE EXCEPTION 'READER_IDENTITY: se requiere un login nuevo del lector en la base autorizada.';
    END IF;
    IF current_setting('default_transaction_read_only') <> 'on'
       OR current_setting('transaction_read_only') <> 'on' THEN
        RAISE EXCEPTION 'READER_READ_ONLY_CONFIGURATION';
    END IF;
END
$guard$;

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


\echo '1. Identidad: lector; atributos administrativos y herencia: todos false'
SELECT current_database(), current_user, session_user,
       current_setting('transaction_read_only') AS read_only;
SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls
FROM pg_roles WHERE rolname = current_user;

\echo '2. Membresias del lector: se esperan cero filas (NOINHERIT solo no basta)'
SELECT parent.rolname AS granted_role, m.admin_option
FROM pg_auth_members m JOIN pg_roles parent ON parent.oid = m.roleid
WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user);

\echo '3. Base/esquemas: connect=true; create/temporary=false; public usage=true'
SELECT has_database_privilege(current_user, current_database(), 'CONNECT') AS connect,
       has_database_privilege(current_user, current_database(), 'CREATE') AS database_create,
       has_database_privilege(current_user, current_database(), 'TEMPORARY') AS temporary,
       has_schema_privilege(current_user, 'public', 'USAGE') AS public_usage;
SELECT nspname FROM pg_namespace
WHERE nspname <> 'information_schema' AND nspname !~ '^pg_'
  AND has_schema_privilege(current_user, oid, 'CREATE');

\echo '4. Tablas/vistas: SELECT=true solo en las ocho tablas; escritura/grant option=false'
-- CASE evita evaluar has_table_privilege/has_sequence_privilege con tipos incompatibles.
SELECT n.nspname, c.relname,
       CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
         has_table_privilege(current_user, c.oid, 'SELECT') ELSE false END AS can_select,
       CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
         has_table_privilege(current_user, c.oid, 'SELECT WITH GRANT OPTION') ELSE false END AS can_grant_select,
       CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
         (has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
          OR has_any_column_privilege(current_user, c.oid, 'INSERT,UPDATE,REFERENCES'))
         ELSE false END AS can_write,
       CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f')
                 AND current_setting('server_version_num')::int >= 170000
            THEN has_table_privilege(current_user, c.oid, 'MAINTAIN') ELSE false END AS can_maintain,
       c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) AS owns_table
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY 1, 2;

\echo '5. Secuencias: todos false; alembic_version tambien debe tener can_select=false'
SELECT n.nspname, c.relname,
       CASE WHEN c.relkind = 'S' THEN
         has_sequence_privilege(current_user, c.oid, 'SELECT,USAGE,UPDATE')
       ELSE false END AS any_sequence_privilege
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND c.relkind = 'S'
ORDER BY 1, 2;

\echo '6. Rutinas SECURITY DEFINER invocables en esquemas de usuario: revisar cualquier fila'
SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS arguments
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND p.prosecdef
  AND has_schema_privilege(current_user, n.oid, 'USAGE')
  AND has_function_privilege(current_user, p.oid, 'EXECUTE') ORDER BY 1, 2, 3;

\echo '7. Lectura real: ocho resultados; todos cero antes de la sincronizacion'
SELECT 'Products' AS table_name, count(*) AS row_count FROM public."Products"
UNION ALL SELECT 'ProductVariants', count(*) FROM public."ProductVariants"
UNION ALL SELECT 'ProductModifiers', count(*) FROM public."ProductModifiers"
UNION ALL SELECT 'ProductComplements', count(*) FROM public."ProductComplements"
UNION ALL SELECT 'ProductModifierAssignments', count(*) FROM public."ProductModifierAssignments"
UNION ALL SELECT 'ProductImages', count(*) FROM public."ProductImages"
UNION ALL SELECT 'Tags', count(*) FROM public."Tags"
UNION ALL SELECT 'ProductTags', count(*) FROM public."ProductTags" ORDER BY 1;
\echo '8. Funciones: ACL observadas; riesgos potenciales, no prueba de efectos'
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

SELECT 'reader_permissions_verified' AS status,
       'Funciones potenciales requieren revision; no es una garantia de cero escrituras absolutas.' AS scope;
ROLLBACK;
