from pathlib import Path
import sys
import pytest

# Ensure project root is on sys.path so we can import from scripts package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.geo import (  # noqa: E402
    StubNotam,
    build_geometry,
    dms_min_to_decimal,
    circle_polygon,  # imported to ensure coverage of import path (may be used later)
    MAX_CIRCLE_RADIUS_NM,
)


@pytest.fixture
def airports():
    return {
        "UUWW": {"name": "Vnukovo", "lat": 55.5915, "lon": 37.2615},
        "UUDD": {"name": "Domodedovo", "lat": 55.4088, "lon": 37.9063},
    }


def test_dms_min_to_decimal():
    v1 = dms_min_to_decimal("5535N")
    v2 = dms_min_to_decimal("03716E")
    assert v1 is not None and v2 is not None
    assert v1 == pytest.approx(55 + 35 / 60.0)
    assert v2 == pytest.approx(37 + 16 / 60.0)
    assert dms_min_to_decimal("INVALID") is None


def test_circle_geometry_under_threshold(airports):
    n = StubNotam(
        area={"lat": "5535N", "long": "03716E", "radius": 10}, location=["UUWW"]
    )
    geom = build_geometry(n, airports, max_circle_radius_nm=MAX_CIRCLE_RADIUS_NM)
    assert geom is not None
    assert geom["type"] == "Polygon"
    # Expect 64 segments + closing point
    assert len(geom["coordinates"][0]) == 65


def test_point_geometry_over_threshold(airports):
    n = StubNotam(
        area={"lat": "5535N", "long": "03716E", "radius": MAX_CIRCLE_RADIUS_NM + 1},
        location=["UUWW"],
    )
    geom = build_geometry(n, airports)
    assert geom is not None
    assert geom["type"] == "Point"


def test_fallback_to_airport_location(airports):
    n = StubNotam(area={}, location=["UUDD"])
    geom = build_geometry(n, airports)
    assert geom is not None
    assert geom["type"] == "Point"
    assert geom["coordinates"] == [airports["UUDD"]["lon"], airports["UUDD"]["lat"]]


def test_invalid_area_uses_fallback(airports):
    n = StubNotam(area={"lat": "BAD", "long": "03716E"}, location=["UUWW"])
    geom = build_geometry(n, airports)
    assert geom is not None
    assert geom["type"] == "Point"
    assert geom["coordinates"][1] == airports["UUWW"]["lat"]
