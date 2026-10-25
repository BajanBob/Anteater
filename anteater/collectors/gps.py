"""GPS sources: gpsd (JSON over TCP, no extra dependency), a fixed position, or none."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from anteater.geo.gnss import Fix
from anteater.settings import GpsSettings

log = logging.getLogger(__name__)
STALE_AFTER_S = 10.0


class GpsSource:
    """Base: no fix ever."""

    def current(self) -> Fix | None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FixedGps(GpsSource):
    def __init__(self, lat: float, lon: float) -> None:
        self.lat, self.lon = lat, lon

    def current(self) -> Fix | None:
        return Fix(self.lat, self.lon, time.time())


class GpsdClient(GpsSource):
    """Watches gpsd and keeps the latest TPV fix. Reconnects forever."""

    def __init__(self, host: str = "localhost", port: int = 2947) -> None:
        self.host, self.port = host, port
        self._fix: Fix | None = None
        self._task: asyncio.Task[None] | None = None

    def current(self) -> Fix | None:
        if self._fix and time.time() - self._fix.ts <= STALE_AFTER_S:
            return self._fix
        return None

    @staticmethod
    def parse_tpv(msg: dict[str, object]) -> Fix | None:
        if msg.get("class") != "TPV" or int(msg.get("mode", 0) or 0) < 2:  # type: ignore[call-overload]
            return None
        lat, lon = msg.get("lat"), msg.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            return None
        alt = msg.get("altMSL", msg.get("alt"))
        speed = msg.get("speed")
        return Fix(
            lat=float(lat),
            lon=float(lon),
            ts=time.time(),
            alt_m=float(alt) if isinstance(alt, (int, float)) else None,
            speed_mps=float(speed) if isinstance(speed, (int, float)) else None,
        )

    async def _run(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                writer.write(b'?WATCH={"enable":true,"json":true};\n')
                await writer.drain()
                log.info("gpsd connected at %s:%d", self.host, self.port)
                while line := await reader.readline():
                    with contextlib.suppress(ValueError):
                        fix = self.parse_tpv(json.loads(line))
                        if fix:
                            self._fix = fix
                writer.close()
            except OSError as exc:
                log.debug("gpsd unavailable: %s", exc)
            await asyncio.sleep(5)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def make_gps(cfg: GpsSettings) -> GpsSource:
    if cfg.source == "fixed":
        if cfg.fixed_lat is None or cfg.fixed_lon is None:
            raise ValueError("gps.source = 'fixed' needs gps.fixed_lat and gps.fixed_lon")
        return FixedGps(cfg.fixed_lat, cfg.fixed_lon)
    if cfg.source == "gpsd":
        return GpsdClient(cfg.host, cfg.port)
    return GpsSource()
