import re
import json
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any, Iterable

from shapely.geometry import (
    Point,
    LineString,
    Polygon,
    mapping,
    MultiPolygon,
    GeometryCollection,
)
from shapely.ops import transform, unary_union
from shapely.affinity import rotate, scale
from pyproj import CRS, Transformer


# ============== Utilities ==============


def dms_token_to_deg(token: str) -> float:
    """
    Convert 'DDMMSSN' or 'DDDMMSSW' to signed decimal degrees.
    Examples:
      '595835N' -> +59 +58/60 +35/3600  -> 59.976389
      '0301229E' -> +30 +12/60 +29/3600 -> 30.208056
    Supports DDMMSS or DDMM and N/S/E/W.
    """
    token = token.strip()
    # Accept 4-7 digits to support both latitude (DDMM[SS]) and longitude (DDDMM[SS]) forms.
    m = re.fullmatch(r"(\d{4,7})([NSEW])", token)
    if not m:
        raise ValueError(f"Bad DMS token: {token}")
    num, hemi = m.groups()
    # Split into degrees, minutes, seconds depending on length
    if len(num) == 6:
        dd = int(num[:2])
        mm = int(num[2:4])
        ss = int(num[4:6])
    elif len(num) == 5:
        dd = int(num[:2])
        mm = int(num[2:4])
        ss = int(num[4:5]) * 10  # rare; not common here
    elif len(num) == 4:
        dd = int(num[:2])
        mm = int(num[2:4])
        ss = 0
    else:
        # could be 7 digits for longitude degrees DDD
        dd = int(num[:3])
        mm = int(num[3:5])
        ss = int(num[5:7]) if len(num) >= 7 else 0

    deg = dd + mm / 60.0 + ss / 3600.0
    if hemi in ("S", "W"):
        deg = -deg
    return deg


def parse_latlon_pair(pair: str) -> Tuple[float, float]:
    """
    Parse '595835N0301229E' into (lon, lat) decimal degrees.
    Also supports '595835N 0301229E' or with separators.
    """
    s = pair.strip().replace(",", " ").replace("-", " ").replace("–", " ")
    # Support hemisphere-first compact format like 'N314705E0351414'
    m_prefixed = re.fullmatch(r"([NS])(\d{4,6})([EW])(\d{5,7})", s)
    if m_prefixed:
        hemi_lat, lat_digits, hemi_lon, lon_digits = m_prefixed.groups()
        # Recompose into existing expected format DDMMSSN DDDMMSS E
        lat_tok = f"{lat_digits}{hemi_lat}"
        lon_tok = f"{lon_digits}{hemi_lon}"
        lat = dms_token_to_deg(lat_tok)
        lon = dms_token_to_deg(lon_tok)
        return (lon, lat)
    # Try to split by letter boundary
    m = re.fullmatch(r"(\d{4,6}[NS])\s*(\d{5,7}[EW])", s)
    if not m:
        # try with missing spaces
        m = re.match(r"(\d{4,6}[NS])(\d{5,7}[EW])", s)
    if not m:
        # try explicit spacing tokens split
        toks = s.split()
        if (
            len(toks) >= 2
            and re.match(r"\d{4,6}[NS]", toks[0])
            and re.match(r"\d{5,7}[EW]", toks[1])
        ):
            lat_tok, lon_tok = toks[0], toks[1]
        else:
            raise ValueError(f"Cannot parse latlon pair: {pair}")
    else:
        lat_tok, lon_tok = m.group(1), m.group(2)

    lat = dms_token_to_deg(lat_tok)
    lon = dms_token_to_deg(lon_tok)
    return (lon, lat)


