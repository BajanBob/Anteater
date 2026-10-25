"""Wireless camera heuristics on top of config/vendors/cameras.yaml.

Consumer cameras broadcast set-up or direct-connect SSIDs that start with the
brand name. Those are cheap to spot and worth a LOW alert: a new camera
SSID in a familiar place is more interesting than the 50th one in a city
centre, which is what the grid_square engine's baseline sorts out.
"""

from __future__ import annotations

import re

from anteater.detections.base import WIFI_AP, Observation
from anteater.detections.fingerprints import register_heuristic
from anteater.scoring.severity import Severity

_SETUP_SSID = re.compile(
    r"^(?:HIK[-_]|Hikvision|EZVIZ[-_]|Dahua|IPC[-_]|Reolink|Wyze|Arlo|Ring[-_ ]Setup|Blink[-_]|"
    r"eufy|Tapo[_-]Cam|IPCAM|IP[-_]?CAM|Imou[-_])",
    re.IGNORECASE,
)


@register_heuristic
def camera_heuristics(obs: Observation) -> list[tuple[str, str, Severity, str]]:
    if obs.kind != WIFI_AP or not obs.ssid:
        return []
    if _SETUP_SSID.match(obs.ssid):
        return [
            ("camera", "camera set-up / direct SSID", Severity.LOW,
             "SSID matches a consumer camera brand's set-up or direct-connect pattern")
        ]
    return []
