"""Operación única de idempotencia autorizada; no modifica Loyverse.

Sólo permite el catálogo comercial idéntico al validado en el Bloque 3.
Reutiliza las operaciones del servicio y conserva evidencia antes/después.
La parte de actualización de precios no está incluida en este procedimiento.
"""

import asyncio
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

EVIDENCE = Path(__file__).resolve().parent
REPORT = EVIDENCE / "hu008_block4_idempotency_report.json"
BEFORE = EVIDENCE / "hu008_block4_idempotency_before.json"


def serializable(value):
    return json.loads(json.dumps(value, default=encode))


def save(report):
    REPORT.write_text(json.dumps(report, default=encode, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def business_rows(data):
    # synced_at es metadato de la nueva consulta; las demás columnas deben
    # permanecer exactamente iguales, incluidas PK, FK y campos locales.
    return {table: [{key: value for key, value in row.items() if key != "synced_at"}
                    for row in rows] for table, rows in data.items()}


def commercial_plan(plan):
    result = {"store_id": plan["store_id"]}
    for entity in ("products", "variants", "modifiers", "complements"):
        rows = [{key: Decimal(str(value)) if key == "price" else value
                 for key, value in row.items()} for row in plan[entity]]
        result[entity] = sorted(rows, key=lambda row: row["loyverse_id"])
    for key in ("assignments", "deleted_variants", "deleted_complements"):
        result[key] = sorted(tuple(row) for row in plan[key])
    for key in ("deleted_products", "deleted_modifiers"):
        result[key] = sorted(plan[key])
    return result


def verify_commercial_values(data, plan):
    products = {row["loyverse_id"]: row for row in data["Products"]}
    modifiers = {row["loyverse_id"]: row for row in data["ProductModifiers"]}
    variants = {row["loyverse_id"]: row for row in data["ProductVariants"]}
    for table, expected in (("Products", plan.products), ("ProductVariants", plan.variants),
                            ("ProductModifiers", plan.modifiers), ("ProductComplements", plan.complements)):
        indexed = {row["loyverse_id"]: row for row in data[table]}
        require(set(indexed) == {row.loyverse_id for row in expected}, f"Identidades inesperadas en {table}.")
        for item in expected:
            row = indexed[item.loyverse_id]
            require(row["name"] == item.name, f"Nombre inesperado en {table}.")
            if hasattr(item, "price"):
                require(Decimal(row["price"]) == item.price, f"Precio inesperado en {table}.")
            if hasattr(item, "display_order"):
                require(row["display_order"] == item.display_order, f"Orden inesperado en {table}.")
            if hasattr(item, "product_loyverse_id"):
                require(row["product_id"] == products[item.product_loyverse_id]["product_id"], "Producto padre incorrecto.")
            if hasattr(item, "modifier_loyverse_id"):
                require(row["product_modifier_id"] == modifiers[item.modifier_loyverse_id]["product_modifier_id"],
                        "Grupo padre incorrecto.")
    require(all(row["is_active"] for row in data["Products"] + data["ProductComplements"]),
            "Un estado comercial local difiere del catálogo activo.")
    expected_links = {(variants[v]["product_variant_id"], modifiers[m]["product_modifier_id"])
                      for v, m in plan.assignments}
    actual_links = {(row["product_variant_id"], row["product_modifier_id"])
                    for row in data["ProductModifierAssignments"]}
    require(actual_links == expected_links and len(actual_links) == len(data["ProductModifierAssignments"]),
            "Asignaciones inesperadas o repetidas.")


def comparison(before, after, synced_at):
    require(business_rows(before) == business_rows(after),
            "La repetición alteró identificadores, precios, relaciones o campos locales.")
    updates = {}
    for table, rows in after.items():
        old = before[table]
        updates[table] = sum(row.get("synced_at") != previous.get("synced_at")
                             for previous, row in zip(old, rows))
        if rows and "synced_at" in rows[0]:
            require(all(datetime.fromisoformat(row["synced_at"]) == synced_at for row in rows),
                    "La fecha guardada no coincide con la descarga nueva.")
    return {"counts_unchanged": True, "internal_and_external_ids_unchanged": True,
            "all_prices_unchanged": True, "relationships_unchanged": True,
            "local_fields_unchanged": True, "business_rows_equal": True,
            "changed_column": "synced_at", "sync_timestamps_updated": updates}


async def run():
    if REPORT.exists():
        previous = json.loads(REPORT.read_text(encoding="utf-8"))
        require(previous.get("status") not in {"writing", "committed", "verified"},
                "La escritura ya se inició o confirmó; revisar evidencia antes de otro intento.")
    baseline = json.loads(BEFORE.read_text(encoding="utf-8"))
    previous = json.loads((EVIDENCE / "hu008_block3_report.json").read_text(encoding="utf-8"))
    require(previous["status"] == "verified", "Falta la verificación del Bloque 3.")
    url = make_url(settings.database_url)
    require(url == make_url(dotenv_values(".env")["DATABASE_URL"])
            and url.get_backend_name() == "postgresql" and url.database == "mitbol_dev",
            "La configuración efectiva y .env deben apuntar a mitbol_dev.")
    require(settings.loyverse_store_id == previous["store"]["id"], "La tienda configurada cambió.")
    require(baseline["counts"] == previous["verified_after_commit"]["counts"], "Los conteos iniciales cambiaron.")
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000"})
    readonly = create_engine(url, connect_args={
        "connect_timeout": 5, "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
    })
    report = {"status": "inspecting", "part": "idempotency_only", "database": "mitbol_dev",
              "real_sync_commits": 0, "before_rows": baseline["before_rows"], "before_counts": baseline["counts"]}
    try:
        service = SyncService(sessionmaker(bind=engine), expected_database="mitbol_dev", store_id=settings.loyverse_store_id)
        with service._transaction() as session:
            require(set(inspect(session.connection()).get_table_names(schema="public")) == {*Base.metadata.tables, "alembic_version"},
                    "Las tablas difieren del esquema aprobado.")
            require(session.scalar(text("SELECT version_num FROM public.alembic_version")) == "20261029_0001",
                    "La revisión cambió.")
            before = serializable(read_rows(session))
            require(before == baseline["before_rows"], "El estado inicial cambió después de guardarlo.")
            report["before_sequences"] = [dict(row) for row in session.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY sequencename",
            )).mappings()]
            async with LoyverseAPIClient(settings.loyverse_access_token, page_size=2) as client:
                snapshot = await fetch_catalog(client, settings.loyverse_store_id, include_deleted=True)
                report.update(http_requests=client.request_count, pages_received=dict(client.page_counts))
            require(snapshot.store["name"] == "I.C.E", "La tienda no es I.C.E.")
            plan = service._plan(snapshot)
            report.update(store=snapshot.store, downloaded_at_utc=snapshot.fetched_at, plan=asdict(plan))
            actual = commercial_plan(asdict(plan))
            expected = commercial_plan(previous["plan"])
            differences = [key for key in expected if actual[key] != expected[key]]
            report["unexpected_remote_changes"] = differences
            if differences:
                report["status"] = "stopped_before_write"
                save(report)
                print(json.dumps(report, default=encode, ensure_ascii=True, indent=2), flush=True)
                require(False, "El catálogo remoto cambió; se detuvo la prueba antes de escribir.")
            verify_commercial_values(before, plan)
            report.update(status="validated_before_write", remote_business_catalog_unchanged=True,
                          expected_new_rows=0, expected_removed_rows=0)
            save(report)
            print(json.dumps({key: value for key, value in report.items() if key != "before_rows"},
                             default=encode, ensure_ascii=True, indent=2), flush=True)
            require(input("Operador: escribir REPETIR_UNA_VEZ para aplicar la prueba autorizada: ").strip() == "REPETIR_UNA_VEZ",
                    "Cancelado antes de escribir.")
            require(serializable(read_rows(session)) == before, "El estado cambió antes de la escritura.")
            report["status"] = "writing"
            save(report)
            report["service_result"] = service._write(session, plan)
            report["verified_before_commit"] = comparison(before, serializable(read_rows(session)), plan.synced_at)
        report.update(status="committed", real_sync_commits=1, committed_at_utc=datetime.now(timezone.utc))
        save(report)
        with readonly.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            require(connection.scalar(select(func.current_database())) == "mitbol_dev", "Base de verificación incorrecta.")
            require(connection.scalar(text("SHOW transaction_read_only")) == "on", "La verificación debe ser de sólo lectura.")
            after = serializable(read_rows(connection))
            report["verified_after_commit"] = comparison(before, after, plan.synced_at)
            report["after_sequences"] = [dict(row) for row in connection.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY sequencename",
            )).mappings()]
        report.update(status="verified", after_rows=after, after_counts={table: len(rows) for table, rows in after.items()})
        save(report)
        print(json.dumps({key: value for key, value in report.items() if key not in {"before_rows", "after_rows", "plan"}},
                         default=encode, ensure_ascii=True, indent=2), flush=True)
    finally:
        engine.dispose()
        readonly.dispose()


if __name__ == "__main__":
    asyncio.run(run())
