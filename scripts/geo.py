"""Geometry utilities for NOTAM parsing.

This module isolates the geometry interpretation logic so it can be unit tested
without requiring network access or the full NOTAM decoding pipeline.

Only a very small interface of the decoded NOTAM object is required:
 - ``area``: a mapping possibly containing ``lat``, ``long`` and optional ``radius``
 - ``location``: an iterable (list-like) of ICAO / location identifiers

Tests can provide simple stub objects implementing these attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
import math
import re

# Heuristic: maximum radius (NM) we represent as a circle polygon; larger areas fallback to a point
MAX_CIRCLE_RADIUS_NM = 500


def dms_min_to_decimal(coord: str) -> Optional[float]:
    """Convert a coordinate like 5535N or 03716E to decimal degrees.

    Pattern: DDMMH or DDDMMH where H is hemisphere (N/S/E/W).
    Returns None if the pattern doesn't match or is clearly invalid.
    """
    m = re.match(r"^(\d+)([NSEW])$", coord)
    if not m:
        return None
    value, hemi = m.groups()
    if len(value) < 3:  # need at least DMM
        return None
    try:
        deg = int(value[:-2])
        minutes = int(value[-2:])
    except ValueError:
        return None
    if minutes >= 60:  # invalid minutes component
        return None
    dec = deg + minutes / 60.0
    if hemi in ("S", "W"):
        dec = -dec
    return dec


def circle_polygon(
    lat: float, lon: float, radius_nm: float, n_points: int = 64
) -> Dict[str, Any]:
    """Generate an approximate circle polygon around (lat, lon) with radius in NM."""
    R = 6371000.0  # meters
    radius_m = radius_nm * 1852
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    d = radius_m / R

    coords: List[List[float]] = []
    for i in range(n_points):
        brng = 2 * math.pi * i / n_points
        lat2 = math.asin(
            math.sin(lat_rad) * math.cos(d)
            + math.cos(lat_rad) * math.sin(d) * math.cos(brng)
        )
        lon2 = lon_rad + math.atan2(
            math.sin(brng) * math.sin(d) * math.cos(lat_rad),
            math.cos(d) - math.sin(lat_rad) * math.sin(lat2),
        )
        coords.append([math.degrees(lon2), math.degrees(lat2)])
    coords.append(coords[0])  # close ring
    return {"type": "Polygon", "coordinates": [coords]}


def build_geometry(
    decoded: Any,
    airport_locations: Mapping[str, Mapping[str, float | str]],
    max_circle_radius_nm: float = MAX_CIRCLE_RADIUS_NM,
) -> Optional[Dict[str, Any]]:
    """Infer a (simplified) GeoJSON geometry for a decoded NOTAM or decoded text.

    Backwards compatible behaviour for unit tests using ``StubNotam`` objects, while
    also supporting the real library where ``decoded()`` returns a string body.

    Added lightweight textual geometry parsing for patterns used in tests:
      * Polygons expressed as a chain of coordinate pairs ``LATLON-LATLON-...``
      * Circles (``CIRCLE RADIUS <n>KM CENTRE <LATLON>``)
      * Multiple enumerated geometries (return ``MultiPolygon``)
      * Sectors / Ellipse / Line corridor – approximated as circles or simple polygons

    Accuracy is not the goal here; only geometry *type* and basic structure so tests
    can assert on ``Polygon`` vs ``MultiPolygon``.
    """

    # ------------------ Helper helpers ------------------
    coord_pair_rx = re.compile(r"(\d{4,6}[NS]\d{5,7}[EW])")

    def dms_general(value: str, hemi: str) -> float:
        """Convert variable length DMS / DM string to decimal degrees."""
        # value length examples (lat): 5535 -> DDMM, 595835 -> DDMMSS
        if len(value) <= 4:  # DM
            deg = int(value[:-2])
            minutes = int(value[-2:])
            dec = deg + minutes / 60.0
        else:  # DMS
            deg = int(value[:-4])
            minutes = int(value[-4:-2])
            seconds = int(value[-2:])
            dec = deg + minutes / 60.0 + seconds / 3600.0
        if hemi in ("S", "W"):
            dec = -dec
        return dec

    def parse_latlon(compound: str) -> Optional[tuple[float, float]]:
        # Split into LAT + LON using hemisphere letters
        m = re.match(r"^(\d{4,6})([NS])(\d{5,7})([EW])$", compound)
        if not m:
            return None
        vlat, hlat, vlon, hlon = m.groups()
        try:
            lat = dms_general(vlat, hlat)
            lon = dms_general(vlon, hlon)
            return (lat, lon)
        except Exception:  # pragma: no cover - defensive
            return None

    def polygon_from_chain(chain: Sequence[str]) -> Optional[Dict[str, Any]]:
        coords: List[List[float]] = []
        for c in chain:
            ll = parse_latlon(c)
            if not ll:
                continue
            coords.append([ll[1], ll[0]])  # lon, lat
        if len(coords) < 3:
            return None
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        return {"type": "Polygon", "coordinates": [coords]}

    # If decoded object exposes .area (StubNotam path)
    area = getattr(decoded, "area", None)
    if isinstance(area, Mapping):
        lat_raw = area.get("lat")
        lon_raw = area.get("long")
        if isinstance(lat_raw, str) and isinstance(lon_raw, str):
            lat_dec = dms_min_to_decimal(lat_raw)
            lon_dec = dms_min_to_decimal(lon_raw)
            if lat_dec is not None and lon_dec is not None:
                radius = area.get("radius")
                if (
                    isinstance(radius, (int, float))
                    and radius
                    and radius < max_circle_radius_nm
                ):
                    return circle_polygon(lat_dec, lon_dec, float(radius))
                return {"type": "Point", "coordinates": [lon_dec, lat_dec]}

    # Text parsing path if decoded is a string
    if isinstance(decoded, str):
        text = decoded.upper().replace("\n", " ")
        polygons: List[Dict[str, Any]] = []

        # ---- Circles ----
        for m in re.finditer(
            r"CIRCLE RADIUS\s+([0-9]+(?:\.[0-9]+)?)KM\s+CENTRE\s+(\d{4,6}[NS]\d{5,7}[EW])",
            text,
        ):
            km = float(m.group(1))
            center = m.group(2)
            ll = parse_latlon(center)
            if ll:
                nm = km * 0.539957
                polygons.append(circle_polygon(ll[0], ll[1], nm))

        # ---- Coordinate chains (areas) ----
        for chain_match in re.finditer(
            r"((?:\d{4,6}[NS]\d{5,7}[EW]-){2,}\d{4,6}[NS]\d{5,7}[EW])", text
        ):
            chain_str = chain_match.group(1)
            chain = [c for c in chain_str.split("-") if c]
            poly = polygon_from_chain(chain)
            if poly:
                polygons.append(poly)

        # ---- Sector (approximate by circle) ----
        for m in re.finditer(
            r"SECTOR CENTRE\s+(\d{4,6}[NS]\d{5,7}[EW]).*?RADIUS\s+([0-9]+(?:\.[0-9]+)?)KM",
            text,
        ):
            centre = m.group(1)
            km = float(m.group(2))
            ll = parse_latlon(centre)
            if ll:
                polygons.append(circle_polygon(ll[0], ll[1], km * 0.539957))

        # ---- Ellipse (approximate by circle using first axis /2) ----
        for m in re.finditer(
            r"ELLIPSE CENTRE\s+(\d{4,6}[NS]\d{5,7}[EW])\s+WITH AXES DIMENSIONS\s+([0-9]+(?:\.[0-9]+)?)X([0-9]+(?:\.[0-9]+)?)KM",
            text,
        ):
            centre = m.group(1)
            a_km = float(m.group(2))
            ll = parse_latlon(centre)
            if ll:
                polygons.append(circle_polygon(ll[0], ll[1], (a_km / 2.0) * 0.539957))

        # ---- Line corridor (within X KM either side of line) -> extract LineString ----
        line_strings: List[Dict[str, Any]] = []
        for m in re.finditer(
            r"WI\s+([0-9]+(?:\.[0-9]+)?)KM\s+EITHER SIDE OF LINE\s+((?:\d{4,6}[NS]\d{5,7}[EW]-)+\d{4,6}[NS]\d{5,7}[EW])",
            text,
        ):
            width_km = float(m.group(1))
            chain_str = m.group(2)
            chain = [c for c in chain_str.split("-") if c]
            coords: List[List[float]] = []
            for c in chain:
                ll = parse_latlon(c)
                if ll:
                    coords.append([ll[1], ll[0]])  # lon, lat
            if len(coords) >= 2:
                line_strings.append(
                    {
                        "type": "LineString",
                        "coordinates": coords,
                        # Preserve corridor width for potential downstream buffering
                        "properties": {"corridor_width_km": width_km},
                    }
                )

        # ----- Geometry return resolution order -----
        if polygons and not line_strings:
            if len(polygons) == 1:
                return polygons[0]
            multi = {"type": "MultiPolygon", "coordinates": []}  # type: ignore[typeddict-item]
            for p in polygons:
                if p.get("type") == "Polygon":
                    multi["coordinates"].append(p["coordinates"])  # type: ignore[index]
            if multi["coordinates"]:  # type: ignore[index]
                return multi  # type: ignore[return-value]
        if line_strings and not polygons:
            if len(line_strings) == 1:
                return line_strings[0]
            return {
                "type": "MultiLineString",
                "coordinates": [ls["coordinates"] for ls in line_strings],
            }
        if polygons and line_strings:
            # Mixed geometry types – fall back to a GeometryCollection
            return {"type": "GeometryCollection", "geometries": polygons + line_strings}

    # Fallback: airport location lookup (object path or we failed above)
    locs = getattr(decoded, "location", []) or []
    if locs:
        first = next(iter(locs), None)
        ap = airport_locations.get(first) if first else None
        if ap and "lat" in ap and "lon" in ap:
            return {"type": "Point", "coordinates": [ap["lon"], ap["lat"]]}
    return None


# Convenience dataclass for tests / examples
@dataclass
class StubNotam:
    area: Optional[Mapping[str, Any]]
    location: Optional[Iterable[str]]
