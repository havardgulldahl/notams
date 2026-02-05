from pathlib import Path
import sys
import math
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.geo import build_geometry  # noqa: E402


@pytest.mark.parametrize(
    "raw, shape, expect_type",
    [
        # Sector with azimuth wedge (should yield Polygon with meta shape sector)
        (
            """(Q2500/25 NOTAMN\nQ)ULLL/QRTCA/IV/BO/W/000/050/6104N03310E001\nA)ULLL B)2509060800 C)2509111645\nE)AIRSPACE CLSD AS FLW: WI SECTOR CENTRE 610424N0331023E AZM 321-144 DEG RADIUS 8KM.\nF)SFC G)1500M AMSL)""",
            "sector",
            "Polygon",
        ),
        # Ellipse with azimuth; check oriented ellipse presence (type Polygon, meta shape ellipse)
        (
            """(Q2501/25 NOTAMN\nQ)ULLL/QRTCA/IV/BO/W/000/010/5846N03044E001\nA)ULLL B)2509090700 C)2509101400\nE)AIRSPACE CLSD AS FLW: ELLIPSE CENTRE 584622N0304438E WITH AXES DIMENSIONS 4.0X2.0KM AZM OF MAJOR AXIS 045DEG\nF)SFC G)150M AMSL)""",
            "ellipse",
            "Polygon",
        ),
        # Circle given in meters
        (
            """(Q2502/25 NOTAMN\nQ)ULLL/QRTCA/IV/BO/W/000/010/5750N02821E001\nA)ULLL B)2509110700 C)2509111000\nE)AIRSPACE CLSD WI CIRCLE RADIUS 700M CENTRE 575013N0282127E.\nF)GND G)150M AMSL)""",
            "circle",
            "Polygon",
        ),
        # Circle given in NM
        (
            """(Q2503/25 NOTAMN\nQ)ULLL/QRTCA/IV/BO/W/000/050/6243N04029E017\nA)ULLL B)2509050000 C)2509102359\nE)AIRSPACE CLSD WI CIRCLE RADIUS 5NM CENTRE 624300N0402926E.\nF)SFC G)1500M AMSL)""",
            "circle",
            "Polygon",
        ),
    ],
)
def test_new_shape_parsing(raw, shape, expect_type):
    # We only need the decoded() text; import notam lazily to avoid heavy dependency if missing
    import notam  # type: ignore

    n = notam.Notam.from_str(raw)
    geo = build_geometry(n, {})
    assert geo is not None, "Geometry should not be None"
    assert geo["type"] == expect_type
    meta = geo.get("meta", {})
    assert meta.get("shape") == shape
    # Basic coordinate sanity: at least 4 points for polygon ring
    assert len(geo["coordinates"][0]) >= 4
