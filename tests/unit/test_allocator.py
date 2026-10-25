import numpy as np
import pytest

from termite.plan import dwell_qubo
from termite.plan.allocator import Band, allocate, load_bands
from termite.qml.kernel_rff import RFFClassifier
from termite.sdr.features import feature_vector, find_carriers, psd_db
from termite.sdr.soapy import SimulatedReceiver

BANDS = [
    Band("narrow", 433.05, 434.79, priority=2),
    Band("cell", 925, 960, priority=3),
    Band("wide", 2400, 2483.5, priority=1),
    Band("radar", 24050, 24250, priority=1),
]


def test_steps_cover_band():
    b = Band("wide", 2400, 2483.5)
    steps = b.steps(10e6)
    assert len(steps) == 11  # 83.5 MHz / 8 MHz usable
    assert steps[0] > 2400e6 and steps[-1] < 2483.5e6


def test_allocation_respects_budget_and_range():
    plan = allocate(BANDS, budget_s=30, sample_rate=10e6, freq_range_hz=(24e6, 6e9))
    assert plan.seconds == pytest.approx(30, rel=1e-6)
    assert [b.name for b, _ in plan.skipped] == ["radar"]
    by_name = {d.band.name: d for d in plan.dwells}
    # higher priority gets more time per step
    assert by_name["cell"].seconds_per_step > by_name["wide"].seconds_per_step


def test_downconverter_brings_band_in_range():
    plan = allocate(BANDS, 30, 10e6, freq_range_hz=(24e6, 6e9), downconverter_lo_hz=21e9)
    assert "radar" in {d.band.name for d in plan.dwells}


def test_tiny_budget_skips_low_priority():
    plan = allocate(BANDS, budget_s=0.2, sample_rate=10e6, min_dwell_s=0.05, freq_range_hz=(24e6, 6e9))
    assert plan.seconds <= 0.2 + 1e-9
    assert "wide" in {b.name for b, _ in plan.skipped}


@pytest.mark.parametrize("region", [1, 2, 3])
def test_band_plans_load(region):
    bands = load_bands(region)
    assert len(bands) >= 8
    assert all(b.lo_mhz < b.hi_mhz for b in bands)
    # An unquoted comma inside a YAML {flow: mapping} splits the value; names would lose their ")"
    assert all(b.name.count("(") == b.name.count(")") for b in bands), [b.name for b in bands]


@pytest.mark.parametrize("sweep", [1, 2])
def test_sweep_finds_carriers_at_step_centres(sweep):
    """433.92 MHz is both a single-step band's centre and the commonest keyfob frequency.

    psd_db blanks the bins at DC, so without offset tuning it would never be seen.
    """
    from termite.sweep import sweep_once

    bands = [Band("433", 433.05, 434.79), Band("x", 10500, 10550), Band("ism", 902, 928)]
    # 915 MHz sits exactly on the boundary between two ISM steps (911.75 and 918.25)
    rx = SimulatedReceiver(10e6, carriers={433.92e6: 25.0, 10525e6: 20.0, 915e6: 18.0})
    plan = allocate(bands, 3.0, rx.sample_rate, freq_range_hz=rx.freq_range())
    assert plan.dwells[0].centres_hz == [433.92e6]
    found = sweep_once(rx, plan, sweep)
    mhz = sorted(round(o["freq_mhz"], 1) for o in found)
    assert mhz == [433.9, 915.0, 10525.0], mhz  # each exactly once, despite overlapping steps
    assert {o["extra"]["band"] for o in found} == {"433", "x", "ism"}


def test_qubo_allocation_uses_budget_sensibly():
    bands = BANDS[:3]
    out = dwell_qubo.solve(bands, units=6, seed=1)
    assert set(out) == {"narrow", "cell", "wide"}
    assert sum(out.values()) <= 6
    assert sum(out.values()) >= 4  # the reward makes spending most of the budget worthwhile
    assert out["cell"] >= out["wide"]


def test_qubo_energy_prefers_valid_one_hot():
    bands = BANDS[:2]
    q = dwell_qubo.build_qubo(bands, units=2)
    valid = {(0, 1): 1, (1, 1): 1}
    invalid = {(0, 1): 1, (0, 2): 1, (1, 1): 1}
    assert dwell_qubo.energy(q, valid) < dwell_qubo.energy(q, invalid)


def test_carrier_detection_on_simulated_receiver():
    rx = SimulatedReceiver(10e6, carriers={915.5e6: 25.0})
    rx.tune(915e6)
    freqs, power = psd_db(rx.read(65536), rx.sample_rate)
    carriers = find_carriers(freqs, power, min_snr_db=10)
    assert carriers, "expected the simulated carrier"
    assert abs(carriers[0].offset_hz - 0.5e6) < 20e3
    assert carriers[0].snr_db > 15


def test_rff_classifier_separates_emitters(tmp_path):
    rng = np.random.default_rng(0)
    X, y = [], []
    # The receiver is centred on 0 Hz at 2 MS/s, so a carrier must sit inside +/-0.9 MHz to exist.
    for label, carrier in (("tone", {0.5e6: 25.0}), ("noise", {})):
        for i in range(20):
            rx = SimulatedReceiver(2e6, carriers=carrier, seed=int(rng.integers(1e9)))
            X.append(feature_vector(rx.read(8192), 2e6))
            y.append(label)
    X, y = np.array(X), np.array(y)
    clf = RFFClassifier(n_features=128).fit(X[::2], y[::2])
    assert clf.score(X[1::2], y[1::2]) >= 0.9
    path = tmp_path / "m.npz"
    clf.save(path)
    assert (RFFClassifier.load(path).predict(X) == clf.predict(X)).all()
