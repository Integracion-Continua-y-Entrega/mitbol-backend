-- Etapa 3C: auditoria previa a crear mitbol_render_reader. Ejecutar con psql.
-- No crea roles/objetos ni altera datos/ACL. No usar verify_neon: exige base vacia.
-- Host/TLS se verifican en el cliente con verify-full, CA y channel_binding=require.
\set ON_ERROR_STOP on
\pset pager off

BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';
SET LOCAL search_path = pg_catalog, public;

DO $guard$
BEGIN
    IF current_database() <> 'mitbol_cloud_dev'
       OR current_user <> 'neondb_owner' OR session_user <> 'neondb_owner'
       OR current_setting('transaction_read_only') <> 'on' THEN
        RAISE EXCEPTION 'Destino, operador o modo de lectura no autorizado.';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1
       OR NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '20261102_0003') THEN
        RAISE EXCEPTION 'Se requiere exactamente la revision 20261102_0003.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mitbol_render_reader') THEN
        RAISE EXCEPTION 'READER_ALREADY_EXISTS: usar la verificacion posterior y revisar el estado.';
    END IF;
END
$guard$;

\echo '1. Identidad y revision; TLS efectivo: revisar tambien conninfo del cliente'
SELECT current_database() AS database_name, current_user AS operator,
       session_user AS session_operator, current_setting('transaction_read_only') AS read_only,
       current_setting('server_version') AS server_version;
SELECT version_num FROM public.alembic_version;
-- Neon puede terminar TLS en el proxy: esta vista no sustituye la evidencia del cliente.
SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid();

\echo '2. Objetos: exactamente ocho tablas public y alembic_version, sin vistas adicionales'
SELECT n.nspname AS schema_name, c.relname, c.relkind,
       pg_get_userbyid(c.relowner) AS owner, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY 1, 2;

\echo '3. Diferencias de columnas contra los modelos: se esperan cero filas'
WITH expected(table_name, column_name, sql_type, nullable, default_value) AS (
    VALUES
    ('ProductComplements', 'product_complement_id', 'integer', false, 'serial'),
    ('ProductComplements', 'product_modifier_id', 'integer', false, NULL),
    ('ProductComplements', 'loyverse_id', 'character varying(100)', false, NULL),
    ('ProductComplements', 'name', 'character varying(150)', false, NULL),
    ('ProductComplements', 'price', 'numeric', false, NULL),
    ('ProductComplements', 'is_active', 'boolean', false, 'true'),
    ('ProductComplements', 'synced_at', 'timestamp with time zone', false, NULL),
    ('ProductComplements', 'display_order', 'integer', false, NULL),
    ('ProductImages', 'product_image_id', 'integer', false, 'serial'),
    ('ProductImages', 'product_variant_id', 'integer', false, NULL),
    ('ProductImages', 'image_url', 'text', false, NULL),
    ('ProductImages', 'display_order', 'integer', false, NULL),
    ('ProductImages', 'created_at', 'timestamp with time zone', false, 'now()'),
    ('ProductModifierAssignments', 'product_variant_id', 'integer', false, NULL),
    ('ProductModifierAssignments', 'product_modifier_id', 'integer', false, NULL),
    ('ProductModifiers', 'product_modifier_id', 'integer', false, 'serial'),
    ('ProductModifiers', 'loyverse_id', 'character varying(100)', false, NULL),
    ('ProductModifiers', 'name', 'character varying(150)', false, NULL),
    ('ProductModifiers', 'display_order', 'integer', false, NULL),
    ('ProductModifiers', 'synced_at', 'timestamp with time zone', true, NULL),
    ('ProductTags', 'product_variant_id', 'integer', false, NULL),
    ('ProductTags', 'tag_id', 'integer', false, NULL),
    ('ProductVariants', 'product_variant_id', 'integer', false, 'serial'),
    ('ProductVariants', 'loyverse_id', 'character varying(100)', false, NULL),
    ('ProductVariants', 'name', 'character varying(150)', false, NULL),
    ('ProductVariants', 'price', 'numeric', false, NULL),
    ('ProductVariants', 'is_available', 'boolean', false, 'true'),
    ('ProductVariants', 'is_commercially_available', 'boolean', false, 'false'),
    ('ProductVariants', 'synced_at', 'timestamp with time zone', false, NULL),
    ('ProductVariants', 'product_id', 'integer', false, NULL),
    ('Products', 'product_id', 'integer', false, 'serial'),
    ('Products', 'loyverse_id', 'character varying(100)', false, NULL),
    ('Products', 'name', 'character varying(150)', false, NULL),
    ('Products', 'description', 'text', true, NULL),
    ('Products', 'is_available', 'boolean', false, 'true'),
    ('Products', 'is_active', 'boolean', false, 'true'),
    ('Products', 'is_featured', 'boolean', false, 'false'),
    ('Products', 'is_new', 'boolean', false, 'true'),
    ('Products', 'synced_at', 'timestamp with time zone', false, NULL),
    ('Tags', 'tag_id', 'integer', false, 'serial'),
    ('Tags', 'name', 'character varying(150)', false, NULL),
    ('Tags', 'created_at', 'timestamp with time zone', false, 'now()')
), actual AS (
    SELECT c.relname::text AS table_name, a.attname::text AS column_name,
           format_type(a.atttypid, a.atttypmod) AS sql_type, NOT a.attnotnull AS nullable,
           pg_get_expr(d.adbin, d.adrelid) AS default_value
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
      AND c.relname <> 'alembic_version' AND a.attnum > 0 AND NOT a.attisdropped
)
SELECT coalesce(e.table_name, a.table_name) AS table_name,
       coalesce(e.column_name, a.column_name) AS column_name,
       e.sql_type AS expected_type, a.sql_type AS actual_type,
       e.nullable AS expected_nullable, a.nullable AS actual_nullable,
       e.default_value AS expected_default, a.default_value AS actual_default
