-- SOLO LECTURA. psql 18, conexion directa verify-full y operador neondb_owner.
-- Requiere: -v expected_revision=20261102_0003|20261105_0004|20261106_0005.
-- Verificar en Console que el endpoint pertenece al proyecto/rama production.
\set ON_ERROR_STOP on
\pset pager off
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '3s';
\if :{?expected_revision}
\else
  DO $error$ BEGIN RAISE EXCEPTION 'AUTH_PREFLIGHT_ARGUMENT: falta expected_revision'; END $error$;
\endif

-- Comprobar search_path original: Alembic no fija schema=public en las revisiones.
SELECT pg_catalog.current_database() = 'mitbol_cloud_dev'
   AND current_user = 'neondb_owner' AND session_user = 'neondb_owner'
   AND pg_catalog.current_setting('server_version_num')::int / 10000 = 18
   AND pg_catalog.current_setting('transaction_read_only') = 'on'
   AND pg_catalog.current_schema() = 'public'
   AND coalesce((SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid = pg_catalog.pg_backend_pid()), false)
   AND :'expected_revision' IN ('20261102_0003', '20261105_0004', '20261106_0005') AS target_ok
\gset
\if :target_ok
\else
  DO $error$ BEGIN RAISE EXCEPTION 'AUTH_PREFLIGHT_TARGET_REJECTED: base, identidad, version, schema, TLS o revision inesperados'; END $error$;
\endif
SET LOCAL search_path = pg_catalog, public;
SELECT count(*) = 1 AND coalesce(bool_and(version_num = :'expected_revision'), false) AS revision_ok
FROM public.alembic_version
\gset
\if :revision_ok
\else
  DO $error$ BEGIN RAISE EXCEPTION 'AUTH_PREFLIGHT_REVISION_REJECTED'; END $error$;
\endif

DO $checks$
DECLARE
    rev text;
    item text;
    expected_auth text[];
    auth_core CONSTANT text[] := ARRAY['Persons','PersonalAccounts','Employees','Roles','EmployeeRoles','AccountSessions','AuditEvents'];
    limits CONSTANT text[] := ARRAY['AuthRateLimitPolicy','AuthRateLimits'];
    catalog CONSTANT text[] := ARRAY['Products','ProductVariants','ProductModifiers','ProductComplements','ProductModifierAssignments','ProductImages','Tags','ProductTags'];
    planned CONSTANT text[] := ARRAY[
        'Persons','PersonalAccounts','Employees','Roles','EmployeeRoles','AccountSessions','AuditEvents','AuthRateLimitPolicy','AuthRateLimits',
        'Persons_person_id_seq','PersonalAccounts_account_id_seq','Roles_role_id_seq','AuditEvents_audit_event_id_seq',
        'pk_Persons','pk_PersonalAccounts','pk_Employees','pk_Roles','pk_EmployeeRoles','pk_AccountSessions','pk_AuditEvents','pk_AuthRateLimitPolicy','pk_AuthRateLimits',
        'uq_PersonalAccounts_person_id','uq_PersonalAccounts_email','uq_Employees_employee_code','uq_Roles_code','uq_AccountSessions_token_hash',
        'ix_AccountSessions_account_id','ix_AccountSessions_expires_at','ix_AuditEvents_actor_created','ix_AuthRateLimits_expires_at'];
    total bigint;
BEGIN
    SELECT version_num INTO STRICT rev FROM public.alembic_version;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY(catalog)) <> 8 THEN
        RAISE EXCEPTION 'AUTH_PREFLIGHT_CATALOG: faltan tablas ordinarias del catalogo en public';
    END IF;
    IF rev = '20261102_0003' THEN
        IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='public' AND c.relname=ANY(planned))
           OR EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                      WHERE n.nspname='public' AND t.typname=ANY(auth_core || limits)) THEN
            RAISE EXCEPTION 'AUTH_PREFLIGHT_CONFLICT: tabla, vista, secuencia, indice o tipo de autenticacion ya existe';
        END IF;
    ELSE
        expected_auth := auth_core || CASE WHEN rev='20261106_0005' THEN limits ELSE ARRAY[]::text[] END;
        IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY(expected_auth)) <> cardinality(expected_auth) THEN
            RAISE EXCEPTION 'AUTH_PREFLIGHT_AUTH_TABLES';
        END IF;
        IF rev='20261105_0004' AND EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=ANY(limits)) THEN
            RAISE EXCEPTION 'AUTH_PREFLIGHT_UNEXPECTED_LIMITS';
        END IF;
        IF (SELECT coalesce(array_agg(code ORDER BY code),ARRAY[]::varchar[]) FROM public."Roles") <> ARRAY['admin','staff']::varchar[] THEN
            RAISE EXCEPTION 'AUTH_PREFLIGHT_SEED';
        END IF;
        FOREACH item IN ARRAY expected_auth LOOP
            IF item <> 'Roles' THEN
                EXECUTE format('SELECT count(*) FROM public.%I', item) INTO total;
                IF total <> 0 THEN
                    RAISE EXCEPTION 'AUTH_PREFLIGHT_UNEXPECTED_ROWS: %', item;
                END IF;
            END IF;
        END LOOP;
    END IF;
