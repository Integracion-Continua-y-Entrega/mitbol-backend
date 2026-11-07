"""Diagnóstico de una conexión de migración existente; nunca ejecuta DDL ni carga .env.

Uso con el entorno temporal del procedimiento controlado:
python -B -m scripts.diagnose_neon_auth_migration --revision 20261105_0004
"""

import argparse
import json
import logging
import os
import re


EXPECTED_HOST = "ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech"
PREVIOUS = {"20261105_0004": "20261102_0003", "20261106_0005": "20261105_0004"}
STAGES = {
    "arguments", "imports", "configuration", "alembic_graph", "engine", "connect",
    "readonly_setup", "target_tls", "identity", "postgres_version", "schema",
    "permissions", "revision_before", "alembic_validation", "readonly_rollback",
    "transaction_setup", "migration_lock", "alembic_upgrade", "revision_after", "commit",
}
STOP_CODES = {
    "ARGUMENTS_REJECTED", "TARGET_NOT_APPROVED", "OPERATOR_NOT_APPROVED",
    "ALEMBIC_GRAPH_MISMATCH", "IDENTITY_MISMATCH", "POSTGRES_VERSION_MISMATCH",
    "SCHEMA_MISMATCH", "READONLY_NOT_ACTIVE", "DEFAULT_READONLY_ENABLED",
    "OPERATOR_PERMISSIONS_MISSING", "PREVIOUS_REVISION_MISMATCH",
}


class DiagnosticStop(ValueError):
    def __init__(self, code):
        self.code = code if code in STOP_CODES else "TARGET_NOT_APPROVED"
        super().__init__(self.code)


def failure_report(error, stage, checks_passed=()):
    """Salida cerrada: tampoco refleja mensajes/args del error anidado en Alembic."""
    safe_stage = stage if stage in STAGES else "imports"
    checks = [check for check in checks_passed if check in STAGES]
    # Alembic usa `raise CommandError(...) from None`: __context__ aún conserva
    # el error SQLAlchemy y su .orig. Recorrerlos sin imprimirlos ni el traceback.
    chain, seen = [], set()
    current = error
    while isinstance(current, Exception) and id(current) not in seen and len(chain) < 16:
        seen.add(id(current))
        chain.append(current)
        current = (getattr(current, "orig", None) or current.__cause__ or current.__context__)
    original = next((item for item in chain if isinstance(item, DiagnosticStop)), None)
    original = original or next((item for item in chain if re.fullmatch(
        r"[0-9A-Z]{5}", str(getattr(item, "pgcode", "")),
    )), chain[-1])
    if isinstance(original, DiagnosticStop):
        diagnostic = {"stage": safe_stage, "category": "validation", "code": original.code,
                      "error_type": "ValueError", "sqlstate": None}
    elif isinstance(original, ImportError):
        diagnostic = {"stage": safe_stage, "category": "client", "code": "DEPENDENCY_IMPORT_FAILED",
                      "error_type": "ImportError", "sqlstate": None}
    else:
        try:
            from alembic.util import CommandError
            from app.cli.verify_neon import connection_diagnostic
            if isinstance(original, CommandError):
                diagnostic = {"stage": safe_stage, "category": "alembic", "code": "ALEMBIC_COMMAND_FAILED",
                              "error_type": "CommandError", "sqlstate": None}
            else:
                diagnostic = connection_diagnostic(original, safe_stage)
        except Exception:
            diagnostic = {"stage": safe_stage, "category": "client", "code": "DIAGNOSTIC_UNAVAILABLE",
                          "error_type": "UnexpectedError", "sqlstate": None}
    diagnostic["code"] = {
        "55P03": "LOCK_NOT_AVAILABLE", "57014": "QUERY_CANCELLED",
        "42P07": "RELATION_ALREADY_EXISTS", "42710": "OBJECT_ALREADY_EXISTS",
        "42P01": "RELATION_NOT_FOUND",
    }.get(diagnostic["sqlstate"], diagnostic["code"])
    return {"status": "stopped", **diagnostic, "checks_passed": checks}


