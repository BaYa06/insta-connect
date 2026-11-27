"""History storage (stage 5). Snapshots are append-only: each collection run INSERTs
new rows, it never updates or overwrites an earlier measurement."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, create_engine, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import Settings
from .errors import ConfigError


class Base(DeclarativeBase):
    pass


class Media(Base):
    __tablename__ = "media"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    media_type: Mapped[str | None] = mapped_column(String(32))
    media_product_type: Mapped[str | None] = mapped_column(String(32))
    caption: Mapped[str | None] = mapped_column(Text)
    permalink: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MediaMetricSnapshot(Base):
    __tablename__ = "media_metric_snapshots"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(64))
    # Stored exactly as the API returned it (watch-time metrics: milliseconds). NULL = no data.
    value: Mapped[float | None] = mapped_column(Numeric(20, 3))
    raw_unit: Mapped[str | None] = mapped_column(String(16))   # 'count' | 'milliseconds'
    reason: Mapped[str | None] = mapped_column(Text)           # why value is NULL
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_snap_media_metric_time", "media_id", "metric", "captured_at"),)


class TokenEvent(Base):
    __tablename__ = "token_events"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(__import__("sqlalchemy").Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32))  # status_check | check_failed
    is_valid: Mapped[bool | None]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    days_left: Mapped[float | None] = mapped_column(Numeric(8, 2))
    details: Mapped[dict | None] = mapped_column(JSON)   # never contains the token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def make_engine(settings: Settings) -> Engine:
    if not settings.database_url:
        raise ConfigError("DATABASE_URL is not set.")
    return create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
