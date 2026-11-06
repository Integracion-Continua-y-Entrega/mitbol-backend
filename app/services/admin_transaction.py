"""Integra los servicios existentes en la transacción de la sesión individual."""

import logging

from app.models import AuditEvent

logger = logging.getLogger(__name__)


def managed(db):
    return "auth_context" in db.info


def commit_changes(db):
    # La dependencia confirma sesión, cambio y auditoría antes de responder.
    if managed(db):
        db.flush()
    else:
        db.commit()


def rollback_changes(db):
    if not managed(db):
        db.rollback()


def audit_success(db, action, resource_type, resource_id):
    context = db.info.get("auth_context")
    if context is not None:
        db.add(AuditEvent(actor_account_id=context.principal.account_id,
                          action=action, outcome="success", request_id=context.request_id,
                          resource_type=resource_type, resource_id=str(resource_id)))


def track_image_storage(db, storage):
    if managed(db):
        db.info.setdefault("admin_image_storages", {})[storage.root] = storage


def recover_image_storages(db, transaction):
    """Tras commit/rollback consulta el estado real, incluso con commit incierto.

    Una nueva transacción toma el mismo bloqueo antes de resolver el diario.
    Nunca decide borrar/restaurar un archivo basándose en una respuesta antigua.
    """
    from app.services.product_enrichment_service import _storage_lock

    for storage in db.info.pop("admin_image_storages", {}).values():
        try:
            with transaction() as recovery_db:
                _storage_lock(recovery_db)
                storage.recover(recovery_db)
        except Exception as error:
            # No cambia el resultado confirmado. El diario privado permite reintento.
            logger.warning("Recuperación de imágenes pendiente (%s).", type(error).__name__)
