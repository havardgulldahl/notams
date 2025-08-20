import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime

BASE_URL = "https://www.caica.ru/ANI_Official/notam/notam_series/?lang=en"
OUTPUT_FILE = "docs/notams.geojson"  # GitHub Pages serves from /docs

def fetch_page():
    r = requests.get(BASE_URL)
    r.raise_for_status()
    return r.text

def parse_html_list(html):
    """Find NOTAM files listed on the page"""
    soup = BeautifulSoup(html, "html.parser")
    files = []
    for link in soup.find_all("a"):
        href = link.get("href", "")
        if href.endswith(".htm") or href.endswith(".html"):
            files.append(href)
    return files

def parse_notam_html(url):
    """Download and extract NOTAM text from one HTML document."""
    r = requests.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    # try to extract coordinates (pattern like N5543.0 E03736.0)
    coords = re.findall(r"N\d{2,4}\.\d{1,2}\s*E\d{2,4}\.\d{1,2}", text)
    lon, lat = None, None
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

def main():
    html = fetch_page()
    files = parse_html_list(html)

    features = []
    for f in files:
        url = f if f.startswith("http") else "https://www.caica.ru" + f
        try:
            data = parse_notam_html(url)
            if data["lat"] and data["lon"]:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [data["lon"], data["lat"]]},
                    "properties": {"url": data["url"], "text": data["text"]}
                })
        except Exception as e:
            print("Failed:", url, e)

    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"updated": datetime.utcnow().isoformat() + "Z"}
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(features)} NOTAMs to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
