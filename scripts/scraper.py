import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime


from typing import List, Dict, Any, Optional

BASE_URL: str = "https://www.caica.ru/ANI_Official/notam/notam_series/?lang=en"
OUTPUT_FILE: str = "docs/notams.geojson"  # GitHub Pages serves from /docs

def fetch(url: str) -> requests.Response:
    r = requests.get(url)
    r.raise_for_status()
    return r

def parse_html_list(html: str) -> List[str]:
    """Find NOTAM files listed on the page"""
    soup = BeautifulSoup(html, "html.parser")
    files: List[str] = []
    for link in soup.find_all("a"):
        href = link.get("href", "")
        if href.endswith(".htm") or href.endswith(".html"):
            files.append(href)
    return files

def parse_notam_html(url: str) -> Dict[str, Any]:
    """Download and extract NOTAM text from one HTML document."""
    r = fetch(url)
    # store file in history directory
    with open(f"history/{url.split('/')[-1]}", "w", encoding="utf-8") as file:
        file.write(r.text)
    soup = BeautifulSoup(r.text, "html.parser")
    text: str = soup.get_text(" ", strip=True)

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
    html: str = fetch(BASE_URL).text
    files: List[str] = parse_html_list(html)

    features: List[Dict[str, Any]] = []
    for f in files:
        url: str = f if f.startswith("http") else "https://www.caica.ru" + f
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
