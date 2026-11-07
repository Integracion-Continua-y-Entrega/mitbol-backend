"""Pool administrativo separado; TLS y perfil aprobado por conexión en cloud."""

from sqlalchemy.orm import sessionmaker

from app.db.connection import create_database_engine


def auth_session_factory_for(config):
    config.validate_auth_configuration()
    engine = (create_database_engine(config.auth_database_url, target=config.cloud_target, writer_profile=True)
              if config.app_env == "cloud" else create_database_engine(config.auth_database_url))
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
