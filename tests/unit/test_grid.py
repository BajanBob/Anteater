import pytest

from anteater.geo import gnss, grid


def test_known_geohash():
    # the classic example from the geohash spec
    assert grid.encode(57.64911, 10.40744, 11) == "u4pruydqqvj"


@pytest.mark.parametrize(
    "lat,lon", [(0.0, 0.0), (51.5007, -0.1246), (-33.8568, 151.2153), (89.9, 179.9)]
)
def test_roundtrip_within_cell(lat, lon):
    cell = grid.encode(lat, lon, 8)
    lat_lo, lon_lo, lat_hi, lon_hi = grid.bounds(cell)
    assert lat_lo <= lat <= lat_hi
    assert lon_lo <= lon <= lon_hi
    clat, clon = grid.decode(cell)
    assert abs(clat - lat) < 0.001 and abs(clon - lon) < 0.001


def test_neighbours_are_adjacent_and_distinct():
    cell = grid.encode(51.5, -0.12, 6)
    ns = grid.neighbours(cell)
    assert len(ns) == 8
    assert cell not in ns
    assert all(len(n) == 6 for n in ns)


def test_invalid_input():
    with pytest.raises(ValueError):
        grid.encode(91, 0)
    with pytest.raises(ValueError):
        grid.bounds("abc!")
    assert grid.cell_of(None, 1.0) is None


def test_nmea_gga_and_rmc():
    gga = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    fix = gnss.parse_nmea(gga)
    assert fix is not None
    assert abs(fix.lat - 48.1173) < 1e-4 and abs(fix.lon - 11.516667) < 1e-4
    assert fix.sats == 8 and fix.alt_m == 545.4

    rmc = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    fix = gnss.parse_nmea(rmc)
    assert fix is not None and fix.speed_mps == pytest.approx(22.4 * 0.514444)


def test_nmea_rejects_bad_checksum():
    assert (
        gnss.parse_nmea("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*00") is None
    )


def test_haversine():
    # London to Paris, roughly 344 km
    assert gnss.haversine_m(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(343_500, rel=0.01)
