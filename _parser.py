import parsimonious
import re
import timeutils

# Precompiled regex for Q) area segment (radius optional: some NOTAMs omit distance)
AREA_RE = re.compile(
    r"^(?P<lat>[0-9]{4}[NS])(?P<long>[0-9]{5}[EW])(?P<radius>[0-9]{0,3})$"
)


class ParseQClauseError(Exception):
    """Raised when a Q) clause cannot be parsed into expected parts."""

    def __init__(self, clause: str, message: str = "Malformed Q clause"):
        super().__init__(f"{message}: {clause}")
        self.clause = clause


grammar = parsimonious.Grammar(
    r"""
    # Use optional whitespace separators '_' (0+ spaces/newlines) instead of '__' (1+) because
    # some clause productions (e.g. C) with trailing _*) may already consume the separating
    # whitespace, leaving none for the root rule and causing a parse failure at the next clause.
    root = "(" _ header _ q_clause _ a_clause _ b_clause _ (c_clause _)? (d_clause _)? e_clause (_ f_clause _ g_clause)? ")"

    header = notamn_header / notamr_header / notamc_header
    notamn_header = notam_id _ "NOTAMN"
    notamr_header = notam_id _ "NOTAMR" _ notam_id
    notamc_header = notam_id _ "NOTAMC" _ notam_id
    # NOTAM id: Series letter + 4 digits + '/' + 2-digit year, optional part designator (letter + 2 digits), e.g. C3795/25A05
    notam_id = ~r"[A-Z][0-9]{4}/[0-9]{2}([A-Z][0-9]{2})?"

    q_clause = "Q)" _? fir "/" notam_code "/" traffic_type "/" purpose "/" scope "/" lower_limit "/" upper_limit ("/" area_of_effect)?
    fir = icao_id
    notam_code = ~r"Q[A-Z]{4}"
    # traffic_type may be empty (some NOTAMs omit it): allow zero or more of I,V,K
    traffic_type = ~r"[IVK]*"
    # purpose may also be empty (some NOTAMs show consecutive slashes): allow zero or more of N,B,O,M,K
    purpose = ~r"[NBOMK]*"
    # Allow any non-empty combination (with possible repeats) of A,E,W,K (some states repeat letters, e.g. EE)
    scope = ~r"(?=[AEWK]+)[AEWK]+"
    lower_limit = int3
    # Upper limit may be blank (represented by three spaces). Accept any 3 chars of digits or spaces.
    upper_limit = int3_blank
    area_of_effect = ~r"(?P<lat>[0-9]{4}[NS])(?P<long>[0-9]{5}[EW])(?P<radius>[0-9]{0,3})"

    a_clause = "A)" _ location_icao ((" " / "/") location_icao)*
    location_icao = icao_id

    b_clause = "B)" _ datetime
    c_clause = "C)" _ ((datetime _* estimated? _*) / permanent)
    estimated = "EST"
    permanent = "PERM"

    d_clause = "D)" _ till_next_clause
    e_clause = "E)" _ till_next_clause
    f_clause = "F)" _ till_next_clause
    g_clause = "G)" _ till_next_clause

    _ = (" " / "\n")*
    __ = (" " / "\n")+
    icao_id = ~r"[A-Z]{4}"
    datetime = int2 int2 int2 int2 int2 # year month day hours minutes
    int2 = ~r"[0-9]{2}"
    int3 = ~r"[0-9]{3}"
    int3_blank = ~r"[0-9 ]{3}"
    # Stop lazily at either the final closing parenthesis of the NOTAM or a space/newline
    # followed by a valid next clause label (A-G). Avoid matching generic capital letters
    # inside the body text (e.g. '(6100 M).)') which previously caused premature termination.
    till_next_clause = ~r".*?(?=(?:\)$)|(?:\s[A-G]\)))"s
"""
)


