"""python -m app.cli.probe_loyverse: diagnóstico remoto sin acceso a PostgreSQL."""

import argparse
import asyncio
import json
from pathlib import Path

from app.services.catalog_probe import inspect_catalog
from app.services.loyverse_client import LoyverseAPIClient, LoyverseAPIError


async def run_probe(page_size: int) -> dict:
    from app.core.config import settings

    async with LoyverseAPIClient(
        access_token=settings.loyverse_access_token, page_size=page_size,
    ) as client:
        return await inspect_catalog(client, settings.loyverse_store_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Consulta Loyverse sin escribir en PostgreSQL.")
    parser.add_argument("--page-size", type=int, default=250)
    parser.add_argument("--output", type=Path, help="Archivo local opcional para el informe filtrado.")
    arguments = parser.parse_args()
    if not 1 <= arguments.page_size <= 250:
        parser.error("--page-size debe estar entre 1 y 250")
    try:
        report = asyncio.run(run_probe(arguments.page_size))
    except LoyverseAPIError as error:
        print(json.dumps({"status": "error", "message": str(error), "http_status": error.status_code}, ensure_ascii=True))
        return 1
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
