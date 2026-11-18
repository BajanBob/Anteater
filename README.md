# Anteater

Passive RF surveillance-detection suite. Finds the SSIDs, MAC addresses, cell
towers, cameras and drones that keep showing up wherever *you* are.

Built on [Kismet](https://www.kismetwireless.net/) for the RF layer. Companion
SDR tool `termite` covers 433 MHz – 15 GHz.

[![ci](https://github.com/USER/anteater/actions/workflows/ci.yml/badge.svg)](https://github.com/USER/anteater/actions/workflows/ci.yml)
[![licence: proprietary](https://img.shields.io/badge/licence-all%20rights%20reserved-red.svg)](LICENSE)
[![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

---

## What it detects

| Engine | What it catches |
|---|---|
| `ssid_anomaly` | duplicate SSIDs on new BSSIDs, non-printable/overlong SSIDs, hidden↔broadcast flips |
| `evil_twin` | same SSID with a downgraded security config or a mismatched OUI |
| `karma_beacon` | APs that answer probes for SSIDs they never beaconed |
| `fingerprints` | Flock Safety, ALPR backhaul, wireless cameras, AirTags, TPMS |
| `follow_score` | devices that track you across sessions and grid squares |
| `grid_square` | per-geohash baselines; novelty and repeat-offence scoring |
| `cell_ping_anomaly` | towers paging you at unusual hours, or from a single grid cell |
| `imsi_catcher` | 2G downgrades, A5/1 cipher, rogue ARFCN, unregistered LAC/CID |
| `drones` | ASTM F3411 Remote ID over BLE and Wi-Fi NAN |
| `radar` | X/K/Ka-band traffic radar, 24 GHz door sensors |

Alerts are audio (distinct motif per severity, optional TTS), video (1080p HUD),
MQTT, and an evidence log in the database.

---

## Quickstart

```bash
git clone https://github.com/USER/anteater && cd anteater
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

cp config/anteater.example.toml config/anteater.toml
docker compose up -d                 # TimescaleDB + Mosquitto
anteater db upgrade
anteater fp sync                     # pull the IEEE OUI registry
anteater selftest --no-radio         # proves the install without hardware

anteater collect --kismet ws://localhost:2501 --user kismet --pass changeme
anteater detect --engines all
anteater ui --port 8080
```

`collect`, `detect` and `ui` are separate processes that share the database, so
each can run (and restart) on its own. Open the dashboard at
<http://localhost:8080>.

No database server? Set `ANTEATER_GENERAL__DB_URL=sqlite+aiosqlite:///./anteater.db`
and skip `docker compose`.

Try it without a radio by replaying a capture of observations:

```bash
anteater detect --replay tests/data/sample.jsonl --engines all
```

---

## Recursive queries

The database answers four questions with recursion. The SQL runs unchanged on SQLite and on PostgreSQL/TimescaleDB, and every recursion has a hard limit, so bad data can't make a query run forever.

| Endpoint | What it shows | How |
|---|---|---|
| `/api/trail/{mac}` | Where one device has been, leg by leg, with how long it stayed in each grid square | `WITH RECURSIVE` walk over its sightings in time order (up to 5,000 steps) |
| `/api/chains` | Random MAC addresses that are probably one phone changing its address | `WITH RECURSIVE` chain: B follows A when it appears within `gap` seconds of A going quiet, probing the same SSID in the same square (up to 50 hops, no loops) |
| `/api/timeline` | Sightings and devices per time slot, quiet slots included | `WITH RECURSIVE` slot series joined to the sightings (up to 10,000 slots) |
| `/api/hotspots` | Clusters of touching busy grid squares | Recursive flood fill in Python (`anteater.geo.grid.clusters`), safe for any cluster size |

`anteater.geo.grid.rollup()` recursively groups per-square counts into a zoomable tree for maps. The queries are in `anteater/db/recursive.py`.

## termite (SDR)

```bash
termite devices                       # SoapySDR devices attached
termite plan --region 2 --budget 120  # dwell plan for ITU Region 2, 120 s per sweep
termite scan --post http://localhost:8080/api/observations
```

SoapySDR is not on PyPI. Install it and a driver module from your OS packages,
for example `apt install python3-soapysdr soapysdr-module-rtlsdr`. Bands above
your SDR's range (X, K and Ka radar) need a downconverter; the band plans in
`config/spectrum/` mark them.

---

## Hardware

One monitor-mode Wi-Fi adapter and a GPS are enough to start. See
[docs/HARDWARE.md](docs/HARDWARE.md) for the full list, including Bluetooth,
cellular and SDR options.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Detections](docs/DETECTIONS.md)
- [termite](docs/TERMITE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

## Executables

Tagging a release (`git tag v0.1.0 && git push --tags`) makes GitHub Actions
build standalone executables for Linux (binary + AppImage), Windows (installer)
and macOS. To build one locally: `pip install -e ".[dev]" && make build`.

## Legal

Anteater only receives. It never transmits, deauthenticates, jams or connects to
anything it sees. Laws on receiving and decoding radio signals, cellular
especially, differ between countries; check yours. Don't use it to track people.

## Licence


Copyright (c) 2026 BajanBob. All rights reserved. See [LICENSE](LICENSE).
