import timeutils
import _abbr
import re as _re
import _parser
from io import StringIO as _StringIO
from dataclasses import dataclass, field
from typing import Optional, Set, Dict, List, Tuple, Any


class NotamParseError(Exception):
    """Raised when a NOTAM cannot be parsed; wraps underlying parser error with context."""

    def __init__(
        self,
        message: str,
        *,
        line: Optional[int] = None,
        column: Optional[int] = None,
        snippet: Optional[str] = None,
        original: Optional[BaseException] = None,
    ):
        parts = [message]
        if line is not None and column is not None:
            parts.append(f"(line {line}, col {column})")
        if snippet:
            parts.append(f"snippet: {snippet!r}")
        super().__init__(" ".join(parts))
        self.line = line
        self.column = column
        self.snippet = snippet
        self.original = original


@dataclass
class Notam:
    # Core identification
    full_text: Optional[str] = None
    notam_id: Optional[str] = None
    notam_type: Optional[str] = None  # NEW / REPLACE / CANCEL
    ref_notam_id: Optional[str] = None

    # Q) clause derived
    fir: Optional[str] = None
    notam_code: Optional[str] = None
    traffic_type: Optional[Set[str]] = None
    purpose: Optional[Set[str]] = None
    scope: Optional[Set[str]] = None
    fl_lower: Optional[int] = None
    fl_upper: Optional[int] = None
    area: Optional[Dict[str, Any]] = None  # {'lat': str, 'long': str, 'radius': int}

    # A) clause
    location: Optional[List[str]] = None

    # B/C clauses
    valid_from: Optional[timeutils.datetime] = None
    valid_till: Optional[timeutils.datetime] = None  # or EstimatedDateTime / None

    # D-G clauses
    schedule: Optional[str] = None
    body: Optional[str] = None
    limit_lower: Optional[str] = None
    limit_upper: Optional[str] = None

    # Indices for sections inside full_text (start,end)
    indices_item_a: Optional[Tuple[int, int]] = None
    indices_item_b: Optional[Tuple[int, int]] = None
    indices_item_c: Optional[Tuple[int, int]] = None
    indices_item_d: Optional[Tuple[int, int]] = None
    indices_item_e: Optional[Tuple[int, int]] = None
    indices_item_f: Optional[Tuple[int, int]] = None
    indices_item_g: Optional[Tuple[int, int]] = None

    # Derived geometry (GeoJSON-like mapping) built from E) body text if recognizable.
    # None if no geometry could be parsed or an error occurred (backwards compatible).
    geometry: Optional[Dict[str, Any]] = None

    def decoded(self):
        """Returns the full text of the NOTAM, with ICAO abbreviations decoded into their un-abbreviated
        form where appropriate."""

        with _StringIO() as sb:
            if not self.full_text:
                return ""
            indices = [
                getattr(self, "indices_item_{}".format(i)) for i in ("d", "e", "f", "g")
            ]
            indices = [i for i in indices if i is not None]
            indices.sort()  # The items should already be listed in the order of their apperance in the text, but
            # we sort them here just in case
            indices = [(0, 0)] + indices + [(-1, -1)]

            for cur, nxt in zip(indices, indices[1:]):
                (cs, ce) = cur
                (ns, ne) = nxt
                sb.write(
                    self.decode_abbr(self.full_text[cs:ce])
                )  # decode the text of this range
                sb.write(
                    self.full_text[ce:ns]
                )  # copy the text from end of current range to start
                # of next verbatim
            return sb.getvalue()

    @staticmethod
    def from_str(s: str) -> "Notam":
        """Parse NOTAM text into a Notam instance.

        Raises NotamParseError with contextual line/column info on failure."""
        # Basic corruption check: require starting '(' and ending ')'
        if not (s.strip().startswith("(") and s.rstrip().endswith(")")):
            raise NotamParseError(
                "Corrupted NOTAM: missing opening or closing parenthesis",
                line=None,
                column=None,
                snippet=s.splitlines()[-1].strip()[:120] if s.strip() else None,
            )
        n = Notam()
        visitor = _parser.NotamParseVisitor(n)
        try:
            visitor.parse(s)
        except Exception as e:  # parsimonious ParseError / VisitationError etc.
            # Attempt to extract line/column from the error repr if available
            line = column = None
            snippet = None
            msg = str(e)
            if hasattr(e, "pos") and hasattr(e, "text"):
                # parsimonious ParseError
                text = getattr(e, "text")
                pos = getattr(e, "pos")
                # compute line/col
                prior = text[:pos]
                line = prior.count("\n") + 1
                col_start = prior.rfind("\n")
                column = pos + 1 if col_start == -1 else pos - col_start
                snippet_line = text.splitlines()[line - 1]
                snippet = snippet_line.strip()[:120]
            raise NotamParseError(
                "Failed to parse NOTAM",
                line=line,
                column=column,
                snippet=snippet,
                original=e,
            ) from e

        # Derive geometry (best-effort, swallow errors for backward compatibility)
        try:  # noqa: SIM105
            # Lazy imports so importing notam.py does not require shapely unless geometry accessed.
            from _geo import build_parts_from_E, parse_alt_text  # type: ignore
            from shapely.ops import unary_union  # type: ignore
            from shapely.geometry import GeometryCollection, mapping  # type: ignore

            body_text = n.body or ""
            # Re-parse altitude limits if present; fall back to SFC/UNL placeholders.
            f_alt = parse_alt_text(n.limit_lower or "SFC")
            g_alt = parse_alt_text(n.limit_upper or "UNL")
            parts = build_parts_from_E(body_text, f_alt, g_alt)
            if parts:
                geoms = [p.geom for p in parts]
                try:
                    merged = unary_union(geoms)
                except Exception:  # pragma: no cover - defensive
                    merged = GeometryCollection(geoms)
                n.geometry = mapping(merged)
        except Exception:  # pragma: no cover - never let geometry issues break parsing
            n.geometry = None
        return n

    @staticmethod
    def decode_abbr(txt):
        """Decodes ICAO abbreviations in 'txt' to their un-abbreviated form."""
        if not getattr(Notam.decode_abbr, "regex", False):
            Notam.decode_abbr.regex = _re.compile(
                r"\b("
                + "|".join([_re.escape(key) for key in _abbr.ICAO_abbr.keys()])
                + r")\b"
            )
        return Notam.decode_abbr.regex.sub(lambda m: _abbr.ICAO_abbr[m.group()], txt)
