"""Cellular paging anomalies.

Input is ``kind: cell`` observations with ``extra.event`` set to ``paging``
(your identity was paged) or ``silent_sms`` (a type-0 SMS was delivered).
Anteater doesn't decode the air interface itself; feed these from a
baseband-diagnostics tool or termite. See docs/DETECTIONS.md for the format.
"""

from __future__ import annotations

import time
from collections import deque

from anteater.detections.base import Context, CELL, Detector, Finding, Observation
from anteater.scoring.severity import Severity

NIGHT_HOURS = range(0, 6)
BURST_WINDOW_S = 3600.0
BURST_MIN = 5


class CellPingAnomaly(Detector):
    name = "cell_ping_anomaly"
    description = "towers paging you at unusual hours, or from a single grid cell"
    kinds = (CELL,)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.pages: deque[float] = deque()
        self.tower_cells: dict[str, set[str]] = {}
        self.tower_pages: dict[str, int] = {}
        self.reported: set[tuple[str, str]] = set()

    def process(self, obs: Observation) -> list[Finding]:
        tower = obs.subject
        if obs.cell:
            self.tower_cells.setdefault(tower, set()).add(obs.cell)
        event = str(obs.extra.get("event", "")).lower()
        out: list[Finding] = []

        if event == "silent_sms":
            out.append(
                self.finding(obs, Severity.HIGH, "silent (type-0) SMS received", 0.8,
                             tower=tower, pid=obs.extra.get("pid"))
            )
            return out
        if event != "paging":
            return out

        self.tower_pages[tower] = self.tower_pages.get(tower, 0) + 1
        self.pages.append(obs.ts)
        while self.pages and obs.ts - self.pages[0] > BURST_WINDOW_S:
            self.pages.popleft()

        hour = time.localtime(obs.ts).tm_hour
        if hour in NIGHT_HOURS and len(self.pages) >= 3 and (tower, "night") not in self.reported:
            self.reported.add((tower, "night"))
            out.append(
                self.finding(obs, Severity.MEDIUM, "paged repeatedly at night", 0.5,
                             pages_last_hour=len(self.pages), local_hour=hour, tower=tower)
            )
        if len(self.pages) >= BURST_MIN and (tower, f"burst{len(self.pages) // BURST_MIN}") not in self.reported:
            self.reported.add((tower, f"burst{len(self.pages) // BURST_MIN}"))
            out.append(
                self.finding(obs, Severity.MEDIUM, "paging burst", 0.5,
                             pages_last_hour=len(self.pages), tower=tower)
            )
        cells = self.tower_cells.get(tower, set())
        if self.tower_pages[tower] >= 3 and len(cells) == 1 and (tower, "single") not in self.reported:
            self.reported.add((tower, "single"))
            out.append(
                self.finding(obs, Severity.LOW, "paging tower only ever seen in one grid cell", 0.3,
                             tower=tower, pages=self.tower_pages[tower], cell=next(iter(cells)))
            )
        return out
