import pathlib
import re
import json
import csv
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse
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
RUN_HISTORY_PATH = pathlib.Path("docs/run_history.json")
RUN_HISTORY_LIMIT = 90
ESCALATION_THRESHOLD_DAYS = 3
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
    return [entry["filename"] for entry in parse_html_entries(html)]


def parse_html_entries(html: str) -> List[dict[str, str]]:
    """Extract direct English NOTAM file URLs from the CAICA index page."""
    soup = BeautifulSoup(html, "html.parser")
    entries: List[dict[str, str]] = []
    seen_filenames: set[str] = set()
    rx = re.compile(r"(?P<filename>[A-Z]\d{10}_eng\.html)")

    for node in soup.find_all("td"):
        if isinstance(node, Tag):
            onclick_val = node.get("onclick")
            if isinstance(onclick_val, str):
                direct_url = extract_direct_notam_url(onclick_val)
                if direct_url:
                    filename = pathlib.PurePosixPath(urlparse(direct_url).path).name
                    if filename not in seen_filenames:
                        entries.append({"filename": filename, "url": direct_url})
                        seen_filenames.add(filename)
                    continue

                match = rx.search(onclick_val)
                if match:
                    filename = match.group("filename")
                    if filename not in seen_filenames:
                        entries.append(
                            {
                                "filename": filename,
                                "url": urljoin(BASE_URL, filename),
                            }
                        )
                        seen_filenames.add(filename)
    return entries


def extract_direct_notam_url(onclick_value: str) -> Optional[str]:
    """Extract the direct NOTAM URL from a CAICA click-counter onclick value."""
    if "uri=" not in onclick_value:
        return None

    match = re.search(r"location='([^']+)'", onclick_value)
    if not match:
        return None

    click_url = match.group(1)
    parsed = urlparse(click_url)
    uri_values = parse_qs(parsed.query).get("uri")
    if not uri_values:
        return None

    direct_url = re.sub(r"\s+", "", uri_values[0])
    if not direct_url.endswith("_eng.html"):
        return None
    if direct_url.startswith("//"):
        return f"https:{direct_url}"
    if direct_url.startswith("http://") or direct_url.startswith("https://"):
        return direct_url
    return f"https://{direct_url.lstrip('/')}"


