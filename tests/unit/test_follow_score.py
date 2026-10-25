from anteater.detections.follow_score import FollowScore, follow_score
from anteater.geo.grid import encode
from anteater.scoring.severity import Severity

T0 = 1_700_000_000.0


def _walk(det, obs, subject, places, minutes_apart=15, session="s1", lat0=51.5, lon0=-0.12):
    out = []
    for i in range(places):
        lat, lon = lat0 + 0.03 * i, lon0 + 0.03 * i  # ~3-4 km apart
        o = obs("ble", subject, ts=T0 + i * minutes_apart * 60, lat=lat, lon=lon,
                cell=encode(lat, lon, 7), session=session)
        out += det.process(o)
    return out


def test_score_function():
    assert follow_score(2, 1, 60, 3, False) == 0.0
    low = follow_score(3, 1, 20, 3, False)
    high = follow_score(6, 3, 120, 3, False)
    assert 0 < low < high <= 1.0
    assert follow_score(5, 2, 60, 3, True) < follow_score(5, 2, 60, 3, False)


def test_device_across_places_is_flagged(ctx, obs):
    det = FollowScore(ctx)
    findings = _walk(det, obs, "c0:ff:ee:00:00:01", places=5)
    assert findings
    assert findings[-1].severity >= Severity.MEDIUM
    assert findings[-1].evidence["places"] >= 3


def test_one_place_is_not_following(ctx, obs):
    det = FollowScore(ctx)
    out = []
    for i in range(10):
        o = obs("ble", "c0:ff:ee:00:00:02", ts=T0 + i * 600, lat=51.5, lon=-0.12,
                cell=encode(51.5, -0.12, 7), session="s1")
        out += det.process(o)
    assert out == []


def test_home_cells_and_allowlist_are_ignored(ctx, obs):
    ctx.allowlist.add("c0:ff:ee:00:00:03")
    det = FollowScore(ctx)
    assert _walk(det, obs, "c0:ff:ee:00:00:03", places=6) == []

    ctx.home_cells = ("g",)  # everything in the 'g' geohash region counts as home (UK)
    assert _walk(det, obs, "c0:ff:ee:00:00:04", places=6) == []


def test_only_reports_when_level_rises(ctx, obs):
    det = FollowScore(ctx)
    findings = _walk(det, obs, "c0:ff:ee:00:00:05", places=8)
    levels = [f.severity for f in findings]
    assert levels == sorted(set(levels))  # strictly increasing, no repeats
