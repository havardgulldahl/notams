import re
import json
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any, Iterable, Mapping, Sequence

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

# Heuristic: maximum radius (NM) we represent as a circle polygon; larger areas fallback to a point
MAX_CIRCLE_RADIUS_NM = 200

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
    m = re.fullmatch(r"(\d{4,7})([NSEW])", token)
    if not m:
        raise ValueError(f"Bad DMS token: {token}")
    num, hemi = m.groups()
    # Split into degrees, minutes, seconds depending on length
    if len(num) == 7:
        dd = int(num[:3])
        mm = int(num[3:5])
        ss = int(num[5:7])
    elif len(num) == 6:
        dd = int(num[:2])
        mm = int(num[2:4])
        ss = int(num[4:6])
    elif len(num) == 5:
        dd = int(num[:3])
        mm = int(num[3:5])
        ss = 0
    elif len(num) == 4:
        dd = int(num[:2])
        mm = int(num[2:4])
        ss = 0
    else:
        # Fallback or error?
        raise ValueError(f"Unexpected DMS length {len(num)} in {token}")

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
    scan text for all coordinate occurrences.
    """
    # Pattern:
    # 1. Lat (4-6 digits + N/S)
    # 2. Separator (optional space, or none)
    # 3. Lon (5-7 digits + E/W)
    # Note: Regex from parse_latlon_pair logic
    pat = re.compile(r"(\d{4,6}[NS])\s*(\d{5,7}[EW])")

    coords = []
    for m in pat.finditer(text):
        lat_tok, lon_tok = m.groups()
        try:
            lat = dms_token_to_deg(lat_tok)
            lon = dms_token_to_deg(lon_tok)
            coords.append((lon, lat))
        except ValueError:
            continue

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
    Convert '5KM' -> 5000, '0.5KM' -> 500, '500M' -> 500, '1NM' -> 1852.
    """
    t = val_text.strip().upper().replace(" ", "")
    m = re.match(r"(\d+(?:\.\d+)?)(KM|NM|M)", t)
    if not m:
        raise ValueError(f"Cannot parse distance: {val_text}")
    v, unit = m.groups()
    v = float(v)
    if unit == "KM":
        return v * 1000.0
    elif unit == "NM":
        return v * 1852.0
    else:
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
    # Deduplicate adjacent points
    if not coords:
        raise ValueError("Polygon requires at least 3 points")

    unique_coords = [coords[0]]
    for pt in coords[1:]:
        if pt != unique_coords[-1]:
            unique_coords.append(pt)

    if len(unique_coords) < 3:
        # If we only have 1 or 2 unique points, it can't be a polygon.
        # But maybe original had 3 points and duplicates reduced it?
        # Fallback to original behavior to let caller handle error or producing invalid geom.
        # But 'Polygon' constructor raises ValueError for insufficient points.
        # We'll try to use unique_coords if enough, else original (which might still fail or be weird)
        if len(coords) >= 3:
            # Original had enough points, but they were duplicates. e.g. A-A-B-B-C-C.
            pass
        else:
            raise ValueError("Polygon requires at least 3 points")

    # If deduplication left < 3 points, we have a problem.
    if len(unique_coords) < 3:
        # Try to preserve at least start/end if they were intended.
        # If truly degenerate, return a small buffer? Or raise.
        # For notam parsing, better to skip than crash?
        # But we can't skip here (return type Polygon).
        # We'll rely on original coords if filtered is too small, assume callers check.
        pass
    else:
        coords = unique_coords

    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]

    poly = Polygon(coords)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def build_circle(
    center: Tuple[float, float], radius_m: float, n_points: int = 128
) -> Polygon:
    """
    Build a circle polygon by buffering a point in local AEQD projection.
    """
    lon, lat = center
    p = Point(lon, lat)
    proj = project_geom(p, center=(lon, lat), inverse=False)
    circ = proj.buffer(radius_m, quad_segs=max(4, n_points // 4))
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
    buf = proj.buffer(half_width_m, join_style=2)  # mitre/round: 2=mitre, 1=round
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


def build_arc(
    center: Tuple[float, float],
    radius_m: float,
    start_pt: Tuple[float, float],
    end_pt: Tuple[float, float],
    clockwise: bool,
    n_points: int = 64,
) -> Polygon:
    """
    Build an arc polygon.
    """
    p_start = Point(start_pt)
    p_end = Point(end_pt)

    # Project to local plane
    p_s_proj = project_geom(p_start, center=center, inverse=False)
    p_e_proj = project_geom(p_end, center=center, inverse=False)

    x1, y1 = p_s_proj.x, p_s_proj.y
    x2, y2 = p_e_proj.x, p_e_proj.y

    ang_start_rad = math.atan2(y1, x1)
    ang_end_rad = math.atan2(y2, x2)

    start_deg = math.degrees(ang_start_rad)
    end_deg = math.degrees(ang_end_rad)

    # Clockwise: angle decreases. Anti-clockwise: angle increases.
    if clockwise:
        if end_deg > start_deg:
            end_deg -= 360.0
        diff = start_deg - end_deg
    else:
        if end_deg < start_deg:
            end_deg += 360.0
        diff = end_deg - start_deg

    pts = [(0.0, 0.0)]
    steps = max(4, int(n_points * (diff / 360.0)))
    for i in range(steps + 1):
        if clockwise:
            a = start_deg - i * (diff / steps)
        else:
            a = start_deg + i * (diff / steps)
        rad = math.radians(a)
        pts.append((radius_m * math.cos(rad), radius_m * math.sin(rad)))
    pts.append((0.0, 0.0))

    poly_local = Polygon(pts)
    if not poly_local.is_valid:
        poly_local = poly_local.buffer(0)
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
    circ = proj_p.buffer(1.0, quad_segs=max(4, n // 4))
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
    effective: Dict[str, str]  # B) start, C) end, D) daily times if present
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
    Extract a field content starting with 'code)' or '(code)'.
    Example: code='E' -> matches 'E)' or '(E)'.
    Stops at the next field occurrence (A-G) or end of block.
    """
    # Regex to find the specific field tag:
    # Matches start of string or whitespace or '(', then the code, then ')'
    # We allow 'Q' to look for 'Q)' just in case, though Q is usually special.
    # We mainly target A-G.

    pattern = re.compile(rf"(?:^|\s|\()({code}\))", re.MULTILINE)
    m = pattern.search(block)
    if not m:
        # Fallback: sometimes fields are missing ')', e.g. '(A ...' ? Quite rare for A-G.
        # But let's stick to X) for now based on test data.
        return None

    start_pos = m.end()
    remainder = block[start_pos:]

    # Find start of next field to stop extraction
    # Look for any A-G followed by ) preceded by whitespace or start of line or (
    next_field_pat = re.compile(r"(?:^|\s|\()([A-G]\))", re.MULTILINE)

    m_next = next_field_pat.search(remainder)
    if m_next:
        # We found another field start, cut before it
        # Note: m_next.start() includes the prefix whitespace/paren if matched via group 0
        # boolean OR logic in regex (?:...) is non-capturing, but the match object covers it.
        # We matched (?:^|\s|\() which implies we found the separator.
        # So remainder[:m_next.start()] cuts at the separator.
        content = remainder[: m_next.start()]
    else:
        content = remainder

    # Cleanup
    content = content.strip()
    # Remove trailing ')' if it looks like the end-of-notam-block paren
    # Only if the original block ended with ) and we are at the end?
    # Simple heuristic: if content ends with ) and has no matching (, remove it?
    # Or just leave it. "100M AMSL)" -> "100M AMSL" is better.
    if content.endswith(")") and "(" not in content:
        # e.g. "150M AMSL)" -> "150M AMSL"
        # but be careful of "(some info)"
        content = content[:-1].strip()

    return content


def parse_altitude_pair(block: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    f_text = extract_field(block, "F")
    g_text = extract_field(block, "G")
    f_parsed = parse_alt_text(f_text or "SFC")
    g_parsed = parse_alt_text(g_text or "UNL")
    return f_parsed, g_parsed


# Pattern helpers
CIRCLE_RE = re.compile(
    r"WI\s+CIRCLE\s+RADIUS\s+([0-9.]+\s*(?:KM|NM|M))\s+CENTR(?:E|ED\s+AT)\s+\(?\s*([0-9NS\s]+[0-9EW]+)\s*\)?\.?",
    re.I,
)
SECTOR_RE = re.compile(
    r"WI\s+SECTOR\s+"
    r"(?:"
    r"(?:CENTR(?:E|ED\s+AT)\s+)?\(?\s*([0-9NS\s]+[0-9EW]+)\s*\)?\s+(?:BTN\s+)?(?:AZM(?:AG)?\s+)?(\d+)\s*-\s*(\d+)\s*DEG"
    r"|"
    r"(?:BTN\s+)?(?:AZM(?:AG)?\s+)?(\d+)\s*-\s*(\d+)\s*DEG\s+(?:FROM|CENTR(?:E|ED\s+AT))\s+\(?\s*([0-9NS\s]+[0-9EW]+)\s*\)?"
    r")"
    r"\s+RADIUS\s+([0-9.]+\s*(?:KM|NM|M))",
    re.I,
)
ELLIPSE_RE = re.compile(
    r"ELLIPSE\s+CENTR(?:E|ED\s+AT)\s+\(?\s*([0-9NS\s]+[0-9EW]+)\s*\)?\s+WITH\s+AXES\s+DIMENSIONS\s+([0-9.]+)X([0-9.]+)\s*(KM|NM|M)\s+AZM\s+OF\s+MAJOR\s+AXIS\s+(\d+)",
    re.I,
)
ARC_RE = re.compile(
    r"(\d{4,6}[NS]\s*\d{5,7}[EW]).*?"
    r"(?:THEN\s+)?(CLOCKWISE|ANTICLOCKWISE|COUNTER-CLOCKWISE)\s+"
    r"(?:ALONG\s+|BY\s+)?ARC\s+(?:OF\s+A?\s*CIRCLE\s+)?"
    r"RADIUS\s+(?:OF\s+)?([0-9]+(?:\.[0-9]+)?)\s*(KM|NM|M)\s+"
    r"CENTR(?:E|ED\s+AT)\s+\(?\s*(\d{4,6}\s*[NS]\s*\d{5,7}\s*[EW])\s*\)?\s+"
    r"TO\s+(\d{4,6}\s*[NS]\s*\d{5,7}\s*[EW])",
    re.I | re.DOTALL,
)
LINE_EITHER_SIDE_RE = re.compile(
    r"WI\s+([0-9.]+)\s*(KM|NM|M)\s+EITHER\s+SIDE\s+OF\s+LINE\s+JOINING\s+POINTS:\s*(.+)$",
    re.I,
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
    m = LINE_EITHER_SIDE_RE.search(text)
    if not m:
        return None
    # Group 1: width, Group 2: unit, Group 3: points text
    points_str = m.group(3)
    # points may span multiple lines until a period
    points_str = points_str.split("\n")[0]
    # allow over multiple lines by capturing until 'F)' or end
    points_block = re.split(r"\.\s|F\)|G\)", text[m.start() :], maxsplit=1)[0]
    # Extract all coords from points_block
    coords = parse_multi_latlon_seq(points_block)
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

        # 2) Sector
        for m in SECTOR_RE.finditer(sub):
            if m.group(1):
                center_text = m.group(1)
                az1 = float(m.group(2))
                az2 = float(m.group(3))
            else:
                az1 = float(m.group(4))
                az2 = float(m.group(5))
                center_text = m.group(6)

            radius_text = m.group(7)
            radius_m = m_from_text(radius_text)
            center = parse_latlon_pair(center_text)
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
            major = float(m.group(2))
            minor = float(m.group(3))
            unit = m.group(4)
            azm = float(m.group(5))

            if unit == "NM":
                major_km = major * 1.852
                minor_km = minor * 1.852
            elif unit == "M":
                major_km = major / 1000.0
                minor_km = minor / 1000.0
            else:
                major_km = major
                minor_km = minor

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

        # 3.5) Arc
        for m in ARC_RE.finditer(sub):
            start_coord = parse_latlon_pair(m.group(1))
            direction = m.group(2).upper()
            radius_val = float(m.group(3))
            radius_unit = m.group(4).upper()
            center_coord = parse_latlon_pair(m.group(5))
            end_coord = parse_latlon_pair(m.group(6))

            if radius_unit == "KM":
                radius_m = radius_val * 1000.0
            elif radius_unit == "NM":
                radius_m = radius_val * 1852.0
            else:  # M
                radius_m = radius_val

            clockwise = (
                "CLOCKWISE" in direction
                and "COUNTER" not in direction
                and "ANTI" not in direction
            )

            geom = build_arc(center_coord, radius_m, start_coord, end_coord, clockwise)
            local_parts.append(
                NotamGeometryPart(
                    kind="ARC",
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
            half_width_val = float(m.group(1))
            unit = m.group(2)
            if unit == "KM":
                half_width_km = half_width_val
            elif unit == "NM":
                half_width_km = half_width_val * 1.852
            else:  # M
                half_width_km = half_width_val / 1000.0

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


@dataclass
class StubNotam:
    """Lightweight stand-in for a pynotam.Notam used in unit tests."""

    area: Optional[Mapping[str, Any]] = None
    location: Optional[Iterable[str]] = None
    body: Optional[str] = None

    def decoded(self) -> str:
        return self.body or ""


def build_geometry(
    notam: Any,
    airport_locations: Mapping[str, Mapping[str, float | str]],
    max_circle_radius_nm: float = MAX_CIRCLE_RADIUS_NM,
) -> Optional[Dict[str, Any]]:
    """
    Adapter function to be compatible with scripts/geo.py build_geometry interface.
    Extracts geometry from a Notam object (or string) using high-precision parsing.
    """
    e_text = ""
    # Try pynotam Notam object attributes
    if hasattr(notam, "body") and notam.body:
        e_text = notam.body
    elif hasattr(notam, "decoded"):
        # Fallback to full decoded text
        e_text = notam.decoded()
    elif isinstance(notam, str):
        e_text = notam

    if e_text:
        e_text = e_text.upper().replace("\n", " ").strip()

    # Parse Item E text
    # We pass empty altitude dicts as we only need the 2D geometry here
    parts = build_parts_from_E(e_text, {}, {})
    geoms = [p.geom for p in parts]

    # Fallback to structured data if no geometry found in text
    if not geoms:
        # Check 'area' attribute (pynotam parsed structure)
        area = getattr(notam, "area", None)
        if isinstance(area, Mapping):
            lat_raw = area.get("lat")
            lon_raw = area.get("long")
            if isinstance(lat_raw, str) and isinstance(lon_raw, str):
                try:
                    lat = dms_token_to_deg(lat_raw)
                    lon = dms_token_to_deg(lon_raw)
                    radius = area.get("radius")
                    # Assume pynotam radius is NM
                    if (
                        isinstance(radius, (int, float))
                        and radius < max_circle_radius_nm
                    ):
                        geoms.append(build_circle((lon, lat), radius * 1852.0))
                    else:
                        geoms.append(Point(lon, lat))
                except ValueError:
                    pass

        # Check 'location' attribute (ICAO list) -> Airport lookup
        if not geoms:
            locs = getattr(notam, "location", []) or []
            # pynotam location is a list
            if locs and len(locs) > 0:
                first = locs[0]
                ap = airport_locations.get(first)
                if ap:
                    try:
                        geoms.append(Point(float(ap["lon"]), float(ap["lat"])))
                    except (ValueError, KeyError, TypeError):
                        pass

    if not geoms:
        return None

    # Merge geometries
    try:
        final_geom = unary_union(geoms) if len(geoms) > 1 else geoms[0]
        # Ensure result is valid
        if not final_geom.is_valid:
            final_geom = final_geom.buffer(0)
    except Exception:
        # Fallback for heterogeneous collections
        final_geom = GeometryCollection(geoms)

    out = mapping(final_geom)
    # Add metadata for tests (infer from first part found in text)
    if parts:
        out["meta"] = {"shape": parts[0].kind.lower()}

    return out