FROM expected e FULL JOIN actual a USING (table_name, column_name)
WHERE e.table_name IS NULL OR a.table_name IS NULL
   OR e.sql_type IS DISTINCT FROM a.sql_type OR e.nullable IS DISTINCT FROM a.nullable
   OR CASE WHEN e.default_value = 'serial'
           THEN a.default_value IS NULL OR a.default_value NOT LIKE 'nextval(%'
           ELSE e.default_value IS DISTINCT FROM a.default_value END
ORDER BY 1, 2;

\echo '4. Diferencias de PK, FK, UNIQUE y CHECK: se esperan cero filas'
WITH expected(table_name, constraint_name, kind, columns, referenced_table, referenced_columns) AS (
    VALUES
    ('ProductComplements', 'fk_ProductComplements_product_modifier_id', 'f', ARRAY['product_modifier_id']::text[], 'ProductModifiers', ARRAY['product_modifier_id']::text[]),
    ('ProductComplements', 'pk_ProductComplements', 'p', ARRAY['product_complement_id']::text[], NULL, NULL::text[]),
    ('ProductComplements', 'uq_ProductComplements_loyverse_id', 'u', ARRAY['loyverse_id']::text[], NULL, NULL::text[]),
    ('ProductImages', 'fk_ProductImages_product_variant_id', 'f', ARRAY['product_variant_id']::text[], 'ProductVariants', ARRAY['product_variant_id']::text[]),
    ('ProductImages', 'pk_ProductImages', 'p', ARRAY['product_image_id']::text[], NULL, NULL::text[]),
    ('ProductModifierAssignments', 'fk_ProductModifierAssignments_product_modifier_id', 'f', ARRAY['product_modifier_id']::text[], 'ProductModifiers', ARRAY['product_modifier_id']::text[]),
    ('ProductModifierAssignments', 'fk_ProductModifierAssignments_product_variant_id', 'f', ARRAY['product_variant_id']::text[], 'ProductVariants', ARRAY['product_variant_id']::text[]),
    ('ProductModifierAssignments', 'pk_ProductModifierAssignments', 'p', ARRAY['product_variant_id', 'product_modifier_id']::text[], NULL, NULL::text[]),
    ('ProductModifiers', 'pk_ProductModifiers', 'p', ARRAY['product_modifier_id']::text[], NULL, NULL::text[]),
    ('ProductModifiers', 'uq_ProductModifiers_loyverse_id', 'u', ARRAY['loyverse_id']::text[], NULL, NULL::text[]),
    ('ProductTags', 'fk_ProductTags_product_variant_id', 'f', ARRAY['product_variant_id']::text[], 'ProductVariants', ARRAY['product_variant_id']::text[]),
    ('ProductTags', 'fk_ProductTags_tag_id', 'f', ARRAY['tag_id']::text[], 'Tags', ARRAY['tag_id']::text[]),
    ('ProductTags', 'pk_ProductTags', 'p', ARRAY['product_variant_id', 'tag_id']::text[], NULL, NULL::text[]),
    ('ProductVariants', 'fk_ProductVariants_product_id', 'f', ARRAY['product_id']::text[], 'Products', ARRAY['product_id']::text[]),
    ('ProductVariants', 'pk_ProductVariants', 'p', ARRAY['product_variant_id']::text[], NULL, NULL::text[]),
    ('ProductVariants', 'uq_ProductVariants_loyverse_id', 'u', ARRAY['loyverse_id']::text[], NULL, NULL::text[]),
    ('Products', 'pk_Products', 'p', ARRAY['product_id']::text[], NULL, NULL::text[]),
    ('Products', 'uq_Products_loyverse_id', 'u', ARRAY['loyverse_id']::text[], NULL, NULL::text[]),
    ('Tags', 'pk_Tags', 'p', ARRAY['tag_id']::text[], NULL, NULL::text[]),
    ('Tags', 'uq_Tags_name', 'u', ARRAY['name']::text[], NULL, NULL::text[])
), actual AS (
    SELECT c.relname::text AS table_name, con.conname::text AS constraint_name,
           con.contype::text AS kind,
           ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY k(num, pos)
                 JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.num ORDER BY k.pos) AS columns,
           ref.relname::text AS referenced_table,
           CASE WHEN con.contype = 'f' THEN
             ARRAY(SELECT a.attname::text FROM unnest(con.confkey) WITH ORDINALITY k(num, pos)
                   JOIN pg_attribute a ON a.attrelid = ref.oid AND a.attnum = k.num ORDER BY k.pos)
           ELSE NULL::text[] END AS referenced_columns,
           con.convalidated, con.condeferrable, con.condeferred, con.confdeltype, con.confupdtype,
           con.confmatchtype, ref.relnamespace AS referenced_namespace,
           pg_get_constraintdef(con.oid) AS definition
    FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_class ref ON ref.oid = con.confrelid
    WHERE n.nspname = 'public' AND c.relname <> 'alembic_version'
      AND con.contype IN ('p', 'u', 'f', 'c', 'x')
)
SELECT coalesce(e.table_name, a.table_name) AS table_name,
       coalesce(e.constraint_name, a.constraint_name) AS constraint_name,
       e.kind AS expected_kind, a.kind AS actual_kind, a.definition
