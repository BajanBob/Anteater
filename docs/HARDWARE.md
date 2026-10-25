# Hardware

Everything Anteater uses is receive-only. Start with the minimum and add
radios as you need them.

## Minimum

| Part | Why | Notes |
|---|---|---|
| Linux computer | runs Kismet and Anteater | Raspberry Pi 4/5 (4 GB+), or any laptop |
| Wi-Fi adapter with monitor mode | Wi-Fi APs and clients | MediaTek MT7612U / MT7921AU chipsets work well with Kismet |
| GPS | places, follow score, grid squares | any gpsd-supported USB receiver (u-blox 7/8 class) |

Without GPS, every engine except `follow_score` and `grid_square` still
works. For a fixed install, set `[gps] source = "fixed"` with your
coordinates.

## Recommended additions

| Part | Adds |
|---|---|
| A second Wi-Fi adapter | one radio on 2.4 GHz full time while the other hops 5 GHz (see `config/kismet_site.conf`) |
| Bluetooth LE adapter | trackers (AirTag, Tile, SmartTag), BLE Remote ID, Flock BLE names. A USB dongle with an external antenna beats a laptop's internal one |
| RTL-SDR (v3/v4) | termite: 433 MHz, 868/915 MHz, cellular downlinks below 1.7 GHz, GNSS jamming |
| HackRF One / SDRplay / LimeSDR | termite up to 6 GHz: all cellular bands, 2.4 and 5.8 GHz drone links |
| 10 GHz downconverter (LNB) | X-band radar with termite (`--downconverter-lo`) |
| rtl_433 on a second RTL-SDR | TPMS sensors as `kind: tpms` observations, for vehicle following |

## Cellular

`imsi_catcher` and `cell_ping_anomaly` need serving-cell information:
radio technology, cipher, ARFCN, LAC/CID and paging events. Anteater doesn't
decode the cellular air interface itself. Feed it `kind: cell` observations
from a baseband diagnostics tool on a rooted phone or modem, or a dedicated
IMSI-catcher detector, via `POST /api/observations` (format in
[DETECTIONS.md](DETECTIONS.md#observation-format)).

## Power and antennas

- A Pi 5 with two Wi-Fi adapters, BLE and an SDR draws 10–15 W; use a
  powered USB hub so the SDR doesn't brown out.
- Antenna placement matters more than adapter choice. Mount externally on a
  vehicle; keep SDR antennas away from USB 3 ports (they radiate at 2.4 GHz).

## Setting up Kismet

```bash
sudo apt install kismet gpsd
sudo ln -s "$PWD/config/kismet_site.conf" /etc/kismet/kismet_site.conf
# edit the interface names in it, then:
sudo kismet --no-ncurses
```

Create an API key in the Kismet web UI (Settings → API Keys) and put it in
`.env` as `ANTEATER_KISMET__API_KEY`, or use the user and password.
