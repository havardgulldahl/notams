#!/usr/bin/env python3
import argparse
import json
import sys
from typing import Optional

import notam


def _normalize_notam_text(text: str) -> str:
    """Normalize NOTAM text for parsing while preserving semantic content.

    Operations:
      - Strip UTF-8 BOM if present.
      - Normalize line endings to \n.
      - Remove leading/trailing blank lines.
      - Trim trailing whitespace on each line (keeps original indentation/inner spaces).
    We deliberately avoid collapsing internal multiple blank lines or internal spacing
    to keep clause/body indices meaningful.
    """
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    # Trim trailing whitespace per line
    lines = [ln.rstrip() for ln in lines]
    # Drop leading/trailing blank lines
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(lines)


def read_input(path: Optional[str]) -> str:
    if path and path != "-":
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            print(f"Error: could not read file '{path}': {e}", file=sys.stderr)
            sys.exit(2)
    # Read from stdin
    try:
        data = sys.stdin.read()
        if not data:
            print("Error: no input provided on stdin", file=sys.stderr)
            sys.exit(2)
        return data
    except Exception as e:
        print(f"Error: failed reading stdin: {e}", file=sys.stderr)
        sys.exit(2)


def notam_to_dict(n: notam.Notam) -> dict:
    # Convert key fields to a JSON-serializable dict
    def dt(x):
        # Render datetimes and EstimatedDateTime-ish objects as ISO strings if possible
        try:
            return x.isoformat()
        except Exception:
            return str(x) if x is not None else None

    return {
        "notam_id": n.notam_id,
        "notam_type": n.notam_type,
        "ref_notam_id": n.ref_notam_id,
        "fir": n.fir,
        "notam_code": n.notam_code,
        "traffic_type": (
            sorted(n.traffic_type)
            if isinstance(n.traffic_type, set)
            else n.traffic_type
        ),
        "purpose": sorted(n.purpose) if isinstance(n.purpose, set) else n.purpose,
        "scope": sorted(n.scope) if isinstance(n.scope, set) else n.scope,
        "fl_lower": n.fl_lower,
        "fl_upper": n.fl_upper,
        "area": n.area,
        "location": n.location,
        "valid_from": dt(n.valid_from),
        "valid_till": dt(n.valid_till),
        "schedule": n.schedule,
        "body": n.body,
        "limit_lower": n.limit_lower,
        "limit_upper": n.limit_upper,
        "decoded": n.decoded() if n.full_text else None,
    }


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pynotam",
        description="Parse ICAO NOTAM text and print decoded text or JSON.",
    )
    p.add_argument(
        "input",
        nargs="?",
        help="Path to a file with NOTAM text. Use '-' or omit to read from stdin.",
        default="-",
    )
    out = p.add_mutually_exclusive_group()
    out.add_argument(
        "--decoded",
        action="store_true",
        help="Print decoded NOTAM text (default).",
    )
    out.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON object with parsed fields and decoded text.",
    )
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress non-error messages.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    raw_text = read_input(args.input)
    text = _normalize_notam_text(raw_text)

    try:
        n = notam.Notam.from_str(text)
    except notam.NotamParseError as e:
        loc = f" (line {e.line}, col {e.column})" if e.line and e.column else ""
        snippet = f"\n>> {e.snippet}" if getattr(e, "snippet", None) else ""
        print(f"Parse error{loc}: {e}{snippet}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error while parsing NOTAM: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(notam_to_dict(n), ensure_ascii=False, indent=2))
        return 0

    # Default behavior: print decoded text
    try:
        decoded = n.decoded()
    except Exception as e:
        print(f"Error: failed to decode NOTAM abbreviations: {e}", file=sys.stderr)
        return 1

    print(decoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