FROM expected e FULL JOIN actual a USING (table_name, constraint_name)
WHERE e.table_name IS NULL OR a.table_name IS NULL
   OR e.kind IS DISTINCT FROM a.kind OR e.columns IS DISTINCT FROM a.columns
   OR e.referenced_table IS DISTINCT FROM a.referenced_table
   OR e.referenced_columns IS DISTINCT FROM a.referenced_columns
   OR NOT a.convalidated OR a.condeferrable OR a.condeferred
   OR (a.kind = 'f' AND (a.confdeltype <> 'a' OR a.confupdtype <> 'a'
       OR a.confmatchtype <> 's' OR a.referenced_namespace <> 'public'::regnamespace))
ORDER BY 1, 2;

\echo '5. Indices: se esperan catorce, todos validos/listos (incluye Alembic)'
SELECT t.relname AS table_name, i.relname AS index_name,
       x.indisprimary, x.indisunique, x.indisvalid, x.indisready,
       pg_get_indexdef(i.oid) AS definition
FROM pg_index x JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace JOIN pg_class i ON i.oid = x.indexrelid
WHERE n.nspname = 'public' ORDER BY 1, 2;

\echo '6. Secuencias: seis filas, association_ok=true; no se invoca nextval/setval'
WITH expected(table_name, column_name, sequence_name) AS (
    VALUES
    ('ProductComplements', 'product_complement_id', 'ProductComplements_product_complement_id_seq'),
    ('ProductImages', 'product_image_id', 'ProductImages_product_image_id_seq'),
    ('ProductModifiers', 'product_modifier_id', 'ProductModifiers_product_modifier_id_seq'),
    ('ProductVariants', 'product_variant_id', 'ProductVariants_product_variant_id_seq'),
    ('Products', 'product_id', 'Products_product_id_seq'),
    ('Tags', 'tag_id', 'Tags_tag_id_seq')
)
SELECT e.*, pg_get_serial_sequence(format('public.%I', e.table_name), e.column_name) AS owned_sequence,
       to_regclass(pg_get_serial_sequence(format('public.%I', e.table_name), e.column_name))
         = to_regclass(format('public.%I', e.sequence_name)) AS association_ok,
       EXISTS (
         SELECT 1 FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
         JOIN pg_depend dep ON dep.classid = 'pg_attrdef'::regclass AND dep.objid = d.oid
         WHERE d.adrelid = to_regclass(format('public.%I', e.table_name)) AND a.attname = e.column_name
           AND dep.refclassid = 'pg_class'::regclass
           AND dep.refobjid = to_regclass(format('public.%I', e.sequence_name))
       ) AS default_uses_owned_sequence,
       s.data_type, s.start_value, s.min_value, s.max_value, s.increment_by, s.cycle, s.cache_size
