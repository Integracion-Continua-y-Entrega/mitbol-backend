"""Ejecuta pytest en un PostgreSQL desechable propio; no lee .env ni usa Neon.

Uso: python scripts/test_auth_phase1.py [argumentos adicionales de pytest]
Requiere binarios PostgreSQL 18 disponibles; MITBOL_TEST_PG_BIN puede indicarlos.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from uuid import uuid4


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    binary = os.getenv("MITBOL_TEST_PG_BIN")
    initdb = (str(Path(binary) / "initdb") if binary else shutil.which("initdb"))
    if not initdb and os.name == "nt":
        initdb = r"C:\Program Files\PostgreSQL\18\bin\initdb.exe"
    if not initdb:
        raise SystemExit("Configura MITBOL_TEST_PG_BIN con binarios PostgreSQL 18.")
    pg_ctl = str(Path(initdb).parent / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl"))
    # Conserva la convención de aislamiento ya validada por las fixtures de catálogo.
    root = Path(tempfile.gettempdir()).resolve() / ("mitbol-sync-preflight-" + uuid4().hex)
    root.mkdir()
    data = root / "data"
    child_env = {key: value for key, value in os.environ.items()
                 if not key.startswith(("PG", "MITBOL_", "AUTH_")) and key not in {
                     "DATABASE_URL", "PUBLIC_MENU_URL", "ADMIN_API_KEY", "LOYVERSE_ACCESS_TOKEN",
                     "EXPECTED_DATABASE", "EXPECTED_DATABASE_HOST", "RENDER", "GITHUB_ACTIONS",
                 }}
    child_env.update({
        "APP_ENV": "test", "ADMIN_ENABLED": "false", "PYTHONDONTWRITEBYTECODE": "1",
        "AUTH_ENABLED": "false", "WEB_CONCURRENCY": "1", "UVICORN_WORKERS": "1",
        "PYTHONIOENCODING": "utf-8",
        "DATABASE_URL": "postgresql+psycopg2://mitbol_ci@127.0.0.1:1/mitbol_unused",
        "MITBOL_SYNC_POSTGRES_TESTS": "1", "MITBOL_POSTGRES_TESTS": "1",
        "MITBOL_SYNC_TEST_CLUSTER": str(data), "MITBOL_AUTH_TEST_CLUSTER": str(data),
    })
    child_env["PATH"] = str(Path(initdb).parent) + os.pathsep + child_env.get("PATH", "")
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    command_number = 0

    def run(args, *, check=True):
        nonlocal command_number
        command_number += 1
        output_file = root / f"command-{command_number}.log"
        # PostgreSQL puede heredar handles en Windows. Un archivo evita que una
        # tubería capturada espere a que termine también el servidor en background.
        with output_file.open("w", encoding="utf-8") as output:
            process = subprocess.run(args, env=child_env, cwd=project, stdout=output,
                                     stderr=subprocess.STDOUT, **hidden)
        content = output_file.read_text(encoding="utf-8", errors="replace")
        with (root / "test-run.log").open("a", encoding="utf-8") as log:
            log.write(content)
        print(content, end="", flush=True)
        if check:
            process.check_returncode()
        return process

    started = False
    reader_started = False
    reader_data = None
    args = sys.argv[1:]
    with_reader = "--with-reader" in args
    args = [argument for argument in args if argument != "--with-reader"]
    try:
        run([initdb, "-D", str(data), "-U", "mitbol_ci", "--auth=trust", "--encoding=UTF8", "--no-locale"])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        options = f"-h 127.0.0.1 -p {port} -F"
        if os.name != "nt":
            options += f" -k {root}"
        run([pg_ctl, "-D", str(data), "-l", str(root / "postgres.log"), "-o", options, "-w", "start"])
        started = True
        child_env["MITBOL_SYNC_TEST_PORT"] = str(port)
        child_env["MITBOL_AUTH_TEST_PORT"] = str(port)
        if with_reader:
            # Las pruebas de ACL necesitan roles de clúster propios y no comparten
            # ese estado con las pruebas de catálogo/autenticación.
            reader_root = root.parent / ("mitbol-reader-hardening-" + uuid4().hex)
            reader_root.mkdir()
            reader_data = reader_root / "data"
            run([initdb, "-D", str(reader_data), "-U", "mitbol_ci", "--auth=trust", "--encoding=UTF8", "--no-locale"])
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                reader_port = probe.getsockname()[1]
            reader_options = f"-h 127.0.0.1 -p {reader_port} -F"
            if os.name != "nt":
                reader_options += f" -k {reader_root}"
            run([pg_ctl, "-D", str(reader_data), "-l", str(reader_root / "postgres.log"),
                 "-o", reader_options, "-w", "start"])
            reader_started = True
            child_env["MITBOL_READER_TEST_CLUSTER"] = str(reader_data)
            child_env["MITBOL_READER_TEST_PORT"] = str(reader_port)
            child_env["MITBOL_READER_TEST_PSQL"] = str(Path(initdb).parent / ("psql.exe" if os.name == "nt" else "psql"))
        args = args or ["tests/test_auth_security.py", "tests/test_auth_persistence.py",
                        "tests/test_auth_phase2_core.py", "tests/test_auth_api.py",
                        "tests/test_cloud_configuration.py", "tests/test_migrations.py"]
        return run([sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider",
                    "--basetemp", str(root / "pytest"), *args], check=False).returncode
    finally:
        # pg_ctl verifica el clúster por su directorio absoluto, nunca por puerto.
        failed_stops = []
        for cluster_data, was_started in ((reader_data, reader_started), (data, started)):
            if cluster_data is not None and (was_started or (cluster_data / "postmaster.pid").exists()):
                stopped = run([pg_ctl, "-D", str(cluster_data), "-m", "fast", "-w", "stop"], check=False)
                if stopped.returncode:
                    failed_stops.append(str(cluster_data))
        # Guardar los archivos facilita revisar evidencia; no borrar rutas calculadas.
        if failed_stops:
            raise RuntimeError("No se pudieron detener clústeres temporales: " + ", ".join(failed_stops))
        print(f"Clústeres temporales detenidos; archivos de prueba: {root}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
