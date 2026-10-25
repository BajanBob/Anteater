"""Drone Remote ID (ASTM F3411) over Bluetooth LE and Wi-Fi.

BLE: service data for UUID 0xFFFA. Wi-Fi: a vendor IE with OUI FA:0B:BC and
type 0x0D in beacons or NAN frames. Both carry an app code / counter followed
by 25-byte messages (or a message pack). Only the Basic ID (type 0) and
Location (type 1) messages are decoded; that's enough to name the drone and
place it.
"""

from __future__ import annotations

import struct
from typing import Any

from anteater.detections.base import Context, BLE, WIFI_AP, Detector, Finding, Observation
from anteater.geo.gnss import haversine_m
from anteater.scoring.severity import Severity

ODID_UUID = "fffa"
ODID_OUI = "fa0bbc"
MSG_LEN = 25
ID_TYPES = {0: "none", 1: "serial", 2: "caa_registration", 3: "utm_assigned", 4: "specific_session"}


def decode_message(msg: bytes) -> dict[str, Any]:
    if len(msg) < MSG_LEN:
        return {}
    mtype = msg[0] >> 4
    if mtype == 0:  # Basic ID
        id_type = msg[1] >> 4
        uas_id = msg[2:22].split(b"\x00")[0].decode("ascii", "replace").strip()
        return {"type": "basic_id", "id_type": ID_TYPES.get(id_type, str(id_type)), "uas_id": uas_id}
    if mtype == 1:  # Location
        lat_i, lon_i = struct.unpack_from("<ii", msg, 5)
        alt_raw = struct.unpack_from("<H", msg, 15)[0]  # geodetic altitude
        out: dict[str, Any] = {"type": "location"}
        if lat_i or lon_i:
            out["lat"] = lat_i * 1e-7
            out["lon"] = lon_i * 1e-7
        if alt_raw:
            out["alt_m"] = alt_raw * 0.5 - 1000.0
        return out
    if mtype == 0xF:  # message pack
        size, count = msg[1], msg[2]
        size = size or MSG_LEN
        merged: dict[str, Any] = {"type": "pack"}
        for i in range(count):
            part = msg[3 + i * size : 3 + (i + 1) * size]
            merged.update({k: v for k, v in decode_message(part).items() if k != "type"})
        return merged
    return {"type": f"msg{mtype}"}


def decode_payload(payload: bytes, *, ble: bool) -> dict[str, Any]:
    """BLE service data starts with app code 0x0D + counter; Wi-Fi IE body with counter."""
    if ble:
        if len(payload) < 2 or payload[0] != 0x0D:
            return {}
        body = payload[2:]
    else:
        body = payload[1:] if payload else b""
    return decode_message(body) if body else {}


class Drones(Detector):
    name = "drones"
    description = "ASTM F3411 Remote ID over BLE and Wi-Fi NAN"
    kinds = (BLE, WIFI_AP)

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.reported: set[str] = set()

    def _payloads(self, obs: Observation) -> list[tuple[bytes, bool]]:
        out: list[tuple[bytes, bool]] = []
        for uuid, data in (obs.extra.get("service_data") or {}).items():
            if str(uuid).lower().replace("0x", "")[-4:] == ODID_UUID:
                out.append((bytes.fromhex(str(data)), True))
        for ie in obs.extra.get("vendor_ies") or []:
            if not isinstance(ie, dict):
                continue
            if str(ie.get("oui", "")).lower().replace(":", "") == ODID_OUI and int(ie.get("type", 0x0D)) == 0x0D:
                out.append((bytes.fromhex(str(ie.get("data", ""))), False))
        return out

    def process(self, obs: Observation) -> list[Finding]:
        payloads = self._payloads(obs)
        if not payloads:
            return []
        info: dict[str, Any] = {}
        for data, ble in payloads:
            try:
                info.update({k: v for k, v in decode_payload(data, ble=ble).items() if k != "type"})
            except (ValueError, struct.error):
                continue
        key = str(info.get("uas_id") or obs.subject)
        if key in self.reported:
            return []
        self.reported.add(key)
        if "lat" in info and obs.lat is not None and obs.lon is not None:
            info["distance_m"] = round(haversine_m(obs.lat, obs.lon, info["lat"], info["lon"]))
        near = info.get("distance_m", 9e9) < 300
        return [
            self.finding(obs, Severity.HIGH if near else Severity.MEDIUM,
                         "drone broadcasting Remote ID", 0.7 if near else 0.5,
                         transport="ble" if obs.kind == BLE else "wifi", **info)
        ]
