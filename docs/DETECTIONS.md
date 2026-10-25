# Detections

Every engine reads `Observation`s and emits `Finding`s with a severity
(info, low, medium, high, critical), a 0–1 confidence score and an evidence
dict. Pick engines with `anteater detect --engines all`, a comma list, or
exclusions such as `--engines all,-radar`.

No single finding proves surveillance. The useful signal is
**correlation**: the same subject flagged by several engines, or the same
finding in several places. That is why repeat offenders escalate and why
`grid_square` and `follow_score` exist.

## Engines

### ssid_anomaly (Wi-Fi APs)
- **New BSSID for an established SSID** (medium): an SSID seen for longer
  than `detect.ssid_warmup_seconds` gains an extra access point. Skipped for
  networks that already have 4+ APs (campus and enterprise Wi-Fi).
- **Malformed SSID** (medium): longer than 32 bytes, or containing control
  or NUL characters. Fuzzers and some attack tools do this.
- **Visibility flip** (low): an AP switches between hidden and broadcast.

### evil_twin (Wi-Fi APs)
- **Security downgrade** (high if open, otherwise medium): an SSID usually
  served with WPA2/WPA3 appears from another BSSID with weaker security.
- **Vendor mismatch** (medium): two or more APs for an SSID share a vendor,
  and a new one has a different, non-randomised vendor.

### karma_beacon (Wi-Fi APs)
- **KARMA-style AP** (medium/high): answers probe requests for 3+ SSIDs it
  never beacons. This is how rogue APs get phones to auto-join.
- **Beacon flood** (medium): one radio advertising 12+ SSIDs.

### fingerprints (all kinds)
Rules from `config/vendors/*.yaml` plus heuristics in `flock.py` and
`cameras.py`. Covers Flock Safety and other ALPR vendors, ALPR backhaul
modems, network cameras, Find My / Tile / SmartTag trackers, TPMS and drone
makers. Vendor rules need `config/oui.csv`; run `anteater fp sync`.
`anteater fp list` shows what is loaded.

### follow_score (Wi-Fi, BLE, TPMS)
A device seen in `detect.follow_min_cells` (default 3) distinct ~1.2 km
places, across at least `follow_min_minutes` or `follow_min_sessions`.
Score rises with places, sessions and time span; randomised MACs score 20%
lower because they rotate. Home cells and allowlisted devices never count.

### grid_square (Wi-Fi, BLE, TPMS)
- **New device in a familiar place** (info): a strong signal never seen
  before in a cell you've visited in 3+ sessions.
- **Repeat offender** (medium, escalating): a subject with earlier findings
  turns up in another grid square.

### cell_ping_anomaly (cellular)
Paging bursts (5+ an hour), repeated paging at night (00:00–05:59), paging
from a tower seen in only one grid cell, and silent type-0 SMS (high).

### imsi_catcher (cellular)
Forced LTE/5G → 2G downgrade within two minutes (high), no encryption
A5/0 (critical), weak A5/1 (medium) or A5/2 (high), GSM ARFCN outside the
regional band plan, 2G cells with no neighbours, cells missing from your
`config/cells.yaml`, and too many LACs in one grid square.

### drones (BLE, Wi-Fi)
Decodes ASTM F3411 Remote ID (BLE service 0xFFFA, Wi-Fi vendor IE
FA:0B:BC): the UAS ID and, when present, the drone's position and distance.
High when within 300 m.

### radar (SDR)
termite carriers 10 dB+ above the noise floor in the X, K and Ka bands from
`config/vendors/radar.yaml`. Three or more sweeps in a row raise the level.

## Observation format

Collectors produce these, and anything else can POST them to
`/api/observations` (one object or a list) or put them in a JSON Lines file
for `anteater detect --replay`:

```json
{"kind": "wifi_ap", "subject": "00:11:22:33:44:55", "ts": 1767225600.0,
 "ssid": "HomeNet", "crypt": "WPA2", "rssi": -61, "lat": 51.5, "lon": -0.12,
 "extra": {"responded_ssids": ["eduroam"]}}
```

| kind | subject | useful extra fields |
|---|---|---|
| `wifi_ap` | BSSID | `beaconed_ssids`, `responded_ssids`, `vendor_ies` |
| `wifi_client` | MAC | `probed_ssids` |
| `ble` | address | `name`, `manufacturer_data` (hex), `service_uuids`, `service_data` {uuid: hex} |
| `cell` | `mcc-mnc-lac-cid` | `rat`, `cipher`, `arfcn`, `neighbours`, `event` (`paging`/`silent_sms`) |
| `sdr` | `sdr:<MHz>` | `snr_db`, `bandwidth_khz`, `band` |
| `tpms` | sensor id | `model`, `pressure_kpa` |

## Writing fingerprint rules

See the header of `anteater/detections/fingerprints.py` for every match
key. Keep vendor regexes specific (a broad "Amazon" would flag every Echo),
say where a signature came from in `note`, and default to `low` unless the
signature is unambiguous.
