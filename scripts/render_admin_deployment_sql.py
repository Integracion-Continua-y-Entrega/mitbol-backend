"""Genera SQL propuesto para Neon desde las ACL probadas; nunca conecta a la red."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "docs/database"


def render_prepare():
    source = (DIRECTORY / "prepare_admin_writer_local.sql").read_text(encoding="utf-8")
    start = source.index("DO $guard$")
    end = source.index("CREATE ROLE mitbol_admin_writer")
    guard = """DO $guard$
BEGIN
    IF current_database() <> 'mitbol_cloud_dev' OR current_user <> 'neondb_owner'
       OR session_user <> 'neondb_owner'
       OR coalesce(current_setting('mitbol.approved_admin_write', true), '') <> 'phase5-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18
       OR coalesce(current_setting('mitbol.external_tls_verified', true), '') <> 'verify-full-channel-binding' THEN
        RAISE EXCEPTION 'NEON_WRITER_NOT_APPROVED: destino, operador, versión o marcas de ejecución no válidos.';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261106_0005', '20261111_0006', '20261114_0007', '20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012','20261120_0013','20261121_0014','20261124_0015','20261124_0016','20261124_0017')) THEN
        RAISE EXCEPTION 'NEON_WRITER_SCHEMA';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mitbol_admin_writer') THEN
        RAISE EXCEPTION 'NEON_WRITER_EXISTS: revisar sin sobrescribir.';
    END IF;
END
$guard$;

"""
    return ("-- PROPUESTA NO EJECUTADA. Generada desde ACL de laboratorio. Revisar antes de autorizar.\n"
            "-- TLS externo obligatorio en el cliente: verify-full, CA y channel_binding=require.\n"
            "-- La marca de sesión no acredita TLS por sí sola; usar el procedimiento fase 5, sección 7.\n"
            + source[source.index("BEGIN;"):start] + guard + source[end:]).replace(
                "base temporal", "base aprobada").replace("base temporal", "base aprobada")


def render_verify():
    source = (DIRECTORY / "verify_admin_writer_local.sql").read_text(encoding="utf-8")
    source = source.replace("current_database() !~ '^mitbol_test_auth_[a-f0-9]{32}$'", "current_database() <> 'mitbol_cloud_dev'")
    source = source.replace("OR coalesce(inet_server_addr() NOT IN ('127.0.0.1'::inet, '::1'::inet), true)",
        "OR coalesce(current_setting('mitbol.external_tls_verified', true), '') <> 'verify-full-channel-binding'")
    return ("-- PROPUESTA NO EJECUTADA: LOGIN independiente con TLS verificado. Sólo catálogos.\n"
            "-- TLS externo obligatorio en el cliente: verify-full, CA y channel_binding=require.\n"
            "-- La marca de sesión no acredita TLS por sí sola; usar el procedimiento fase 5, sección 7.\n" + source).replace(
        "WRITER_POLICY_OK_LOCAL_ONLY", "WRITER_POLICY_OK_REVIEWED_PROFILE").replace(
        "WRITER_LOCAL_IDENTITY", "WRITER_NEON_IDENTITY")


def render_update():
    """Delta para los roles existentes; comparte las ACL con el perfil probado."""
    source = (DIRECTORY / "prepare_admin_writer_local.sql").read_text(encoding="utf-8")
    start = source.index("DO $recent$")
    end = source.index("$recent$;", start) + len("$recent$;")
    return """-- PROPUESTA NO EJECUTADA: delta aditivo para el escritor EXISTENTE.
-- Sin roles nuevos, datos comerciales, credenciales ni cambios de configuración.
-- Requiere revisión explícita y transporte/destino contrastados por el operador.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';
DO $guard$
DECLARE
    writer oid;
BEGIN
    IF coalesce(current_setting('mitbol.approved_admin_write', true), '') <> 'db-compat-0012-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'RECENT_NOT_APPROVED';
    END IF;
    IF NOT coalesce(
        (current_database() ~ '^mitbol_test_auth_[a-f0-9]{32}$'
         AND current_user = 'mitbol_ci' AND session_user = 'mitbol_ci'
         AND inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet))
        OR
        (current_database() = 'mitbol_cloud_dev'
         AND current_user = 'neondb_owner' AND session_user = 'neondb_owner'
         AND current_setting('mitbol.external_tls_verified', true) = 'verify-full-channel-binding')
    , false) THEN
        RAISE EXCEPTION 'RECENT_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num IN
        ('20261119_0009','20261119_0010','20261119_0011','20261119_0012','20261120_0013','20261121_0014','20261124_0015','20261124_0016','20261124_0017')) THEN
        RAISE EXCEPTION 'RECENT_WRONG_SCHEMA';
    END IF;
    SELECT oid INTO writer FROM pg_roles WHERE rolname='mitbol_admin_writer'
        AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
        AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit;
    IF writer IS NULL OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=writer) THEN
        RAISE EXCEPTION 'RECENT_UNSAFE_WRITER';
    END IF;
END
$guard$;
""" + source[start:end] + "\nCOMMIT;\n"


def main():
    check = sys.argv[1:] == ["--check"]
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit("Uso: render_admin_deployment_sql.py [--check]")
    for name, content in (("prepare_admin_writer_neon.sql", render_prepare()),
                          ("verify_admin_writer_neon.sql", render_verify()),
                          ("update_admin_writer_accounts_orders.sql", render_update())):
        path = DIRECTORY / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit("SQL propuesto desactualizado; revisar y regenerar localmente.")
        else:
            path.write_text(content, encoding="utf-8")
    print("SQL propuesto comprobado." if check else "SQL propuesto generado; no se ejecutó SQL ni se abrió una conexión.")


if __name__ == "__main__":
    main()
