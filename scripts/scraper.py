import pathlib
import re
import json
import csv
import math
from time import time
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from bs4.element import Tag
import notam  # pynotam library

# Local geometry utilities (extracted for testability)
try:  # Support running as module or script
    from .geo import build_geometry, MAX_CIRCLE_RADIUS_NM
except ImportError:  # pragma: no cover
    from geo import build_geometry, MAX_CIRCLE_RADIUS_NM  # type: ignore

BASE_URL: str = "https://www.caica.ru/ANI_Official/notam/notam_series/"
# NOTE: MAX_CIRCLE_RADIUS_NM now imported from geo.py


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
    the expected pattern.
    """
    soup = BeautifulSoup(html, "html.parser")
    files: List[str] = []
    rx = re.compile(r"location='(.*)_eng.html'")
    for node in soup.find_all("td", onclick=rx, width=""):
        if isinstance(node, Tag):
            onclick_val = node.get("onclick")
            if isinstance(onclick_val, str):
                match = rx.search(onclick_val)
                if match:
                    files.append(f"{match.group(1)}_eng.html")
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


def polygon_geometry(
    coords: list[tuple[Optional[float], Optional[float]]],
) -> dict[str, Any]:
    """(Deprecated) Polygon helper retained for backward compatibility."""
    lonlat = [[lon, lat] for lat, lon in coords if lat is not None and lon is not None]
    if len(lonlat) < 3:
        return {"type": "Polygon", "coordinates": [[[]]]}
    lonlat.append(lonlat[0])
    return {"type": "Polygon", "coordinates": [lonlat]}


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

    success_count = 0
    failure_count = 0

    for file_path in html_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
        except FileNotFoundError:
            print(f"⚠ File not found: {file_path}")
            continue

        # remove clutter (guard: title tag may be missing in minimal test HTML)
        title_tag = soup.find("title")
        if title_tag:
            title_tag.decompose()
        for tag in soup.find_all("font", {"color": "red"}):
            tag.decompose()

        raw_text = soup.get_text("\n").translate({0xA0: 0x20})
        separated = raw_text.replace("\n\n(", "U7U7U7U7U7U7(")  # unique separator
        records = [
            rec.strip() for rec in separated.split("U7U7U7U7U7U7") if rec.strip()
        ]

        geojson: dict[str, Any] = {"type": "FeatureCollection", "features": []}

        for rec in records:
            # NOTAMS start with a paranthesis
            if not rec.startswith("("):
                continue
            try:
                decoded = notam.Notam.from_str(rec)
            except Exception as e:
                print(f"Failed to decode NOTAM record: {e}")
                failure_count += 1
                continue
            success_count += 1

            geometry: Optional[dict[str, Any]] = build_geometry(
                decoded, airport_locations, MAX_CIRCLE_RADIUS_NM
            )

            traffic = getattr(decoded, "traffic_type", None)
            purpose = getattr(decoded, "purpose", None)
            scope = getattr(decoded, "scope", None)
            locations_val = getattr(decoded, "location", None)
            all_props = {
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
            airport_name = None
            try:
                if decoded.location:
                    first_loc = decoded.location[0]
                    ap = airport_locations.get(first_loc)
                    if ap:
                        airport_name = ap.get("name")  # type: ignore[index]
            except Exception:  # pragma: no cover - defensive
                airport_name = None
            props = {
                "title": f"{decoded.notam_id} for {airport_name}",
                "text": f"From: {str(decoded.valid_from)}\nTo: {str(decoded.valid_till)}\n\n{expand_abbreviations(decoded.body) if decoded.body else ''}",
            }

            geojson["features"].append(
                {"type": "Feature", "geometry": geometry, "properties": props}
            )

        notam_class = file_path.split("/")[-1][0:1]
        out_path = f"{output}{notam_class}.geojson"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, indent=2, ensure_ascii=False)
        print(f"✅ Decoded NOTAMs saved to {out_path}")

    print(
        f"Summary: decoded {success_count} NOTAMs, {failure_count} failed (files processed: {len(html_files)})"
    )


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
    import sys, os

    if False:  # run if debug
        parse_notam_files(
            html_files=pathlib.Path("current").glob("*.html"),
            airports_csv="ru-airports.csv",
            output="docs/",
        )
        sys.exit()

    if len(sys.argv) == 1:
        main()
    else:
        parse_notam_files(
            html_files=sys.argv[1:],  # e.g. files under current/*.html
            airports_csv="ru-airports.csv",
            output="docs/",
        )
