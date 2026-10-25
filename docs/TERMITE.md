# termite

termite sweeps a band plan with a SoapySDR receiver, finds carriers above the
noise floor and reports them to Anteater as `kind: sdr` observations. It is
receive-only: it never opens a transmit stream.

It's installed alongside anteater as the `termite` command, and the
standalone executable has it as `anteater termite`.

## Install SoapySDR

SoapySDR isn't on PyPI. Install it and the module for your radio from your
OS packages:

```bash
# Debian / Ubuntu / Raspberry Pi OS
sudo apt install python3-soapysdr soapysdr-module-rtlsdr soapysdr-module-hackrf
# macOS
brew install soapysdr soapyrtlsdr soapyhackrf
```

If you use a virtualenv, create it with `--system-site-packages` so it can
see the system SoapySDR. `termite devices` confirms it's working.

## Commands

```bash
termite devices                                  # attached radios
termite plan --region 1 --budget 60              # what a 60 s sweep covers
termite plan --region 2 --budget 60 --qubo       # whole-second allocation via QUBO
termite scan --device driver=rtlsdr --post http://localhost:8080/api/observations
termite scan --simulate --sweeps 1               # no hardware: simulated carriers
termite scan --out data/sdr.jsonl                # write observations to a file instead
termite classify --train labelled.csv --model data/termite_rff.npz
termite classify --predict unknown.csv
```

To have Anteater run the engines on posted observations, start the dashboard
with `anteater ui --detect`.

## Band plans

`config/spectrum/itu_r{1,2,3}.yaml` list the bands per ITU region, each
with a priority from 1 to 3 and a reason. Cellular entries are downlinks,
which is where fake base stations transmit. Edit them for your country; SRD
and cellular allocations differ inside each region.

## Dwell planning

A sweep has a time budget (`termite.sweep_budget_s`). Each band needs
`ceil(span / (0.8 × sample rate))` tuning steps:

- **Greedy allocator** (`plan/allocator.py`, default): every step gets a
  minimum dwell, highest-priority bands first. The remaining time is shared
  in proportion to priority. Bands outside the receiver's range are skipped
  and listed.
- **QUBO planner** (`plan/dwell_qubo.py`, `--qubo`): picks whole seconds per
  band to maximise `priority × log(1 + seconds)` under the budget. That
  favours covering everything a little over covering one band a lot. It uses
  `dwave-samplers` if installed (`pip install 'anteater[termite]'`), and the
  built-in numpy annealer otherwise.

### How a step is read

Zero-IF receivers (RTL-SDR, HackRF and most others) show a false spike at the
exact frequency they are tuned to, so termite blanks the few FFT bins around
it. To stop that blind spot landing on a step's centre (for a narrow band
that is often the very frequency devices use, e.g. 433.92 MHz), each step is
tuned 4 bins off-centre ("offset tuning"), and the offset flips direction
every sweep, so no frequency is blind two sweeps running.

Neighbouring steps overlap, so one emitter can show up in two of them, a few
kHz apart. termite merges detections within two FFT bins of each other per
band and keeps the strongest, so each emitter is reported once per sweep.

## Above 6 GHz

Most SDRs stop at 1.7 GHz (RTL-SDR) or 6 GHz (HackRF). For X-band radar
(10.5 GHz), put a downconverter such as a satellite LNB in front of the SDR
and pass its local-oscillator frequency:

```bash
termite scan --downconverter-lo 9.75e9
```

termite tunes `f − LO` but reports the real frequency.

## Emitter classifier

`qml/kernel_rff.py` is a random-Fourier-feature kernel classifier (an
approximate RBF SVM in plain numpy). Train it on feature vectors from
`termite.sdr.features.feature_vector`, written as CSV rows of
`label,f1,…,f10`. It distinguishes emitter types (keyfob, TPMS, LoRa, drone
video) from a few dozen labelled captures each.
