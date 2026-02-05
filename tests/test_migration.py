import pytest
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Mapping, Optional, Any, List

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import the NEW build_geometry
from scripts.geo import build_geometry, MAX_CIRCLE_RADIUS_NM


# Mock objects needed for testing
@dataclass
class NotamStub:
    body: str = ""
    decoded_text: str = ""
    area: Optional[Mapping[str, Any]] = None
    location: Optional[List[str]] = None

    def decoded(self):
        return self.decoded_text


def test_migration_circles():
    # Test Circle buffer
    # E) AIRSPACE CLSD WI CIRCLE RADIUS 700M CENTRE 575013N0282127E
    n = NotamStub(body="AIRSPACE CLSD WI CIRCLE RADIUS 1NM CENTRE 575013N0282127E.")
    geom = build_geometry(n, {})
    assert geom is not None
    assert geom["type"] == "Polygon"
    # area of 1 NM radius circle ~ 10.8 km^2.
    # Just check validity and non-emptiness


def test_migration_arc():
    # Test Arc
    # ARC RADIUS 5NM CENTRE 624300N0402926E TO ...
    # From geo.py logic
    txt = """
    595835N0301229E CLOCKWISE ARC RADIUS 5NM CENTRE 595000N0301229E TO 595811N0301228E
    """
    n = NotamStub(body=txt)
    geom = build_geometry(n, {})
    assert geom is not None
    # Depending on implementation, might return Polygon.
    assert geom["type"] == "Polygon"


def test_migration_fallback_area():
    # Test fallback to pynotam area
    n = NotamStub(
        area={"lat": "5535N", "long": "03716E", "radius": 10}, location=["UUWW"]
    )
    geom = build_geometry(n, {}, max_circle_radius_nm=200)
    assert geom is not None
    assert geom["type"] == "Polygon"


def test_migration_fallback_point():
    # Test fallback to airport
    airports = {"UUWW": {"lon": 37.0, "lat": 55.0}}
    n = NotamStub(location=["UUWW"])
    geom = build_geometry(n, airports)
    assert geom is not None
    assert geom["type"] == "Point"
    assert geom["coordinates"] == (37.0, 55.0)