FROM expected e LEFT JOIN pg_sequences s
  ON s.schemaname = 'public' AND s.sequencename = e.sequence_name ORDER BY 1;
SELECT schemaname, sequencename FROM pg_sequences
WHERE schemaname <> 'information_schema' AND schemaname !~ '^pg_' ORDER BY 1, 2;

\echo '7. Filas: ocho resultados, todos deben ser cero'
SELECT 'Products' AS table_name, count(*) AS row_count FROM public."Products"
UNION ALL SELECT 'ProductVariants', count(*) FROM public."ProductVariants"
UNION ALL SELECT 'ProductModifiers', count(*) FROM public."ProductModifiers"
UNION ALL SELECT 'ProductComplements', count(*) FROM public."ProductComplements"
UNION ALL SELECT 'ProductModifierAssignments', count(*) FROM public."ProductModifierAssignments"
UNION ALL SELECT 'ProductImages', count(*) FROM public."ProductImages"
UNION ALL SELECT 'Tags', count(*) FROM public."Tags"
UNION ALL SELECT 'ProductTags', count(*) FROM public."ProductTags" ORDER BY 1;

\echo '8. Relaciones huerfanas: siete resultados, todos deben ser cero'
SELECT 'ProductVariants.product_id' AS relationship, count(*) AS orphans
FROM public."ProductVariants" v LEFT JOIN public."Products" p USING (product_id) WHERE p.product_id IS NULL
UNION ALL SELECT 'ProductComplements.product_modifier_id', count(*)
FROM public."ProductComplements" c LEFT JOIN public."ProductModifiers" m USING (product_modifier_id)
WHERE m.product_modifier_id IS NULL
UNION ALL SELECT 'ProductModifierAssignments.product_variant_id', count(*)
FROM public."ProductModifierAssignments" a LEFT JOIN public."ProductVariants" v USING (product_variant_id)
WHERE v.product_variant_id IS NULL
UNION ALL SELECT 'ProductModifierAssignments.product_modifier_id', count(*)
FROM public."ProductModifierAssignments" a LEFT JOIN public."ProductModifiers" m USING (product_modifier_id)
WHERE m.product_modifier_id IS NULL
UNION ALL SELECT 'ProductImages.product_variant_id', count(*)
FROM public."ProductImages" i LEFT JOIN public."ProductVariants" v USING (product_variant_id)
WHERE v.product_variant_id IS NULL
UNION ALL SELECT 'ProductTags.product_variant_id', count(*)
FROM public."ProductTags" t LEFT JOIN public."ProductVariants" v USING (product_variant_id)
WHERE v.product_variant_id IS NULL
UNION ALL SELECT 'ProductTags.tag_id', count(*)
FROM public."ProductTags" p LEFT JOIN public."Tags" t USING (tag_id) WHERE t.tag_id IS NULL ORDER BY 1;

