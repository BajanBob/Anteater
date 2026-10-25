"""IMSI-catcher (fake base station) indicators.

Input is ``kind: cell`` observations of the serving cell with these extras:
``rat`` (GSM/UMTS/LTE/NR), ``cipher`` (A5/0..A5/4, for GSM), ``arfcn``,
``mcc``, ``mnc``, ``lac``, ``cid`` and optionally ``neighbours`` (count).
No single indicator proves anything; several together are worth acting on.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from anteater.detections.base import Context, CELL, Detector, Finding, Observation
from anteater.paths import config_file
from anteater.scoring.severity import Severity

RAT_RANK = {"GSM": 2, "2G": 2, "UMTS": 3, "3G": 3, "LTE": 4, "4G": 4, "NR": 5, "5G": 5}
DOWNGRADE_WINDOW_S = 120.0

# Valid GSM ARFCNs per ITU region (GSM 850/900/1800/1900)
GSM_ARFCNS = {
    1: [(0, 124), (975, 1023), (512, 885)],
    2: [(128, 251), (512, 810)],
    3: [(0, 124), (975, 1023), (512, 885), (128, 251)],
}


def arfcn_valid(arfcn: int, region: int) -> bool:
    return any(lo <= arfcn <= hi for lo, hi in GSM_ARFCNS.get(region, GSM_ARFCNS[1]))


def load_known_cells(path: Path | None) -> set[str]:
    """Optional config/cells.yaml: ``cells: ["310-260-1234-56789", ...]``."""
    if path is None or not path.is_file():
        return set()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(c) for c in doc.get("cells", [])}


class ImsiCatcher(Detector):
    name = "imsi_catcher"
    description = "2G downgrades, A5/1 cipher, rogue ARFCN, unregistered LAC/CID"
    kinds = (CELL,)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.known = load_known_cells(config_file("cells.yaml"))
        self.last_rat: tuple[float, int] | None = None
        self.lac_by_cell: dict[str, set[str]] = {}
        self.reported: set[tuple[str, str]] = set()

    def _once(self, tower: str, tag: str) -> bool:
        if (tower, tag) in self.reported:
            return False
        self.reported.add((tower, tag))
        return True

    def process(self, obs: Observation) -> list[Finding]:
        x = obs.extra
        if x.get("event"):  # paging events belong to cell_ping_anomaly
            return []
        tower = obs.subject
        rat = RAT_RANK.get(str(x.get("rat", "")).upper(), 0)
        out: list[Finding] = []

        if rat and self.last_rat:
            prev_ts, prev_rat = self.last_rat
            if prev_rat >= 4 and rat == 2 and obs.ts - prev_ts <= DOWNGRADE_WINDOW_S and self._once(tower, "downgrade"):
                out.append(self.finding(obs, Severity.HIGH, "forced downgrade to 2G", 0.7,
                                        from_rat=prev_rat, seconds=round(obs.ts - prev_ts)))
        if rat:
            self.last_rat = (obs.ts, rat)

        cipher = str(x.get("cipher", "")).upper().replace(" ", "")
        if rat == 2 and cipher:
            if cipher in ("A5/0", "NONE") and self._once(tower, "a50"):
                out.append(self.finding(obs, Severity.CRITICAL, "GSM with no encryption (A5/0)", 0.95, cipher=cipher))
            elif cipher in ("A5/1", "A5/2") and self._once(tower, "weak"):
                sev = Severity.HIGH if cipher == "A5/2" else Severity.MEDIUM
                out.append(self.finding(obs, sev, f"weak GSM cipher ({cipher})", 0.6, cipher=cipher))

        arfcn = x.get("arfcn")
        region = self.ctx.settings.termite.itu_region
        if rat == 2 and arfcn is not None and not arfcn_valid(int(arfcn), region) and self._once(tower, "arfcn"):
            out.append(self.finding(obs, Severity.MEDIUM, "GSM ARFCN outside the regional band plan", 0.5,
                                    arfcn=int(arfcn), itu_region=region))

        if rat == 2 and x.get("neighbours") == 0 and self._once(tower, "noneigh"):
            out.append(self.finding(obs, Severity.LOW, "2G cell advertises no neighbours", 0.3))

        if self.known and tower not in self.known and self._once(tower, "unknown"):
            out.append(self.finding(obs, Severity.MEDIUM, "cell not in your known-cells list", 0.45,
                                    tower=tower))

        lac = x.get("lac")
        if lac is not None and obs.cell:
            lacs = self.lac_by_cell.setdefault(obs.cell, set())
            lacs.add(str(lac))
            if len(lacs) > 2 and self._once(tower, f"lac{obs.cell}"):
                out.append(self.finding(obs, Severity.LOW, "unusual number of LACs in one grid square", 0.3,
                                        lacs=sorted(lacs)))
        return out
