"""Actualiza sólo la base desechable validada de las fixtures HTTP."""

from pathlib import Path
import re

from alembic import command
from sqlalchemy import text

from test_auth_persistence import config_for


def upgrade_runtime(engine):
    assert engine.url.host in {"127.0.0.1", "::1"}
    assert engine.url.username == "mitbol_ci"
    assert re.fullmatch(r"mitbol_test_auth_[a-f0-9]{32}", engine.url.database)
    with engine.begin() as db:
        command.upgrade(config_for(db), "20261115_0008")
        if db.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname IN ('mitbol_render_reader','mitbol_admin_writer')")) == 2:
            directory = Path(__file__).resolve().parents[1] / "docs/database"
            for filename, marker in (("update_admin_writer_discounts.sql", "hu009-reviewed"),
                                     ("update_admin_writer_sync.sql", "hu010-reviewed")):
                db.exec_driver_sql(f"SET LOCAL mitbol.approved_admin_write='{marker}'")
                source = (directory / filename).read_text(encoding="utf8")
                db.exec_driver_sql(source.replace("BEGIN;", "").replace("COMMIT;", ""),
                                   execution_options={"no_parameters": True})
            sql = (Path(__file__).resolve().parents[1] / "docs/database/update_catalog_categories.sql").read_text(encoding="utf8")
            db.exec_driver_sql("SET LOCAL mitbol.approved_category_write='hu008-categories-reviewed'")
            db.exec_driver_sql(sql.replace("BEGIN;", "").replace("COMMIT;", ""),
                               execution_options={"no_parameters": True})