\echo '9. Duplicados: siete resultados, todos deben ser cero'
SELECT 'Products.loyverse_id' AS unique_key, count(*) AS duplicate_groups
FROM (SELECT loyverse_id FROM public."Products" GROUP BY loyverse_id HAVING count(*) > 1) d
UNION ALL SELECT 'ProductVariants.loyverse_id', count(*)
FROM (SELECT loyverse_id FROM public."ProductVariants" GROUP BY loyverse_id HAVING count(*) > 1) d
UNION ALL SELECT 'ProductModifiers.loyverse_id', count(*)
FROM (SELECT loyverse_id FROM public."ProductModifiers" GROUP BY loyverse_id HAVING count(*) > 1) d
UNION ALL SELECT 'ProductComplements.loyverse_id', count(*)
FROM (SELECT loyverse_id FROM public."ProductComplements" GROUP BY loyverse_id HAVING count(*) > 1) d
UNION ALL SELECT 'Tags.name', count(*)
FROM (SELECT name FROM public."Tags" GROUP BY name HAVING count(*) > 1) d
UNION ALL SELECT 'ProductModifierAssignments.composite_pk', count(*)
FROM (SELECT product_variant_id, product_modifier_id FROM public."ProductModifierAssignments"
      GROUP BY product_variant_id, product_modifier_id HAVING count(*) > 1) d
UNION ALL SELECT 'ProductTags.composite_pk', count(*)
FROM (SELECT product_variant_id, tag_id FROM public."ProductTags"
      GROUP BY product_variant_id, tag_id HAVING count(*) > 1) d ORDER BY 1;

\echo '10. Roles actuales y permisos efectivos que deben revisarse antes de revocar PUBLIC'
SELECT r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication, r.rolbypassrls,
       pg_get_userbyid(d.datdba) = r.rolname AS owns_database,
       has_database_privilege(r.oid, d.oid, 'CREATE') AS database_create,
       has_database_privilege(r.oid, d.oid, 'TEMPORARY') AS database_temporary,
       has_schema_privilege(r.oid, 'public', 'CREATE') AS public_schema_create
FROM pg_roles r CROSS JOIN pg_database d
WHERE r.rolcanlogin AND d.datname = current_database() ORDER BY 1;
SELECT 'database' AS object_type, d.datname AS object_name,
       CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
       a.privilege_type, a.is_grantable
FROM pg_database d CROSS JOIN LATERAL aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a
WHERE d.datname = current_database()
UNION ALL
SELECT 'schema', n.nspname,
       CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
       a.privilege_type, a.is_grantable
FROM pg_namespace n CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' ORDER BY 1, 2, 3, 4;
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mitbol_render_reader') AS reader_already_exists;

\echo '11. Defaults de objetos futuros: revisar especialmente PUBLIC y roles creadores'
SELECT pg_get_userbyid(d.defaclrole) AS creator,
       coalesce(n.nspname, '(global en esta base)') AS schema_scope, d.defaclobjtype,
       CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
       a.privilege_type, a.is_grantable
FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
CROSS JOIN LATERAL aclexplode(d.defaclacl) a
WHERE d.defaclobjtype IN ('r', 'S', 'f', 'n') ORDER BY 1, 2, 3, 4, 5;

\echo '12. ACL PUBLIC en tablas, columnas y secuencias: se esperan cero filas'
SELECT n.nspname, c.relname, '(objeto)' AS column_name, a.privilege_type, a.is_grantable
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL aclexplode(c.relacl) a
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S') AND a.grantee = 0
UNION ALL
SELECT n.nspname, c.relname, col.attname, a.privilege_type, a.is_grantable
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute col ON col.attrelid = c.oid AND col.attnum > 0 AND NOT col.attisdropped
CROSS JOIN LATERAL aclexplode(col.attacl) a
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND a.grantee = 0
ORDER BY 1, 2, 3, 4;