def inspect_revision(revision, progress):
    """Sólo SELECT/SHOW/SET LOCAL dentro de READ ONLY; salida mediante rollback."""
    checks = progress["checks_passed"]

    def start(stage):
        progress["stage"] = stage

    def passed():
        checks.append(progress["stage"])

    start("imports")
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.cli.verify_neon import runtime_versions
    from app.db.connection import cloud_target_from_environment, create_database_engine
    from migrations.validation import validate_initial_database, validate_migration_target
    passed()
    progress["runtime"] = runtime_versions()
    start("configuration")
    target = cloud_target_from_environment()
    if target is None or target.expected_database != "mitbol_cloud_dev" or target.expected_host != EXPECTED_HOST:
        raise DiagnosticStop("TARGET_NOT_APPROVED")
    url = target.validate_url(os.environ["DATABASE_URL"])
    if url.username != "neondb_owner" or "-pooler." in url.host:
        raise DiagnosticStop("OPERATOR_NOT_APPROVED")
    passed()
    start("alembic_graph")
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    if script.get_revision(revision).down_revision != PREVIOUS[revision]:
        raise DiagnosticStop("ALEMBIC_GRAPH_MISMATCH")
    passed()
    start("engine")
    engine = create_database_engine(url, target=target, read_only=False)
    passed()
    try:
        start("connect")
        with engine.connect() as db:
            passed()
            start("readonly_setup")
            db.execution_options(postgresql_readonly=True)
            # Antes de cualquier SELECT sobre esta conexión SQLAlchemy. El hook
            # de conexión compartido sólo valida el driver/base mediante SELECT.
            db.exec_driver_sql("SET TRANSACTION READ ONLY")
            db.exec_driver_sql("SET LOCAL search_path=public")
            db.exec_driver_sql("SET LOCAL lock_timeout='5s'")
            db.exec_driver_sql("SET LOCAL statement_timeout='15s'")
            if db.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
                raise DiagnosticStop("READONLY_NOT_ACTIVE")
            passed()
            start("target_tls")
            target.validate_connection(db)
            passed()
            start("identity")
            if db.exec_driver_sql("SELECT current_user,session_user").one() != ("neondb_owner",) * 2:
                raise DiagnosticStop("IDENTITY_MISMATCH")
            passed()
            start("postgres_version")
            if int(db.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 != 18:
                raise DiagnosticStop("POSTGRES_VERSION_MISMATCH")
            passed()
            start("schema")
            if db.exec_driver_sql("SELECT current_schema()").scalar_one() != "public":
                raise DiagnosticStop("SCHEMA_MISMATCH")
            passed()
            start("permissions")
            if db.exec_driver_sql("SHOW default_transaction_read_only").scalar_one() != "off":
                raise DiagnosticStop("DEFAULT_READONLY_ENABLED")
            if not db.exec_driver_sql(
                "SELECT has_schema_privilege(current_user,'public','USAGE') "
                "AND has_schema_privilege(current_user,'public','CREATE') "
                "AND has_table_privilege(current_user,'public.alembic_version','SELECT') "
                "AND has_table_privilege(current_user,'public.alembic_version','UPDATE')"
            ).scalar_one():
                raise DiagnosticStop("OPERATOR_PERMISSIONS_MISSING")
            passed()
            start("revision_before")
            if db.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalars().all() != [PREVIOUS[revision]]:
                raise DiagnosticStop("PREVIOUS_REVISION_MISMATCH")
            passed()
            start("alembic_validation")
            validate_migration_target(db, target)
            validate_initial_database(db)
            passed()
            start("readonly_rollback")
            db.rollback()
            passed()
    finally:
        engine.dispose()
    return {"status": "inspected_readonly", "revision": revision, "previous": PREVIOUS[revision],
            "checks_passed": checks, "runtime": progress["runtime"], "ddl_executed": False}


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise DiagnosticStop("ARGUMENTS_REJECTED")


def main(argv=None):
    progress = {"stage": "arguments", "checks_passed": []}
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        parser = SafeParser(description=__doc__)
        parser.add_argument("--revision", required=True, choices=tuple(PREVIOUS))
        args = parser.parse_args(argv)
        report = inspect_revision(args.revision, progress)
        code = 0
    except Exception as error:
        report = failure_report(error, progress["stage"], progress["checks_passed"])
        if "runtime" in progress:
            report["runtime"] = progress["runtime"]
        code = 1
    finally:
        logging.disable(previous_logging)
    print(json.dumps(report, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
