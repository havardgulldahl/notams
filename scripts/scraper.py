from datetime import datetime
import re
import json
import csv
import math
import requests
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, cast
from bs4.element import Tag
import notam  # pynotam library

BASE_URL: str = "https://www.caica.ru/ANI_Official/notam/notam_series/"
OUTPUT_FILE: str = "docs/notams.geojson"  # GitHub Pages serves from /docs


def fetch(url: str, timeout: int = 10) -> Optional[requests.Response]:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        print(f"Network error while fetching {url}: {e}")
        return None


def parse_html_list(html: str) -> List[str]:
    """Find NOTAM files listed on the page.

    Extracts file names from <td> elements that have an onclick attribute matching
    the expected pattern. Safely handles elements without a title attribute.
    """
    soup = BeautifulSoup(html, "html.parser")
    files: List[str] = []
    rx = re.compile(r"location='(.*)_eng.html'")
    for node in soup.find_all("td", onclick=rx, width=""):
        if isinstance(node, Tag):
            title_val = node.get("title")
            if isinstance(title_val, list):  # BeautifulSoup may return list
                if title_val:
                    title_val = title_val[0]
            if isinstance(title_val, str):
                filename = title_val.strip()
                if filename:
                    files.append(filename)
    return files


# ----------------------------
# Abbreviation expansions
# ----------------------------
abbr_map = {
    "U/S": "Unserviceable",
    "AVBL": "Available",
    "NOT AVBL": "Not Available",
    "NOTAMR": "NOTAM Replacement",
    "PROC": "Procedure",
    "EQPT": "Equipment",
    "GNSS": "Global Navigation Satellite System",
    "GPS": "Global Positioning System",
    "GBAS": "Ground Based Augmentation System",
    "GLS": "GNSS Landing System",
    "APCH": "Approach",
    "RWY": "Runway",
    "NAV": "Navigation",
    "WI": "Within",
    "OPR": "Operation",
    "POSS": "Possible",
    "INTERRUPTIONS": "Interruptions",
    "DISRUPTIONS": "Disruptions",
    "ALTN": "Alternate",
    "EST": "Estimated",
    "REF": "Reference",
    "AIP": "Aeronautical Information Publication",
    "AD": "Aerodrome",
    "CH": "Channel",
}


def expand_abbreviations(text: str) -> str:
    for abbr, full in abbr_map.items():
        text = re.sub(rf"\b{abbr}\b", full, text)
    return text


# ----------------------------
# FIR centroids (fallbacks)
# ----------------------------
fir_centers = {
    "UEEE": (129.75, 62.1),  # Yakutsk FIR approx
    "UHHH": (135.2, 48.5),  # Khabarovsk FIR approx
    "ULLL": (37.6, 55.75),  # Moscow FIR approx
    "USSV": (60.0, 56.9),  # Ekaterinburg FIR approx
}


# ----------------------------
# Coordinate parsing
# ----------------------------
def parse_coord(coord_str: str) -> tuple[Optional[float], Optional[float]]:
    match = re.match(r"(\d{2,4})([NS])(\d{3,5})([EW])", coord_str)
    if not match:
        return None, None
    lat_degmin, ns, lon_degmin, ew = match.groups()
    lat = int(lat_degmin[:-2]) + int(lat_degmin[-2:]) / 60
    if ns == "S":
        lat = -lat
    lon = int(lon_degmin[:-2]) + int(lon_degmin[-2:]) / 60
    if ew == "W":
        lon = -lon
    return lat, lon


def parse_q_line(q_line: str) -> Optional[dict[str, Any]]:
    parts = q_line.split("/")
    if len(parts) < 8:
        return None
    lower = int(parts[5]) * 100
    upper = int(parts[6]) * 100
    coord_part = parts[7]

    # Circle
    circle_match = re.match(r"(\d+[NS]\d+[EW])(\d+)", coord_part)
    if circle_match:
        coord_str, radius_str = circle_match.groups()
        lat, lon = parse_coord(coord_str)
        return {
            "type": "circle",
            "lat": lat,
            "lon": lon,
            "radius_nm": int(radius_str),
            "lower_ft": lower,
            "upper_ft": upper,
        }

    # Polygon
    poly_match = re.findall(r"\d+[NS]\d+[EW]", coord_part)
    if poly_match and len(poly_match) >= 2:
        points = [parse_coord(c) for c in poly_match]
        return {
            "type": "polygon",
            "coordinates": points,
            "lower_ft": lower,
            "upper_ft": upper,
        }

    # FIR-wide
    return {
        "type": "fir",
        "fir": parts[0].replace("Q)", "").strip(),
        "lower_ft": lower,
        "upper_ft": upper,
    }


