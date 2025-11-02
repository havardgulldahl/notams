"""Test edge cases for geometric structure extraction."""
from pathlib import Path
import sys
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.geo import build_geometry  # noqa: E402


@pytest.mark.parametrize(
    "raw_text, expected_type, description",
    [
        # Arc with parentheses around center coordinates
        (
            """AIRSPACE CLSD WI AREA:
            620506N1294106E-622044N1295822E-THEN CLOCKWISE
            ALONG ARC RADIUS 30KM CENTRE (620536N1294624E) TO
            614952N1295408E.""",
            "Polygon",
            "Arc with parentheses in center coordinate",
        ),
        # Arc with "CENTRED AT" variation
        (
            """AIRSPACE CLSD WI AREA:
            471001N1431544E-464313N1433602E THEN CLOCKWISE BY ARC OF A CIRCLE 
            RADIUS OF 70KM CENTRED AT (465318N1424300E) TO 472830N1422256E.""",
            "Polygon",
            "Arc with CENTRED AT variation",
        ),
        # Arc with spaces in coordinates
        (
            """AIRSPACE CLSD WI AREA:
            FM 513432N0512308E ALONG STATE BORDER TO 512534N0502235E THEN
            CLOCKWISE ALONG ARC RADIUS 200KM CENTRE (531300N 501100E) TO
            513432N0512308E.""",
            "Polygon",
            "Arc with space-separated coordinates",
        ),
        # Sector with AZMAG notation
        (
            """AIRSPACE CLSD WI SECTOR BTN AZMAG 360-130 DEG FROM 543830N0393418E
            RADIUS 40KM.""",
            "Polygon",
            "Sector with AZMAG notation",
        ),
        # ANTICLOCKWISE arc
        (
            """AIRSPACE CLSD WI AREA:
            560519N0374847E THEN ANTICLOCKWISE ALONG ARC RADIUS 28KM
            CENTRE (555200N0380000E) TO 554927N0382950E.""",
            "Polygon",
            "Arc with ANTICLOCKWISE direction",
        ),
        # Circle with parentheses
        (
            """AIRSPACE CLSD WI CIRCLE RADIUS 50KM CENTRE (620536N 1294624E).""",
            "Polygon",
            "Circle with parentheses and space in coordinates",
        ),
        # Coordinate chain with mixed formats (some with seconds)
        (
            """AIRSPACE CLSD WI AREA:
            595835N0301229E-595811N0301228E-595809N0301307E-595835N0301229E.""",
            "Polygon",
            "Polygon with DDMMSS format coordinates",
        ),
        # Multiple numbered areas
        (
            """AIRSPACE CLSD AS FLW:
            1. AREA: 601818N0303722E-603000N0303756E-603006N0304300E-601818N0303722E
            2. AREA: 600250N0303240E-600650N0303222E-600845N0303620E-600250N0303240E""",
            "MultiPolygon",
            "Multiple numbered areas",
        ),
    ],
)
def test_edge_case_geometries(raw_text, expected_type, description):
    """Test various edge cases in geometric parsing."""
    geom = build_geometry(raw_text, {})
    assert geom is not None, f"Failed for: {description}"
    assert geom["type"] == expected_type, f"Wrong type for: {description}"
    if "coordinates" in geom:
        assert len(geom["coordinates"]) > 0, f"Empty coordinates for: {description}"
