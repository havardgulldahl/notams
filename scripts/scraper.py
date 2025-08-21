from datetime import datetime
import re
import json
import csv
import math
import requests
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional

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
    """Find NOTAM files listed on the page"""
    soup = BeautifulSoup(html, "html.parser")
    files: List[str] = []
    # loop through all <td> tags with title="File: ..."
    rx = re.compile(r"location='(.*)_eng.html'")
    for link in soup.find_all("td", onclick=rx, width=""):
        # extract filename from the attribute title="A2508210553_eng.html"
        filename = link.get("title", "").strip()
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
    lonlat = [[lon, lat] for lat, lon in coords if lat and lon]
    lonlat.append(lonlat[0])
    return {"type": "Polygon", "coordinates": [lonlat]}


# ----------------------------
# Main parser function
# ----------------------------
def parse_notam_files(
    html_files: list[str], airports_csv: str = "airports.csv", output: str = "."
) -> None:
    # Load airport database
    airport_locations = {}
    with open(airports_csv, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # extract ident,type,name,latitude_deg,longitude_deg
            for z in ["ident", "type", "name", "latitude_deg", "longitude_deg"]:
                airport_locations[row["ident"]] = {
                    "name": row["name"],
                    "lat": float(row["latitude_deg"]),
                    "lon": float(row["longitude_deg"]),
                }

    geojson = {"type": "FeatureCollection", "features": []}

    for file_path in html_files:
        with open(file_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        raw_text = soup.get_text("\n")
        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

        messages = []
        current_airport = None
        buffer = []

        for line in lines:
            if re.match(r"^[A-Z]{4}:$", line):
                if buffer and current_airport:
                    messages.append((current_airport, " ".join(buffer)))
                    buffer = []
                current_airport = line.replace(":", "")
            else:
                buffer.append(line)

        if buffer and current_airport:
            messages.append((current_airport, " ".join(buffer)))

        # Build features
        for airport, notam_text in messages:
            expanded = expand_abbreviations(notam_text)
            geometry = None
            lower_ft = None
            upper_ft = None

            q_match = re.search(r"Q\)([A-Z0-9/]+)", notam_text)
            if q_match:
                q_data = parse_q_line("Q)" + q_match.group(1))
                if q_data:
                    lower_ft, upper_ft = q_data.get("lower_ft"), q_data.get("upper_ft")
                    if q_data["type"] == "circle":
                        geometry = circle_polygon(
                            q_data["lat"], q_data["lon"], q_data["radius_nm"]
                        )
                    elif q_data["type"] == "polygon":
                        geometry = polygon_geometry(q_data["coordinates"])
                    elif q_data["type"] == "fir":
                        fir = q_data["fir"]
                        if fir in fir_centers:
                            lon, lat = fir_centers[fir]
                            geometry = {"type": "Point", "coordinates": [lon, lat]}

            if geometry is None and airport in airport_locations:
                coords = [
                    airport_locations[airport]["lon"],
                    airport_locations[airport]["lat"],
                ]
                geometry = {"type": "Point", "coordinates": coords}

            feature = {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "airport": airport,
                    "location": airport_locations.get(airport, {}).get("name"),
                    # "raw": notam_text,
                    "expanded": expanded,
                    "lower_ft": lower_ft,
                    "upper_ft": upper_ft,
                },
            }
            geojson["features"].append(feature)

        # Save GeoJSON
        notam_class = file_path.split("/")[-1][0:1]
        with open(output + notam_class + ".geojson", "w", encoding="utf-8") as f:
            json.dump(geojson, f, indent=2, ensure_ascii=False)
        print(f"✅ Combined NOTAMs saved to {notam_class}.geojson")

    return geojson


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
