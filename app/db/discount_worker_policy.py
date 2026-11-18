"""El mismo perfil HU-009 revisado se exige en la automatización."""

from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.db.connection import DatabaseTargetError


PRODUCTION_DATABASE = "mitbol_cloud_dev"
PRODUCTION_HOST = "ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech"


def discount_worker_policy_sql():
    try:
        source = (Path(__file__).resolve().parents[2] /
                  "docs/database/verify_discount_worker.sql").read_text(encoding="utf-8")
        start = source.index("DO $policy$")
        end = source.index("$policy$;", start) + len("$policy$;")
        return source[start:end]
    except (OSError, ValueError):
        raise DatabaseTargetError("DISCOUNT_WORKER_POLICY_UNAVAILABLE") from None


def validate_discount_worker_connection(connection, target):
    # El llamante abre una transacción READ ONLY. La marca LOCAL no persiste.
    target.validate_connection(connection)
    try:
        connection.exec_driver_sql("SET LOCAL mitbol.external_tls_verified='verify-full-channel-binding'")
        connection.exec_driver_sql(discount_worker_policy_sql(), execution_options={"no_parameters": True})
    except SQLAlchemyError:
        raise DatabaseTargetError("DISCOUNT_WORKER_POLICY_REJECTED") from None
