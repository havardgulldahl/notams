from pathlib import Path
import sys
import pytest

# Ensure project root is on sys.path so we can import from scripts package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import geo
from scripts.waypoint_lookup import lookup_waypoint


def test_russian_notam_waypoints_parsing(monkeypatch):
    """Test that ATS route segment parsing builds line geometries from waypoints."""
    waypoint_coords = {
        "ATKUP": (30.0, 60.0),
        "LIMUS": (31.0, 61.0),
        "KROTA": (32.0, 62.0),
        "AKATI": (33.0, 63.0),
        "EVMUV": (34.0, 64.0),
        "RAMUG": (35.0, 65.0),
    }

    def fake_lookup(code: str, country: str = "RU"):
        return waypoint_coords.get(code)

    monkeypatch.setattr(geo, "lookup_waypoint_coords", fake_lookup)
    notam_text = """
    (Q1173/26&nbsp;NOTAMN<br>
Q)ULLL/QARLC/IV/NBO/E/075/540/6951N03348E068<br>
A)ULLL&nbsp;B)2602021500&nbsp;C)2602072200<br>
D)02-07&nbsp;1500-2200<br>
E)ATS&nbsp;RTE&nbsp;SEGMENTS&nbsp;CLSD:<br>
M745&nbsp;ATKUP-LIMUS&nbsp;FL325-FL420,<br>
P190&nbsp;KROTA-AKATI&nbsp;FL075-FL540,<br>
T150&nbsp;EVMUV-LIMUS&nbsp;FL325-FL420,<br>
T570&nbsp;KROTA-LIMUS&nbsp;FL265-FL540,<br>
T608&nbsp;RAMUG-AKATI&nbsp;FL280-FL540,<br>
F)FL075&nbsp;&nbsp;G)FL540)<br>
<br>"""

    geometry = geo.build_geometry(notam_text, {})

    assert geometry is not None, "Geometry should not be None"
    assert geometry.get("meta", {}).get("shape") == "linestring"
    assert geometry.get("type") in {"LineString", "MultiLineString"}

    coords = geometry.get("coordinates")
    assert coords is not None

    points = set()
    if geometry["type"] == "LineString":
        points.update(tuple(pt) for pt in coords)
    else:
        for line in coords:
            points.update(tuple(pt) for pt in line)

    for pt in waypoint_coords.values():
        assert pt in points


@pytest.mark.network
def test_waypoint_lookup_live_network():
    """Live network test for waypoint lookup and downstream geometry creation."""
    start_code = "LAGAT"
    end_code = "LAGAT"

    try:
        start_data = lookup_waypoint(start_code)
        end_data = lookup_waypoint(end_code)
    except Exception as exc:
        pytest.skip(f"Network lookup failed: {exc}")

    try:
        start_pt = (float(start_data["longitude"]), float(start_data["latitude"]))
        end_pt = (float(end_data["longitude"]), float(end_data["latitude"]))
    except Exception as exc:
        pytest.skip(f"Waypoint data missing/invalid: {exc}")

    notam_text = f"""
    (Q0000/26 NOTAMN
Q)ULLL/QARLC/IV/NBO/E/000/999/0000N00000E000
A)ULLL B)2602021500 C)2602072200
E)ATS RTE SEGMENTS CLSD:
T999 {start_code}-{end_code} FL100-FL200,
F)FL100 G)FL200)
"""

    geometry = geo.build_geometry(notam_text, {})
    if geometry is None:
        pytest.skip("No geometry built from live waypoint data")

    assert geometry.get("type") in {"LineString", "MultiLineString"}
    coords = geometry.get("coordinates")
    assert coords is not None

    points = set()
    if geometry["type"] == "LineString":
        points.update(tuple(pt) for pt in coords)
    else:
        for line in coords:
            points.update(tuple(pt) for pt in line)

    assert start_pt in points
    assert end_pt in points
