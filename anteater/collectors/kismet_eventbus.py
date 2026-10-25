"""Kismet collector.

Two channels, used together:

* **REST polling** of ``/devices/last-time/<ts>/devices.json`` with a field
  list. This is the reliable source of full device records.
* **Event bus** websocket (``/eventbus/events.ws``) for ``NEW_DEVICE`` and
  alert events, which wakes the poller early so new emitters show up within
  a second instead of on the next poll.

Kismet field names vary a little between releases; everything is read with
fallbacks, and missing fields simply stay ``None``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from anteater.collectors.gps import GpsSource
from anteater.detections.base import BLE, WIFI_AP, WIFI_CLIENT, Observation
from anteater.settings import KismetSettings

log = logging.getLogger(__name__)

FIELDS = [
    "kismet.device.base.macaddr",
    "kismet.device.base.type",
    "kismet.device.base.phyname",
    "kismet.device.base.manuf",
    "kismet.device.base.name",
    "kismet.device.base.commonname",
    "kismet.device.base.channel",
    "kismet.device.base.frequency",
    "kismet.device.base.crypt",
    "kismet.device.base.last_time",
    "kismet.device.base.signal/kismet.common.signal.last_signal",
    "dot11.device/dot11.device.last_beaconed_ssid_record",
    "dot11.device/dot11.device.advertised_ssid_map",
    "dot11.device/dot11.device.responded_ssid_map",
    "dot11.device/dot11.device.probed_ssid_map",
    "bluetooth.device",
]

TYPE_TO_KIND = {
    "wi-fi ap": WIFI_AP,
    "wi-fi wds ap": WIFI_AP,
    "wi-fi ad-hoc": WIFI_AP,
    "wi-fi client": WIFI_CLIENT,
    "wi-fi bridged": WIFI_CLIENT,
    "wi-fi device": WIFI_CLIENT,
    "btle": BLE,
    "bt": BLE,
    "bluetooth": BLE,
}


def _get(d: dict[str, Any], *keys: str) -> Any:
    """First present key. Kismet uses full dotted names, or the last path segment when simplified."""
    for k in keys:
        if k in d and d[k] not in (None, "", 0):
            return d[k]
        short = k.rsplit("/", 1)[-1]
        if short in d and d[short] not in (None, "", 0):
            return d[short]
    return None


def _ssids(ssid_map: Any) -> list[str]:
    """Kismet ssid maps are lists (or dicts) of records with dot11.*.ssid fields."""
    records = ssid_map.values() if isinstance(ssid_map, dict) else (ssid_map or [])
    out: list[str] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        s = _get(rec, "dot11.advertisedssid.ssid", "dot11.probedssid.ssid", "dot11.respondedssid.ssid")
        if isinstance(s, str) and s and s not in out:
            out.append(s)
    return out


def device_to_observation(dev: dict[str, Any]) -> Observation | None:
    mac = _get(dev, "kismet.device.base.macaddr")
    dtype = str(_get(dev, "kismet.device.base.type") or "").lower()
    kind = TYPE_TO_KIND.get(dtype)
    if not mac or kind is None:
        return None
    freq_khz = _get(dev, "kismet.device.base.frequency")
    beacon = _get(dev, "dot11.device.last_beaconed_ssid_record") or {}
    ssid = _get(beacon, "dot11.advertisedssid.ssid") if isinstance(beacon, dict) else None
    cloaked = _get(beacon, "dot11.advertisedssid.cloaked") if isinstance(beacon, dict) else None
    crypt = _get(dev, "kismet.device.base.crypt")
    if crypt is None and isinstance(beacon, dict):
        crypt = _get(beacon, "dot11.advertisedssid.crypt_string")

    extra: dict[str, Any] = {}
    if kind == WIFI_AP:
        extra["beaconed_ssids"] = _ssids(_get(dev, "dot11.device.advertised_ssid_map"))
        extra["responded_ssids"] = _ssids(_get(dev, "dot11.device.responded_ssid_map"))
        ies = beacon.get("dot11.advertisedssid.ie_tag_content") if isinstance(beacon, dict) else None
        if ies:
            extra["vendor_ies"] = ies
    elif kind == WIFI_CLIENT:
        probed = _ssids(_get(dev, "dot11.device.probed_ssid_map"))
        if probed:
            extra["probed_ssids"] = probed
    else:
        name = _get(dev, "kismet.device.base.name", "kismet.device.base.commonname")
        if name and name != mac:
            extra["name"] = name
        bt = dev.get("bluetooth.device") or {}
        if isinstance(bt, dict):
            uuids = bt.get("bluetooth.device.service_uuid_vec")
            if uuids:
                extra["service_uuids"] = uuids
            sdata = bt.get("bluetooth.device.service_data_bytes")
            if isinstance(sdata, dict):
                extra["service_data"] = {k: v for k, v in sdata.items() if isinstance(v, str)}
            mfr = bt.get("bluetooth.device.manuf_data")
            if mfr:
                extra["manufacturer_data"] = mfr if isinstance(mfr, list) else [mfr]

    return Observation(
        kind=kind,
        subject=str(mac),
        ts=float(_get(dev, "kismet.device.base.last_time") or time.time()),
        ssid=ssid if isinstance(ssid, str) and ssid else None,
        channel=str(_get(dev, "kismet.device.base.channel") or "") or None,
        freq_mhz=float(freq_khz) / 1000.0 if freq_khz else None,
        rssi=_get(dev, "kismet.common.signal.last_signal"),
        vendor=_vendor(_get(dev, "kismet.device.base.manuf")),
        crypt=str(crypt) if crypt else ("open" if kind == WIFI_AP else None),
        hidden=bool(cloaked) if cloaked is not None else (ssid in (None, "") if kind == WIFI_AP else None),
        extra=extra,
    )


def _vendor(manuf: Any) -> str | None:
    if not isinstance(manuf, str) or manuf.lower() in ("", "unknown", "unknown manufacturer"):
        return None
    return manuf


class KismetCollector:
    def __init__(self, cfg: KismetSettings, gps: GpsSource | None = None) -> None:
        self.cfg = cfg
        self.gps = gps
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()

    def _auth(self) -> tuple[dict[str, str], tuple[str, str] | None]:
        if self.cfg.api_key:
            return {"Cookie": f"KISMET={self.cfg.api_key}"}, None
        return {}, (self.cfg.user, self.cfg.password)

    async def check(self) -> dict[str, Any]:
        """GET /system/status.json; raises on failure. Used by selftest."""
        import httpx

        headers, auth = self._auth()
        async with httpx.AsyncClient(base_url=self.cfg.http_url, headers=headers, auth=auth, timeout=5) as client:
            r = await client.get("/system/status.json")
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            return data

    async def _eventbus(self) -> None:
        import websockets

        url = self.cfg.ws_url + "/eventbus/events.ws"
        if self.cfg.api_key:
            url += f"?KISMET={quote(self.cfg.api_key)}"
        else:
            url += f"?user={quote(self.cfg.user)}&password={quote(self.cfg.password)}"
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, open_timeout=5) as ws:
                    for topic in ("NEW_DEVICE", "ALERT"):
                        await ws.send(json.dumps({"SUBSCRIBE": topic}))
                    log.info("kismet event bus connected")
                    async for _ in ws:
                        self._wake.set()
            except Exception as exc:
                log.debug("event bus unavailable (%s); polling only", exc)
                await asyncio.sleep(10)

    async def stream(self) -> AsyncIterator[Observation]:
        """Yield observations until :meth:`stop` is called. Reconnects on errors."""
        import httpx

        headers, auth = self._auth()
        bus = asyncio.create_task(self._eventbus())
        since = -int(max(10, self.cfg.poll_seconds * 5))  # start with the last few seconds
        backoff = 1.0
        try:
            async with httpx.AsyncClient(base_url=self.cfg.http_url, headers=headers, auth=auth, timeout=15) as client:
                while not self._stop.is_set():
                    try:
                        r = await client.post(f"/devices/last-time/{since}/devices.json", json={"fields": FIELDS})
                        r.raise_for_status()
                        devices = r.json()
                        backoff = 1.0
                    except (httpx.HTTPError, ValueError) as exc:
                        log.warning("kismet poll failed: %s (retrying in %.0fs)", exc, backoff)
                        await asyncio.sleep(backoff)
                        backoff = min(30.0, backoff * 2)
                        continue
                    newest = since
                    fix = self.gps.current() if self.gps else None
                    for dev in devices if isinstance(devices, list) else []:
                        obs = device_to_observation(dev)
                        if obs is None:
                            continue
                        if fix is not None:
                            obs.lat, obs.lon = fix.lat, fix.lon
                        newest = max(newest, int(obs.ts))
                        yield obs
                    if newest > 0:
                        since = newest
                    self._wake.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.poll_seconds)
        finally:
            bus.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bus

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
