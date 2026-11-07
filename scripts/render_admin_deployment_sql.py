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
       (SELECT 1 FROM public.alembic_version WHERE version_num = '20261106_0005') THEN
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


def main():
    check = sys.argv[1:] == ["--check"]
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit("Uso: render_admin_deployment_sql.py [--check]")
    for name, content in (("prepare_admin_writer_neon.sql", render_prepare()),
                          ("verify_admin_writer_neon.sql", render_verify())):
        path = DIRECTORY / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit("SQL propuesto desactualizado; revisar y regenerar localmente.")
        else:
            path.write_text(content, encoding="utf-8")
    print("SQL propuesto comprobado." if check else "SQL propuesto generado; no se ejecutó SQL ni se abrió una conexión.")


if __name__ == "__main__":
    main()
