"""Representación pública y estable del plan HU-008; sin I/O ni credenciales."""

from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json

from app.services.catalog_errors import CatalogValidationError
from app.services.catalog_transform import CatalogPlan


class CatalogPlanChanged(CatalogValidationError):
    def __init__(self, expected: dict, received: dict):
        super().__init__("El catálogo cambió respecto al preflight aprobado; no se ejecutó DML.")
        self.differences = {
            key: {"expected": expected.get(key), "received": value}
            for key, value in received.items() if expected.get(key) != value
        }


def _decimal_text(value):
    if not isinstance(value, Decimal):
        raise TypeError("Tipo no admitido en el manifiesto comercial")
    fixed = format(value, "f")
    return (fixed.rstrip("0").rstrip(".") if "." in fixed else fixed) if value else "0"


def catalog_manifest(plan: CatalogPlan, store: dict) -> dict:
    data = asdict(plan)
    data.pop("synced_at")
    data.pop("store_id")
    for key, rows in data.items():
        data[key] = sorted(rows, key=lambda row: row["loyverse_id"] if isinstance(row, dict) else row)
    data["store"] = {"id": plan.store_id, "name": store["name"]}
    # JSON normaliza tuplas; excluye tiempos de consulta, orden HTTP y escala decimal.
    return json.loads(json.dumps(data, default=_decimal_text, ensure_ascii=True))


def manifest_sha256(manifest: dict) -> str:
    return sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()
