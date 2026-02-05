from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.waypoint_lookup as waypoint_lookup


def test_lookup_waypoint_parses_lagat_html(monkeypatch):
    html_path = Path(__file__).resolve().parent / "LAGAT.html"
    html = html_path.read_text(encoding="utf-8")

    def fake_get(url: str, timeout: int = 15):
        assert url == "https://opennav.com/waypoint/RU/LAGAT"
        assert timeout == 15
        return SimpleNamespace(text=html, raise_for_status=lambda: None)

    monkeypatch.setattr(waypoint_lookup.requests, "get", fake_get)

    data = waypoint_lookup.lookup_waypoint("LAGAT")

    assert data == {
        "identifier": "LAGAT",
        "name": "LAGAT",
        "description": "waypoint",
        "country": "RU",
        "latitude": "65.998488",
        "longitude": "38.484042",
        "url": "https://opennav.com/waypoint/RU/LAGAT",
        "image": "/images/opennav-icon-44.png",
    }


@pytest.mark.network
def test_lookup_waypoint_live_network():
    """Live network test for waypoint lookup."""
    try:
        data = waypoint_lookup.lookup_waypoint("LAGAT")
    except Exception as exc:
        pytest.skip(f"Network lookup failed: {exc}")

    assert data.get("identifier") == "LAGAT"
    assert "latitude" in data
    assert "longitude" in data