# ----------------------------
# Geometry builders (no shapely)
# ----------------------------
def circle_polygon(
    lat: float, lon: float, radius_nm: float, n_points: int = 64
) -> dict[str, Any]:
    R = 6371000.0  # Earth radius meters
    radius_m = radius_nm * 1852
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    d = radius_m / R

    coords = []
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
    coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def polygon_geometry(
    coords: list[tuple[Optional[float], Optional[float]]],
) -> dict[str, Any]:
    lonlat = [[lon, lat] for lat, lon in coords if lat is not None and lon is not None]
    if len(lonlat) < 3:
        # Fallback to empty geometry-like structure (GeoJSON validity minimal)
        return {"type": "Polygon", "coordinates": [[[]]]}
    lonlat.append(lonlat[0])  # close ring
    return {"type": "Polygon", "coordinates": [lonlat]}


# ----------------------------
# Main parser function
# ----------------------------
def parse_notam_files(
    html_files: list[str], airports_csv: str = "airports.csv", output: str = "."
) -> None:
    """Parse NOTAM HTML files, decode each record with pynotam, and output GeoJSON per class.

    Each HTML file is assumed to contain multiple NOTAM records separated by blank lines.
    A GeoJSON file per NOTAM series (first letter of source filename) is produced.
    """

    # Load airport database (ident -> name, lat, lon)
    airport_locations: dict[str, dict[str, float | str]] = {}
    try:
        with open(airports_csv, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                try:
                    airport_locations[row["ident"]] = {
                        "name": row.get("name", ""),
                        "lat": float(row["latitude_deg"]),
                        "lon": float(row["longitude_deg"]),
                    }
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        print(
            f"⚠ Airport CSV '{airports_csv}' not found; proceeding without airport enrichment."
        )

    def dms_min_to_decimal(coord: str) -> Optional[float]:
        """Convert a coordinate like 5535N or 03716E to decimal degrees."""
        m = re.match(r"^(\d+)([NSEW])$", coord)
        if not m:
            return None
        value, hemi = m.groups()
        # Split value into degrees and minutes (last 2 digits = minutes, rest = degrees)
        if len(value) < 3:
            return None
        deg = int(value[:-2])
        minutes = int(value[-2:])
        dec = deg + minutes / 60.0
        if hemi in ("S", "W"):
            dec = -dec
        return dec

    for file_path in html_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
        except FileNotFoundError:
            print(f"⚠ File not found: {file_path}")
            continue

        raw_text = soup.get_text("\n")
        records = [rec.strip() for rec in raw_text.split("\n\n") if rec.strip()]

        geojson: dict[str, Any] = {"type": "FeatureCollection", "features": []}

        for rec in records:
            try:
                decoded = notam.Notam.from_str(rec)
            except Exception as e:
                print(f"Failed to decode NOTAM record: {e}")
                continue

            # Build geometry
            geometry: Optional[dict[str, Any]] = None
            area = getattr(decoded, "area", None)
            if area and area.get("lat") and area.get("long"):
                lat_raw = area.get("lat")
                lon_raw = area.get("long")
                lat_dec = dms_min_to_decimal(lat_raw)
                lon_dec = dms_min_to_decimal(lon_raw)
                if lat_dec is not None and lon_dec is not None:
                    radius = area.get("radius")
                    if (
                        isinstance(radius, (int, float)) and radius and radius < 500
                    ):  # heuristic
                        geometry = circle_polygon(lat_dec, lon_dec, float(radius))
                    else:
                        geometry = {"type": "Point", "coordinates": [lon_dec, lat_dec]}

            # Fallback using first location code
            if geometry is None:
                locs = getattr(decoded, "location", []) or []
                if locs:
                    loc = locs[0]
                    ap = airport_locations.get(loc)
                    if ap:
                        geometry = {
                            "type": "Point",
                            "coordinates": [ap["lon"], ap["lat"]],
                        }
                # FIR fallback
                if geometry is None and decoded.fir in fir_centers:
                    lon_fir, lat_fir = fir_centers[decoded.fir]
                    geometry = {"type": "Point", "coordinates": [lon_fir, lat_fir]}

            traffic = getattr(decoded, "traffic_type", None)
            purpose = getattr(decoded, "purpose", None)
            scope = getattr(decoded, "scope", None)
            locations_val = getattr(decoded, "location", None)
            props = {
                "notam_id": decoded.notam_id,
                "notam_type": decoded.notam_type,
                "fir": decoded.fir,
                "notam_code": decoded.notam_code,
                # Convert potential set/list fields to sorted lists (or keep None)
                "traffic_type": (
                    sorted(list(traffic))
                    if isinstance(traffic, (set, list, tuple))
                    else traffic
                ),
                "purpose": (
                    sorted(list(purpose))
                    if isinstance(purpose, (set, list, tuple))
                    else purpose
                ),
                "scope": (
                    sorted(list(scope))
                    if isinstance(scope, (set, list, tuple))
                    else scope
                ),
                "fl_lower": decoded.fl_lower,
                "fl_upper": decoded.fl_upper,
                "valid_from": str(decoded.valid_from),
                "valid_till": str(decoded.valid_till),
                "schedule": decoded.schedule,
                "body": decoded.body,
                "locations": (
                    list(locations_val)
                    if isinstance(locations_val, (set, list, tuple))
                    else locations_val
                ),
                "area_raw": decoded.area,
            }

            geojson["features"].append(
                {"type": "Feature", "geometry": geometry, "properties": props}
            )

        notam_class = file_path.split("/")[-1][0:1]
        out_path = f"{output}{notam_class}.geojson"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, indent=2, ensure_ascii=False)
        print(f"✅ Decoded NOTAMs saved to {out_path}")


