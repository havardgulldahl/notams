# NOTAM Geometry Parsing Documentation

This document describes the geometric parsing capabilities of the `scripts/geo.py` module.

## Overview

The geometry parsing module extracts geographic shapes from NOTAM text and converts them into GeoJSON-compatible formats. This enables mapping and visualization of airspace restrictions and other geographic NOTAM information.

## Supported Geometric Patterns

### 1. Circles

Circles are the most common geometric shape in NOTAMs, used to define cylindrical airspace restrictions.

**Supported Formats:**
```
CIRCLE RADIUS 5KM CENTRE 612800N0401500E
CIRCLE RADIUS 50KM CENTRE (620536N 1294624E)
CIRCLE RADIUS 700M CENTRE 575013N0282127E
CIRCLE RADIUS 5NM CENTRE 624300N0402926E
```

**Features:**
- Supports KM, NM (nautical miles), and M (meters) units
- Handles coordinates with spaces: `620536N 1294624E`
- Handles coordinates in parentheses: `(620536N1294624E)`
- Supports both "CENTRE" and "CENTRED AT" variations

**Output:** Polygon with 64 points approximating a circle

### 2. Coordinate Chain Polygons

Polygons defined by a sequence of coordinate points.

**Supported Formats:**
```
595835N0301229E-595811N0301228E-595809N0301307E-595835N0301229E
601818N0303722E-603000N0303756E-603006N0304300E-601818N0303722E
```

**Features:**
- Supports both DDMM and DDMMSS coordinate formats
- Automatically closes polygon if not already closed
- Multiple numbered areas (1. AREA: ... 2. AREA: ...) create MultiPolygon

**Output:** Polygon or MultiPolygon

### 3. Arc-Based Geometries

Arcs represent portions of circles, commonly used for TMA/CTR boundaries.

**Supported Formats:**
```
620506N1294106E THEN CLOCKWISE ALONG ARC RADIUS 30KM CENTRE (620536N1294624E) TO 614952N1295408E
471001N1431544E THEN CLOCKWISE BY ARC OF A CIRCLE RADIUS OF 70KM CENTRED AT (465318N1424300E) TO 472830N1422256E
560519N0374847E THEN ANTICLOCKWISE ALONG ARC RADIUS 28KM CENTRE (555200N0380000E) TO 554927N0382950E
```

**Features:**
- Supports both CLOCKWISE and ANTICLOCKWISE directions
- Handles various text patterns: "ALONG ARC", "BY ARC OF A CIRCLE"
- Supports "CENTRE" and "CENTRED AT"
- Parentheses and spaces in coordinates supported
- Multiple radius units: KM, NM, M

**Output:** Polygon approximating the arc with start point, arc segments, and return to center

### 4. Sectors (Wedges)

Sectors define pie-slice shaped areas, often used for approach/departure corridors.

**Supported Formats:**
```
SECTOR CENTRE 610424N0331023E AZM 321-144 DEG RADIUS 8KM
SECTOR BTN AZMAG 360-130 DEG FROM 543830N0393418E RADIUS 40KM
WI SECTOR CENTRE 595900N0300000E RADIUS 10KM  (fallback to circle)
```

**Features:**
- Azimuth ranges define the sector boundaries
- "AZMAG" (azimuth magnetic) variation supported
- "BTN" (between) and "FROM" keywords supported
- Falls back to circle if no azimuth specified
- Handles azimuth wrapping across 360°

**Output:** Polygon approximating the wedge shape

### 5. Ellipses

Ellipses are used for oriented airspace restrictions.

**Supported Formats:**
```
ELLIPSE CENTRE 584622N0304438E WITH AXES DIMENSIONS 4.0X2.0KM AZM OF MAJOR AXIS 045DEG
ELLIPSE CENTRE 584622N0304438E WITH AXES DIMENSIONS 2.8X1.3KM
```

**Features:**
- Optional azimuth defines major axis orientation
- Default orientation is North if azimuth not specified
- Supports KM, NM, M units

**Output:** Polygon approximating the ellipse with 72 points

### 6. Line Corridors

Corridors represent airspace along a path, common for route restrictions.

**Supported Formats:**
```
WI 1KM EITHER SIDE OF LINE JOINING POINTS: 600000N0321929E-601400N0334417E
WI 5KM EITHER SIDE OF LINE 595000N0300000E-600000N0310000E
```

