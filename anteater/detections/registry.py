"""Engine registry: name -> class, and building the active set from settings/CLI."""

from __future__ import annotations

from collections.abc import Iterable

from anteater.detections.base import Context, Detector
from anteater.detections.cell_ping_anomaly import CellPingAnomaly
from anteater.detections.drones import Drones
from anteater.detections.evil_twin import EvilTwin
from anteater.detections.fingerprints import FingerprintEngine
from anteater.detections.follow_score import FollowScore
from anteater.detections.grid_square import GridSquare
from anteater.detections.imsi_catcher import ImsiCatcher
from anteater.detections.karma_beacon import KarmaBeacon
from anteater.detections.radar import Radar
from anteater.detections.ssid_anomaly import SsidAnomaly

ENGINES: dict[str, type[Detector]] = {
    cls.name: cls
    for cls in (
        SsidAnomaly,
        EvilTwin,
        KarmaBeacon,
        FingerprintEngine,
        FollowScore,
        GridSquare,
        CellPingAnomaly,
        ImsiCatcher,
        Drones,
        Radar,
    )
}


def resolve(names: Iterable[str]) -> list[str]:
    """Expand 'all', accept comma lists and '-name' exclusions. Keeps registry order."""
    wanted: set[str] = set()
    excluded: set[str] = set()
    for raw in names:
        for part in str(raw).split(","):
            name = part.strip()
            if not name:
                continue
            if name == "all":
                wanted.update(ENGINES)
            elif name.startswith("-"):
                excluded.add(name[1:])
            elif name in ENGINES:
                wanted.add(name)
            else:
                raise ValueError(f"unknown engine {name!r}; choose from: all, {', '.join(ENGINES)}")
    if not wanted and excluded:
        wanted = set(ENGINES)
    return [n for n in ENGINES if n in wanted and n not in excluded]


def build(ctx: Context, names: Iterable[str]) -> list[Detector]:
    return [ENGINES[n](ctx) for n in resolve(names)]