class NotamParseVisitor(parsimonious.NodeVisitor):
    def __init__(self, tgt=None):
        """tgt must be an instance of an object with a __dict__ attribute. All data attributes
        resulting from the parsing of the NOTAM will be assigned to that object."""
        self.tgt = self if tgt is None else tgt
        super().__init__()

    grammar = grammar

    @staticmethod
    def has_descendant(node, descnd_name):
        if node.expr_name == descnd_name:
            return True
        else:
            return any(
                [
                    NotamParseVisitor.has_descendant(c, descnd_name)
                    for c in node.children
                ]
            )

    def visit_simple_regex(self, node, _):
        return node.match.group(0)

    visit_till_next_clause = visit_simple_regex

    def visit_code_node(self, *args, meanings):
        """Maps coded strings, where each character encodes a special meaning, into a corresponding decoded set
        according to the meanings dictionary (see examples of usage further below)"""
        codes = self.visit_simple_regex(*args)
        if not codes:
            return set()
        return {meanings[code] for code in codes}

    def visit_intX(self, *args):
        v = self.visit_simple_regex(*args)
        return int(v)

    visit_int2 = visit_intX
    visit_int3 = visit_intX

    @staticmethod
    def visit_notamX_header(notam_type):
        def inner(self, _, visited_children):
            self.tgt.notam_id = visited_children[0]
            self.tgt.notam_type = notam_type
            if self.tgt.notam_type in ("REPLACE", "CANCEL"):
                self.tgt.ref_notam_id = visited_children[-1]

        return inner

    visit_notamn_header = visit_notamX_header("NEW")
    visit_notamr_header = visit_notamX_header("REPLACE")
    visit_notamc_header = visit_notamX_header("CANCEL")

    visit_icao_id = visit_simple_regex
    visit_notam_id = visit_simple_regex
    visit_notam_code = visit_simple_regex

    def visit_q_clause(self, node, visited_children):
        """Robust extraction of Q) clause fields allowing optional empties and area."""
        text = node.text
        q_body = text[2:].lstrip() if text.startswith("Q)") else text
        parts = q_body.split("/")
        if len(parts) < 7:
            raise ParseQClauseError(text, "Too few parts")
        fir, notam_code, traffic, purpose, scope, lower, upper, *rest = parts
        self.tgt.fir = fir[:4]
        self.tgt.fl_lower = int(lower) if lower.isdigit() else None
        self.tgt.fl_upper = int(upper) if upper.isdigit() else None
        area = rest[0] if rest else None
        if area:
            m = AREA_RE.match(area)
            if m:
                gd = m.groupdict()
                radius = gd.get("radius") or ""
                if radius:
                    self.tgt.area = {
                        "lat": gd["lat"],
                        "long": gd["long"],
                        "radius": int(radius),
                    }
                elif not hasattr(self.tgt, "area"):
                    self.tgt.area = None
            elif not hasattr(self.tgt, "area"):
                self.tgt.area = None
        elif not hasattr(self.tgt, "area"):
            self.tgt.area = None

    def visit_notam_code(self, *args):
        self.tgt.notam_code = self.visit_simple_regex(
            *args
        )  # TODO: Parse this into the code's meaning. One day...

    def visit_traffic_type(self, *args):
        self.tgt.traffic_type = self.visit_code_node(
            *args, meanings={"I": "IFR", "V": "VFR", "K": "CHECKLIST"}
        )

    def visit_purpose(self, *args):
        self.tgt.purpose = self.visit_code_node(
            *args,
            meanings={
                "N": "IMMEDIATE ATTENTION",
                "B": "OPERATIONAL SIGNIFICANCE",
                "O": "FLIGHT OPERATIONS",
                "M": "MISC",
                "K": "CHECKLIST",
            },
        )

    def visit_scope(self, *args):
        self.tgt.scope = self.visit_code_node(
            *args,
            meanings={
                "A": "AERODROME",
                "E": "EN-ROUTE",
                "W": "NAV WARNING",
                "K": "CHECKLIST",
            },
        )

    def visit_area_of_effect(self, node, _):
        gd = node.match.groupdict()
        radius = gd.get("radius") or ""
        if radius:
            self.tgt.area = {
                "lat": gd["lat"],
                "long": gd["long"],
                "radius": int(radius),
            }
        else:
            if not hasattr(self.tgt, "area"):
                self.tgt.area = None

    def visit_a_clause(self, node, _):
        def _dfs_icao_id(n):
            if n.expr_name == "icao_id":
                return [self.visit_simple_regex(n, [])]
            return sum(
                [_dfs_icao_id(c) for c in n.children], []
            )  # flatten list-of-lists

        start = node.children[2].start
        end = node.children[-1].end
        self.tgt.location = _dfs_icao_id(node)
        self.tgt.indices_item_a = (start, end)

    def visit_b_clause(self, node, visited_children):
        self.tgt.valid_from = visited_children[2]
        content_child = node.children[2]
        self.tgt.indices_item_b = (content_child.start, content_child.end)

    def visit_c_clause(self, node, visited_children):
        if self.has_descendant(node, "permanent"):
            dt = timeutils.datetime.max
        else:
            dt = visited_children[2][0][0]
            if self.has_descendant(node, "estimated"):
                dt = timeutils.EstimatedDateTime(dt)
        self.tgt.valid_till = dt
        content_child = node.children[2]
        self.tgt.indices_item_c = (content_child.start, content_child.end)

    def visit_d_clause(self, node, visited_children):
        self.tgt.schedule = visited_children[2]
        content_child = node.children[2]
        self.tgt.indices_item_d = (content_child.start, content_child.end)

    def visit_e_clause(self, node, visited_children):
        self.tgt.body = visited_children[2]
        content_child = node.children[2]
        self.tgt.indices_item_e = (content_child.start, content_child.end)

    def visit_f_clause(self, node, visited_children):
        self.tgt.limit_lower = visited_children[2]
        content_child = node.children[2]
        self.tgt.indices_item_f = (content_child.start, content_child.end)

    def visit_g_clause(self, node, visited_children):
        self.tgt.limit_upper = visited_children[2]
        content_child = node.children[2]
        self.tgt.indices_item_g = (content_child.start, content_child.end)

    def visit_datetime(self, _, visited_children):
        dparts = visited_children
        dparts[0] = (
            1900 + dparts[0] if dparts[0] > 80 else 2000 + dparts[0]
        )  # interpret 2-digit year
        return timeutils.datetime(*dparts, tzinfo=timeutils.timezone.utc)

    def generic_visit(self, _, visited_children):
        return visited_children

    def visit_root(self, node, _):
        self.tgt.full_text = node.full_text
        # If C) clause missing, provide a default valid_till (None to indicate open ended)
        if not hasattr(self.tgt, "valid_till"):
            self.tgt.valid_till = None
