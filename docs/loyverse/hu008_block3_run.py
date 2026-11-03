"""Ejecución única del Bloque 3 autorizado; no es un endpoint ni una tarea periódica.

Publica la lectura y el plan antes de esperar la señal del operador. Reutiliza
la transacción y la persistencia del servicio sin duplicar sus UPSERT. Rechaza
una base que ya contenga catálogo o identidades ajenas a las del Bloque 1.
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
from app.services.catalog_errors import CatalogValidationError
from app.services.catalog_probe import fetch_catalog
from app.services.loyverse_client import LoyverseAPIClient
from app.services.sync_service import SyncService

EVIDENCE = Path(__file__).resolve().parent
REPORT = EVIDENCE / "hu008_block3_report.json"
BASELINE = EVIDENCE / "hu008_block1_report.json"


def encode(value):
    if isinstance(value, (Decimal, datetime)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    raise TypeError(f"Tipo no serializable: {type(value).__name__}")


def save(report):
    REPORT.write_text(json.dumps(report, default=encode, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def counts(connection):
    return {name: connection.scalar(select(func.count()).select_from(table))
            for name, table in Base.metadata.tables.items()}


def read_rows(connection):
    return {name: [dict(row) for row in connection.execute(
        select(table).order_by(*table.primary_key.columns),
    ).mappings()] for name, table in Base.metadata.tables.items()}


def require(condition, message):
    if not condition:
        raise CatalogValidationError(message)


def check_scope(plan, baseline):
    expected = {
        "products": {row["id"] for row in baseline["items"]},
        "variants": {v["id"] for row in baseline["items"] for v in row["variants"]},
        "modifiers": {row["id"] for row in baseline["modifiers"]},
        "complements": {v["id"] for row in baseline["modifiers"] for v in row["options"]},
    }
    for entity, identifiers in expected.items():
        require({row.loyverse_id for row in getattr(plan, entity)} == identifiers,
                f"Las identidades de {entity} difieren del catálogo de prueba autorizado.")
    expected_links = {(variant["id"], modifier_id)
                      for item in baseline["items"] for variant in item["variants"]
                      for modifier_id in item["modifier_ids"]}
    require(set(plan.assignments) == expected_links,
            "Las asociaciones difieren del catálogo de prueba autorizado.")
    deleted = {
        "products": set(plan.deleted_products), "variants": {row[0] for row in plan.deleted_variants},
        "modifiers": set(plan.deleted_modifiers), "complements": {row[0] for row in plan.deleted_complements},
    }
    for entity, identifiers in expected.items():
        require(not identifiers.intersection(deleted[entity]),
                f"Una baja afecta a las identidades autorizadas de {entity}.")
    # El destino se comprobó vacío: las bajas de IDs ajenos a los registros
    # activos no encuentran filas, ni necesitan crear datos históricos ficticios.


def verify_rows(connection, plan):
    data = read_rows(connection)
    specs = {
        "Products": plan.products, "ProductVariants": plan.variants,
        "ProductModifiers": plan.modifiers, "ProductComplements": plan.complements,
    }
    indexes = {}
    duplicates = {}
    for table_name, expected in specs.items():
        table = Base.metadata.tables[table_name]
        duplicates[table_name] = len(connection.execute(
            select(table.c.loyverse_id).group_by(table.c.loyverse_id).having(func.count() > 1),
        ).all())
        actual = {row["loyverse_id"]: row for row in data[table_name]}
        indexes[table_name] = actual
        require(set(actual) == {row.loyverse_id for row in expected}, f"Identidades incorrectas en {table_name}.")
        for expected_row in expected:
            actual_row = actual[expected_row.loyverse_id]
            require(actual_row["name"] == expected_row.name, f"Nombre incorrecto en {table_name}.")
            require(actual_row["synced_at"] == plan.synced_at, f"Fecha incorrecta en {table_name}.")
            if hasattr(expected_row, "price"):
                require(actual_row["price"] == expected_row.price, f"Precio incorrecto en {table_name}.")
            if hasattr(expected_row, "display_order"):
                require(actual_row["display_order"] == expected_row.display_order, f"Orden incorrecto en {table_name}.")
        require(duplicates[table_name] == 0, f"Duplicados en {table_name}.")

    products = indexes["Products"]
    variants = indexes["ProductVariants"]
    modifiers = indexes["ProductModifiers"]
    complements = indexes["ProductComplements"]
    for variant in plan.variants:
        actual = variants[variant.loyverse_id]
        require(actual["product_id"] == products[variant.product_loyverse_id]["product_id"],
                "Una variante tiene un producto incorrecto.")
        require(actual["is_available"] is True, "Preferencia local inicial de variante incorrecta.")
        require(actual["is_commercially_available"] == variant.is_commercially_available,
                "Disponibilidad comercial inicial de variante incorrecta.")
    for complement in plan.complements:
        actual = complements[complement.loyverse_id]
        require(actual["product_modifier_id"] == modifiers[complement.modifier_loyverse_id]["product_modifier_id"],
                "Un complemento tiene un grupo incorrecto.")
        require(actual["is_active"] is True, "Un complemento nuevo no está activo.")
    for product in products.values():
        require((product["description"], product["is_available"], product["is_active"],
                 product["is_featured"], product["is_new"]) == (None, True, True, False, True),
                "Los campos locales nuevos no coinciden con los defaults aprobados.")
    assignments = Base.metadata.tables["ProductModifierAssignments"]
    duplicates["ProductModifierAssignments"] = len(connection.execute(
        select(*assignments.primary_key.columns).group_by(*assignments.primary_key.columns).having(func.count() > 1),
    ).all())
    actual_links = {(row["product_variant_id"], row["product_modifier_id"])
                    for row in data["ProductModifierAssignments"]}
    expected_links = {(variants[v]["product_variant_id"], modifiers[m]["product_modifier_id"])
                      for v, m in plan.assignments}
    require(actual_links == expected_links and duplicates["ProductModifierAssignments"] == 0,
            "Asignaciones incorrectas o duplicadas.")
    return {"counts": counts(connection), "duplicate_groups": duplicates,
            "identities_prices_names_and_relationships_match": True, "rows": data}


async def run():
    if REPORT.exists():
        previous = json.loads(REPORT.read_text(encoding="utf-8"))
        require(previous.get("status") not in {"committed", "verified"},
                "Esta operación ya fue confirmada. No se permite repetir el Bloque 3.")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    url = make_url(settings.database_url)
    env_url = make_url(dotenv_values(".env")["DATABASE_URL"])
    require(url.get_backend_name() == "postgresql" and url == env_url and url.database == "mitbol_dev",
            "La configuración efectiva y .env deben coincidir y apuntar a mitbol_dev.")
    require(settings.loyverse_store_id == baseline["store"]["id"], "La tienda configurada no es la autorizada.")
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000"})
    readonly = create_engine(url, connect_args={
        "connect_timeout": 5, "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
    })
    report = {"status": "inspecting", "database": "mitbol_dev", "real_sync_commits": 0}
    try:
        with readonly.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            require(connection.scalar(select(func.current_database())) == "mitbol_dev", "Base incorrecta.")
            require(set(inspect(connection).get_table_names(schema="public")) == {*Base.metadata.tables, "alembic_version"},
                    "El esquema no contiene exclusivamente las tablas aprobadas.")
            report["revision"] = connection.scalar(text("SELECT version_num FROM public.alembic_version"))
            require(report["revision"] == "20261029_0001", "Revisión inesperada.")
            report["before_counts"] = counts(connection)
            require(not any(report["before_counts"].values()), "La primera importación requiere tablas vacías.")

        service = SyncService(sessionmaker(bind=engine), expected_database="mitbol_dev", store_id=settings.loyverse_store_id)
        # Es la misma secuencia interna de synchronize(), con una pausa para
        # publicar el plan revisable antes de la única llamada a _write().
        with service._transaction() as session:
            require(not any(counts(session).values()), "El catálogo cambió desde la inspección inicial.")
            async with LoyverseAPIClient(settings.loyverse_access_token, page_size=2) as client:
                snapshot = await fetch_catalog(client, settings.loyverse_store_id, include_deleted=True)
                report["http_requests"] = client.request_count
                report["pages_received"] = dict(client.page_counts)
            require(snapshot.store["name"] == "I.C.E", "El nombre actual de la tienda no es I.C.E.")
            plan = service._plan(snapshot)
            check_scope(plan, baseline)
            report.update(status="validated_before_write", store=snapshot.store,
                          downloaded_at_utc=snapshot.fetched_at, anomalies=[], plan=asdict(plan))
            report["deleted_ids_absent_from_empty_destination"] = {
                "products": list(plan.deleted_products), "variants": list(plan.deleted_variants),
                "modifiers": list(plan.deleted_modifiers), "complements": list(plan.deleted_complements),
            }
            report["notices"] = (["Hay bajas de registros nunca importados; no se crearán ni modificarán filas por ellas."]
                                 if any(report["deleted_ids_absent_from_empty_destination"].values()) else [])
            report["expected_changes"] = {
                table: {"insert": len(getattr(plan, entity)), "update": 0, "delete": 0}
                for table, entity in (("Products", "products"), ("ProductVariants", "variants"),
                                      ("ProductModifiers", "modifiers"), ("ProductComplements", "complements"),
                                      ("ProductModifierAssignments", "assignments"))
            }
            save(report)
            print(json.dumps(report, default=encode, ensure_ascii=True, indent=2), flush=True)
            print("PLAN VALIDADO. Pausa operativa para mostrar el resumen; autorización del usuario ya recibida.", flush=True)
            require(input("Operador: escribir EJECUTAR_UNA_VEZ para continuar: ").strip() == "EJECUTAR_UNA_VEZ",
                    "Ejecución cancelada antes de escribir.")
            # Revalidar la misma lectura en memoria y el estado vacío antes de DML.
            require(not any(counts(session).values()), "El catálogo cambió antes de la escritura.")
            require(service._plan(snapshot) == plan, "La instantánea cambió después de revisar el plan.")
            report["service_result"] = service._write(session, plan)
            report["verified_before_commit"] = verify_rows(session, plan)
        report.update(status="committed", real_sync_commits=1, committed_at_utc=datetime.now(timezone.utc))
        save(report)
        # Otra conexión y otra transacción: comprueba datos ya confirmados.
        with readonly.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            require(connection.scalar(select(func.current_database())) == "mitbol_dev", "Base de verificación incorrecta.")
            require(connection.scalar(text("SHOW transaction_read_only")) == "on", "La verificación debe ser de sólo lectura.")
            report["verified_after_commit"] = verify_rows(connection, plan)
        report["status"] = "verified"
        save(report)
        print(json.dumps({"status": report["status"], "real_sync_commits": 1,
                          "service_result": report["service_result"],
                          "verification": report["verified_after_commit"]}, default=encode, ensure_ascii=True, indent=2), flush=True)
    finally:
        engine.dispose()
        readonly.dispose()


if __name__ == "__main__":
    asyncio.run(run())
