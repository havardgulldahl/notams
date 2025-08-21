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

def main() -> None:
    print(f"Fetching NOTAM index page: {BASE_URL}")
    html: str = fetch(BASE_URL).text
    print("Parsing NOTAM file list...")
    files: List[str] = parse_html_list(html)
    print(f"Found {len(files)} NOTAM files.")

    for i, f in enumerate(files, 1):
        url: str = BASE_URL + f
        print(f"[{i}/{len(files)}] Downloading: {url}")
        # store notam in current/ directory
        with open(f"current/{f}", "w", encoding="utf-8") as file:
            file.write(fetch(url).text)

    print(f"Saved {len(files)} NOTAMs to current/")

if __name__ == "__main__":
    main()
