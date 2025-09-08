#!/usr/bin/env python3
"""Convert a GeoJSON file to CSV.

Features:
- Auto-discovers property keys across all features unless --fields specified.
- Flattens nested property dicts using dot notation (e.g., owner.name).
- Handles list property values by joining with ';'.
- Adds geometry_type column.
- Adds centroid_lon / centroid_lat (rough geometric centroid for Point/LineString/Polygon/MultiPolygon).
- Adds bbox_* columns (min/max lon/lat).

Usage:
    python geojson_to_csv.py input.geojson [-o output.csv] [--fields key1,key2,...]

Pure standard library; no external dependencies.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

Feature = Dict[str, Any]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert GeoJSON to CSV")
    p.add_argument("input", help="Path to input GeoJSON file")
    p.add_argument(
        "-o", "--output", help="Path to output CSV (default: input with .csv)"
    )
    p.add_argument(
        "--fields",
        help=(
            "Comma separated list of property keys to include. "
            "If omitted, all discovered top-level property keys are exported."
        ),
    )
    p.add_argument(
        "--progress",
        type=int,
        default=0,
        help="Print a progress line every N features (0 = disable)",
    )
    p.add_argument(
        "--stats-only",
        action="store_true",
        help="Compute and print statistics without writing CSV (ignores --output)",
    )
    p.add_argument(
        "--top-keys",
        type=int,
        default=0,
        help="Show top N most frequent property keys (0 = all suppressed)",
    )
    return p.parse_args()


def load_geojson(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_features(obj: Dict[str, Any]) -> Iterable[Feature]:
    if obj.get("type") == "FeatureCollection":
        for feat in obj.get("features", []) or []:
            if isinstance(feat, dict) and feat.get("type") == "Feature":
                yield feat
    elif obj.get("type") == "Feature":
        yield obj
    else:  # maybe a bare geometry
        yield {"type": "Feature", "properties": {}, "geometry": obj}


def flatten_props(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            flat.update(flatten_props(v, key))
        else:
            if isinstance(v, (list, tuple, set)):
                v = ";".join(map(str, v))
            flat[key] = v
    return flat


def collect_property_keys(features: Sequence[Feature]) -> List[str]:
    keys: set[str] = set()
    for feat in features:
        props = feat.get("properties") or {}
        if isinstance(props, dict):
            flat = flatten_props(props)
            keys.update(flat.keys())
    return sorted(keys)


def geom_points(geometry: Dict[str, Any]) -> List[List[float]]:
    t = geometry.get("type")
    coords = geometry.get("coordinates")
    pts: List[List[float]] = []
    if t == "Point" and isinstance(coords, (list, tuple)):
        pts.append(list(coords))
    elif t == "MultiPoint":
        pts.extend(coords or [])
    elif t == "LineString":
        pts.extend(coords or [])
    elif t == "MultiLineString":
        for line in coords or []:
            pts.extend(line)
    elif t == "Polygon":
        # exterior ring is first
        if coords:
            pts.extend(coords[0])
    elif t == "MultiPolygon":
        for poly in coords or []:
            if poly:
                pts.extend(poly[0])
    return [p for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]


def centroid(points: List[List[float]]) -> tuple[float | None, float | None]:
    if not points:
        return None, None
    # For polygons we can attempt area-weighted centroid for better accuracy
    # Detect polygon ring (first==last) heuristic
    if len(points) >= 4 and points[0] == points[-1]:
        # Polygon centroid (simple planar, WGS84 distortion ignored)
        area_acc = 0.0
        cx_acc = 0.0
        cy_acc = 0.0
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            cross = x1 * y2 - x2 * y1
            area_acc += cross
            cx_acc += (x1 + x2) * cross
            cy_acc += (y1 + y2) * cross
        if area_acc != 0:
            area = area_acc / 2.0
            cx = cx_acc / (6.0 * area)
            cy = cy_acc / (6.0 * area)
            return cx, cy
    # Fallback: arithmetic mean
    sx = sy = 0.0
    for x, y in points:
        sx += x
        sy += y
    n = len(points)
    return sx / n, sy / n


def bbox(
    points: List[List[float]],
) -> tuple[float | None, float | None, float | None, float | None]:
    if not points:
        return None, None, None, None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"Input file not found: {in_path}")
    out_path = Path(args.output) if args.output else in_path.with_suffix(".csv")

    data = load_geojson(in_path)
    feats = list(iter_features(data))
    if not feats:
        raise SystemExit("No features found in GeoJSON")

    if args.fields:
        field_order = [f.strip() for f in args.fields.split(",") if f.strip()]
    else:
        field_order = collect_property_keys(feats)

    # Always append geometry summary columns
    geometry_columns = [
        "geometry_type",
        "centroid_lon",
        "centroid_lat",
        "bbox_min_lon",
        "bbox_min_lat",
        "bbox_max_lon",
        "bbox_max_lat",
    ]
    header = field_order + geometry_columns

    geom_type_counts: dict[str, int] = {}
    key_frequency: dict[str, int] = {}
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    start = time.time()

    def update_extent(lon: float | None, lat: float | None) -> None:
        nonlocal min_lon, min_lat, max_lon, max_lat
        if lon is None or lat is None:
            return
        if lon < min_lon:
            min_lon = lon
        if lon > max_lon:
            max_lon = lon
        if lat < min_lat:
            min_lat = lat
        if lat > max_lat:
            max_lat = lat

    writer: csv.DictWriter | None = None
    f_handle = None
    if not args.stats_only:
        f_handle = out_path.open("w", encoding="utf-8", newline="")
        writer = csv.DictWriter(f_handle, fieldnames=header)
        writer.writeheader()

    for idx, feat in enumerate(feats, 1):
        props = feat.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        flat = flatten_props(props)
        for k in flat:
            key_frequency[k] = key_frequency.get(k, 0) + 1
        row = {k: flat.get(k, "") for k in field_order}
        geom = feat.get("geometry") or {}
        gtype = geom.get("type") if isinstance(geom, dict) else None
        geom_type_counts[gtype or "(none)"] = (
            geom_type_counts.get(gtype or "(none)", 0) + 1
        )
        pts = geom_points(geom) if isinstance(geom, dict) else []
        cx, cy = centroid(pts)
        update_extent(cx, cy)
        bminx, bminy, bmaxx, bmaxy = bbox(pts)
        row.update(
            {
                "geometry_type": gtype or "",
                "centroid_lon": cx if cx is not None else "",
                "centroid_lat": cy if cy is not None else "",
                "bbox_min_lon": bminx if bminx is not None else "",
                "bbox_min_lat": bminy if bminy is not None else "",
                "bbox_max_lon": bmaxx if bmaxx is not None else "",
                "bbox_max_lat": bmaxy if bmaxy is not None else "",
            }
        )
        if writer:
            writer.writerow(row)
        if args.progress and idx % args.progress == 0:
            elapsed = time.time() - start
            print(
                f".. {idx}/{len(feats)} ({idx/len(feats)*100:5.1f}%) in {elapsed:0.1f}s"
            )

    if f_handle:
        f_handle.close()
        print(f"Wrote {len(feats)} features to {out_path}")

    elapsed_total = time.time() - start
    print("--- Statistics ---")
    print(f"Total features: {len(feats)}")
    print(
        f"Geometry types: "
        + ", ".join(f"{k}={v}" for k, v in sorted(geom_type_counts.items()))
    )
    if min_lon != float("inf"):
        print(
            f"Centroid extent: lon [{min_lon:.4f}, {max_lon:.4f}] lat [{min_lat:.4f}, {max_lat:.4f}]"
        )
    print(f"Unique property keys (flattened): {len(key_frequency)}")
    if args.top_keys:
        top = sorted(key_frequency.items(), key=lambda x: (-x[1], x[0]))[
            : args.top_keys
        ]
        for k, c in top:
            print(f"  {k}: {c}")
    print(f"Elapsed: {elapsed_total:0.2f}s")


if __name__ == "__main__":  # pragma: no cover
    main()
