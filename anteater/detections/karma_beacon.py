"""KARMA / MANA-style APs and beacon floods.

A normal AP only answers probe requests for SSIDs it serves. A KARMA AP
answers *any* probed SSID so phones auto-join it. Kismet records the SSIDs an
AP beaconed (``beaconed_ssids``) and those it sent probe responses for
(``responded_ssids``); the collector puts both in ``Observation.extra``.
"""

from __future__ import annotations

from anteater.detections.base import WIFI_AP, Context, Detector, Finding, Observation
from anteater.scoring.severity import Severity

KARMA_MIN_FOREIGN = 3
FLOOD_MIN_SSIDS = 12


class KarmaBeacon(Detector):
    name = "karma_beacon"
    description = "APs that answer probes for SSIDs they never beaconed"
    kinds = (WIFI_AP,)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.beaconed: dict[str, set[str]] = {}
        self.responded: dict[str, set[str]] = {}
        self.reported: dict[str, int] = {}

    def process(self, obs: Observation) -> list[Finding]:
        bssid = obs.subject
        beaconed = self.beaconed.setdefault(bssid, set())
        responded = self.responded.setdefault(bssid, set())
        if obs.ssid:
            beaconed.add(obs.ssid)
        beaconed.update(s for s in obs.extra.get("beaconed_ssids", []) if s)
        responded.update(s for s in obs.extra.get("responded_ssids", []) if s)

        out: list[Finding] = []
        foreign = responded - beaconed
        level = 0
        if len(foreign) >= KARMA_MIN_FOREIGN:
            level = 2 if len(foreign) >= 2 * KARMA_MIN_FOREIGN else 1
            if self.reported.get(bssid, 0) < level:
                out.append(
                    self.finding(
                        obs,
                        Severity.HIGH if level == 2 else Severity.MEDIUM,
                        "KARMA-style AP: answers probes for SSIDs it never beacons",
                        0.8 if level == 2 else 0.6,
                        foreign_ssids=sorted(foreign)[:20],
                        beaconed_ssids=sorted(beaconed)[:20],
                    )
                )
        if len(beaconed) >= FLOOD_MIN_SSIDS and self.reported.get(bssid, 0) < 3:
            level = 3
            out.append(
                self.finding(
                    obs,
                    Severity.MEDIUM,
                    "beacon flood: one radio advertising many SSIDs",
                    0.5,
                    ssid_count=len(beaconed),
                    sample=sorted(beaconed)[:20],
                )
            )
        if level:
            self.reported[bssid] = max(level, self.reported.get(bssid, 0))
        return out
