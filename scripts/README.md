# Scripts Directory

This directory contains Python scripts and modules used for scraping, parsing, and processing NOTAM (Notice to Air Missions) data. The repository currently contains two distinct approaches to NOTAM parsing (production vs. experimental).

## Files Description

### 1. `scraper.py` (Production Pipeline)
**Role:** The main active script for the data acquisition pipeline.
**Usage:** `python scripts/scraper.py`
**Dependencies:** `requests`, `beautifulsoup4`, `pynotam` (external), `scripts.geo` (internal).
**Description:** 
- Scrapes NOTAM data from the official source (caica.ru).
- Parses HTML content to find NOTAM files.
- **Decoding:** Relies on the external `pynotam` library to parse the ICAO NOTAM format.
- **Geometry:** delegates geometric extraction to `geo.py`.
- **Enrichment:** Adds airport metadata from `ru-airports.csv`.
- **Output:** Generates one GeoJSON file per NOTAM series (e.g., `current/A.geojson`) in `docs/`.

### 2. `geo.py` (Production Geometry)
**Role:** Robust geometry extraction utility used by `scraper.py`.
**Usage:** Imported by `scraper.py`.
**Dependencies:** `shapely`, `pyproj`.
**Description:** 
- **Robustness:** Uses `shapely` and `pyproj` for accurate geometry construction and geodesic calculations.
- **Parsing:** Implements advanced regex-based parsing to extract geometric definitions from NOTAM item E) text.
- **Features:** 
  - Extracts polygons, circles, sectors, arcs, ellipses, and line corridors.
  - Handles complex multi-part geometries (e.g., "Area 1", "Area 2").
  - Uses Azimuthal Equidistant projections for accurate buffering (e.g. for corridor width).
  - Implements the `build_geometry(notam)` adapter to interface with the scraper.

### 3. `geojson_to_csv.py` (Utility)
**Role:** Data flattening tool.
**Usage:** `python scripts/geojson_to_csv.py <input.geojson>`
**Description:**
- Converts the hierarchical GeoJSON output from the scraper into flat CSV files.
- Useful for loading data into non-spatial tools or legacy systems.
- Computes rough centroids and bounding boxes for every feature during conversion.

## Pipeline Summary

1. **Scraping**: `scraper.py` fetches HTML.
2. **Parsing**: `scraper.py` uses `pynotam` to decode fields.
3. **Geometry**: `scraper.py` calls `geo.build_geometry()` to parse the text description in item E) and generate coordinates.
4. **Output**: GeoJSON files are saved to `docs/`.

*(Note: The legacy `notam_geo.py` has been merged into `geo.py`, making it the standard engine.)*
