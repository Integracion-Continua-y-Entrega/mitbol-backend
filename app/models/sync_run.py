"""HU-010: cola e historial independientes de los registros ORM anteriores."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, MetaData, SmallInteger, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base


class SyncBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class SyncRun(SyncBase):
    __tablename__ = "SyncRuns"
    __table_args__ = (
        CheckConstraint("catalog_key = 1", name="ck_SyncRuns_singleton_catalog"),
        CheckConstraint("source IN ('manual', 'scheduled', 'webhook')", name="ck_SyncRuns_source"),
        CheckConstraint("status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed')", name="ck_SyncRuns_status"),
        CheckConstraint("attempts BETWEEN 0 AND 3", name="ck_SyncRuns_attempts"),
        CheckConstraint("(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)", name="ck_SyncRuns_finished"),
        Index("uq_sync_runs_active", "catalog_key", unique=True,
              postgresql_where=text("status IN ('queued', 'running', 'retry_wait')")),
        Index("ix_sync_runs_created", "created_at", "run_id"),
        {"schema": "public"},
    )

    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    catalog_key: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    store_id: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'queued'"))
    # Identificador histórico sin FK: borrar una cuenta no borra el historial.
    requested_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    attempt_token: Mapped[UUID | None] = mapped_column(Uuid)
    result: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error_code: Mapped[str | None] = mapped_column(String(32))
