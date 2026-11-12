"""Entrada operativa reutilizable por un programador externo. No carga .env."""

import argparse
import asyncio
import json

from app.services.catalog_errors import CatalogSyncBusyError
from app.services.discount_sync_service import DiscountSyncBusyError
from app.services.sync_execution_errors import public_sync_error
from app.services.sync_worker_runtime import SyncWorkerConfig, SyncWorkerRuntime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["catalog", "discounts"], required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--skip-if-busy", action="store_true", help="Para jobs periódicos: omitir con código 0 si otra operación tiene el bloqueo.")
    args = parser.parse_args(argv)
    if args.skip_if_busy and not args.execute:
        parser.error("--skip-if-busy requiere --execute")
    runtime = None
    try:
        runtime = SyncWorkerRuntime(SyncWorkerConfig.from_environment())
        if args.execute:
            result = asyncio.run(runtime.execute(args.target)).model_dump(mode="json")
        else:
            runtime.preflight(args.target)
            result = dict(status="preflight_ok", target=args.target, writes=False, loyverse_contacted=False)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (CatalogSyncBusyError, DiscountSyncBusyError):
        print(json.dumps({"status": "skipped_busy" if args.skip_if_busy else "busy", "target": args.target, "writes": False}))
        return 0 if args.skip_if_busy else 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "message": "Comprueba el estado antes de repetir la operación."}))
        return 130
    except Exception as error:
        status, detail = public_sync_error(error)
        print(json.dumps({"status": "error", "code": status, "message": detail}, ensure_ascii=True))
        return 1
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
