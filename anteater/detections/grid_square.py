"""Grid-square baselines: novelty in familiar places, and repeat offenders.

Every observation is recorded in the shared Baseline by the pipeline. This
engine reads it: a device that has never been seen in a cell you visit often
is novel (INFO, useful for the map and for correlation), and a device that has
already raised findings turning up in a new cell is a repeat offence.
"""

from __future__ import annotations

from anteater.detections.base import Context, BLE, TPMS, WIFI_AP, WIFI_CLIENT, Detector, Finding, Observation
from anteater.scoring.severity import Severity, escalate


class GridSquare(Detector):
    name = "grid_square"
    description = "per-geohash baselines; novelty and repeat-offence scoring"
    kinds = (WIFI_AP, WIFI_CLIENT, BLE, TPMS)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.repeat_cells: dict[str, set[str]] = {}

    def process(self, obs: Observation) -> list[Finding]:
        cell = obs.cell
        if not cell or self.ctx.is_allowed(obs.subject):
            return []
        out: list[Finding] = []
        base = self.ctx.baseline
        new_here = obs.extra.get("_new_in_cell", False)

        if new_here and base.is_familiar(cell) and (obs.rssi is None or obs.rssi > -70):
            out.append(
                self.finding(obs, Severity.INFO, "new device in a familiar place", 0.15,
                             cell_sessions=len(base.cells[cell].sessions), rssi=obs.rssi)
            )

        prior = self.ctx.flagged.get(obs.subject, 0)
        if prior:
            cells = self.repeat_cells.setdefault(obs.subject, set())
            if cell not in cells:
                cells.add(cell)
                if len(cells) > 1:
                    sev = escalate(Severity.MEDIUM, len(cells))
                    out.append(
                        self.finding(obs, sev, "repeat offender seen in another grid square",
                                     min(0.95, 0.45 + 0.1 * len(cells)),
                                     prior_findings=prior, squares=len(cells))
                    )
        return out
