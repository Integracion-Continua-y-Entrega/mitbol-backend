"""python -m app.cli.admin_accounts create | access ACCOUNT_ID; sin .env ni secretos en argv."""

from contextlib import contextmanager
from datetime import date
from getpass import GetPassWarning, getpass
import os
import re
import sys
import warnings

from sqlalchemy.orm import Session

from app.db.connection import (CloudDatabaseTarget, create_database_engine, environment_mode,
                               local_database_url, validate_local_connection)
from app.services.account_provisioning import AccountData, change_access, create_account, operator_identity


@contextmanager
def operator_transaction():
    mode = environment_mode(os.getenv("APP_ENV"))
    operator = os.getenv("MITBOL_PROVISION_OPERATOR", "")
    value = os.getenv("MITBOL_PROVISION_DATABASE_URL", "")
    target = None
    if mode == "cloud":
        target = CloudDatabaseTarget(os.getenv("EXPECTED_DATABASE", ""), os.getenv("EXPECTED_DATABASE_HOST", ""))
        url = target.validate_url(value)
    elif mode == "test":
        url = local_database_url(value)
        if operator != "mitbol_ci" or not re.fullmatch(r"mitbol_test_auth_[a-f0-9]{32}|mitbol_auth_local", url.database or ""):
            raise ValueError("Sólo una base aislada autorizada para este operador.")
    else:
        raise ValueError("Requiere APP_ENV test o cloud explícito; no se carga .env.")
    if not operator or operator != url.username or operator in {"mitbol_admin_writer", "mitbol_render_reader"}:
        raise ValueError("El operador debe ser explícito e independiente de la aplicación.")
    engine = create_database_engine(url, target=target)
    try:
        with Session(engine) as db, db.begin():
            if target:
                target.validate_connection(db.connection())
            else:
                validate_local_connection(db.connection())
            if operator_identity(db) != operator:
                raise ValueError("Identidad de operador inesperada.")
            yield db
    finally:
        engine.dispose()


def yes_no(prompt):
    value = input(prompt + " [si/no]: ").strip().lower()
    if value not in {"si", "no"}:
        raise ValueError("Activación explícita requerida.")
    return value == "si"


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if args == ["--help"]:
        print("Uso: python -m app.cli.admin_accounts create | access ACCOUNT_ID. Datos y contraseña por terminal interactiva.")
        return 0
    if not (args == ["create"] or len(args) == 2 and args[0] == "access" and args[1].isdigit() and 0 < int(args[1]) <= 2_147_483_647):
        print("Uso inválido. No se admiten contraseñas, URL ni datos personales como argumentos.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print("Se requiere terminal interactiva; se rechazan entrada redirigida y password con eco.", file=sys.stderr)
        return 2
    try:
        # Recoger datos antes de abrir transacción/conexión; no mantener locks al escribir.
        roles = tuple(input("Roles explícitos (admin,staff): ").strip().split(","))
        if args[0] == "create":
            data = AccountData(name=input("Nombre real: "), phone=input("Teléfono real: "),
                email=input("Correo: "), employee_code=input("Código laboral autorizado: "),
                hired_at=date.fromisoformat(input("Fecha laboral legítima AAAA-MM-DD: ")),
                active=yes_no("Activar cuenta y perfil laboral"), roles=roles)
            with warnings.catch_warnings():
                warnings.simplefilter("error", GetPassWarning)
                password = getpass("Contraseña nueva (15–1024 caracteres): ")
                confirmation = getpass("Repetir contraseña: ")
            if password != confirmation:
                raise ValueError("Las contraseñas no coinciden.")
            with operator_transaction() as db:
                result = create_account(db, data, password)
        else:
            active, employed = yes_no("Activar cuenta"), yes_no("Activar perfil laboral")
            with operator_transaction() as db:
                result = change_access(db, int(args[1]), account_active=active, employee_active=employed, role_codes=roles)
        print(f"Operación confirmada. Cuenta {result[0]}; referencia de auditoría {result[1]}.")
        return 0
    except (Exception, KeyboardInterrupt):
        print("Operación no confirmada. Revise datos, duplicados, destino y permisos con el operador; consulte el estado antes de repetir.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
