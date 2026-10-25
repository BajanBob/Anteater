"""SSID anomalies: new BSSIDs on established SSIDs, malformed SSIDs, hidden<->broadcast flips."""

from __future__ import annotations

from anteater.detections.base import Context, WIFI_AP, Detector, Finding, Observation
from anteater.scoring.severity import Severity

MAX_SSID_BYTES = 32
# Networks with more APs than this are campus/enterprise; a new BSSID is normal there.
ENTERPRISE_BSSIDS = 4


def ssid_problems(ssid: str) -> list[str]:
    problems: list[str] = []
    if len(ssid.encode("utf-8", "surrogatepass")) > MAX_SSID_BYTES:
        problems.append("longer than 32 bytes")
    if any((ord(c) < 0x20 or ord(c) == 0x7F) for c in ssid):
        problems.append("contains control characters")
    if ssid and not ssid.strip("\x00"):
        problems.append("all NUL bytes (hidden SSID sent as padding)")
    elif "\x00" in ssid:
        problems.append("contains NUL bytes")
    return problems


class SsidAnomaly(Detector):
    name = "ssid_anomaly"
    description = "duplicate SSIDs on new BSSIDs, non-printable/overlong SSIDs, hidden<->broadcast flips"
    kinds = (WIFI_AP,)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.first_seen: dict[str, float] = {}  # ssid -> first time seen
        self.bssids: dict[str, set[str]] = {}  # ssid -> bssids
        self.hidden_state: dict[str, bool] = {}  # bssid -> last hidden flag
        self.reported_malformed: set[str] = set()

    def process(self, obs: Observation) -> list[Finding]:
        out: list[Finding] = []
        bssid = obs.subject

        if obs.hidden is not None:
            prev = self.hidden_state.get(bssid)
            if prev is not None and prev != obs.hidden:
                direction = "hidden -> broadcast" if prev else "broadcast -> hidden"
                out.append(
                    self.finding(obs, Severity.LOW, f"SSID visibility flip ({direction})", 0.3,
                                 ssid=obs.ssid, previous_hidden=prev)
                )
            self.hidden_state[bssid] = obs.hidden

        ssid = obs.ssid
        if not ssid:
            return out

        problems = ssid_problems(ssid)
        if problems and bssid not in self.reported_malformed:
            self.reported_malformed.add(bssid)
            out.append(
                self.finding(obs, Severity.MEDIUM, "malformed SSID", 0.5,
                             ssid=repr(ssid), problems=problems)
            )

        warmup = self.ctx.settings.detect.ssid_warmup_seconds
        first = self.first_seen.setdefault(ssid, obs.ts)
        known = self.bssids.setdefault(ssid, set())
        if bssid not in known:
            established = obs.ts - first >= warmup and len(known) >= 1
            if established and len(known) < ENTERPRISE_BSSIDS:
                out.append(
                    self.finding(obs, Severity.MEDIUM, "new BSSID for an established SSID", 0.5,
                                 ssid=ssid, known_bssids=sorted(known),
                                 ssid_age_s=round(obs.ts - first))
                )
            known.add(bssid)
        return out
