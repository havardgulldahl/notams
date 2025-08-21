import requests
from bs4 import BeautifulSoup
import re
import json
from datetime import datetime


from typing import List, Dict, Any, Optional

BASE_URL: str = "https://www.caica.ru/ANI_Official/notam/notam_series/"
OUTPUT_FILE: str = "docs/notams.geojson"  # GitHub Pages serves from /docs

def fetch(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url)
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
                if f[1:10] == timestamp:
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
        timestamp = files[0][1:10]
        with open("current/.scrape_timestamp", "w", encoding="utf-8") as file:
           file.write(timestamp)
    except IndexError:
        print("No files found to extract timestamp.")
    print(f"Saved {saved} NOTAMs to current/")

if __name__ == "__main__":
    main()
