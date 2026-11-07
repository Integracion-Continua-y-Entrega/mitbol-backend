-- MITBOL: SELECT de inspeccion, Neon SQL Editor. Preflight A-E ya aprobado.
-- Tras cada revision reutilizar solo A y D para comparar el estado nuevo.
-- Mismo contexto YA confirmado: Console HTTPS, production, mitbol_cloud_dev,
-- neondb_owner, PG18, public, Time Travel desactivado. B solo ANTES de 0004.
-- Ejecutar UNA consulta A-E por Run; todas son SELECT sin DO, SET, DDL o DML.
-- No ejecutar el archivo completo como lote. No hay instrucciones de migracion.
-- Si cambia el destino/rol o se sospecha cambio de revision, revalidarlo primero.
-- TLS externo sigue siendo obligatorio; este archivo no lo valida ni lo cambia.
-- Solo copia resultados (conteos, hashes y metadatos), nunca credenciales.

-- CONSULTA A: CONTEOS Y HUELLAS (8 filas, incluso tablas vacias).
-- Misma formula SHA-256 del preflight original. Incluye precios e IDs Loyverse
-- sin mostrar datos de filas. Guardar salida para comparacion antes/despues.
WITH expected(name) AS (VALUES
    ('Products'),('ProductVariants'),('ProductModifiers'),('ProductComplements'),
    ('ProductModifierAssignments'),('ProductImages'),('Tags'),('ProductTags')),
rows_by_table(name, row_json) AS (
    SELECT 'Products', row_to_json(t)::text FROM public."Products" t
    UNION ALL SELECT 'ProductVariants', row_to_json(t)::text FROM public."ProductVariants" t
    UNION ALL SELECT 'ProductModifiers', row_to_json(t)::text FROM public."ProductModifiers" t
    UNION ALL SELECT 'ProductComplements', row_to_json(t)::text FROM public."ProductComplements" t
    UNION ALL SELECT 'ProductModifierAssignments', row_to_json(t)::text FROM public."ProductModifierAssignments" t
    UNION ALL SELECT 'ProductImages', row_to_json(t)::text FROM public."ProductImages" t
    UNION ALL SELECT 'Tags', row_to_json(t)::text FROM public."Tags" t
    UNION ALL SELECT 'ProductTags', row_to_json(t)::text FROM public."ProductTags" t)
SELECT e.name, count(r.row_json) AS row_count,
       encode(sha256(convert_to(coalesce(string_agg(r.row_json, E'\n' ORDER BY r.row_json), ''), 'UTF8')), 'hex') AS sha256
FROM expected e LEFT JOIN rows_by_table r ON r.name=e.name GROUP BY e.name ORDER BY e.name;

-- CONSULTA B: CONFLICTOS CON NUEVOS OBJETOS (conflicts=0).
-- Incluye 31 tablas/indices/secuencias y 9 tipos de fila.
-- Nombres CHECK/FK repetidos en tablas distintas no bloquean CREATE TABLE.
WITH planned(name) AS (SELECT unnest(ARRAY[
        'Persons','PersonalAccounts','Employees','Roles','EmployeeRoles',
        'AccountSessions','AuditEvents','AuthRateLimitPolicy','AuthRateLimits','Persons_person_id_seq',
        'PersonalAccounts_account_id_seq','Roles_role_id_seq','AuditEvents_audit_event_id_seq','pk_Persons','pk_PersonalAccounts',
        'pk_Employees','pk_Roles','pk_EmployeeRoles','pk_AccountSessions','pk_AuditEvents',
        'pk_AuthRateLimitPolicy','pk_AuthRateLimits','uq_PersonalAccounts_person_id','uq_PersonalAccounts_email','uq_Employees_employee_code',
        'uq_Roles_code','uq_AccountSessions_token_hash','ix_AccountSessions_account_id','ix_AccountSessions_expires_at','ix_AuditEvents_actor_created',
        'ix_AuthRateLimits_expires_at']::text[])), conflicts AS (
    SELECT 'relation' AS kind, c.relname::text AS name
    FROM pg_catalog.pg_class c JOIN planned p ON p.name=c.relname
    WHERE c.relnamespace='public'::regnamespace
    UNION ALL
    SELECT 'type',t.typname::text FROM pg_catalog.pg_type t
    WHERE t.typnamespace='public'::regnamespace AND t.typname::text=ANY(ARRAY[
        'Persons','PersonalAccounts','Employees','Roles','EmployeeRoles',
        'AccountSessions','AuditEvents','AuthRateLimitPolicy','AuthRateLimits']))