def parse_multi_latlon_seq(text: str) -> List[Tuple[float, float]]:
    """
    Parse sequences like:
    595835N0301229E-595811N0301228E-...
    into [(lon,lat), ...]
    """
    raw = re.sub(r"[\s]+", "", text.strip())
    parts = re.split(r"[-\s]+", raw)
    coords = []
    for p in parts:
        if not p:
            continue
        # Sometimes split lines contain trailing punctuation
        p = p.strip(".,;")
        # Expect pattern ...N...E or ...S...W
        m = re.match(r"(\d{4,6}[NS])(\d{5,7}[EW])$", p)
        if not m:
            # try with separator inside
            m2 = re.match(r"(\d{4,6}[NS])\s*(\d{5,7}[EW])", p)
            if not m2:
                # skip if it's clearly not a coordinate (e.g., FL, AMSL)
                continue
            lat_tok, lon_tok = m2.group(1), m2.group(2)
        else:
            lat_tok, lon_tok = m.group(1), m.group(2)
        lat = dms_token_to_deg(lat_tok)
        lon = dms_token_to_deg(lon_tok)
        coords.append((lon, lat))
    return coords


def local_equal_area_crs(lon: float, lat: float) -> CRS:
    """
    Build a local Azimuthal Equidistant CRS centered on the given lon/lat,
    suitable for buffering distances in meters.
    """
    return CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def project_geom(geom, center: Tuple[float, float], inverse=False):
    """
    Project geometry to/from local AEQD centered at 'center' (lon,lat).
    """
    lon0, lat0 = center
    src = CRS.from_epsg(4326)
    dst = local_equal_area_crs(lon0, lat0)
    fwd = Transformer.from_crs(src, dst, always_xy=True).transform
    inv = Transformer.from_crs(dst, src, always_xy=True).transform
    return transform(inv if inverse else fwd, geom)


def km(value: float) -> float:
    return value * 1000.0


def m_from_text(val_text: str) -> float:
    """
    Convert '5KM' -> 5000, '0.5KM' -> 500, '500M' -> 500.
    """
    t = val_text.strip().upper().replace(" ", "")
    m = re.match(r"(\d+(?:\.\d+)?)(KM|M|NM)", t)
    if not m:
        raise ValueError(f"Cannot parse distance: {val_text}")
    v, unit = m.groups()
    v = float(v)
    if unit == "KM":
        return v * 1000.0
    if unit == "NM":
        return v * 1852.0
    return v


def parse_alt_text(alt: str) -> Dict[str, Any]:
    """
    Parse F)/G) altitude text into structured dict, e.g.:
      'SFC', 'GND', '250M AMSL', 'FL100', '700M AMSL', '3000M AGL'
    Returns: { 'type': 'SFC'|'GND'|'ALT', 'unit': 'M'|'FL'|None, 'value': float|None, 'ref': 'AMSL'|'AGL'|None }
    """
    t = alt.strip().upper()
    if t in ("SFC", "GND"):
        return {"type": t}
    # FLxxx
    m = re.fullmatch(r"FL(\d{2,3})", t)
    if m:
        return {"type": "ALT", "unit": "FL", "value": int(m.group(1))}
    # meters AMSL/AGL
    m = re.fullmatch(r"(\d+(?:\.\d+)?)M\s+(AMSL|AGL)", t)
    if m:
        return {
            "type": "ALT",
            "unit": "M",
            "value": float(m.group(1)),
            "ref": m.group(2),
        }
    # meters only
    m = re.fullmatch(r"(\d+(?:\.\d+)?)M", t)
    if m:
        return {"type": "ALT", "unit": "M", "value": float(m.group(1))}
    # empty
    return {"type": "UNKNOWN", "raw": alt}


# ============== Geometry Builders ==============


def build_polygon(coords: List[Tuple[float, float]]) -> Polygon:
    """
    Build a polygon from lon/lat coords, close if needed.
    """
    if len(coords) < 3:
        raise ValueError("Polygon requires at least 3 points")
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    return Polygon(coords)


