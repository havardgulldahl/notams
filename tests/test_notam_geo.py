"""Tests for notam_geo.py"""

import math
import json
import pytest
from shapely.geometry import shape, Polygon

from scripts.geo import (
    parse_latlon_pair,
    parse_multi_latlon_seq,
    m_from_text,
    build_circle,
    build_line_corridor,
    build_sector,
    build_ellipse,
    build_polygon,
    parse_notam_block,
    parse_notam_file_text,
)


def almost_equal(a, b, eps=1e-6):
    return abs(a - b) < eps


def test_parse_latlon_pair():
    lon, lat = parse_latlon_pair("595835N0301229E")
    assert lat > 0 and lon > 0
    assert almost_equal(round(lat, 4), 59.9764, 1e-3)
    assert almost_equal(round(lon, 4), 30.2081, 1e-3)


def test_parse_multi_latlon_seq():
    coords = parse_multi_latlon_seq("595835N0301229E-595811N0301228E-595809N0301307E")
    assert len(coords) == 3
    for lon, lat in coords:
        assert -180 <= lon <= 180
        assert -90 <= lat <= 90


def test_m_from_text():
    assert m_from_text("5KM") == 5000
    assert m_from_text("0.5KM") == 500
    assert m_from_text("150M") == 150


def test_build_circle_area():
    center = (30.0, 60.0)
    poly = build_circle(center, 5000)
    assert poly.is_valid
    # centroid near center
    cx, cy = poly.centroid.x, poly.centroid.y
    assert abs(cx - center[0]) < 0.01
    assert abs(cy - center[1]) < 0.01


def test_build_line_corridor():
    pts = [(30.0, 60.0), (30.1, 60.1)]
    poly = build_line_corridor(pts, 1000)
    assert poly.is_valid
    assert poly.area > 0


def test_build_sector():
    center = (30.0, 60.0)
    poly = build_sector(center, 4000, 300, 60)  # wrap-around supported
    assert poly.is_valid
    assert poly.area > 0


def test_build_ellipse():
    center = (30.0, 60.0)
    poly = build_ellipse(center, major_km=2.8, minor_km=1.3, azm_deg=141)
    assert poly.is_valid
    assert poly.area > 0


def test_build_polygon():
    coords = [(30, 60), (30.1, 60), (30.1, 60.1), (30, 60.1)]
    poly = build_polygon(coords)
    assert poly.is_valid
    assert poly.area > 0


def test_parse_notam_circle():
    block = """(Q1762/25 NOTAMN
E) AIRSPACE CLSD WI CIRCLE RADIUS 1KM CENTRE 585106N0304315E.
F) SFC  G) 150M AMSL)"""
    nf = parse_notam_block(block)
    assert nf and nf.parts
    p = nf.parts[0]
    assert p.kind == "CIRCLE"
    assert p.altitude_to["unit"] == "M"


def test_parse_notam_sector():
    block = """(Q1507/25 NOTAMN
E) AIRSPACE CLSD AS FLW:
WI SECTOR CENTRE 610424N0331023E AZM 321-144 DEG RADIUS 8KM.
F) SFC  G) 1500M AMSL)"""
    nf = parse_notam_block(block)
    assert nf and nf.parts
    assert any(prt.kind == "SECTOR" for prt in nf.parts)


def test_parse_notam_ellipse():
    block = """(Q1760/25 NOTAMN
E) AIRSPACE CLSD AS FLW:
ELLIPSE CENTRE 584622N0304438E WITH AXES DIMENSIONS 2.8X1.3KM AZM OF MAJOR AXIS 141DEG
F) SFC  G) 150M AMSL)"""
    nf = parse_notam_block(block)
    assert nf and nf.parts
    assert nf.parts[0].kind == "ELLIPSE"


def test_parse_notam_line_corridor():
    block = """(Q1624/25 NOTAMN
E) AIRSPACE CLSD WI 0.75KM EITHER SIDE OF LINE JOINING POINTS:
595217N0304217E-594911N0305154E.
F) SFC  G) 300M AMSL)"""
    nf = parse_notam_block(block)
    assert nf and nf.parts
    assert nf.parts[0].kind == "LINE_CORRIDOR"


def test_parse_notam_area_polygon():
    block = """(Q0338/25 NOTAMN
E) AIRSPACE CLSD WI AREA:
595835N0301229E-595811N0301228E-595809N0301307E-595811N0301313E-595835N0301229E.
F) SFC  G) 100M AMSL)"""
    nf = parse_notam_block(block)
    assert nf and nf.parts
    assert nf.parts[0].kind == "POLYGON"
    # Closed ring:
    geom = nf.parts[0].geom
    assert isinstance(geom, Polygon)
    assert geom.exterior.is_ring


