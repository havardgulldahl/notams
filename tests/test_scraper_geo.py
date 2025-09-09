import pytest
import math
import notam
from pathlib import Path
import sys

# Ensure project root is on sys.path so we can import from scripts package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.geo import (
    StubNotam,
    build_geometry,
    dms_min_to_decimal,
    MAX_CIRCLE_RADIUS_NM,
)

EARTH_RADIUS = 6371000.0  # m


def circle_polygon(lon, lat, radius_m, num_points=32):
    """Approximate a circle as a polygon."""
    coords = []
    for i in range(num_points + 1):
        angle = 2 * math.pi * (i / num_points)
        dx = radius_m * math.cos(angle)
        dy = radius_m * math.sin(angle)
        new_lat = lat + (dy / EARTH_RADIUS) * (180 / math.pi)
        new_lon = lon + (dx / (EARTH_RADIUS * math.cos(math.radians(lat)))) * (
            180 / math.pi
        )
        coords.append([new_lon, new_lat])
    return {"type": "Polygon", "coordinates": [coords]}


@pytest.mark.parametrize(
    "raw, expected_type",
    [
        # --- Simple polygon ---
        (
            """(Q0338/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/010/5959N03015E002
    A)ULLL B)2509130700 C)2509131650
    E)AIRSPACE CLSD WI AREA:
    595835N0301229E-595811N0301228E-595809N0301307E-595835N0301229E.
    F)SFC G)100M AMSL)""",
            "Polygon",
        ),
        # --- Circle #1 ---
        (
            """(Q1233/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/010/6128N04015E003
    A)ULLL B)2509270800 C)2509271400
    E)AIRSPACE CLSD WI CIRCLE RADIUS 5KM CENTRE 612800N0401500E.
    F)SFC G)150M AMSL)""",
            "Polygon",
        ),
        # --- Circle #2 small 6km ---
        (
            """(Q1491/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/100/5940N03004E004
    A)ULLL B)2509050601 C)2509101659
    D)DAILY 0601-1659
    E)AIRSPACE CLSD WI CIRCLE RADIUS 6KM CENTRE 594030N0300420E.
    F)SFC G)FL100)""",
            "Polygon",
        ),
        # --- Circle #3 small 1km ---
        (
            """(Q1871/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/010/5750N02821E001
    A)ULLL B)2509110700 C)2509111000
    E)AIRSPACE CLSD WI CIRCLE RADIUS 0.7KM CENTRE 575013N0282127E.
    F)GND G)150M AMSL)""",
            "Polygon",
        ),
        # --- Circle #4 30km ---
        (
            """(Q1401/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/050/6243N04029E017
    A)ULLL B)2509050000 C)2509102359
    E)AIRSPACE CLSD WI CIRCLE RADIUS 30KM CENTRE 624300N0402926E.
    F)SFC G)1500M AMSL)""",
            "Polygon",
        ),
        # --- Line corridor ---
        (
            """(Q1423/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/050/6004N03241E034
    A)ULLL B)2509080700 C)2509121659
    D)08-12 0700-1659
    E)AIRSPACE CLSD AS FLW:
    WI 1KM EITHER SIDE OF LINE JOINING POINTS:
    600000N0321929E-601400N0334417E.
    F)SFC G)1500M AMSL)""",
            "LineString",
        ),
        # --- Ellipse ---
        (
            """(Q1760/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/010/5846N03044E001
    A)ULLL B)2509090700 C)2509101400
    D)09 10 0700-1400
    E)AIRSPACE CLSD AS FLW:
    ELLIPSE CENTRE 584622N0304438E WITH AXES DIMENSIONS 2.8X1.3KM
    AZM OF MAJOR AXIS 141DEG
    F)SFC G)150M AMSL)""",
            "Polygon",
        ),
        # --- Sector ---
        (
            """(Q1507/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/050/6104N03310E001
    A)ULLL B)2509060800 C)2509111645
    D)06-11 0800-1645
    E)AIRSPACE CLSD AS FLW:
    WI SECTOR CENTRE 610424N0331023E AZM 321-144 DEG RADIUS 8KM.
    F)SFC G)1500M AMSL)""",
            "Polygon",
        ),
        # --- Multiple areas (Q1257/25 has 2 geometries) ---
        (
            """(Q1257/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/050/6029N03048E012
    A)ULLL B)2509030705 C)2509081655
    D)03-08 0705-1655
    E)AIRSPACE CLSD AS FLW:
    1. AREA: 601818N0303722E-603000N0303756E-603006N0304300E-
    601818N0303722E
    SFC-900M AMSL.
    2. WI CIRCLE RADIUS 2.5KM CENTRE 602111N0304628E
    900M AMSL-1500M AMSL.
    F)SFC G)1500M AMSL)""",
            "MultiPolygon",
        ),
        # --- Multiple geometries (Q1579/25 has 6 circles + 1 polygon!) ---
        (
            """(Q1579/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/010/5905N02914E011
    A)ULLL B)2509080610 C)2509131655
    D)08-13 0610-1655
    E)AIRSPACE CLSD AS FLW:
    1. AREA: 590910N0285835E-590349N0290439E-590220N0290748E-
    590910N0285835E
    250M AMSL-290M AMSL.
    2. WI CIRCLE RADIUS 1KM CENTRE 590047N0290636E
    SFC-290M AMSL.
    3. WI CIRCLE RADIUS 1KM CENTRE 585844N0290908E
    SFC-290M AMSL.
    4. WI CIRCLE RADIUS 1KM CENTRE 585753N0292205E
    SFC-290M AMSL.
    5. WI CIRCLE RADIUS 1KM CENTRE 591113N0290204E
    SFC-290M AMSL.
    6. WI CIRCLE RADIUS 1KM CENTRE 591108N0290747E
    SFC-290M AMSL.
    F)SFC G)290M AMSL)""",
            "MultiPolygon",
        ),
        # --- Multiple mixed polygons (Q1654/25 has 5 different areas) ---
        (
            """(Q1654/25 NOTAMN
    Q)ULLL/QRTCA/IV/BO/W/000/200/6016N03039E015
    A)ULLL B)2509080700 C)2509131655
    D)08-13 0700-1655
    E)AIRSPACE CLSD AS FLW:
    1. AREA: 600250N0303240E-600650N0303222E-600845N0303620E-
    600250N0303240E
    GND-300M AMSL.
    2. AREA: 601153N0303605E-601755N0303540E-601805N0304700E-
    601153N0303605E
    GND-FL060.
    3. AREA: 601800N0304123E-601805N0304700E-602437N0304054E-
    601800N0304123E
    2700M AMSL-FL200.
    4. AREA: 601754N0303540E-601800N0304123E-602337N0303525E-
    601754N0303540E
    1500M AMSL-FL200.
    5. AREA: 600425N0303426E-600430N0303645E-601153N0303605E-
    600425N0303426E
    GND-400M AMSL.
    F)GND G)FL200)""",
            "MultiPolygon",
        ),
    ],
)
def test_parse_notam_various(raw, expected_type):
    n = notam.Notam.from_str(raw)
    decoded = n.decoded()
    geojson = build_geometry(decoded, {})
    if geojson is None:
        raise AssertionError("Geometry should not be None")
    assert geojson["type"] in ["Polygon", "MultiPolygon"]
    assert geojson["type"] == expected_type
    assert "coordinates" in geojson
    assert len(geojson["coordinates"]) > 0