def build_circle(
    center: Tuple[float, float], radius_m: float, n_points: int = 128
) -> Polygon:
    """
    Build a circle polygon by buffering a point in local AEQD projection.
    """
    lon, lat = center
    p = Point(lon, lat)
    proj = project_geom(p, center=(lon, lat), inverse=False)
    circ = proj.buffer(radius_m, resolution=max(16, n_points // 4))
    return project_geom(circ, center=(lon, lat), inverse=True)


def build_line_corridor(
    points: List[Tuple[float, float]], half_width_m: float
) -> Polygon:
    """
    Build a corridor polygon buffering a polyline by half_width_m.
    Uses local AEQD around the line centroid for low distortion.
    """
    line = LineString(points)
    centroid = line.centroid
    center = (centroid.x, centroid.y)
    proj = project_geom(line, center=center, inverse=False)
    # use 'mitre' for sharp corners (numeric 2 previously used)
    buf = proj.buffer(half_width_m, join_style="mitre")
    return project_geom(buf, center=center, inverse=True)


def build_sector(
    center: Tuple[float, float],
    radius_m: float,
    az_min_deg: float,
    az_max_deg: float,
    n: int = 128,
) -> Polygon:
    """
    Build an azimuth sector (fan) polygon. Bearings clockwise from North.
    """
    lon, lat = center
    cpt = Point(lon, lat)
    proj_cpt = project_geom(cpt, center=center, inverse=False)

    def polar_to_xy(r, bearing_deg):
        # shapely buffer used local projection; bearings measured from North clockwise
        theta = math.radians(90 - bearing_deg)  # convert to mathematical (x from East)
        x = r * math.cos(theta)
        y = r * math.sin(theta)
        return (x, y)

    # handle wrap-around if az_max < az_min
    span = (az_max_deg - az_min_deg) % 360
    steps = max(8, int(n * (span / 360)))
    pts = [polar_to_xy(0, 0)]  # center
    for i in range(steps + 1):
        b = (az_min_deg + i * span / steps) % 360
        pts.append(polar_to_xy(radius_m, b))
    pts.append(polar_to_xy(0, 0))
    poly_local = Polygon(pts)
    return project_geom(poly_local, center=center, inverse=True)


def build_ellipse(
    center: Tuple[float, float],
    major_km: float,
    minor_km: float,
    azm_deg: float,
    n: int = 128,
) -> Polygon:
    """
    Build an ellipse polygon using local AEQD projection. 'azm_deg' is heading of major axis (clockwise from North).
    """
    lon, lat = center
    p = Point(lon, lat)
    proj_p = project_geom(p, center=center, inverse=False)
    # unit circle
    circ = proj_p.buffer(1.0, resolution=max(16, n // 4))
    # scale by semi-axes (meters)
    a = km(major_km) / 2.0
    b = km(minor_km) / 2.0
    ell = scale(circ, xfact=a, yfact=b, origin="center")
    # rotate: azm_deg clockwise from North -> convert to mathematical angle from x-axis
    # In projected plane, rotate positive is counter-clockwise; we want clockwise-from-North.
    # North is +y; angle from x-axis CCW == 90 - azm
    rot_ccw = 90 - azm_deg
    ell_rot = rotate(ell, rot_ccw, origin="center", use_radians=False)
    return project_geom(ell_rot, center=center, inverse=True)


# ============== NOTAM Parsing ==============


@dataclass
class NotamGeometryPart:
    kind: str  # 'POLYGON'|'CIRCLE'|'LINE_CORRIDOR'|'SECTOR'|'ELLIPSE'
    geom: Polygon
    altitude_from: Dict[str, Any] = field(default_factory=dict)
    altitude_to: Dict[str, Any] = field(default_factory=dict)
    index: Optional[int] = None
    raw: Optional[str] = None


@dataclass
class NotamFeature:
    qid: str
    icao: str
    schedule: Optional[str]
    effective: Dict[
        str, Optional[str]
    ]  # B) start, C) end, D) daily times if present (may be None)
    text: str  # E)
    parts: List[NotamGeometryPart] = field(default_factory=list)


Q_HEADER_RE = re.compile(r"^\(Q(?P<qid>\d{4})/\d+\s+NOTAM[NR]?", re.I)
FIELD_RE = re.compile(r"^\(([A-Z]\d{0,4}.*?)\)$")  # simplistic


def split_notams(raw: str) -> List[str]:
    """
    Split the big file content into individual NOTAM blocks, starting with '(Qxxxx/..'.
    """
    blocks = []
    current = []
    for line in raw.splitlines():
        if line.strip().startswith("(Q") and Q_HEADER_RE.match(line.strip()):
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        if line.strip() == "":
            continue
        current.append(line.rstrip())
    if current:
        blocks.append("\n".join(current).strip())
    return blocks


def extract_field(block: str, code: str) -> Optional[str]:
    """
    Extract a field starting with '(code' e.g., '(E)' or '(A)' or '(Q...'.
    We join wrapped lines until next '(' line or end.
    """
    lines = block.splitlines()
    # find line starting with '(' + code
    start_idx = None
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith(f"({code}") or stripped.startswith(f"{code})"):
            start_idx = i
            break
    if start_idx is None:
        return None
    buf = []
    field_marker_re = re.compile(r"^[A-Z]\)")
    for j in range(start_idx, len(lines)):
        if j > start_idx:
            stripped_next = lines[j].lstrip()
            if stripped_next.startswith("(") or field_marker_re.match(stripped_next):
                break
        buf.append(lines[j])
    # remove leading '(X)' tag from first line
    joined = "\n".join(buf)
    # Remove leading tag variants: '(E)' or 'E)'
    joined = joined.strip()
    joined = re.sub(rf"^(\({code}\)|{code}\))\s*", "", joined, flags=re.I)
    # Truncate at first occurrence of another field marker inline (e.g. ' G)' or ' F)')
    m_inline = re.search(r"\s([A-Z])\)", joined)
    if m_inline:
        # ensure it's not part of the value itself by cutting at position
        cut = m_inline.start(1) - 1  # include preceding space removal
        joined = joined[:cut].rstrip()
    return joined.strip()


def parse_altitude_pair(block: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    f_text = extract_field(block, "F")
    g_text = extract_field(block, "G")
    if g_text is None:
        # Attempt inline search for 'G)' following 'F)' on same line
        m_inline_g = re.search(r"G\)\s*([^\n)]+)", block)
        if m_inline_g:
            g_text = m_inline_g.group(1).strip()
    f_parsed = parse_alt_text(f_text or "SFC")
    g_parsed = parse_alt_text(g_text or "UNL")
    return f_parsed, g_parsed


# Pattern helpers
CIRCLE_RE = re.compile(
    r"WI\s+CIRCLE\s+RADIUS\s+([0-9.]+\s*(?:KM|M|NM))\s+CENTRE\s+([0-9NS]+\s*[0-9EW]+)\.?",
    re.I,
)
SECTOR_RE = re.compile(
    r"WI\s+SECTOR\s+(?:CENTRE\s+)?([0-9NS]+\s*[0-9EW]+)\s+AZM\s+(\d+)\s*-\s*(\d+)\s*DEG\s+RADIUS\s+([0-9.]+\s*(?:KM|M))",
    re.I,
)
ELLIPSE_RE = re.compile(
    r"ELLIPSE\s+CENTRE\s+([0-9NS]+\s*[0-9EW]+)\s+WITH\s+AXES\s+DIMENSIONS\s+([0-9.]+)X([0-9.]+)\s*KM\s+AZM\s+OF\s+MAJOR\s+AXIS\s+(\d+)",
    re.I,
)
# Alternate circle phrasing e.g. 'THE AREA WI 1KM RADIUS CENTERED ON PSN N314705E0351414'
# and 'THE AREA WI 3NM RADIUS CENTERED ON PSNS N314359E0341658 ...'
ALT_CIRCLE_RE = re.compile(
    r"WI\s+([0-9.]+)\s*(KM|M|NM)\s+RADIUS\s+CENT(?:ER|RE)(?:ED)?(?:\s+ON)?\s+(?:PSN(?:S)?\s+)?([NS]\d{4,6}[EW]\d{5,7})",
    re.I,
)

# Multi-PSN radius definition where radius is given once then multiple PSN lines follow
MULTI_PSN_RADIUS_RE = re.compile(
    r"WI\s+([0-9.]+)\s*(KM|M|NM)\s+RADIUS\s+CENT(?:ER|RE)(?:ED)?\s+PSNS",
    re.I,
)
PsnLine_RE = re.compile(r"^\s*(?:PSN\s+)?([NS]\d{4,6}[EW]\d{5,7})", re.I | re.M)
LINE_EITHER_SIDE_RE = re.compile(
    r"WI\s+([0-9.]+)\s*KM\s+EITHER\s+SIDE\s+OF\s+LINE\s+JOINING\s+POINTS:\s*(.+)$", re.I
)
AREA_COORDS_RE = re.compile(r"AREA:?\s*(.+)$", re.I)
CENTRE_INLINE_RE = re.compile(r"CENTRE\s+([0-9NS]+\s*[0-9EW]+)", re.I)


def parse_subareas(text: str) -> List[str]:
    """
    Split E) text into subarea strings by numbered items '1.' '2.' etc.
    If no numbers present, return [text].
    """
    # Normalize spaces
    t = re.sub(r"\r", "", text)
    # Split on lines starting with '1.' '2.'...
    parts = re.split(r"(?m)^\s*\d+\.\s*", t)
    # If split produced leading preamble, drop it only if following parts exist
    if len(parts) > 1:
        # First part may be preamble text; keep but merge if contains geometry
        # We will rebuild by prepending numbering info
        subareas = []
        # we lost the numbers, but index by enumeration
        for p in parts[1:]:
            subareas.append(p.strip())
        return subareas
    return [text.strip()]


def parse_coords_after(
    text: str, tag_regex: re.Pattern
) -> Optional[List[Tuple[float, float]]]:
    m = tag_regex.search(text)
    if not m:
        return None
    trail = m.group(1).strip()
    # Coordinates usually continue until a period or line break not containing coords
    # Keep only tokens looking like coords and separators
    # Replace newlines
    trail_one = " ".join(trail.splitlines())
    # Some lines end with altitude text; cut at first F) or G) start
    trail_one = re.split(r"\bF\)\b|\bG\)\b", trail_one)[0]
    coords = parse_multi_latlon_seq(trail_one)
    return coords if coords else None


def parse_line_points(text: str) -> Optional[List[Tuple[float, float]]]:
    # Normalize a newline directly after 'LINE JOINING POINTS:' into a space for regex matching
    norm_text = re.sub(r"(LINE\s+JOINING\s+POINTS:)\s*\n", r"\1 ", text, flags=re.I)
    m = LINE_EITHER_SIDE_RE.search(norm_text)
    if not m:
        return None
    points_segment = m.group(2)
    coords = parse_multi_latlon_seq(points_segment)
    return coords if coords else None


def build_parts_from_E(
    e_text: str, f_alt: Dict[str, Any], g_alt: Dict[str, Any]
) -> List[NotamGeometryPart]:
    """
    Parse the E) text to construct one or more geometry parts with altitudes attached.
    """
    parts: List[NotamGeometryPart] = []
    subareas = parse_subareas(e_text)

    for idx, sub in enumerate(subareas, start=1):
        local_parts: List[NotamGeometryPart] = []

        # 1) Circle
        for m in CIRCLE_RE.finditer(sub):
            radius_m = m_from_text(m.group(1))
            center = parse_latlon_pair(m.group(2))
            geom = build_circle(center, radius_m)
            local_parts.append(
                NotamGeometryPart(
                    kind="CIRCLE",
                    geom=geom,
                    altitude_from=f_alt,
                    altitude_to=g_alt,
                    index=idx,
                    raw=m.group(0),
                )
            )

        # 1b) Alternate circle phrasing without word 'CIRCLE RADIUS CENTRE' but 'WI 1KM RADIUS CENTERED ON PSN'
        for m in ALT_CIRCLE_RE.finditer(sub):
            radius_val = float(m.group(1))
            unit = m.group(2).upper()
            if unit == "KM":
                radius_m = radius_val * 1000.0
            elif unit == "NM":
                radius_m = radius_val * 1852.0
            else:
                radius_m = radius_val
            center = parse_latlon_pair(m.group(3))
            geom = build_circle(center, radius_m)
            local_parts.append(
                NotamGeometryPart(
                    kind="CIRCLE",
                    geom=geom,
                    altitude_from=f_alt,
                    altitude_to=g_alt,
                    index=idx,
                    raw=m.group(0),
                )
            )

        # 1c) Multi PSN radius pattern: single radius, many PSN lines each become a circle
        mmulti = MULTI_PSN_RADIUS_RE.search(sub)
        if mmulti:
            radius_val = float(mmulti.group(1))
            unit = mmulti.group(2).upper()
            if unit == "KM":
                radius_m = radius_val * 1000.0
            elif unit == "NM":
                radius_m = radius_val * 1852.0
            else:
                radius_m = radius_val
            for psn_match in PsnLine_RE.finditer(sub):
                coord_token = psn_match.group(1)
                try:
                    center = parse_latlon_pair(coord_token)
                except Exception:
                    continue
                geom = build_circle(center, radius_m)
                local_parts.append(
                    NotamGeometryPart(
                        kind="CIRCLE",
                        geom=geom,
                        altitude_from=f_alt,
                        altitude_to=g_alt,
                        index=idx,
                        raw=psn_match.group(0).strip(),
                    )
                )

        # 2) Sector
        for m in SECTOR_RE.finditer(sub):
            center = parse_latlon_pair(m.group(1))
            az1 = float(m.group(2))
            az2 = float(m.group(3))
            radius_m = m_from_text(m.group(4))
            geom = build_sector(center, radius_m, az1, az2)
            local_parts.append(
                NotamGeometryPart(
                    kind="SECTOR",
                    geom=geom,
                    altitude_from=f_alt,
                    altitude_to=g_alt,
                    index=idx,
                    raw=m.group(0),
                )
            )

        # 3) Ellipse
        for m in ELLIPSE_RE.finditer(sub):
            center = parse_latlon_pair(m.group(1))
            major_km = float(m.group(2))
            minor_km = float(m.group(3))
            azm = float(m.group(4))
            geom = build_ellipse(center, major_km, minor_km, azm)
            local_parts.append(
                NotamGeometryPart(
                    kind="ELLIPSE",
                    geom=geom,
                    altitude_from=f_alt,
                    altitude_to=g_alt,
                    index=idx,
                    raw=m.group(0),
                )
            )

        # 4) Line corridor "either side of line"
        m = LINE_EITHER_SIDE_RE.search(sub)
        if m:
            half_width_km = float(m.group(1))
            pts = parse_line_points(sub)
            if pts and len(pts) >= 2:
                geom = build_line_corridor(pts, km(half_width_km))
                local_parts.append(
                    NotamGeometryPart(
                        kind="LINE_CORRIDOR",
                        geom=geom,
                        altitude_from=f_alt,
                        altitude_to=g_alt,
                        index=idx,
                        raw=m.group(0),
                    )
                )

        # 5) Polygon AREA
        # May appear as "AREA:" or "AIRSPACE CLSD WI AREA:" then coords
        # We'll extract after 'AREA:' occurrences
        area_coords = []
        for m in AREA_COORDS_RE.finditer(sub):
            coords = parse_coords_after(m.group(0), re.compile(r"AREA:?\s*(.+)$", re.I))
            if coords and len(coords) >= 3:
                area_coords.append(coords)
        # Special case: sometimes coords listed directly in E) without explicit "AREA"
        if not area_coords:
            # Try to detect dense coord sequences
            coords = parse_multi_latlon_seq(sub)
            if coords and len(coords) >= 3:
                # Heuristic: if there are many coords and no other geometry matched
                if len(coords) >= 3 and len(local_parts) == 0:
                    area_coords.append(coords)

        for coords in area_coords:
            geom = build_polygon(coords)
            local_parts.append(
                NotamGeometryPart(
                    kind="POLYGON",
                    geom=geom,
                    altitude_from=f_alt,
                    altitude_to=g_alt,
                    index=idx,
                    raw="AREA",
                )
            )

        # 6) “WI 0.XX KM EITHER SIDE OF LINE” with “SECTOR” center syntax variants handled by regexes above.

        # 7) Handle tiny “WI 0.05KM EITHER SIDE OF LINE JOINING POINTS: A-B-C ...”
        # Already covered by LINE_EITHER_SIDE_RE.

        # If none found, keep searching for 'CENTRE' and a radius in subsequent lines — already covered by circle/sector.

        parts.extend(local_parts)

    return parts


def parse_notam_block(block: str) -> Optional[NotamFeature]:
    header_line = next(
        (ln for ln in block.splitlines() if ln.strip().startswith("(Q")), None
    )
    if not header_line:
        return None
    m = Q_HEADER_RE.match(header_line.strip())
    if not m:
        return None
    qid = m.group("qid")

    icao = extract_field(block, "A") or ""
    schedule = extract_field(block, "D")
    e_text = extract_field(block, "E") or ""
    f_alt, g_alt = parse_altitude_pair(block)
    b_field = extract_field(block, "B") or ""
    c_field = extract_field(block, "C") or ""

    parts = build_parts_from_E(e_text, f_alt, g_alt)
    return NotamFeature(
        qid=qid,
        icao=icao.strip(),
        schedule=schedule.strip() if schedule else None,
        effective={
            "B": b_field.strip(),
            "C": c_field.strip(),
            "D": schedule.strip() if schedule else None,
        },
        text=e_text,
        parts=parts,
    )


def notams_to_geojson(features: List[NotamFeature]) -> Dict[str, Any]:
    fc = {"type": "FeatureCollection", "features": []}
    for f in features:
        if not f.parts:
            continue
        # Combine parts per NOTAM as MultiPolygon or GeometryCollection
        geoms = [p.geom for p in f.parts]
        # Try union for cleaner MultiPolygon if all are polygons
        try:
            unioned = unary_union(geoms)
            geom_geojson = mapping(unioned)
        except Exception:
            # fallback to collection
            geom_geojson = mapping(GeometryCollection(geoms))

        props = {
            "qid": f.qid,
            "icao": f.icao,
            "effective": f.effective,
            "schedule": f.schedule,
            "text": f.text,
            "parts": [
                {
                    "index": p.index,
                    "kind": p.kind,
                    "alt_from": p.altitude_from,
                    "alt_to": p.altitude_to,
                    "raw": p.raw,
                }
                for p in f.parts
            ],
        }
        fc["features"].append(
            {"type": "Feature", "geometry": geom_geojson, "properties": props}
        )
    return fc


def parse_notam_file_text(raw: str) -> Dict[str, Any]:
    blocks = split_notams(raw)
    features: List[NotamFeature] = []
    for blk in blocks:
        try:
            nf = parse_notam_block(blk)
            if nf:
                features.append(nf)
        except Exception as e:
            # You may log these and continue
            # print(f"Error parsing block: {e}")
            continue
    return notams_to_geojson(features)
