"""Waypoint lookup helper."""

from __future__ import annotations

from typing import Dict

import re

import requests
from bs4 import BeautifulSoup


def lookup_waypoint(
    code: str, country: str = "RU", base_url: str = "https://opennav.com"
) -> Dict[str, str]:
    """Fetch waypoint page and return GeoCoordinates itemprop data as a dict."""
    if not code or not code.strip():
        raise ValueError("Waypoint code is required.")

    waypoint = code.strip().upper()
    url = f"{base_url.rstrip('/')}/waypoint/{country}/{waypoint}"

    response = requests.get(url, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    main_div = soup.find(
        attrs={"itemscope": True, "itemtype": "http://schema.org/GeoCoordinates"}
    )
    if main_div is None:
        raise ValueError("GeoCoordinates section not found.")

    data: Dict[str, str] = {}
    for tag in main_div.find_all(attrs={"itemprop": True}):
        itemprop = tag.get("itemprop")
        if not itemprop:
            continue
        content = tag.get("content") or tag.get("href") or tag.get_text(strip=True)
        if content is not None:
            data[itemprop] = content

    for label_cell in main_div.find_all("td", class_="datalabel"):
        if "Country" in label_cell.get_text(strip=True):
            country_value = label_cell.find_next_sibling("td")
            if country_value is not None:
                country_text = country_value.get_text(strip=True)
                if country_text:
                    data["country"] = country_text
            break

    if not data:
        raise ValueError("No itemprop data found in GeoCoordinates section.")

    return data


__all__ = ["lookup_waypoint"]
