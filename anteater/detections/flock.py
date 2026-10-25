"""Flock Safety heuristics on top of the rules in config/vendors/flock.yaml.

The YAML rules catch registered OUIs and known SSID patterns. These heuristics
add the weaker signals: a Flock-looking name on a device whose MAC is
randomised (so OUI lookup gives nothing), and the combination of a Flock-style
SSID with a cellular-modem vendor, which is how solar trailer units backhaul.
Treat heuristic hits as leads to verify, not proof.
"""

from __future__ import annotations

import re

from anteater.detections.base import BLE, WIFI_AP, Observation, is_randomised_mac
from anteater.detections.fingerprints import register_heuristic
from anteater.scoring.severity import Severity

_FLOCK_NAME = re.compile(r"(?i)\bflock\b")
_MODEM_VENDORS = re.compile(r"(?i)(sierra wireless|cradlepoint|quectel|telit|inseego|digi international)")


@register_heuristic
def flock_heuristics(obs: Observation) -> list[tuple[str, str, Severity, str]]:
    out: list[tuple[str, str, Severity, str]] = []
    if obs.kind not in (WIFI_AP, BLE):
        return out
    label = obs.ssid or str(obs.extra.get("name") or "")
    if label and _FLOCK_NAME.search(label) and is_randomised_mac(obs.subject):
        out.append(
            ("alpr", "Flock-named device with randomised MAC", Severity.MEDIUM,
             "name mentions Flock but the MAC is randomised, so the vendor can't be confirmed")
        )
    if obs.kind == WIFI_AP and obs.vendor and _MODEM_VENDORS.search(obs.vendor) and obs.hidden:
        out.append(
            ("alpr", "hidden AP on a cellular modem (possible ALPR backhaul)", Severity.LOW,
             "hidden SSID from an LTE modem vendor; common on camera trailers, also on buses and kiosks")
        )
    return out
