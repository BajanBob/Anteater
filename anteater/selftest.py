"""Self-test: prove the install works without radios.

Runs a synthetic scenario that should trip every engine, checks config,
fingerprints, geohash, and (if the drivers are installed) a scratch SQLite
database. ``anteater selftest`` prints the report; tests reuse the scenario.
"""

from __future__ import annotations

import asyncio
import importlib.util
import struct
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from anteater.alerts.router import AlertRouter
from anteater.alerts.sinks import MemorySink
from anteater.detections import registry
from anteater.detections.base import BLE, CELL, SDR, WIFI_AP, Finding, Observation
from anteater.geo import grid
from anteater.pipeline import Pipeline, build_context
from anteater.scoring.baseline import Baseline
from anteater.settings import Settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    optional: bool = False


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok or c.optional for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "", optional: bool = False) -> None:
        self.checks.append(Check(name, ok, detail, optional))


# ------------------------------------------------------------ scenario
def _remote_id_ble(uas_id: str) -> str:
    msg = bytes([0x02, 0x12]) + uas_id.encode().ljust(20, b"\x00") + b"\x00\x00\x00"
    return (bytes([0x0D, 0x01]) + msg).hex()


def _remote_id_location(lat: float, lon: float) -> bytes:
    msg = bytearray(25)
    msg[0] = 0x12
    struct.pack_into("<ii", msg, 5, int(lat * 1e7), int(lon * 1e7))
    return bytes(msg)


def scenario(t0: float | None = None) -> list[Observation]:
    """Observations that exercise every engine. Positions walk ~15 km across a city."""
    t0 = t0 or time.time() - 3 * 3600
    lat0, lon0 = 51.5007, -0.1246
    obs: list[Observation] = []

    def at(minutes: float, step: int = 0) -> dict[str, float]:
        return {"ts": t0 + minutes * 60, "lat": lat0 + 0.03 * step, "lon": lon0 + 0.03 * step}

    # ssid_anomaly: established SSID gains a new BSSID; plus a malformed SSID
    obs.append(Observation(WIFI_AP, "00:11:22:00:00:01", ssid="CoffeeShop", crypt="WPA2", hidden=False, **at(0)))
    obs.append(Observation(WIFI_AP, "00:11:22:00:00:02", ssid="CoffeeShop", crypt="WPA2", hidden=False, **at(10)))
    obs.append(Observation(WIFI_AP, "00:11:22:00:00:03", ssid="bad\x01ssid", crypt="WPA2", **at(1)))
    # evil_twin: open AP copying a WPA2 network
    for i, (mac, crypt) in enumerate([("00:aa:00:00:00:01", "WPA2-PSK"), ("00:aa:00:00:00:02", "WPA2-PSK"),
                                      ("02:aa:00:00:00:03", "Open")]):
        obs.append(Observation(WIFI_AP, mac, ssid="HomeNet", crypt=crypt, **at(2 + i)))
    # karma_beacon
    obs.append(Observation(WIFI_AP, "02:bb:00:00:00:01", ssid="FreeWiFi", crypt="Open",
                           extra={"responded_ssids": ["HomeNet", "eduroam", "Airport_Free", "Starbucks"]}, **at(5)))
    # fingerprints: AirTag-style Find My advertisement (company 0x004C, type 0x12)
    obs.append(Observation(BLE, "c1:00:00:00:00:01", extra={"manufacturer_data": ["4c00121900aabbccdd"]}, **at(6)))
    # follow_score + grid_square: one tracker across five places over 80 minutes
    for step in range(5):
        obs.append(Observation(BLE, "c2:11:22:33:44:55", rssi=-60,
                               extra={"manufacturer_data": ["4c0012190011"]}, **at(20 * step, step)))
    # imsi_catcher: LTE then forced to 2G with A5/1
    obs.append(Observation(CELL, "234-15-100-2001", extra={"rat": "LTE", "mcc": 234, "mnc": 15}, **at(30)))
    obs.append(Observation(CELL, "234-15-999-9999", extra={"rat": "GSM", "cipher": "A5/1", "arfcn": 30,
                                                            "neighbours": 0}, **at(30.5)))
    # cell_ping_anomaly: a burst of pages
    for i in range(6):
        obs.append(Observation(CELL, "234-15-999-9999", extra={"event": "paging"}, **at(31 + i)))
    # drones: Remote ID basic ID over BLE
    obs.append(Observation(BLE, "d0:00:00:00:00:01", extra={"service_data": {"fffa": _remote_id_ble("SELFTEST-UAS-1")}},
                           **at(40)))
    # radar: X band carrier from termite
    obs.append(Observation(SDR, "sdr:10525.0", freq_mhz=10525.0, extra={"snr_db": 22.0}, **at(41)))
    return sorted(obs, key=lambda o: o.ts)


