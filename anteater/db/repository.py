"""Repository: the only place that writes or queries tables."""

from __future__ import annotations

import socket
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, and_, bindparam, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from anteater.db import recursive as rq
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

    # ------------------------------------------------- recursive queries
    def _since(self, since_s: float) -> datetime:
        return utc(time.time() - since_s)

    async def _rows(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        stmt = text(sql).bindparams(bindparam("since", type_=DateTime(timezone=True)))
        async with self.sessions() as s:
            res = await s.execute(stmt, params)
            return [dict(r._mapping) for r in res]

    async def device_trail(
        self, subject: str, since_s: float = 86400.0, max_steps: int = rq.MAX_TRAIL_STEPS
    ) -> list[dict[str, Any]]:
        """One device's route, one row per grid cell it stayed in (recursive walk)."""
        rows = await self._rows(
            rq.TRAIL_SQL,
            subject=subject,
            since=self._since(since_s),
            max_steps=max(1, min(max_steps, rq.MAX_TRAIL_STEPS)),
        )
        for r in rows:
            r["arrived"] = _ts(rq.as_dt(r["arrived"]))
            r["departed"] = _ts(rq.as_dt(r["departed"]))
            r["stay_s"] = round(r["departed"] - r["arrived"], 3)
        return rows

    async def rotation_chains(
        self,
        since_s: float = 86400.0,
        gap_s: float = 120.0,
        kind: str = "wifi",
        max_depth: int = rq.MAX_CHAIN_DEPTH,
    ) -> list[dict[str, Any]]:
        """MAC addresses that are probably one phone rotating its address (recursive chain)."""
        rows = await self._rows(
            rq.chains_sql(self.engine.dialect.name),
            since=self._since(since_s),
            gap=float(gap_s),
            kind=kind,
            max_depth=max(1, min(max_depth, rq.MAX_CHAIN_DEPTH)),
        )
        return rq.group_chains(rows)

    async def timeline(
        self, since_s: float = 3600.0, width_s: float = 60.0, kind: str = ""
    ) -> list[dict[str, Any]]:
        """Sightings per fixed slot, silent slots included (recursive slot series)."""
        width = max(1.0, float(width_s))
        stop = time.time()
        start = stop - since_s
        rows = await self._rows(
            rq.slots_sql(self.engine.dialect.name),
            since=self._since(since_s),
            start=start,
            stop=stop,
            width=width,
            kind=kind,
            max_slots=rq.MAX_SLOTS,
        )
        return [
            {"t0": float(r["t0"]), "sightings": int(r["sightings"]), "devices": int(r["devices"])}
            for r in rows
        ]

    async def cell_counts(self, since_s: float = 86400.0) -> dict[str, int]:
        cutoff = self._since(since_s)
        async with self.sessions() as s:
            res = await s.execute(
                select(ObservationRow.cell, func.count(func.distinct(ObservationRow.subject)))
                .where(ObservationRow.ts >= cutoff, ObservationRow.cell.is_not(None))
                .group_by(ObservationRow.cell)
            )
            return {c: int(n) for c, n in res}

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
