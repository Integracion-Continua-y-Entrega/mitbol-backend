"""Prueba autorizada: sólo Doble pasa de 119 a 129 en mitbol_dev.

No modifica Loyverse ni incorpora una interfaz pública. Compara la descarga
con la primera parte, reutiliza SyncService y conserva evidencia del commit.
"""

import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import Base
from app.services.catalog_probe import fetch_catalog
from app.services.loyverse_client import LoyverseAPIClient
from app.services.sync_service import SyncService
from docs.loyverse.hu008_block3_run import encode, read_rows, require
from docs.loyverse.hu008_block4_idempotency_run import (
    business_rows, commercial_plan, serializable, verify_commercial_values,
)

EVIDENCE = Path(__file__).resolve().parent
REPORT = EVIDENCE / "hu008_block4_price_update_report.json"
BEFORE = EVIDENCE / "hu008_block4_price_update_before.json"
TARGET_ID = "246aad80-4540-4c1a-8877-2e31bc03d668"
OLD_PRICE = Decimal("119.00")
NEW_PRICE = Decimal("129.00")


def save(report):
    REPORT.write_text(json.dumps(report, default=encode, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def target_variant(data):
    matches = [row for row in data["ProductVariants"] if row["loyverse_id"] == TARGET_ID]
    require(len(matches) == 1, "Doble debe existir exactamente una vez.")
    row = matches[0]
    require(row["product_variant_id"] == 5 and row["product_id"] == 3 and row["name"] == "Doble",
            "Doble no conserva su ID 5, producto padre 3 o nombre.")
    return row


def verify_update(before, after, synced_at):
    old = target_variant(before)
    new = target_variant(after)
    require(Decimal(old["price"]) == OLD_PRICE and Decimal(new["price"]) == NEW_PRICE,
            "La transición de precio no es exactamente 119.00 a 129.00.")
    expected = deepcopy(business_rows(before))
    # El importe nuevo ya se comprobó numéricamente. Se usa su representación
    # guardada para permitir 129 y 129.00 sin permitir otro cambio de negocio.
    target_variant(expected)["price"] = new["price"]
    require(expected == business_rows(after),
            "Se alteró otro precio, identidad, relación o campo local.")
    for rows in after.values():
        if rows and "synced_at" in rows[0]:
            require(all(datetime.fromisoformat(row["synced_at"]) == synced_at for row in rows),
                    "La fecha de sincronización no corresponde a la descarga validada.")
    return {"counts_unchanged": True, "internal_and_external_ids_unchanged": True,
            "target_internal_id": new["product_variant_id"], "target_product_id": new["product_id"],
            "old_price": old["price"], "new_price": new["price"],
            "other_prices_unchanged": True, "relationships_unchanged": True,
            "local_fields_unchanged": True, "business_fields_changed": ["ProductVariants[5].price"],
            "metadata_updated": "synced_at"}


async def run():
    if REPORT.exists():
        previous_run = json.loads(REPORT.read_text(encoding="utf-8"))
        require(previous_run.get("status") not in {"writing", "committed", "verified"},
                "Esta escritura ya se inició o confirmó; inspeccionar antes de repetir.")
    previous = json.loads((EVIDENCE / "hu008_block4_idempotency_report.json").read_text(encoding="utf-8"))
    require(previous["status"] == "verified", "La primera parte no está verificada.")
    url = make_url(settings.database_url)
    require(url == make_url(dotenv_values(".env")["DATABASE_URL"])
            and url.get_backend_name() == "postgresql" and url.database == "mitbol_dev",
            "La configuración efectiva y .env deben coincidir y apuntar a mitbol_dev.")
    require(settings.loyverse_store_id == previous["store"]["id"], "La tienda configurada cambió.")
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000"})
    readonly = create_engine(url, connect_args={
        "connect_timeout": 5, "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
    })
    report = {"status": "inspecting", "database": "mitbol_dev", "real_sync_commits": 0,
              "part": "price_update_only", "target_loyverse_id": TARGET_ID}
    try:
        with readonly.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            require(connection.scalar(select(func.current_database())) == "mitbol_dev", "Base inicial incorrecta.")
            before = serializable(read_rows(connection))
            require(before == previous["after_rows"], "PostgreSQL cambió desde la primera parte; revisar antes de escribir.")
            require(Decimal(target_variant(before)["price"]) == OLD_PRICE, "El precio inicial ya no es 119.00.")
            report.update(before_rows=before, before_counts={name: len(rows) for name, rows in before.items()})
            BEFORE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        service = SyncService(sessionmaker(bind=engine), expected_database="mitbol_dev", store_id=settings.loyverse_store_id)
        with service._transaction() as session:
            require(set(inspect(session.connection()).get_table_names(schema="public")) == {*Base.metadata.tables, "alembic_version"},
                    "Las tablas difieren del esquema aprobado.")
            require(session.scalar(text("SELECT version_num FROM public.alembic_version")) == "20261029_0001",
                    "La revisión del esquema cambió.")
            require(serializable(read_rows(session)) == before, "El estado local cambió desde la captura inicial.")
            async with LoyverseAPIClient(settings.loyverse_access_token, page_size=2) as client:
                snapshot = await fetch_catalog(client, settings.loyverse_store_id, include_deleted=True)
                report.update(http_requests=client.request_count, pages_received=dict(client.page_counts))
            require(snapshot.store["name"] == "I.C.E", "La tienda remota no es I.C.E.")
            plan = service._plan(snapshot)
            actual = commercial_plan(asdict(plan))
            expected = commercial_plan(previous["plan"])
            targets = [row for row in expected["variants"] if row["loyverse_id"] == TARGET_ID]
            require(len(targets) == 1 and targets[0]["price"] == OLD_PRICE, "La referencia previa de Doble es inválida.")
            targets[0]["price"] = NEW_PRICE
            differences = [key for key in expected if actual[key] != expected[key]]
            report.update(store=snapshot.store, downloaded_at_utc=snapshot.fetched_at,
                          plan=asdict(plan), unexpected_remote_changes=differences)
            if differences:
                report["status"] = "stopped_before_write"
                save(report)
                print(json.dumps({key: value for key, value in report.items() if key != "before_rows"},
                                 default=encode, ensure_ascii=True, indent=2), flush=True)
                require(False, "Loyverse no devuelve sólo el cambio autorizado a 129.00; no se escribió.")
            # La proyección local debe coincidir con el plan al cambiar sólo
            # el importe de Doble; los campos locales se verifican al escribir.
            remote_target = next(row for row in plan.variants if row.loyverse_id == TARGET_ID)
            require(remote_target.price == NEW_PRICE, "La tienda no devuelve 129.00 para Doble.")
            expected_local = deepcopy(before)
            target_variant(expected_local)["price"] = str(NEW_PRICE)
            verify_commercial_values(expected_local, plan)
            report.update(status="validated_before_write", expected_new_rows=0, expected_removed_rows=0,
                          expected_price_change={"product_variant_id": 5, "old_price": OLD_PRICE,
                                                 "new_price": NEW_PRICE, "other_prices_changed": 0})
            save(report)
            print(json.dumps({key: value for key, value in report.items() if key not in {"before_rows", "plan"}},
                             default=encode, ensure_ascii=True, indent=2), flush=True)
            require(input("Operador: escribir ACTUALIZAR_UNA_VEZ para aplicar el cambio autorizado: ").strip() == "ACTUALIZAR_UNA_VEZ",
                    "Cancelado antes de escribir.")
            require(serializable(read_rows(session)) == before, "El estado local cambió antes de escribir.")
            report["status"] = "writing"
            save(report)
            report["service_result"] = service._write(session, plan)
            report["verified_before_commit"] = verify_update(before, serializable(read_rows(session)), plan.synced_at)
        report.update(status="committed", real_sync_commits=1, committed_at_utc=datetime.now(timezone.utc))
        save(report)
        with readonly.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            require(connection.scalar(select(func.current_database())) == "mitbol_dev", "Base de verificación incorrecta.")
            require(connection.scalar(text("SHOW transaction_read_only")) == "on", "La verificación debe ser de sólo lectura.")
            after = serializable(read_rows(connection))
            report["verified_after_commit"] = verify_update(before, after, plan.synced_at)
        report.update(status="verified", after_rows=after, after_counts={name: len(rows) for name, rows in after.items()})
        save(report)
        print(json.dumps({key: value for key, value in report.items() if key not in {"before_rows", "after_rows", "plan"}},
                         default=encode, ensure_ascii=True, indent=2), flush=True)
    finally:
        engine.dispose()
        readonly.dispose()


if __name__ == "__main__":
    asyncio.run(run())
