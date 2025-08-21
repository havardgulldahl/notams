import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime


from typing import List, Dict, Any, Optional

BASE_URL: str = "https://www.caica.ru/ANI_Official/notam/notam_series/"
OUTPUT_FILE: str = "docs/notams.geojson"  # GitHub Pages serves from /docs

def fetch(url: str) -> requests.Response:
    r = requests.get(url)
    r.raise_for_status()
    return r

def parse_html_list(html: str) -> List[str]:
    """Find NOTAM files listed on the page"""
    soup = BeautifulSoup(html, "html.parser")
    files: List[str] = []
    # loop through all <td> tags with title="File: ..."
    rx = re.compile(r"location='(.*)_eng.html'")
    for link in soup.find_all("td", onclick=rx, width=""):
        # extract filename from the attribute title="File: A2508210553_eng.html" 
        filename = link.get("title", "").strip()
        files.append(filename)
    return files

def parse_notam_html(url: str) -> Dict[str, Any]:
    """Download and extract NOTAM text from one HTML document."""
    r = fetch(url)
    # store file in history directory
    with open(f"history/{url.split('/')[-1]}", "w", encoding="utf-8") as file:
        file.write(r.text)
    soup = BeautifulSoup(r.text, "html.parser")
    text: str = soup.get_text(separator="\n", strip=True)

    # try to extract coordinates (pattern like N5543.0 E03736.0)
    coords: List[str] = re.findall(r"N\d{2,4}\.\d{1,2}\s*E\d{2,4}\.\d{1,2}", text)
    lon: Optional[float] = None
    lat: Optional[float] = None
    if coords:
        # crude conversion to decimal degrees
        match = re.match(r"N(\d{2,4}\.\d{1,2})\s*E(\d{2,4}\.\d{1,2})", coords[0])
        if match:
            lat = float(match.group(1)[:-3])  # simplification
            lon = float(match.group(2)[:-3])

    return {
        "url": url,
        "text": text[:500] + "...",  # short preview
        "lat": lat,
        "lon": lon
    }


def main() -> None:
    print(f"Fetching NOTAM index page: {BASE_URL}")
    html: str = fetch(BASE_URL).text
    print("Parsing NOTAM file list...")
    files: List[str] = parse_html_list(html)
    print(f"Found {len(files)} NOTAM files.")

    features: List[Dict[str, Any]] = []
    for i, f in enumerate(files, 1):
        url: str = BASE_URL + f
        print(f"[{i}/{len(files)}] Processing: {url}")
        try:
            data: Dict[str, Any] = parse_notam_html(url)
            if data["lat"] and data["lon"]:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [data["lon"], data["lat"]]},
                    "properties": {"url": data["url"], "text": data["text"]}
                })
        except Exception as e:
            print("Failed:", url, e)

    geojson: Dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"updated": datetime.utcnow().isoformat() + "Z"}
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(features)} NOTAMs to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
