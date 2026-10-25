"""Evil twins: same SSID, weaker security or an out-of-family vendor."""

from __future__ import annotations

from anteater.detections.base import Context, WIFI_AP, Detector, Finding, Observation, is_randomised_mac
from anteater.scoring.severity import Severity

CRYPT_NAMES = ["open", "WEP", "WPA", "WPA2", "WPA3"]


def crypt_rank(crypt: str | None) -> int:
    """0 open, 1 WEP, 2 WPA, 3 WPA2, 4 WPA3. Unknown counts as open."""
    c = (crypt or "").upper()
    if "WPA3" in c or "SAE" in c or "OWE" in c:
        return 4
    if "WPA2" in c or "RSN" in c or "CCMP" in c:
        return 3
    if "WPA" in c or "TKIP" in c:
        return 2
    if "WEP" in c:
        return 1
    return 0


class EvilTwin(Detector):
    name = "evil_twin"
    description = "same SSID with a downgraded security config or a mismatched OUI"
    kinds = (WIFI_AP,)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        # ssid -> bssid -> (crypt rank, vendor)
        self.profiles: dict[str, dict[str, tuple[int, str | None]]] = {}
        self.reported: set[tuple[str, str]] = set()

    def process(self, obs: Observation) -> list[Finding]:
        if not obs.ssid or obs.crypt is None:
            return []
        ssid, bssid = obs.ssid, obs.subject
        rank = crypt_rank(obs.crypt)
        vendor = obs.vendor
        peers = self.profiles.setdefault(ssid, {})
        others = {b: p for b, p in peers.items() if b != bssid}
        peers[bssid] = (rank, vendor)
        if not others:
            return []

        out: list[Finding] = []
        best = max(p[0] for p in others.values())
        if rank < best and (ssid, f"crypt{rank}") not in self.reported:
            self.reported.add((ssid, f"crypt{rank}"))
            sev = Severity.HIGH if rank == 0 else Severity.MEDIUM
            out.append(
                self.finding(
                    obs, sev, "evil twin: security downgrade", 0.75 if rank == 0 else 0.55,
                    ssid=ssid, this_security=CRYPT_NAMES[rank], usual_security=CRYPT_NAMES[best],
                    other_bssids=sorted(others),
                )
            )

        known_vendors = {p[1] for p in others.values() if p[1]}
        if (
            vendor
            and not is_randomised_mac(bssid)
            and len(known_vendors) == 1
            and len(others) >= 2
            and vendor not in known_vendors
            and (ssid, bssid) not in self.reported
        ):
            self.reported.add((ssid, bssid))
            out.append(
                self.finding(
                    obs, Severity.MEDIUM, "evil twin: vendor mismatch", 0.5,
                    ssid=ssid, this_vendor=vendor, usual_vendor=next(iter(known_vendors)),
                )
            )
        return out