END
$checks$;

SELECT current_database(), current_user, session_user, current_setting('server_version') AS version,
       current_setting('transaction_read_only') AS read_only, version_num AS alembic_revision
FROM public.alembic_version;
SELECT ssl, version AS tls_version FROM pg_stat_ssl WHERE pid=pg_backend_pid();
SELECT n.nspname AS schema, c.relname, c.relkind, c.relowner::regrole AS owner
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' ORDER BY 1,2;
SELECT n.nspname AS schema, c.relname, a.attname, format_type(a.atttypid,a.atttypmod) AS type,
       a.attnotnull, md5(pg_get_expr(d.adbin,d.adrelid)) AS default_fingerprint
FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
ORDER BY c.relname,a.attnum;
SELECT conrelid::regclass AS relation, conname, contype, convalidated,
       CASE WHEN contype IN ('p','u','f') THEN pg_get_constraintdef(oid) ELSE md5(pg_get_constraintdef(oid)) END AS definition_or_fingerprint
FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2;
SELECT i.indrelid::regclass AS relation, i.indexrelid::regclass AS index, i.indisvalid, i.indisready,
       md5(pg_get_indexdef(i.indexrelid)) AS definition_fingerprint
FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
WHERE c.relnamespace='public'::regnamespace ORDER BY 1,2;
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls
FROM pg_roles WHERE rolname IN ('neondb_owner','mitbol_render_reader','mitbol_admin_writer');
SELECT member::regrole, roleid::regrole, admin_option, inherit_option, set_option
FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='mitbol_render_reader');
SELECT c.relname, has_table_privilege('mitbol_render_reader',c.oid,'SELECT') AS table_select,
       has_table_privilege('mitbol_render_reader',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
         OR has_any_column_privilege('mitbol_render_reader',c.oid,'INSERT,UPDATE,REFERENCES') AS any_write,
       has_any_column_privilege('mitbol_render_reader',c.oid,'SELECT') AS any_column_select
FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p','v','m','f') ORDER BY 1;
SELECT defaclrole::regrole AS creator, defaclnamespace::regnamespace AS schema, defaclobjtype, defaclacl
FROM pg_default_acl ORDER BY 1,2,3;
SELECT evtname, evtevent, evtenabled, evtfoid::regprocedure FROM pg_event_trigger ORDER BY 1;
SELECT extname, extversion FROM pg_extension ORDER BY 1;

-- Huellas del catalogo: no emite filas, datos personales ni secretos. No consume secuencias.
DO $snapshot$
DECLARE item text; total bigint; digest text; seq record; last_seen bigint; called boolean;
BEGIN
    FOREACH item IN ARRAY ARRAY['Products','ProductVariants','ProductModifiers','ProductComplements','ProductModifierAssignments','ProductImages','Tags','ProductTags'] LOOP
        EXECUTE format('SELECT count(*), encode(sha256(convert_to(coalesce(string_agg(row_to_json(t)::text, E''\n'' ORDER BY row_to_json(t)::text), ''''), ''UTF8'')), ''hex'') FROM public.%I t', item)
            INTO total,digest;
        RAISE NOTICE 'CATALOG_FINGERPRINT % rows=% sha256=%', item,total,digest;
    END LOOP;
    FOR seq IN SELECT s.oid, s.relname FROM pg_class s JOIN pg_depend d ON d.objid=s.oid AND d.classid='pg_class'::regclass
        JOIN pg_class t ON t.oid=d.refobjid AND d.refclassid='pg_class'::regclass
        WHERE s.relkind='S' AND t.relnamespace='public'::regnamespace
          AND t.relname=ANY(ARRAY['Products','ProductVariants','ProductModifiers','ProductComplements','ProductImages','Tags']) ORDER BY s.relname
    LOOP
        EXECUTE format('SELECT last_value,is_called FROM %s',seq.oid::regclass) INTO last_seen,called;
        RAISE NOTICE 'CATALOG_SEQUENCE % last_value=% is_called=%',seq.relname,last_seen,called;
    END LOOP;
END
$snapshot$;
SELECT 'AUTH_MIGRATION_INSPECTION_OK' AS status,
       'Completar verificacion con login lector independiente y revision humana de ACL/defaults/event triggers' AS scope;
ROLLBACK;