def selftest_settings(tmp: Path) -> Settings:
    s = Settings()
    s.general.data_dir = tmp
    s.general.allowlist = tmp / "none.yaml"
    s.alerts.min_severity = "info"
    s.detect.follow_min_minutes = 20
    return s


async def run_scenario(settings: Settings) -> list[Finding]:
    sink = MemorySink()
    router = AlertRouter([sink], min_severity="info", dedupe_s=0)
    ctx = build_context(settings, Baseline())
    pipeline = Pipeline(ctx, registry.build(ctx, ["all"]), router)
    for o in scenario():
        await pipeline.process(o)
    return sink.findings


# -------------------------------------------------------------- checks
def _try(report: Report, name: str, fn: Callable[[], str], optional: bool = False) -> None:
    try:
        report.add(name, True, fn(), optional)
    except Exception as exc:
        report.add(name, False, f"{type(exc).__name__}: {exc}", optional)


def run(settings: Settings, radio: bool = False) -> Report:
    report = Report()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        test_settings = selftest_settings(tmp)

        def geohash() -> str:
            cell = grid.encode(57.64911, 10.40744, 11)
            assert cell == "u4pruydqqvj", cell
            return cell

        _try(report, "geohash", geohash)

        def fingerprints() -> str:
            ctx = build_context(test_settings, Baseline())
            n = len(ctx.fingerprints.rules)
            assert n > 0, "no fingerprint rules loaded (config/vendors missing?)"
            return f"{n} rules, {len(ctx.oui)} OUI entries" + ("" if len(ctx.oui) else " (run `anteater fp sync`)")

        _try(report, "fingerprints", fingerprints)

        findings = asyncio.run(run_scenario(test_settings))
        fired = {f.engine for f in findings}
        for name in registry.ENGINES:
            report.add(f"engine {name}", name in fired,
                       f"{sum(f.engine == name for f in findings)} finding(s)" if name in fired else "did not fire")

        if importlib.util.find_spec("cv2"):
            def video() -> str:
                from anteater.alerts.video import render_hud

                frame = render_hud(findings[0])
                assert frame.shape == (1080, 1920, 3)
                return "1080p HUD frame rendered"

            _try(report, "video HUD", video, optional=True)

        have_db = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "aiosqlite"))
        if have_db:
            def database() -> str:
                from anteater.db.repository import Repository
                from anteater.db.session import create_all

                async def go() -> str:
                    repo = Repository.from_url(f"sqlite+aiosqlite:///{tmp / 'selftest.db'}")
                    try:
                        await create_all(repo.engine)
                        await repo.add_observations(scenario()[:5])
                        await repo.add_finding(findings[0])
                        rows, _ = await repo.observations_after((0.0, 0))
                        assert len(rows) == 5, len(rows)
                        assert (await repo.recent_findings())[0]["id"] == findings[0].id
                        return "sqlite write/read ok"
                    finally:
                        await repo.close()

                return asyncio.run(go())

            _try(report, "database", database)
        else:
            report.add("database", False, "sqlalchemy/aiosqlite not installed")

    if radio:
        def kismet() -> str:
            from anteater.collectors.kismet_eventbus import KismetCollector

            status = asyncio.run(KismetCollector(settings.kismet).check())
            return f"kismet up at {settings.kismet.http_url} ({status.get('kismet.system.devices.count', '?')} devices)"

        _try(report, "kismet", kismet)

        def gps() -> str:
            from anteater.collectors.gps import make_gps

            async def go() -> str:
                src = make_gps(settings.gps)
                await src.start()
                for _ in range(20):
                    fix = src.current()
                    if fix:
                        await src.stop()
                        return f"fix {fix.lat:.5f}, {fix.lon:.5f}"
                    await asyncio.sleep(0.5)
                await src.stop()
                raise RuntimeError(f"no fix from {settings.gps.source} within 10 s")

            return asyncio.run(go())

        _try(report, "gps", gps, optional=True)
    return report