def extract_notam_records(raw_text: str) -> List[str]:
    """Extract NOTAM records from raw page text.

    Records start with an ICAO-style NOTAM header like:
    (A1234/25 NOTAMN ...)
    This avoids splitting on blank lines that may appear inside E) bodies.
    """
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").translate({0xA0: 0x20})

    start_rx = re.compile(r"\([A-Z]\d{4}/\d{2}(?:[A-Z]\d{1,3})?\s+NOTAM[A-Z]?\b")
    starts = [m.start() for m in start_rx.finditer(text)]
    if not starts:
        separated = text.replace("\n\n(", "U7U7U7U7U7U7(")
        return [rec.strip() for rec in separated.split("U7U7U7U7U7U7") if rec.strip()]

    records: List[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        rec = text[start:end].strip()
        if rec:
            records.append(rec)
    return records


def normalize_record_text(record: str) -> str:
    """Normalize a single NOTAM record to improve parser tolerance."""
    text = record.replace("\r\n", "\n").replace("\r", "\n").translate({0xA0: 0x20})
    # Drop ICAO section headers like "USTV:" that appear between records
    text = re.sub(r"(?m)^[A-Z]{3,5}:\s*$\n?", "", text)
    # Fix broken field labels like "D\n)" or "E\n)" produced by HTML line breaks
    text = re.sub(r"([A-Z])\s*\n\s*\)", r"\1)", text)
    # Fix malformed Q) lines that contain a double slash in the field sequence
    text = re.sub(r"(?m)^Q\)([^\n]*)//([^\n]*)$", r"Q)\1/\2", text)
    # Remove blank lines that appear before a field label like "\n\nA)"
    text = re.sub(r"\n\s*\n(?=[A-Z]\))", "\n", text)
    # Trim trailing whitespace on each line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


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


def write_scrape_timestamp(timestamp: str) -> None:
    """Persist the latest source timestamp for display and diagnostics."""
    with open("current/.scrape_timestamp", "w", encoding="utf-8") as file:
        file.write(timestamp)

    with open("docs/scrape_timestamp", "w", encoding="utf-8") as file:
        file.write(
            f"20{timestamp[:2]}-{timestamp[2:4]}-{timestamp[4:6]} "
            f"{timestamp[6:8]}:{timestamp[8:10]}"
        )


def load_run_history(history_path: pathlib.Path) -> list[dict[str, Any]]:
    """Load persisted scraper run history from disk."""
    if not history_path.exists():
        return []

    try:
        with open(history_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []

    runs = payload.get("runs", [])
    if isinstance(runs, list):
        return [entry for entry in runs if isinstance(entry, dict)]
    return []


def count_consecutive_zero_days(runs: list[dict[str, Any]]) -> int:
    """Count consecutive zero-result days from the latest recorded run."""
    streak = 0
    for run in reversed(runs):
        if run.get("zero_result"):
            streak += 1
            continue
        break
    return streak


def persist_run_summary(
    summary: dict[str, Any],
    history_path: Optional[pathlib.Path] = None,
) -> dict[str, Any]:
    """Store the latest run summary and enrich it with streak metadata."""
    history_path = history_path or RUN_HISTORY_PATH
    history_path.parent.mkdir(parents=True, exist_ok=True)
    runs = load_run_history(history_path)

    if runs and runs[-1].get("run_date") == summary.get("run_date"):
        runs[-1] = summary
    else:
        runs.append(summary)

    runs = runs[-RUN_HISTORY_LIMIT:]
    streak = count_consecutive_zero_days(runs)
    runs[-1]["consecutive_zero_days"] = streak
    runs[-1]["escalate"] = streak >= ESCALATION_THRESHOLD_DAYS

    with open(history_path, "w", encoding="utf-8") as file:
        json.dump({"runs": runs}, file, indent=2)
        file.write("\n")

    return runs[-1]


def build_run_summary(
    *,
    status: str,
    files_found: int,
    files_downloaded: int,
    files_processed: int,
    decoded_count: int,
    decode_failures: int,
    expired_count: int,
    download_failures: int,
    scrape_timestamp: Optional[str],
    error: Optional[str] = None,
) -> dict[str, Any]:
    """Build a machine-readable summary for the current scraper run."""
    zero_statuses = {"no_index_files", "zero_active_notams"}
    return {
        "run_date": datetime.now(timezone.utc).date().isoformat(),
        "status": status,
        "zero_result": status in zero_statuses,
        "files_found": files_found,
        "files_downloaded": files_downloaded,
        "files_processed": files_processed,
        "decoded_count": decoded_count,
        "decode_failures": decode_failures,
        "expired_count": expired_count,
        "download_failures": download_failures,
        "scrape_timestamp": scrape_timestamp,
        "error": error,
    }


def parse_notam_files(
    html_files: list[str], airports_csv: str = "airports.csv", output: str = "."
) -> dict[str, int]:
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
    expired_count = 0
    processed_files = 0

    for file_path in html_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
        except FileNotFoundError:
            print(f"⚠ File not found: {file_path}")
            continue
        processed_files += 1

        # remove clutter (guard: title tag may be missing in minimal test HTML)
        title_tag = soup.find("title")
        if title_tag:
            title_tag.decompose()
        for tag in soup.find_all("font", {"color": "red"}):
            tag.decompose()

        raw_text = soup.get_text("\n")
        records = extract_notam_records(raw_text)

        geojson: dict[str, Any] = {"type": "FeatureCollection", "features": []}

        now_utc = datetime.now(timezone.utc)
        for rec in records:
            # NOTAMS start with a paranthesis
            rec = normalize_record_text(rec)
            if not rec.startswith("("):
                continue
            try:
                decoded = notam.Notam.from_str(rec)
            except Exception as e:
                print(f"Failed to decode NOTAM record: {e}")
                failure_count += 1
                continue

            valid_till = getattr(decoded, "valid_till", None)
            if isinstance(valid_till, datetime) and valid_till.tzinfo is None:
                valid_till = valid_till.replace(tzinfo=timezone.utc)

            if isinstance(valid_till, datetime) and valid_till < now_utc:
                notam_id = getattr(decoded, "notam_id", "<unknown>")
                print(
                    f"Skipping expired NOTAM {notam_id}: valid till {valid_till.isoformat()}"
                )
                expired_count += 1
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
        f"Summary: decoded {success_count} NOTAMs, {failure_count} failed "
        f"(files processed: {processed_files})"
    )
    return {
        "decoded_count": success_count,
        "decode_failures": failure_count,
        "expired_count": expired_count,
        "files_processed": processed_files,
    }


def main() -> int:
    print(f"Fetching NOTAM index page: {BASE_URL}")
    index_response = fetch(BASE_URL)
    if not index_response:
        print("Failed to fetch the NOTAM index page. Exiting.")
        summary = build_run_summary(
            status="index_fetch_failed",
            files_found=0,
            files_downloaded=0,
            files_processed=0,
            decoded_count=0,
            decode_failures=0,
            expired_count=0,
            download_failures=0,
            scrape_timestamp=None,
            error="Failed to fetch the NOTAM index page.",
        )
        persist_run_summary(summary)
        return 1
    html: str = index_response.text
    print("Parsing NOTAM file list...")
    entries = parse_html_entries(html)
    files: List[str] = [entry["filename"] for entry in entries]
    print(f"Found {len(files)} NOTAM files.")

    if not files:
        summary = build_run_summary(
            status="no_index_files",
            files_found=0,
            files_downloaded=0,
            files_processed=0,
            decoded_count=0,
            decode_failures=0,
            expired_count=0,
            download_failures=0,
            scrape_timestamp=None,
            error="No NOTAM files found in the index page.",
        )
        persisted = persist_run_summary(summary)
        print(f"Run summary saved to {RUN_HISTORY_PATH}")
        print(json.dumps(persisted, indent=2))
        return 1

    saved = 0
    download_failures = 0
    downloaded_files: list[str] = []
    for i, entry in enumerate(entries, 1):
        f = entry["filename"]
        url = entry["url"]
        print(f"[{i}/{len(files)}] Downloading: {url}")
        notam_response = fetch(url)
        if notam_response:
            # store notam in current/ directory
            current_path = pathlib.Path("current") / f
            with open(current_path, "w", encoding="utf-8") as file:
                file.write(notam_response.text)
            saved += 1
            downloaded_files.append(str(current_path))
        else:
            print(f"Skipping {url} due to download error.")
            download_failures += 1

    timestamp = files[0][1:11]
    write_scrape_timestamp(timestamp)

    print(f"Saved {saved} NOTAMs to current/")

    parse_result = parse_notam_files(
        html_files=downloaded_files,
        airports_csv="ru-airports.csv",
        output="docs/",
    )

    status = "success"
    error = None
    if parse_result["decoded_count"] == 0:
        status = "zero_active_notams"
        error = "No active NOTAM features were decoded from the fetched files."

    summary = build_run_summary(
        status=status,
        files_found=len(files),
        files_downloaded=saved,
        files_processed=parse_result["files_processed"],
        decoded_count=parse_result["decoded_count"],
        decode_failures=parse_result["decode_failures"],
        expired_count=parse_result["expired_count"],
        download_failures=download_failures,
        scrape_timestamp=timestamp,
        error=error,
    )
    persisted = persist_run_summary(summary)
    print(f"Run summary saved to {RUN_HISTORY_PATH}")
    print(json.dumps(persisted, indent=2))
    return 0 if status == "success" else 1


if __name__ == "__main__":
    import sys

    if False:  # run if debug
        parse_notam_files(
            html_files=pathlib.Path("current").glob("*.html"),
            airports_csv="ru-airports.csv",
            output="docs/",
        )
        sys.exit()

    if len(sys.argv) == 1:
        sys.exit(main())
    else:
        parse_notam_files(
            html_files=sys.argv[1:],  # e.g. files under current/*.html
            airports_csv="ru-airports.csv",
            output="docs/",
        )
