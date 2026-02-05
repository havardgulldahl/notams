"""Additional tests for `scripts/notam_geo.py` parsing logic.

Focus areas:
 - Altitude text parsing variants
 - Subarea splitting and indexing
 - dms_token_to_deg edge cases
 - Multi-part NOTAM union behaviour in GeoJSON output
 - Fallback polygon detection when no explicit AREA: tag
"""

import pytest
from shapely.geometry import shape, Polygon, MultiPolygon

from scripts.geo import (
    dms_token_to_deg,
    parse_latlon_pair,
    parse_multi_latlon_seq,
    m_from_text,
    build_polygon,
    parse_notam_block,
    parse_notam_file_text,
    parse_alt_text,
    notams_to_geojson,
)


def test_dms_token_to_deg_valid_and_hemi():
    assert pytest.approx(59.976389, rel=1e-6) == dms_token_to_deg("595835N")
    assert dms_token_to_deg("0301229E") > 0
    # Western / Southern hemispheres negative
    assert dms_token_to_deg("0301229W") < 0
    assert dms_token_to_deg("595835S") < 0


def test_dms_token_to_deg_invalid():
    with pytest.raises(ValueError):
        dms_token_to_deg("BADTOKEN")
    with pytest.raises(ValueError):
        dms_token_to_deg("123N")  # too short


def test_parse_alt_text_variants():
    assert parse_alt_text("SFC") == {"type": "SFC"}
    assert parse_alt_text("GND") == {"type": "GND"}
    assert parse_alt_text("FL100") == {"type": "ALT", "unit": "FL", "value": 100}
    assert parse_alt_text("700M AMSL") == {
        "type": "ALT",
        "unit": "M",
        "value": 700.0,
        "ref": "AMSL",
    }
    assert parse_alt_text("3000M AGL") == {
        "type": "ALT",
        "unit": "M",
        "value": 3000.0,
        "ref": "AGL",
    }
    assert parse_alt_text("150M") == {"type": "ALT", "unit": "M", "value": 150.0}
    assert parse_alt_text(" ") == {"type": "UNKNOWN", "raw": " "}


def test_subareas_indexing_and_multiple_parts():
    block = """(Q9999/25 NOTAMN\nE) AIRSPACE CLSD AS FLW:\n1. WI CIRCLE RADIUS 1KM CENTRE 585106N0304315E.\n2. WI SECTOR CENTRE 610424N0331023E AZM 321-144 DEG RADIUS 8KM.\nF) SFC  G) 150M AMSL)"""
    nf = parse_notam_block(block)
    assert nf is not None, "Failed to parse NOTAM block"
    assert len(nf.parts) == 2
    kinds = {p.kind for p in nf.parts}
    assert kinds == {"CIRCLE", "SECTOR"}
    indices = {p.index for p in nf.parts}
    assert indices == {1, 2}


def test_polygon_fallback_without_area_tag():
    # Provide a NOTAM E) text listing coords directly; should still parse polygon.
    block = """(Q8888/25 NOTAMN\nE) AIRSPACE CLSD:\n595835N0301229E-595811N0301228E-595809N0301307E-595811N0301313E-595835N0301229E.\nF) SFC  G) 100M AMSL)"""
    nf = parse_notam_block(block)
    assert nf is not None and nf.parts, "Expected polygon part"
    assert nf.parts[0].kind == "POLYGON"
    geom = nf.parts[0].geom
    assert isinstance(geom, Polygon)
    assert geom.exterior.is_ring


def test_geojson_union_of_multiple_parts():
    # Build a NOTAM with two distinct circles far apart, expect MultiPolygon or unioned geometry with two rings
    block = """(Q7777/25 NOTAMN\nE) AIRSPACE CLSD AS FLW:\n1. WI CIRCLE RADIUS 1KM CENTRE 585106N0304315E.\n2. WI CIRCLE RADIUS 1KM CENTRE 595106N0314315E.\nF) SFC  G) 150M AMSL)"""
    nf = parse_notam_block(block)
    assert nf is not None
    fc = notams_to_geojson([nf])
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 1
    geom = fc["features"][0]["geometry"]
    # Depending on union result: MultiPolygon or single Polygon with 2 rings (unlikely unless touching)
    assert geom["type"] in ("MultiPolygon", "Polygon")
    if geom["type"] == "MultiPolygon":
        assert len(geom["coordinates"]) == 2


def test_parse_notam_block_missing_altitude_defaults():
    # Missing F)/G) should default F->SFC and G->UNL
    block = """(Q6666/25 NOTAMN\nE) AIRSPACE CLSD WI CIRCLE RADIUS 1KM CENTRE 585106N0304315E.)"""
    nf = parse_notam_block(block)
    assert nf is not None and nf.parts
    part = nf.parts[0]
    assert part.altitude_from["type"] == "SFC"
    # When G) missing, parser sets raw 'UNL' -> type UNKNOWN
    assert part.altitude_to.get("raw") == "UNL"


def test_m_from_text_errors():
    with pytest.raises(ValueError):
        m_from_text("BAD")


def test_parse_latlon_pair_errors():
    with pytest.raises(ValueError):
        parse_latlon_pair("XXXXXX")


def test_parse_multi_latlon_seq_mixed_content():
    seq = "595835N0301229E-INVALID-595811N0301228E"  # should skip INVALID token
    coords = parse_multi_latlon_seq(seq)
    assert len(coords) == 2


"""End of extra tests."""
