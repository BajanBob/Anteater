"""Repository: the only place that writes or queries tables."""

from __future__ import annotations

import socket
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from anteater.db.models import FindingRow, ObservationRow, SessionRow, new_id, utc
from anteater.db.session import make_engine, make_sessionmaker
from anteater.detections.base import Finding, Observation
from anteater.scoring.severity import Severity


def _obs_row(o: Observation) -> ObservationRow:
    return ObservationRow(
        id=new_id(),
        ts=utc(o.ts),
        kind=o.kind,
        subject=o.subject,
        ssid=(o.ssid or None) and o.ssid[:64],
        channel=o.channel,
        freq_mhz=o.freq_mhz,
        rssi=o.rssi,
        lat=o.lat,
        lon=o.lon,
        cell=o.cell,
        vendor=(o.vendor or None) and o.vendor[:128],
        crypt=o.crypt,
        session=o.session,
        extra={k: v for k, v in o.extra.items() if not k.startswith("_")},
    )


def _row_obs(r: ObservationRow) -> Observation:
    return Observation(
        kind=r.kind,
        subject=r.subject,
        ts=_ts(r.ts),
        ssid=r.ssid,
        channel=r.channel,
        freq_mhz=r.freq_mhz,
        rssi=r.rssi,
        lat=r.lat,
        lon=r.lon,
        cell=r.cell,
        vendor=r.vendor,
        crypt=r.crypt,
        session=r.session,
        extra=dict(r.extra or {}),
    )


def _ts(dt: datetime) -> float:
    # SQLite hands back naive datetimes; they were written as UTC
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


def finding_dict(r: FindingRow) -> dict[str, Any]:
    return {
        "id": r.id,
        "ts": _ts(r.ts),
        "engine": r.engine,
        "severity": Severity(r.severity).label,
        "title": r.title,
        "subject": r.subject,
        "kind": r.kind,
        "cell": r.cell,
        "lat": r.lat,
        "lon": r.lon,
        "score": r.score,
        "evidence": r.evidence or {},
    }


class Repository:
    def __init__(self, engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.engine = engine
        self.sessions = sessions

    @classmethod
    def from_url(cls, url: str) -> Repository:
        engine = make_engine(url)
        return cls(engine, make_sessionmaker(engine))

    async def close(self) -> None:
        await self.engine.dispose()

    # ---------------------------------------------------------- writes
    async def add_observations(self, items: Sequence[Observation]) -> int:
        if not items:
            return 0
        async with self.sessions.begin() as s:
            s.add_all([_obs_row(o) for o in items])
        return len(items)

    async def add_finding(self, f: Finding) -> None:
        async with self.sessions.begin() as s:
            await s.merge(
                FindingRow(
                    id=f.id,
                    ts=utc(f.ts),
                    engine=f.engine,
                    severity=int(f.severity),
                    title=f.title[:200],
                    subject=f.subject,
                    kind=f.kind,
                    cell=f.cell,
                    lat=f.lat,
                    lon=f.lon,
                    score=f.score,
                    evidence=f.evidence,
                )
            )

    async def start_session(self, session_id: str, note: str | None = None) -> None:
        async with self.sessions.begin() as s:
            await s.merge(
                SessionRow(
                    id=session_id, started=utc(time.time()), host=socket.gethostname(), note=note
                )
            )

    async def end_session(self, session_id: str) -> None:
        async with self.sessions.begin() as s:
            await s.execute(
                update(SessionRow).where(SessionRow.id == session_id).values(ended=utc(time.time()))
            )

    # ---------------------------------------------------------- reads
    async def observations_after(
        self, cursor: tuple[float, int], limit: int = 1000
    ) -> tuple[list[Observation], tuple[float, int]]:
        """Observations after ``cursor`` = (ts, id), oldest first, plus the new cursor.

        Paging on (ts, id) rather than ts alone means rows that share a timestamp
        are never skipped, even when they straddle two pages.
        """
        ts, last_id = cursor
        t = utc(ts)
        async with self.sessions() as s:
            rows = list(
                await s.scalars(
                    select(ObservationRow)
                    .where(
                        or_(
                            ObservationRow.ts > t,
                            and_(ObservationRow.ts == t, ObservationRow.id > last_id),
                        )
                    )
                    .order_by(ObservationRow.ts, ObservationRow.id)
                    .limit(limit)
                )
            )
        if not rows:
            return [], cursor
        return [_row_obs(r) for r in rows], (_ts(rows[-1].ts), rows[-1].id)

    async def recent_findings(
        self, limit: int = 100, min_severity: int = 0
    ) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(FindingRow)
                .where(FindingRow.severity >= min_severity)
                .order_by(FindingRow.ts.desc())
                .limit(limit)
            )
            return [finding_dict(r) for r in rows]

    async def stats(self, since_s: float = 86400.0) -> dict[str, Any]:
        cutoff = utc(time.time() - since_s)
        async with self.sessions() as s:
            obs = await s.scalar(
                select(func.count()).select_from(ObservationRow).where(ObservationRow.ts >= cutoff)
            )
            devices = await s.scalar(
                select(func.count(func.distinct(ObservationRow.subject))).where(
                    ObservationRow.ts >= cutoff
                )
            )
            by_sev = await s.execute(
                select(FindingRow.severity, func.count())
                .where(FindingRow.ts >= cutoff)
                .group_by(FindingRow.severity)
            )
            by_engine = await s.execute(
                select(FindingRow.engine, func.count())
                .where(FindingRow.ts >= cutoff)
                .group_by(FindingRow.engine)
            )
            return {
                "window_s": since_s,
                "observations": obs or 0,
                "devices": devices or 0,
                "findings_by_severity": {Severity(k).label: v for k, v in by_sev.all()},
                "findings_by_engine": dict(by_engine.all()),
            }
