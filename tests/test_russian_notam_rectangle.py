from pathlib import Path
import sys
import pytest

# Ensure project root is on sys.path so we can import from scripts package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.geo import build_geometry


def test_russian_notam_rectangle_parsing():
    """Test that a Russian NOTAM with line breaks in coordinate format parses to a closed rectangle."""
    notam_text = """(Q1268/26 NOTAMN
Q)ULLL/QRTCA/IV/BO/W/000/050/6731N03033E002
A)ULLL B)2602090700 C)2602141500
D)09-14 0700-1500
E)AIRSPACE CLSD WI AREA:
673155N0303416E-673039N0303643E-672906N0303114E-
673023N0302847E-673155N0303416E.
F)SFC  G)1500M AMSL)"""

    geometry = build_geometry(notam_text, {})

    assert geometry is not None, "Geometry should not be None"
    assert geometry["type"] == "Polygon", f"Expected Polygon, got {geometry['type']}"

    # Check structure of coordinates
    coords = geometry["coordinates"]
    assert len(coords) == 1, "Should be a single ring polygon"
    ring = coords[0]

    # We expect 5 points (4 corners + closing point)
    # The coordinate string has 5 parts:
    # 1. 673155N0303416E
    # 2. 673039N0303643E
    # 3. 672906N0303114E
    # 4. 673023N0302847E
    # 5. 673155N0303416E (closing)

    assert (
        len(ring) == 5
    ), f"Expected 5 coordinates (4 vertices + 1 closing), got {len(ring)}"

    # Check that it is closed
    assert ring[0] == ring[-1], "Polygon ring should be closed"

    # Verify approximate coordinates of the first point
    # 673155N -> 67 + 31/60 + 55/3600 = 67.531944...
    # 0303416E -> 30 + 34/60 + 16/3600 = 30.571111...

    # Remember geojson is [lon, lat]
    lon, lat = ring[0]
    assert lat == pytest.approx(67.531944, abs=0.0001)
    assert lon == pytest.approx(30.571111, abs=0.0001)


if __name__ == "__main__":
    test_russian_notam_rectangle_parsing()
    print("Test passed!")
