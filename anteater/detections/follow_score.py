"""Follow score: devices that turn up in several of *your* places and sessions.

Places are geohash cells one level coarser than the configured grid (about
1.2 km at the default precision 7), so walking around one block doesn't
count as "several places". Home cells and allowlisted devices never score.
Fixed cell towers and SDR carriers are excluded; everything that can move
(phones, trackers, hotspots, TPMS sensors) is in.
"""

from __future__ import annotations

from anteater.detections.base import (
    Context,
    BLE,
    TPMS,
    WIFI_AP,
    WIFI_CLIENT,
    Detector,
    Finding,
    Observation,
    is_randomised_mac,
)
from anteater.scoring.severity import Severity


def follow_score(cells: int, sessions: int, span_min: float, min_cells: int, randomised: bool) -> float:
    """0..1. Driven by distinct places, boosted by distinct sessions and time span."""
    if cells < min_cells:
        return 0.0
    base = min(1.0, 0.35 + 0.12 * (cells - min_cells))
    base += min(0.25, 0.08 * max(0, sessions - 1))
    base += min(0.1, span_min / 600.0)
    if randomised:
        # randomised MACs rotate, so a long run under one address is weaker evidence
        base *= 0.8
    return round(min(1.0, base), 3)


class FollowScore(Detector):
    name = "follow_score"
    description = "devices that track you across sessions and grid squares"
    kinds = (WIFI_AP, WIFI_CLIENT, BLE, TPMS)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.precision = max(4, ctx.settings.general.grid_precision - 1)
        self.places: dict[str, set[str]] = {}
        self.first: dict[str, float] = {}
        self.sessions: dict[str, set[str]] = {}
        self.last_level: dict[str, Severity] = {}

    def process(self, obs: Observation) -> list[Finding]:
        cell = obs.cell
        if not cell or self.ctx.is_home(cell) or self.ctx.is_allowed(obs.subject):
            return []
        s = obs.subject
        self.places.setdefault(s, set()).add(cell[: self.precision])
        self.first.setdefault(s, obs.ts)
        if obs.session:
            self.sessions.setdefault(s, set()).add(obs.session)

        cfg = self.ctx.settings.detect
        cells = len(self.places[s])
        sessions = len(self.sessions.get(s, ()))
        span_min = (obs.ts - self.first[s]) / 60.0
        if span_min < cfg.follow_min_minutes and sessions < cfg.follow_min_sessions:
            return []
        score = follow_score(cells, sessions, span_min, cfg.follow_min_cells, is_randomised_mac(s))
        if score <= 0:
            return []
        level = Severity.from_score(score)
        if level <= self.last_level.get(s, Severity.INFO):
            return []
        self.last_level[s] = level
        return [
            self.finding(
                obs, level, "device following you across places", score,
                places=cells, sessions=sessions, span_minutes=round(span_min, 1),
                vendor=obs.vendor, ssid=obs.ssid, randomised_mac=is_randomised_mac(s),
            )
        ]
