from fastapi import Request
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.db.connection import create_database_engine
from app.core.config import settings

engine = create_database_engine(
    settings.database_url, target=settings.cloud_target, read_only=settings.app_env == "cloud",
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def session_factory_for(config):
    if config is settings:
        return SessionLocal
    application_engine = create_database_engine(
        config.database_url, target=config.cloud_target, read_only=config.app_env == "cloud",
    )
    return sessionmaker(autocommit=False, autoflush=False, bind=application_engine)


# Dependencia para obtener la sesión de la base de datos en las rutas
def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()