\echo '13. Rutinas SECURITY DEFINER ejecutables por PUBLIC: revisar cualquier fila'
SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS arguments,
       pg_get_userbyid(p.proowner) AS owner, e.extname AS extension
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
LEFT JOIN pg_depend dep ON dep.classid = 'pg_proc'::regclass AND dep.objid = p.oid AND dep.deptype = 'e'
LEFT JOIN pg_extension e ON dep.refclassid = 'pg_extension'::regclass AND e.oid = dep.refobjid
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND p.prosecdef
  AND a.grantee = 0 AND a.privilege_type = 'EXECUTE' ORDER BY 1, 2, 3;

\echo '14. Alcance entre bases: el rol seria compartido; esto NO conecta a otras bases'
SELECT d.datname,
       bool_or(a.grantee = 0 AND a.privilege_type = 'CONNECT') AS public_connect,
       bool_or(a.grantee = 0 AND a.privilege_type = 'TEMPORARY') AS public_temporary
FROM pg_database d CROSS JOIN LATERAL aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a
WHERE d.datallowconn AND NOT d.datistemplate GROUP BY d.datname ORDER BY 1;

\echo '15. Funciones de sistema y rutinas: observaciones y riesgos potenciales'
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

\echo '16. Objetos grandes: solo metadatos/ACL; nunca contenido ni funciones de escritura'
SELECT current_setting('lo_compat_privileges') AS lo_compat_privileges;
SELECT l.oid, pg_get_userbyid(l.lomowner) AS owner,
       CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
       a.privilege_type, a.is_grantable, 'ACL_OBSERVADA_NO_EJECUTADA' AS observation
FROM pg_largeobject_metadata l
CROSS JOIN LATERAL aclexplode(coalesce(l.lomacl, acldefault('L', l.lomowner))) a
ORDER BY 1, 3, 4;

\echo '17. Privilegios efectivos de columnas y secuencias en todos los esquemas de usuario'
WITH objects AS MATERIALIZED (
    SELECT c.oid, c.relname, c.relkind, n.nspname FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
)
SELECT o.nspname, o.relname, r.rolname AS evaluated_role,
       CASE WHEN o.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
         has_any_column_privilege(r.oid, o.oid, 'SELECT') ELSE false END AS any_column_select,
       CASE WHEN o.relkind IN ('r', 'p', 'v', 'm', 'f') THEN
         has_any_column_privilege(r.oid, o.oid, 'INSERT,UPDATE,REFERENCES')
           OR has_table_privilege(r.oid, o.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
         ELSE false END AS table_or_column_write,
       CASE WHEN o.relkind = 'S' THEN
         has_sequence_privilege(r.oid, o.oid, 'SELECT,USAGE,UPDATE') ELSE false END AS any_sequence_privilege
FROM objects o CROSS JOIN pg_roles r
WHERE r.rolcanlogin AND (r.rolname = current_user OR r.rolname = 'mitbol_render_reader'
                        OR NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb))
ORDER BY 1, 2, 3;

\echo '18. Otros objetos persistentes a revisar; no implica acceso al lector'
SELECT srvname, pg_get_userbyid(srvowner) AS owner, srvacl FROM pg_foreign_server;
SELECT fdwname, pg_get_userbyid(fdwowner) AS owner, fdwacl FROM pg_foreign_data_wrapper;
-- No se muestran opciones de servidores/user mappings: podrian contener secretos.
SELECT evtname, pg_get_userbyid(evtowner) AS owner, evtevent, evtenabled
FROM pg_event_trigger ORDER BY 1;

\echo 'LIMITES: pg_database solo muestra metadatos/ACL de base, no tablas/rutinas de neondb o postgres.'
\echo 'CONNECT/TEMPORARY de PUBLIC en esas bases se conservan: decision aceptada, no fallo.'
\echo 'OBSERVADO: ACL/configuracion. POTENCIAL: EXECUTE. CONFIRMADO: solo las desviaciones efectivas de privilegios detectadas.'
\echo 'No se probaron efectos de funciones ni ausencia absoluta de escrituras persistentes.'

ROLLBACK;
\echo 'Auditoria finalizada sin escrituras. Revisar TODOS los resultados antes de autorizar.'
