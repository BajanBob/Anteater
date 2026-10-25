from pathlib import Path

import pytest

from anteater.detections.base import is_randomised_mac
from anteater.detections.drones import decode_payload
from anteater.detections.evil_twin import EvilTwin, crypt_rank
from anteater.detections.fingerprints import FingerprintDB, FingerprintEngine, OuiDB, parse_rules
from anteater.detections.karma_beacon import KarmaBeacon
from anteater.detections.ssid_anomaly import ssid_problems
from anteater.paths import data_dir
from anteater.scoring.severity import Severity
from anteater.selftest import _remote_id_ble, _remote_id_location


def test_shipped_rule_files_parse():
    db = FingerprintDB.load_dir(data_dir("vendors"))
    categories = {r.category for r in db.rules}
    assert {"alpr", "camera", "tracker", "drone"} <= categories
    assert "radar.bands" in db.tables


def test_oui_lookup_prefers_longest_prefix(tmp_path: Path):
    csv = tmp_path / "oui.csv"
    csv.write_text(
        "Registry,Assignment,Organization Name,Organization Address\n"
        "MA-L,001122,Big Corp,addr\n"
        "MA-S,001122334,Small Corp,addr\n"
    )
    db = OuiDB.load(csv)
    assert db.lookup("00:11:22:33:44:55") == "Small Corp"
    assert db.lookup("00-11-22-99-00-00") == "Big Corp"
    assert db.lookup("02:11:22:33:44:55") is None  # randomised, never looked up


def test_randomised_mac():
    assert is_randomised_mac("da:a1:19:00:00:01")
    assert not is_randomised_mac("00:1a:2b:00:00:01")


def test_rule_modes(obs):
    rules = parse_rules({
        "category": "test",
        "rules": [
            {"name": "any", "match": {"vendor": ["(?i)acme"], "ssid": ["^cam"]}},
            {"name": "all", "mode": "all", "match": {"vendor": ["(?i)acme"], "ssid": ["^cam"]}},
            {"name": "ble only", "match": {"ssid": ["^cam"], "kinds": ["ble"]}},
        ],
    })
    o = obs("wifi_ap", "00:00:00:00:00:01", ssid="cam-1", vendor="Other Ltd")
    hits = {r.name for r in rules if r.matches(o)}
    assert hits == {"any"}


def test_airtag_is_recognised(ctx, obs):
    engine = FingerprintEngine(ctx)
    found = engine.process(obs("ble", "c1:00:00:00:00:01", extra={"manufacturer_data": ["4c0012190011"]}))
    assert any("Find My" in f.title for f in found)


def test_camera_setup_ssid_heuristic(ctx, obs):
    found = FingerprintEngine(ctx).process(obs("wifi_ap", "00:00:00:00:00:09", ssid="HIK-12345"))
    assert any(f.evidence["category"] == "camera" for f in found)


@pytest.mark.parametrize("crypt,rank", [("WPA3 SAE", 4), ("WPA2-PSK CCMP", 3), ("WPA TKIP", 2), ("WEP", 1),
                                        ("Open", 0), (None, 0)])
def test_crypt_rank(crypt, rank):
    assert crypt_rank(crypt) == rank


def test_evil_twin_downgrade(ctx, obs):
    det = EvilTwin(ctx)
    assert det.process(obs("wifi_ap", "00:aa:00:00:00:01", ssid="Home", crypt="WPA2")) == []
    out = det.process(obs("wifi_ap", "02:aa:00:00:00:02", ssid="Home", crypt="Open"))
    assert out and out[0].severity is Severity.HIGH


def test_karma(ctx, obs):
    det = KarmaBeacon(ctx)
    out = det.process(obs("wifi_ap", "02:bb:00:00:00:01", ssid="Free",
                          extra={"responded_ssids": ["a", "b", "c"]}))
    assert out and "KARMA" in out[0].title


def test_ssid_problems():
    assert ssid_problems("normal") == []
    assert "longer than 32 bytes" in ssid_problems("x" * 33)
    assert "contains control characters" in ssid_problems("a\x01b")


def test_remote_id_decoding():
    info = decode_payload(bytes.fromhex(_remote_id_ble("ABC123")), ble=True)
    assert info == {"type": "basic_id", "id_type": "serial", "uas_id": "ABC123"}
    loc = decode_payload(b"\x00" + _remote_id_location(51.5, -0.12), ble=False)
    assert loc["lat"] == pytest.approx(51.5) and loc["lon"] == pytest.approx(-0.12)