def main() -> None:
    print(f"Fetching NOTAM index page: {BASE_URL}")
    index_response = fetch(BASE_URL)
    if not index_response:
        print("Failed to fetch the NOTAM index page. Exiting.")
        return
    html: str = index_response.text
    print("Parsing NOTAM file list...")
    files: List[str] = parse_html_list(html)
    print(f"Found {len(files)} NOTAM files.")

    saved = 0
    for i, f in enumerate(files, 1):
        # check with current/.scrape_timestamp to see if we have the file already
        try:
            with open("current/.scrape_timestamp", "r", encoding="utf-8") as file:
                timestamp = file.read().strip()
                if f[1:11] == timestamp:
                    print(f"Already downloaded this timestamp: {timestamp}")
                    return
        except FileNotFoundError:
            pass

        url: str = BASE_URL + f
        print(f"[{i}/{len(files)}] Downloading: {url}")
        notam_response = fetch(url)
        if notam_response:
            # store notam in current/ directory
            with open(f"current/{f}", "w", encoding="utf-8") as file:
                file.write(notam_response.text)
            saved += 1
        else:
            print(f"Skipping {url} due to download error.")

    # extract timestamp from first file, and store it in current/.scrape_timestamp
    try:
        timestamp = files[0][1:11]
        with open("current/.scrape_timestamp", "w", encoding="utf-8") as file:
            file.write(timestamp)
        with open("docs/scrape_timestamp", "w", encoding="utf-8") as file:
            # convert YYMMDDHH to YYYY-MM-DD HH:MM
            file.write(
                f"20{timestamp[:2]}-{timestamp[2:4]}-{timestamp[4:6]} {timestamp[6:8]}:{timestamp[8:10]}"
            )
    except IndexError:
        print("No files found to extract timestamp.")

    print(f"Saved {saved} NOTAMs to current/")

    parse_notam_files(
        html_files=[f"current/{f}" for f in files],
        airports_csv="ru-airports.csv",
        output="docs/",
    )


if __name__ == "__main__":
    main()
