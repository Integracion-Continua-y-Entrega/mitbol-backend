-- SOLO LECTURA. Propuesta para operador autorizado, conexión directa verify-full.
-- No consulta datos personales, contraseñas, hashes ni tokens.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
DO $target$
BEGIN
    IF current_database() <> 'mitbol_cloud_dev' THEN
        RAISE EXCEPTION 'Destino no autorizado para revisión administrativa.';
    END IF;
END
$target$;
SELECT current_database(), current_user, session_user, current_setting('server_version') AS version;
SELECT ssl, version AS tls_version FROM pg_stat_ssl WHERE pid = pg_backend_pid();
TABLE public.alembic_version;
SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls
FROM pg_roles WHERE rolname IN ('neondb_owner','mitbol_render_reader','mitbol_admin_writer');
SELECT member::regrole AS member, roleid::regrole AS granted_role, admin_option, inherit_option, set_option
FROM pg_auth_members WHERE member IN (SELECT oid FROM pg_roles WHERE rolname IN ('mitbol_render_reader','mitbol_admin_writer'));
SELECT defaclrole::regrole AS creator, defaclnamespace::regnamespace AS schema, defaclobjtype, defaclacl FROM pg_default_acl;
SELECT extname, extversion FROM pg_extension ORDER BY extname;
SELECT n.nspname, p.oid::regprocedure, p.prosecdef, p.proowner::regrole
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' ORDER BY 1,2;
COMMIT;