**Features:**
- Width parameter preserved in properties
- Multiple waypoints supported

**Output:** LineString with corridor_width_km in properties

## Coordinate Format Support

The parser accepts various coordinate formats commonly found in NOTAMs:

### Latitude/Longitude Formats

1. **DDMMSS (Degrees, Minutes, Seconds)**
   - Example: `595835N0301229E`
   - Format: `DDMMSS[N/S]DDDMMSS[E/W]`

2. **DDMM (Degrees, Minutes)**
   - Example: `5535N03716E`
   - Format: `DDMM[N/S]DDDMM[E/W]`

3. **With Spaces**
   - Example: `620536N 1294624E`
   - Spaces between lat/lon components accepted

4. **With Parentheses**
   - Example: `(620536N1294624E)`
   - Parentheses around entire coordinate accepted

### Hemispheres

- North: `N`, South: `S`
- East: `E`, West: `W`

## Advanced Features

### Multiple Geometries

When a NOTAM contains multiple distinct areas, the parser creates appropriate multi-geometry types:

- Multiple polygons → `MultiPolygon`
- Multiple lines → `MultiLineString`
- Mixed types → `GeometryCollection`

### Fallback Mechanisms

The parser implements intelligent fallback:

1. Text-based pattern matching (primary)
2. NOTAM `area` attribute (if present)
3. Airport location lookup by ICAO code

### Metadata

Geometry objects include a `meta` field with shape information:

```json
{
  "type": "Polygon",
  "coordinates": [...],
  "meta": {
    "shape": "circle",
    "radius_nm": 10
  }
}
```

This metadata is useful for debugging and testing.

## Usage Examples

### Basic Usage

```python
from scripts.geo import build_geometry

# Parse NOTAM text
text = "CIRCLE RADIUS 5KM CENTRE 612800N0401500E"
geometry = build_geometry(text, {})

print(geometry['type'])  # 'Polygon'
print(len(geometry['coordinates'][0]))  # 65 points (64 + closing)
```

### With Airport Fallback

```python
airports = {
    "UUWW": {"name": "Vnukovo", "lat": 55.5915, "lon": 37.2615}
}

# NOTAM without explicit geometry
geometry = build_geometry(notam_without_coords, airports)
# Falls back to airport location
```

### Multiple Geometries

```python
text = """
1. AREA: 601818N0303722E-603000N0303756E-603006N0304300E-601818N0303722E
2. AREA: 600250N0303240E-600650N0303222E-600845N0303620E-600250N0303240E
"""
geometry = build_geometry(text, {})

print(geometry['type'])  # 'MultiPolygon'
print(len(geometry['coordinates']))  # 2 polygons
```

## Testing

The module includes comprehensive test coverage:

- `tests/test_geo.py` - Basic functionality tests
- `tests/test_geo_new_shapes.py` - Advanced shape tests
- `tests/test_scraper_geo.py` - Real-world NOTAM tests
- `tests/test_geo_edge_cases.py` - Edge case validation

Run tests with:
```bash
pytest tests/test_geo*.py -v
```

## Implementation Notes

### Coordinate Precision

The parser uses great-circle calculations for accurate distance and bearing computations on the Earth's surface. This is appropriate for the typical scales of NOTAMs (0.1 to 200 NM radius).

### Polygon Approximation

Curved shapes (circles, arcs, ellipses) are approximated as polygons with sufficient points to appear smooth when rendered:

- Circles: 64 points
- Ellipses: 72 points
- Arcs: 32 points
- Sectors: Variable based on angular span

### Performance Considerations

Pattern matching uses compiled regex patterns for efficiency. Multiple patterns are checked in a specific order to handle overlapping cases correctly.

## Future Enhancements

Potential improvements for consideration:

1. Support for polygons with arc segments (mixed straight/curved boundaries)
2. Buffer operations for line corridors (convert to polygon)
3. Validation of coordinate reasonableness
4. Support for additional coordinate reference systems
5. Integration with actual NOTAM decoding pipeline

## Contributing

When adding support for new geometric patterns:

1. Add test cases to `tests/test_geo_edge_cases.py`
2. Update this documentation
3. Ensure backward compatibility
4. Validate with real NOTAM examples