SELECT count(*) AS conflicts,
       coalesce(string_agg(kind || ':' || name, ', ' ORDER BY kind,name),'') AS detail
FROM conflicts;

-- CONSULTA C: PERMISOS INDISPENSABLES (exactamente 1 fila).
-- operator_* y reader_login: true; reader_privileged/reader_ddl: false;
-- reader_memberships: 0. No conceder permisos si algo difiere.
SELECT has_schema_privilege(current_user,'public','USAGE') AS operator_schema_usage,
       has_schema_privilege(current_user,'public','CREATE') AS operator_schema_create,
       has_table_privilege(current_user,'public.alembic_version','SELECT') AS operator_version_read,
       has_table_privilege(current_user,'public.alembic_version','UPDATE') AS operator_version_update,
       r.rolcanlogin AS reader_login,
       (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolinherit
        OR r.rolreplication OR r.rolbypassrls) AS reader_privileged,
       (SELECT count(*) FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid) AS reader_memberships,
       (has_database_privilege(r.oid,current_database(),'CREATE,TEMPORARY')
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n
                  WHERE has_schema_privilege(r.oid,n.oid,'CREATE'))) AS reader_ddl
FROM pg_catalog.pg_roles r WHERE r.rolname='mitbol_render_reader';

-- CONSULTA D: LECTOR EFECTIVO, SIN LOGIN ADICIONAL.
-- Las 8 tablas del catalogo: table_select=true. alembic_version: false.
-- En todas: any_write=false y any_sequence_privilege=false.
SELECT n.nspname::text AS schema_name,c.relname::text AS object_name,
       CASE WHEN c.relkind='S' THEN false
            ELSE has_table_privilege('mitbol_render_reader',c.oid,'SELECT') END AS table_select,
       CASE WHEN c.relkind='S' THEN false ELSE
            has_table_privilege('mitbol_render_reader',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
            OR has_any_column_privilege('mitbol_render_reader',c.oid,'INSERT,UPDATE,REFERENCES') END AS any_write,
       CASE WHEN c.relkind='S' THEN
            has_sequence_privilege('mitbol_render_reader',c.oid,'SELECT,USAGE,UPDATE')
            ELSE false END AS any_sequence_privilege
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
  AND c.relkind IN ('r','p','v','m','f','S') ORDER BY 1,2;

-- CONSULTA E: EFECTOS AUTOMATICOS DEL DDL (alerts=0, o revisar solo hallazgos).
-- Defaults relevantes: objetos futuros creados por neondb_owner, global/public,
-- tablas o secuencias, grants a PUBLIC u otro rol distinto del creador.
-- Event triggers: informar los habilitados; no invocar sus funciones.
WITH alerts(kind,detail) AS (
    SELECT 'default_acl',
           CASE WHEN d.defaclnamespace=0 THEN 'global' ELSE 'public' END
           || ' object_type=' || d.defaclobjtype::text
           || ' grantee=' || CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END
           || ' privilege=' || a.privilege_type
           || ' grant_option=' || a.is_grantable::text
    FROM pg_catalog.pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
    WHERE d.defaclrole=current_user::regrole
      AND d.defaclnamespace IN (0,'public'::regnamespace)
      AND d.defaclobjtype IN ('r','S') AND a.grantee<>d.defaclrole
    UNION ALL
    SELECT 'event_trigger',e.evtname::text || ' enabled=' || e.evtenabled::text
           || ' function=' || e.evtfoid::regprocedure::text
    FROM pg_catalog.pg_event_trigger e WHERE e.evtenabled<>'D')
SELECT count(*) AS alerts,
       coalesce(string_agg(kind || ': ' || detail, E'\n' ORDER BY kind,detail),'') AS detail
FROM alerts;