def test_parse_notam_area_polygon2():
    block = """(Q5648/25 NOTAMQ
E) TEMPO DANGER AREA ACT:
693500N0341000E-694500N0340000E-700000N0340000E-701500N0343500E-701500N0380000E-694500N0380000E-692000N0361500E-693500N0341000E.
F) SFC  G) 100M AMSL)"""
    geojson_geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [34.166667, 69.583333],
                [34.000000, 69.750000],
                [34.000000, 70.000000],
                [34.583333, 70.250000],
                [38.000000, 70.250000],
                [38.000000, 69.750000],
                [36.250000, 69.333333],
                [34.166667, 69.583333],
            ]
        ],
    }
    nf = parse_notam_block(block)
    assert nf and nf.parts
    assert nf.parts[0].kind == "POLYGON"
    # Closed ring:
    # assert that the parsed geometry matches this geojson structure
    geom = nf.parts[0].geom
    ground_truth = shape(geojson_geometry)
    assert geom.equals_exact(ground_truth, tolerance=1e-4)


def test_parse_notam_area_polygon_multiline():
    block = """(Q0000/00 NOTAMN
E) AIRSPACE CLSD Within AREA:
691700N0340000E-690000N0340000E-690000N0325000E-691300N0331000E-
692200N0330500E-694000N0315000E-692000N0310000E-685500N0294000E-
682000N0293000E-680000N0301000E-674700N0303600E-674700N0335700E-
680000N0335700E-680000N0345000E-684200N0351500E-691400N0344800E-
691700N0340000E.
VIP FLT AND FLT FOR THEIR SUPPORT, SKED FLT NOT AFFECTED.
F) SFC G) UNL)"""

    expected_geojson = {
        "type": "Feature",
        "properties": {
            "name": "AIRSPACE CLSD",
            "remarks": "VIP FLT AND FLT FOR THEIR SUPPORT, SKED FLT NOT AFFECTED",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [34.000000, 69.283333],
                    [34.000000, 69.000000],
                    [32.833333, 69.000000],
                    [33.166667, 69.216667],
                    [33.083333, 69.366667],
                    [31.833333, 69.666667],
                    [31.000000, 69.333333],
                    [29.666667, 68.916667],
                    [29.500000, 68.333333],
                    [30.166667, 68.000000],
                    [30.600000, 67.783333],
                    [33.950000, 67.783333],
                    [33.950000, 68.000000],
                    [34.833333, 68.000000],
                    [35.250000, 68.700000],
                    [34.800000, 69.233333],
                    [34.000000, 69.283333],
                ]
            ],
        },
    }

    nf = parse_notam_block(block)
    assert nf and nf.parts
    assert nf.parts[0].kind == "POLYGON"
    geom = nf.parts[0].geom
    ground_truth = shape(expected_geojson["geometry"])
    assert geom.equals_exact(ground_truth, tolerance=1e-4)


def test_parse_notam_area_polygon_coord():
    block = """(Q0000/00 NOTAMN
E) TEMPO DANGER AREA FOR ACFT FLT ACT Within COORD:
694920N0333800E-700400N0333800E-700400N0352630E-693150N0352630E-
693150N0343600E-694920N0333800E.
F) SFC G) UNL)"""

    expected_geojson = {
        "type": "Feature",
        "properties": {"name": "TEMPO DANGER AREA FOR ACFT FLT ACT"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [33.633333, 69.822222],
                    [33.633333, 70.066667],
                    [35.441667, 70.066667],
                    [35.441667, 69.530556],
                    [34.600000, 69.530556],
                    [33.633333, 69.822222],
                ]
            ],
        },
    }

    nf = parse_notam_block(block)
    assert nf and nf.parts
    assert nf.parts[0].kind == "POLYGON"
    geom = nf.parts[0].geom
    ground_truth = shape(expected_geojson["geometry"])
    assert geom.equals_exact(ground_truth, tolerance=1e-4)


def test_full_file_parsing_smoke():
    # This is a smoke test; you would load the real file content in practice.
    content = """(Q1762/25 NOTAMN
A) ULLL B)2509100700 C)2509111400
E) AIRSPACE CLSD WI CIRCLE RADIUS 1KM CENTRE 585106N0304315E.
F) SFC  G) 150M AMSL)"""
    fc = parse_notam_file_text(content)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 1
    geom = shape(fc["features"][0]["geometry"])
    assert geom.is_valid


def test_full_file_parsing():
    # Get cached copy of full file from github, and parse it.
    import requests
    import re

    url = "https://raw.githubusercontent.com/havardgulldahl/notams/refs/heads/main/history/Q2508310053_eng.html"
    response = requests.get(url)
    content = response.text

    # Strip HTML tags and entities
    content = re.sub(r"<[^>]+>", "\n", content)
    content = content.replace("&nbsp;", " ")

    fc = parse_notam_file_text(content)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) > 0
    # Check validity of geometries
    for f in fc["features"]:
        geom = shape(f["geometry"])
        assert geom.is_valid
