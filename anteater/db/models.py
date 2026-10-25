"""SQLAlchemy models. Works on TimescaleDB/PostgreSQL (asyncpg) and SQLite (aiosqlite).

IDs are generated client-side (time-ordered 63-bit integers) so the
observations primary key can include ``ts``, which TimescaleDB hypertables
require, without relying on autoincrement (SQLite can't autoincrement a
composite key).
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_lock = threading.Lock()
_last = 0
_node = int.from_bytes(os.urandom(2), "big") & 0x3FF


def new_id() -> int:
    """Millisecond timestamp << 22 | 10-bit node | 12-bit sequence. Monotonic per process."""
    global _last
    with _lock:
        candidate = (int(time.time() * 1000) << 22) | (_node << 12)
        _last = candidate if candidate > _last else _last + 1
        return _last & 0x7FFF_FFFF_FFFF_FFFF


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON}


class ObservationRow(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=new_id, autoincrement=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(64))
    ssid: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str | None] = mapped_column(String(16))
    freq_mhz: Mapped[float | None] = mapped_column(Float)
    rssi: Mapped[float | None] = mapped_column(Float)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    cell: Mapped[str | None] = mapped_column(String(12))
    vendor: Mapped[str | None] = mapped_column(String(128))
    crypt: Mapped[str | None] = mapped_column(String(64))
    session: Mapped[str | None] = mapped_column(String(32))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_observations_subject_ts", "subject", "ts"),
        Index("ix_observations_cell", "cell"),
    )


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    engine: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str | None] = mapped_column(String(16))
    cell: Mapped[str | None] = mapped_column(String(12))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.5)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    host: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(200))
