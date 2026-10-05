"""
Deterministic PDF annotation evidence (Milestone J28).

WHAT THIS MODULE IS

A reader of the PDF's own text-showing and path operators that produces ANNOTATION
OCCURRENCES — "a designation-shaped text run was drawn at this point on this page".
Nothing else. It is the evidence layer a human review process can later stand on.

WHAT IT IS NOT

It is not a member reader. An annotation occurrence is NOT a physical steel member, and
nothing here converts one into the other:

  * two occurrences of the same designation at different coordinates are two
    occurrences, never "two members" and never "one member drawn twice";
  * a leader endpoint is where a drawn polyline stops, never a member's end;
  * a tag box is a drawn rectangle, never a member's outline;
  * the mark grammar is a SHAPE test, not an identity test — `S207` (a section callout),
    `X4` (a setout marker), `W01` (a window code), `D7` (a detail reference) and `SG8`
    (a timber grade) are drawn with exactly the same shape as `P4`. So the field is
    `mark_candidate`, and there is deliberately no `is_steel_member` anywhere in this
    module or in what it stores.

The only statement this layer makes about multiplicity is the one the PDF itself makes:
two text-showing operators with identical content, font and text matrix are one visible
annotation drawn twice (an overprint), recorded as `duplicate_operator_count` rather than
silently collapsed or silently duplicated.

SOURCE OF TRUTH

The PDF's bytes and nothing else. No AI call, no network call, no database read. The
extraction is a pure function of the file: run it twice and the canonical evidence is
byte-identical, because no timestamp, UUID, machine-specific path or other
non-deterministic value is admitted into it. The one hash it carries is the SHA-256 of the
source document, which is a property of the file and not of the machine.

THE PROVENANCE SPLIT

Everything produced here is one of two kinds, and the module keeps them apart:

  DETERMINISTIC PDF FACTS — the page number, the text-operator content, the device-space
  origin (Tm x CTM), the exact duplicate operator tuple, the reassembled raw text run, the
  exact vector geometry, the exact tag-box geometry. These are read off the content stream;
  they are not interpretations.

  HEURISTIC CLASSIFICATIONS — `schedule_row_candidate`, and the run-reassembly merge itself.
  Both depend on a threshold chosen from the measurements recorded in the constants block
  below. A heuristic result is never reported as though the PDF encoded it, and
  `schedule_row_candidate` is three-valued precisely so that "I could not tell" has
  somewhere to go: `None` means unknown, and a page with no detected schedule column yields
  `None` for every one of its occurrences rather than a fabricated `False`.

WIRING

None. Nothing in this module is called by the extraction pipeline, by the continuation or
retry paths, by the J23 capture, or by anything that writes `steel_members`. It builds
evidence; a later milestone decides what to do with it.
"""
from __future__ import annotations

import bisect
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pypdf
from pypdf.generic import ContentStream

# ===========================================================================
# The rule set, and the measurements it was chosen from
# ===========================================================================
# Bumping this name is how a changed reading is recorded as a changed reading. It is part of
# the stored identity of an occurrence, so a re-extraction under a new rule set is a new
# recorded reading rather than a silent rewrite of an old one — an append-only ledger cannot
# be edited in place, and a rule-set change must not pretend otherwise.
EXTRACTOR_VERSION = "j28.1"

# Same-line tolerance: two text-showing operators whose origins are within this many points
# vertically are on one line and may belong to one run.
# MEASURED on the real Arkles Strand PDF: a mark and its neighbours share a baseline
# exactly, and the tightest table row pitch in the document is 5.64 pt.
RUN_Y_TOLERANCE_PT = 0.75

# Horizontal advance below which two same-line, same-font operators are one run.
# HEURISTIC, and the proof measured BOTH sides of it on the real document:
#     a mark split across operators:  'P' -> '4'        advanced  3.4 pt
#     a mark and its section:         'P4' -> '300PFC'  advanced  6.0 pt
# 4.8 pt sits between them. The margin is 1.4 pt on either side and it is NOT universal — a
# PDF that put a mark and its section 4 pt apart would be mis-merged by this value. The
# threshold is a recorded rule, not a claim about the format.
RUN_X_GAP_PT = 4.8

# A table column's rows sit this close together, or closer. HEURISTIC, measured:
#     schedule columns       5.64 - 5.76 pt
#     plan tag columns     102.12 - 182.52 pt
#     one ambiguous column   11.40 pt (page 11, x=743.88 — reported not-a-schedule-column)
# The nearest genuine column is 5.76 and the nearest non-column 11.40, so the value sits in
# a 2x gap; it is still a chosen threshold and the classification it produces is reported as
# heuristic.
SCHEDULE_MEDIAN_PITCH_PT = 8.0

# Fewer than this many occurrences at one x is not treated as a column at all.
SCHEDULE_MIN_ROWS = 3

# How far from a mark's origin its tag rectangle's corner may sit. The mark origin often sits
# just outside the box it labels; measured reach on the real document is under 12 pt.
TAG_BOX_REACH_PT = 12.0
TAG_BOX_MAX_W_PT = 30.0
TAG_BOX_MAX_H_PT = 30.0

# Endpoint coincidence when growing a leader chain: a polyline drawn as separate paths that
# visually abut. Smaller than any real gap between distinct drawn features in the document.
LEADER_SNAP_PT = 0.5

# How far a leader chain is followed from its tag box, and how many segments it may contain.
# These bound the work so that tracing one callout cannot walk the whole sheet, and they are
# HEURISTIC bounds: on the real drawing set a leader is a short callout line, but a chain that
# reaches either bound is reported as TRUNCATED with no endpoint rather than having where the
# trace stopped passed off as a free end.
LEADER_MAX_REACH_PT = 150.0
LEADER_MAX_SEGMENTS = 200

# How near a located text origin another text run may be and still be reported as "nearby".
NEARBY_WINDOW_X_PT = 60.0
NEARBY_WINDOW_Y_PT = 40.0

# Coordinates are reported and stored at this many decimal places — the precision the proof
# established as exact for this document, and the precision the evidence table stores.
COORDINATE_PRECISION = 2

# A designation-shaped token, ALONE. This is a SHAPE test. It matches window codes, sheet
# numbers, detail and section callouts, setout markers and timber grades equally well; see
# the module docstring. It answers "could this be a mark", never "is this a member".
MARK_CANDIDATE_PATTERN = re.compile(r"^[A-Z]{1,3}\d{1,3}[A-Z]?$")

# The same shape, unanchored, for finding a token at the START of a longer run.
TOKEN_PREFIX_PATTERN = re.compile(r"^([A-Z]{1,3}\d{1,3}[A-Z]?)")

# A leading fragment a designation cannot be built from: letters immediately followed by
# digits, but shaped so the grammar cannot tokenise them — four or more letters, or more than
# three digits. Such a run is still emitted, as `mark_readable=False`, so that a designation
# the grammar could not tokenise is recorded as unreadable rather than dropped.
#
# The trailing `\d+` is what keeps this narrow. Without it the pattern is "any word of four or
# more capital letters", which emits every note heading, room name and title-block line on the
# sheet as an unreadable mark — 226 rows on 11 pages of the real set, none of them a mark. The
# runs it does catch are the genuine case it exists for: a treated-timber grade drawn as
# `CCAH3.2treated`, which is a designation-shaped fragment and not a tokenisable one.
MARK_FRAGMENT_PATTERN = re.compile(r"^(?:[A-Z]{1,3}\d{4,}|[A-Z]{4,}\d+)")

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# The rule set as data, so a stored occurrence can be read against the rules that produced it
# without this module being loaded.
RULE_SET: tuple[tuple[str, str], ...] = (
    ("run_y_tolerance_pt", str(RUN_Y_TOLERANCE_PT)),
    ("run_x_gap_pt", str(RUN_X_GAP_PT)),
    ("schedule_median_pitch_pt", str(SCHEDULE_MEDIAN_PITCH_PT)),
    ("schedule_min_rows", str(SCHEDULE_MIN_ROWS)),
    ("tag_box_reach_pt", str(TAG_BOX_REACH_PT)),
    ("tag_box_max_w_pt", str(TAG_BOX_MAX_W_PT)),
    ("tag_box_max_h_pt", str(TAG_BOX_MAX_H_PT)),
    ("leader_snap_pt", str(LEADER_SNAP_PT)),
    ("leader_max_reach_pt", str(LEADER_MAX_REACH_PT)),
    ("leader_max_segments", str(LEADER_MAX_SEGMENTS)),
    ("nearby_window_x_pt", str(NEARBY_WINDOW_X_PT)),
    ("nearby_window_y_pt", str(NEARBY_WINDOW_Y_PT)),
    ("coordinate_precision", str(COORDINATE_PRECISION)),
    ("mark_candidate_pattern", MARK_CANDIDATE_PATTERN.pattern),
    ("mark_fragment_pattern", MARK_FRAGMENT_PATTERN.pattern),
    ("token_prefix_pattern", TOKEN_PREFIX_PATTERN.pattern),
)


class AnnotationExtractionRefused(ValueError):
    """The PDF could not be read as annotation evidence at all."""


# ===========================================================================
# Deterministic facts: the operators
# ===========================================================================
@dataclass(frozen=True)
class TextOperator:
    """One text-showing operation, with its device-space origin.

    `text_op_index` is this operation's position in the page's own text-showing sequence,
    counted BEFORE any deduplication, so a kept operator's index is the index it had on the
    page and stays stable across runs. It is provenance, not an identity.

    `x` / `y` follow the PDF's own convention (origin bottom-left). `y_top` is the same point
    in the top-left convention an occurrence reports; both are carried so a reader never has
    to guess which one a number is in.
    """
    page_number: int
    text_op_index: int
    text: str
    x: float
    y: float
    y_top: float
    font_size: float
    font: str
    text_matrix: tuple[float, float, float, float, float, float]
    ctm: tuple[float, float, float, float, float, float]

    @property
    def identity(self) -> tuple[Any, ...]:
        """The exact operator tuple two operators must share to be one drawn annotation.

        The text matrix and CTM are compared as the PDF's own numbers. An overprint is the
        same operator emitted twice, so the values are equal, and rounding them would only
        risk joining two genuinely different placements.
        """
        return (self.font, self.font_size, self.text, self.text_matrix, self.ctm)


@dataclass(frozen=True)
class TextRun:
    """A maximal same-line, same-font group of kept operators.

    `operator_count` counts the DISTINCT text-showing operators the run was assembled from,
    after exact duplicates were collapsed. `duplicate_operator_count` counts the extra
    byte-identical copies that were collapsed before assembly, so
    `operator_count + duplicate_operator_count` is the number of raw operators the run
    represents and `duplicate_operator_count` is zero for a run nothing was drawn over twice.
    Both are properties of the operators, never of anything the characters might mean.
    """
    page_number: int
    text: str
    x: float
    y: float
    y_top: float
    font: str
    font_size: float
    text_matrix: tuple[float, float, float, float, float, float]
    ctm: tuple[float, float, float, float, float, float]
    first_text_op_index: int
    operator_count: int
    duplicate_operator_count: int


@dataclass(frozen=True)
class Segment:
    """A stroked straight segment in device space, PDF convention (origin bottom-left)."""
    a: tuple[float, float]
    b: tuple[float, float]
    width: float


@dataclass(frozen=True)
class TagBox:
    """A closed four-edge rectangle drawn around a mark. A drawn primitive, nothing more."""
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Leader:
    """A drawn polyline that terminates on a tag box's perimeter.

    `endpoint` is where the traced chain stops being connected. It is AN ANNOTATION FACT. It
    is not a member's end, it is not a member's location, and nothing may bind it to a grid,
    a level or a detail by proximity — the drawn line encodes no such binding.
    """
    endpoint: tuple[float, float] | None
    chain_nodes: int
    truncated: bool = False


@dataclass(frozen=True)
class NearbyText:
    """A text run near an occurrence, with the distance that makes it merely near.

    There is no `applies_to_*` field and there never may be: proximity is not applicability,
    and inferring what a nearby level, grid or detail applies to is exactly the binding the
    J28 proof rejected. Distances are between the two runs' origins.
    """
    text: str
    x: float
    y: float
    y_top: float
    dx: float
    dy: float
    distance_pt: float


@dataclass(frozen=True)
class AnnotationOccurrence:
    """One annotation occurrence — and nothing about a physical member.

    `annotation_x` / `annotation_y` are the device coordinates of the mark token's FIRST
    GLYPH ORIGIN: the origin of the first text-showing operator carrying a visible character
    of the token, transformed by Tm x CTM, reported in the top-left convention. This
    definition is fixed by the J28 proof and is not changed here. The one place it resolves
    no further is an operator whose own string opens with a space; there the operator origin
    is the PDF's own number for that string's first glyph and is reported as such.

    Nothing in this record says the annotation is a steel member, and nothing may be read
    into it that does: two occurrences are two annotations, never two members and never one.
    """
    page_number: int
    mark_candidate: str | None
    mark_readable: bool
    annotation_x: float
    annotation_y: float
    raw_text_run: str
    font: str
    font_size: float
    text_op_index: int
    operator_count: int
    duplicate_operator_count: int
    text_matrix: tuple[float, float, float, float, float, float]
    ctm: tuple[float, float, float, float, float, float]
    tag_box_present: bool
    tag_box_geometry: TagBox | None
    leader_present: bool | None
    leader: Leader | None
    schedule_row_candidate: bool | None
    nearby_text_runs: tuple[NearbyText, ...]


@dataclass(frozen=True)
class DocumentAnnotationEvidence:
    """Every annotation occurrence in one PDF, with the rules that produced it."""
    extractor_version: str
    source_pdf_sha256: str
    page_count: int
    pages_extracted: tuple[int, ...]
    pages_with_schedule_column: tuple[int, ...]
    include_geometry: bool
    rule_set: tuple[tuple[str, str], ...]
    occurrences: tuple[AnnotationOccurrence, ...]


# ===========================================================================
# Reading the operators
# ===========================================================================
def _matrix(value: Any) -> tuple[float, float, float, float, float, float]:
    try:
        return (float(value[0]), float(value[1]), float(value[2]),
                float(value[3]), float(value[4]), float(value[5]))
    except Exception:
        return _IDENTITY


def _multiply(m1: Sequence[float], m2: Sequence[float]) -> tuple[float, ...]:
    """3x3 matrix product over (a, b, c, d, e, f)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def text_operators(page: Any, page_number: int) -> tuple[TextOperator, ...]:
    """Every text-showing operation on a page, in the page's own sequence.

    The origin is Tm x CTM — the device-space position of the operation's first glyph, in PDF
    points with the origin at the page's bottom-left. That is the coordinate the J28 proof
    specified, and it is the PDF's own number, not a re-measurement of a rendering.
    """
    height = float(page.mediabox.height)
    found: list[TextOperator] = []

    def visitor(text: Any, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        if text is None or not str(text).strip():
            return
        cmv = _matrix(cm)
        tmv = _matrix(tm)
        device = _multiply(tmv, cmv)
        font = None
        try:
            font = font_dict.get("/BaseFont") if font_dict else None
        except Exception:
            font = None
        x = float(device[4])
        y = float(device[5])
        found.append(TextOperator(
            page_number=page_number,
            text_op_index=len(found),
            text=str(text),
            x=x,
            y=y,
            y_top=height - y,
            font_size=round(float(font_size or 0.0), 3),
            font=str(font),
            text_matrix=tmv,
            ctm=cmv,
        ))

    page.extract_text(visitor_text=visitor)
    return tuple(found)


def deduplicate_operators(
    operators: Sequence[TextOperator],
) -> tuple[tuple[TextOperator, ...], tuple[int, ...]]:
    """Collapse operators drawn on top of each other, counting them.

    THIS MUST RUN BEFORE RUN REASSEMBLY, and the J28 proof is why: two byte-identical `BF2`
    operators at the same point merge into a single run whose text reads `BF2BF2`, and the
    duplicate then hides inside the phantom token — the proof's first pass reported zero
    duplicates for exactly that reason. Deduplicating raw operators first keeps the duplicate
    visible as a count and keeps the merged text honest.

    The kept operator is the FIRST of its group, so its `text_op_index` is the index the
    annotation actually had on the page.
    """
    kept: list[TextOperator] = []
    counts: list[int] = []
    position_of: dict[tuple[Any, ...], int] = {}
    for operator in operators:
        position = position_of.get(operator.identity)
        if position is None:
            position_of[operator.identity] = len(kept)
            kept.append(operator)
            counts.append(1)
        else:
            counts[position] += 1
    return tuple(kept), tuple(counts)


def _run_of(group: Sequence[TextOperator], duplicates: Mapping[int, int]) -> TextRun:
    """Build a run, anchored on the first operator that carries a visible character.

    The anchor is the first operator with non-blank text, not simply the leftmost operator,
    because the occurrence's coordinate is defined as the mark token's first glyph origin. A
    run that opens with a whitespace-only operator would otherwise be anchored on the space.
    Where a single operator's own string begins with a space (`' BF2'` on the real set) the
    offset within that string is not recoverable from the operator alone without the font's
    width table, and the operator's own origin is reported — see REMAINING LIMITATIONS.
    """
    first = next((operator for operator in group if operator.text.strip()), group[0])
    return TextRun(
        page_number=first.page_number,
        text="".join(operator.text for operator in group),
        x=first.x,
        y=first.y,
        y_top=first.y_top,
        font=first.font,
        font_size=first.font_size,
        text_matrix=first.text_matrix,
        ctm=first.ctm,
        first_text_op_index=first.text_op_index,
        operator_count=len(group),
        # `duplicates` is optional, and an operator it says nothing about counts once —
        # which is the honest reading of "no duplicate information was supplied" and is
        # what makes `reassemble_runs(operators)` usable on its own.
        duplicate_operator_count=(
            sum(duplicates.get(operator.text_op_index, 1) for operator in group) - len(group)
        ),
    )


def reassemble_runs(
    operators: Sequence[TextOperator],
    duplicates: Mapping[int, int] | None = None,
) -> tuple[TextRun, ...]:
    """Group operators into runs: same line, same font, small horizontal advance.

    Line grouping is by the operator's own y; within a line, operators are walked left to
    right and joined while the advance from the previous operator stays under `RUN_X_GAP_PT`
    and the font is unchanged. A run's `duplicate_operator_count` counts the duplicate copies
    collapsed in its operators, so an overprint inside a merged run is not lost — and because
    a collapsed copy shares its kept operator's text matrix and CTM, it always falls in the
    same run, which is why the run total is a complete account.
    """
    duplicates = {} if duplicates is None else duplicates
    lines: list[list[TextOperator]] = []
    for operator in sorted(operators, key=lambda item: (-item.y, item.x)):
        for line in lines:
            if abs(line[0].y - operator.y) <= RUN_Y_TOLERANCE_PT:
                line.append(operator)
                break
        else:
            lines.append([operator])

    runs: list[TextRun] = []
    for line in lines:
        line.sort(key=lambda item: item.x)
        group: list[TextOperator] = [line[0]]
        for operator in line[1:]:
            previous = group[-1]
            if operator.x - previous.x <= RUN_X_GAP_PT and operator.font == previous.font:
                group.append(operator)
            else:
                runs.append(_run_of(group, duplicates))
                group = [operator]
        runs.append(_run_of(group, duplicates))
    return tuple(runs)


def classify_run(run: TextRun) -> tuple[str | None, bool] | None:
    """What this run is, as far as the PDF's own characters go.

    Returns `(mark_candidate, True)` when the run opens with a designation-shaped token,
    `(None, False)` when it opens with a fragment the grammar cannot tokenise, and `None`
    when the run is not a mark annotation at all — body text, a dimension, a note. This layer
    does not claim to have read those, and does not emit them.

    A token is returned as a CANDIDATE. Nothing here decides that it names a steel member.
    """
    text = run.text.strip()
    match = TOKEN_PREFIX_PATTERN.match(text)
    if match is not None and MARK_CANDIDATE_PATTERN.match(match.group(1)):
        return match.group(1), True
    if MARK_FRAGMENT_PATTERN.match(text):
        return None, False
    return None


# ===========================================================================
# Reading the geometry
# ===========================================================================
def _round_point(point: Sequence[float]) -> tuple[float, float]:
    return (round(float(point[0]), COORDINATE_PRECISION),
            round(float(point[1]), COORDINATE_PRECISION))


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def stroked_segments(page: Any) -> tuple[Segment, ...]:
    """Every stroked straight segment on a page, in device space.

    A rectangle (`re`) contributes its four edges; a stroked path contributes each of its
    straight spans. Curves are flattened to their endpoints, which is what the proof did and
    is recorded here as a limitation: a leader drawn as a curve yields its chord.
    """
    try:
        contents = page.get_contents()
    except Exception:
        return ()
    stream = ContentStream(contents, None)
    segments: list[Segment] = []
    ctm = _IDENTITY
    stack: list[tuple[tuple[float, ...], float]] = []
    line_width = 1.0
    current: list[tuple[float, float]] = []

    def device(x: float, y: float) -> tuple[float, float]:
        return _round_point((ctm[0] * x + ctm[2] * y + ctm[4],
                             ctm[1] * x + ctm[3] * y + ctm[5]))

    for operands, operator in stream.operations:
        try:
            if operator == b"q":
                stack.append((ctm, line_width))
            elif operator == b"Q":
                if stack:
                    ctm, line_width = stack.pop()
            elif operator == b"cm":
                ctm = _multiply(tuple(float(v) for v in operands), ctm)
            elif operator == b"w":
                line_width = float(operands[0])
            elif operator == b"m":
                current = [device(float(operands[0]), float(operands[1]))]
            elif operator == b"l":
                current.append(device(float(operands[0]), float(operands[1])))
            elif operator == b"c":
                current.append(device(float(operands[0]), float(operands[1])))
                current.append(device(float(operands[2]), float(operands[3])))
                current.append(device(float(operands[4]), float(operands[5])))
            elif operator in (b"v", b"y"):
                current.append(device(float(operands[0]), float(operands[1])))
                current.append(device(float(operands[2]), float(operands[3])))
            elif operator == b"re":
                x, y, width, height = (float(v) for v in operands[:4])
                corners = [device(x, y), device(x + width, y),
                           device(x + width, y + height), device(x, y + height)]
                for index in range(4):
                    segments.append(Segment(corners[index], corners[(index + 1) % 4],
                                            round(line_width, 3)))
                current = []
            elif operator in (b"S", b"s"):
                for index in range(len(current) - 1):
                    if current[index] != current[index + 1]:
                        segments.append(Segment(current[index], current[index + 1],
                                                round(line_width, 3)))
                current = []
            elif operator in (b"f", b"F", b"f*", b"b", b"B", b"b*", b"B*", b"n"):
                current = []
        except Exception:
            continue
    return tuple(segments)


# A uniform grid over the page, so that a per-mark query touches only the segments near the
# mark. Without it every occurrence would rescan the whole page: one page of the real Arkles
# Strand set carries 8,355 stroked segments, and a full rescan per occurrence is what makes an
# evidence layer unusable rather than merely slow. The index answers only "which drawn
# segments are near this point"; what a drawn segment means is decided by the callers.
_GEOMETRY_CELL_PT = 32.0


class PageGeometry:
    """A page's stroked segments, indexed once and reused by every occurrence on the page."""

    def __init__(self, segments: Sequence[Segment]):
        self.segments: tuple[Segment, ...] = tuple(segments)
        self.drawn: set[tuple[Any, Any]] = set()
        self.verticals: dict[float, list[tuple[float, float]]] = {}
        self.horizontals: dict[float, list[tuple[float, float]]] = {}
        corners: set[tuple[float, float]] = set()
        self._cells: dict[tuple[int, int], list[Segment]] = {}
        for segment in self.segments:
            self.drawn.add((segment.a, segment.b))
            self.drawn.add((segment.b, segment.a))
            corners.add(segment.a)
            corners.add(segment.b)
            (ax, ay), (bx, by) = segment.a, segment.b
            if ax == bx and ay != by:
                self.verticals.setdefault(ax, []).append((min(ay, by), max(ay, by)))
            if ay == by and ax != bx:
                self.horizontals.setdefault(ay, []).append((min(ax, bx), max(ax, bx)))
            for key in self._cells_of(segment):
                self._cells.setdefault(key, []).append(segment)
        self._vertical_x = sorted(self.verticals)
        self._horizontal_y = sorted(self.horizontals)
        self.corners: tuple[tuple[float, float], ...] = tuple(sorted(corners))
        self._corner_cells: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for corner in self.corners:
            self._corner_cells.setdefault(self._cell_of(corner), []).append(corner)

    @staticmethod
    def _cell_of(point: Sequence[float]) -> tuple[int, int]:
        return (int(point[0] // _GEOMETRY_CELL_PT), int(point[1] // _GEOMETRY_CELL_PT))

    def _cells_of(self, segment: Segment) -> list[tuple[int, int]]:
        cx0, cy0 = self._cell_of(segment.a)
        cx1, cy1 = self._cell_of(segment.b)
        return [(cx, cy) for cx in range(min(cx0, cx1), max(cx0, cx1) + 1)
                for cy in range(min(cy0, cy1), max(cy0, cy1) + 1)]

    def _nearby_cells(self, x: float, y: float, reach: float) -> list[tuple[int, int]]:
        cx, cy = self._cell_of((x, y))
        span = int(reach // _GEOMETRY_CELL_PT) + 1
        return [(gx, gy) for gx in range(cx - span, cx + span + 1)
                for gy in range(cy - span, cy + span + 1)]

    def segments_near(self, x: float, y: float, reach: float) -> tuple[Segment, ...]:
        found: set[Segment] = set()
        for key in self._nearby_cells(x, y, reach):
            found.update(self._cells.get(key, ()))
        return tuple(found)

    def corners_near(self, x: float, y: float, reach: float) -> tuple[tuple[float, float], ...]:
        found: set[tuple[float, float]] = set()
        for key in self._nearby_cells(x, y, reach):
            found.update(self._corner_cells.get(key, ()))
        return tuple(found)


def _spanning_positions(keys: Sequence[float], index: Mapping[float, list[tuple[float, float]]],
                        low: float, high: float, span: float,
                        limit: int = 6) -> list[float]:
    """Index keys inside [low, high] whose drawn span encloses `span`, nearest-first."""
    start = bisect.bisect_left(keys, low)
    end = bisect.bisect_right(keys, high)
    inside = [key for key in keys[start:end]
              if any(begin < span < finish for begin, finish in index[key])]
    inside.sort()
    return inside[:limit]


def _box_around(geometry: PageGeometry, x: float, y: float) -> TagBox | None:
    """The tightest closed four-edge rectangle of drawn segments containing a point.

    Every one of the four edges must exist as a drawn segment. A shape that merely looks
    boxed is not a box, and absence is reported as absence.
    """
    left = [key for key in _spanning_positions(
        geometry._vertical_x, geometry.verticals, x - TAG_BOX_MAX_W_PT, x, y) if key <= x]
    right = [key for key in _spanning_positions(
        geometry._vertical_x, geometry.verticals, x, x + TAG_BOX_MAX_W_PT, y) if key >= x]
    below = [key for key in _spanning_positions(
        geometry._horizontal_y, geometry.horizontals, y - TAG_BOX_MAX_H_PT, y, x) if key <= y]
    above = [key for key in _spanning_positions(
        geometry._horizontal_y, geometry.horizontals, y, y + TAG_BOX_MAX_H_PT, x) if key >= y]
    left = sorted(left, reverse=True)[:6]
    below = sorted(below, reverse=True)[:6]
    right = sorted(right)[:6]
    above = sorted(above)[:6]
    if not (left and right and below and above):
        return None
    best: tuple[float, TagBox] | None = None
    for x0 in left:
        for x1 in right:
            if x0 == x1:
                continue
            for y0 in below:
                for y1 in above:
                    if y0 == y1:
                        continue
                    quad = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                            ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
                    if not all(edge in geometry.drawn for edge in quad):
                        continue
                    area = (x1 - x0) * (y1 - y0)
                    if best is None or area < best[0]:
                        best = (area, TagBox(x0=x0, y0=y0, x1=x1, y1=y1))
    return None if best is None else best[1]


def tag_box_for_mark(geometry: PageGeometry, x: float, y: float) -> TagBox | None:
    """The tag box for a mark whose origin may sit just outside the rectangle it labels."""
    containing = _box_around(geometry, x, y)
    if containing is not None:
        return containing
    best: TagBox | None = None
    for corner in geometry.corners_near(x, y, TAG_BOX_REACH_PT):
        if _distance(corner, (x, y)) > TAG_BOX_REACH_PT:
            continue
        candidate = _box_around(geometry, corner[0] + 0.01, corner[1] + 0.01)
        if candidate is None:
            continue
        if best is None or (candidate.x1 - candidate.x0) * (candidate.y1 - candidate.y0) \
                < (best.x1 - best.x0) * (best.y1 - best.y0):
            best = candidate
    return best


def _box_edges(box: TagBox, segments: Sequence[Segment]) -> set[tuple[Any, Any]]:
    edges: set[tuple[Any, Any]] = set()
    for segment in segments:
        if all(abs(point[0] - box.x0) < 0.01 or abs(point[0] - box.x1) < 0.01
               for point in (segment.a, segment.b)) \
                and all(abs(point[1] - box.y0) < 0.01 or abs(point[1] - box.y1) < 0.01
                        for point in (segment.a, segment.b)):
            edges.add((segment.a, segment.b))
    return edges


def _on_perimeter(box: TagBox, point: Sequence[float]) -> bool:
    if not (box.x0 - RUN_Y_TOLERANCE_PT <= point[0] <= box.x1 + RUN_Y_TOLERANCE_PT
            and box.y0 - RUN_Y_TOLERANCE_PT <= point[1] <= box.y1 + RUN_Y_TOLERANCE_PT):
        return False
    inside_x = box.x0 + RUN_Y_TOLERANCE_PT < point[0] < box.x1 - RUN_Y_TOLERANCE_PT
    inside_y = box.y0 + RUN_Y_TOLERANCE_PT < point[1] < box.y1 - RUN_Y_TOLERANCE_PT
    return not (inside_x and inside_y)


def _snap_key(point: tuple[float, float]) -> tuple[int, int]:
    """The bucket a point falls in at the leader snap tolerance — how endpoints coalesce."""
    return (int(point[0] // LEADER_SNAP_PT), int(point[1] // LEADER_SNAP_PT))


def trace_leader(geometry: PageGeometry, box: TagBox) -> Leader | None:
    """Follow a drawn polyline from a tag box's perimeter to the end of its chain.

    Only a segment that TERMINATES ON THE BOX PERIMETER starts a trace; the box's own four
    edges are excluded. A stroke that merely passes near the mark does not qualify — the J28
    proof rejected that test outright, because a hatched or speckled detail puts dozens of
    unrelated strokes within a few points of any mark (84 within 8 pt of one `RB2`).

    The walk is LOCAL and BOUNDED: it grows only through segments that share an endpoint with
    the chain (within `LEADER_SNAP_PT`) and stops at `LEADER_MAX_REACH_PT` from the box or
    `LEADER_MAX_SEGMENTS` segments. Tracing the page-wide connected component instead would
    make one callout cost as much as the whole sheet — on the real drawings the page geometry
    is almost entirely one component, and the first implementation of this function spent
    seconds per mark for exactly that reason.

    Returns None when nothing is drawn attached to a found box, and `leader_present` is then
    False: the box was looked for, was found, and nothing leaves it. A chain that hit either
    bound comes back `truncated` with NO endpoint, because where the trace stopped is not the
    end of the line and must never be reported as if it were.
    """
    centre_x, centre_y = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0
    centre = (centre_x, centre_y)
    reach = max(box.x1 - box.x0, box.y1 - box.y0) / 2.0 + RUN_Y_TOLERANCE_PT
    nearby = geometry.segments_near(centre_x, centre_y, reach)
    edges = _box_edges(box, nearby)
    starts: list[Segment] = []
    for segment in nearby:
        if (segment.a, segment.b) in edges or (segment.b, segment.a) in edges:
            continue
        if _on_perimeter(box, segment.a) or _on_perimeter(box, segment.b):
            starts.append(segment)
    if not starts:
        return None

    used: set[Segment] = set()
    frontier: list[tuple[float, float]] = [point for start in starts for point in (start.a, start.b)]
    truncated = False
    while frontier:
        if len(used) >= LEADER_MAX_SEGMENTS:
            truncated = True
            break
        point = frontier.pop()
        for segment in geometry.segments_near(point[0], point[1], LEADER_SNAP_PT):
            if segment in used:
                continue
            if _distance(segment.a, point) <= LEADER_SNAP_PT:
                far_end = segment.b
            elif _distance(segment.b, point) <= LEADER_SNAP_PT:
                far_end = segment.a
            else:
                continue
            used.add(segment)
            if _distance(far_end, centre) <= LEADER_MAX_REACH_PT:
                frontier.append(far_end)
            else:
                truncated = True

    incident: dict[tuple[int, int], int] = {}
    for segment in used:
        for point in (segment.a, segment.b):
            incident[_snap_key(point)] = incident.get(_snap_key(point), 0) + 1
    leaves = [point for segment in used for point in (segment.a, segment.b)
              if incident[_snap_key(point)] == 1]
    if not leaves:
        return None
    furthest = max(leaves, key=lambda point: _distance(point, centre))
    return Leader(
        endpoint=None if truncated else _round_point(furthest),
        chain_nodes=len(used),
        truncated=truncated,
    )



# ===========================================================================
# Heuristic classification: schedule columns
# ===========================================================================
def schedule_columns(runs: Sequence[TextRun]) -> tuple[float, ...]:
    """The x positions on ONE page that look like a table's mark column.

    HEURISTIC. Same-x grouping plus a median row pitch at or under
    `SCHEDULE_MEDIAN_PITCH_PT`. MEDIAN, not uniformity, and the J28 proof is why: a schedule's
    mark column is interrupted by section headings (a 22.68 pt gap between the last roof beam
    and the first lintel), so a pitch-variance test rejects the very column it is looking for.

    A page may have MORE than one such column — pages 11 and 21 of the real document each
    have two, 123 pt apart — so this returns a tuple and no caller may assume one per page.
    """
    by_x: dict[float, list[TextRun]] = {}
    for run in runs:
        by_x.setdefault(round(run.x, COORDINATE_PRECISION), []).append(run)
    columns: list[float] = []
    for x, group in sorted(by_x.items()):
        if len(group) < SCHEDULE_MIN_ROWS:
            continue
        ordered = sorted(group, key=lambda run: -run.y)
        gaps = sorted(round(ordered[index].y - ordered[index + 1].y, COORDINATE_PRECISION)
                      for index in range(len(ordered) - 1))
        if not gaps:
            continue
        middle = len(gaps) // 2
        median = gaps[middle] if len(gaps) % 2 else (gaps[middle - 1] + gaps[middle]) / 2.0
        if 0.0 < median <= SCHEDULE_MEDIAN_PITCH_PT:
            columns.append(x)
    return tuple(columns)


# ===========================================================================
# Extraction
# ===========================================================================
def page_evidence(
    page: Any,
    page_number: int,
    *,
    include_geometry: bool = True,
) -> tuple[AnnotationOccurrence, ...]:
    """Every annotation occurrence on one page of an open PDF, deterministically.

    The order of operations is fixed and must not be reversed:

        1. read the raw text-showing operators
        2. deduplicate exact duplicate operator tuples, counting them
        3. reassemble runs from the KEPT operators
        4. derive the mark candidate and the coordinate
        5. derive the optional geometry
        6. derive the heuristic classification, separately from all of the above

    Steps 2 and 3 are in that order because merging first turns `BF2` + `BF2` into the
    phantom token `BF2BF2` and hides the duplicate inside it.
    """
    operators = text_operators(page, page_number)
    kept, counts = deduplicate_operators(operators)
    runs = reassemble_runs(kept, {operator.text_op_index: count
                                  for operator, count in zip(kept, counts)})

    classified: list[tuple[TextRun, str | None, bool]] = []
    for run in runs:
        result = classify_run(run)
        if result is not None:
            classified.append((run, result[0], result[1]))

    columns = schedule_columns([run for run, mark, _readable in classified if mark is not None])

    # The page's drawn geometry, indexed once and shared by every occurrence on it.
    geometry = PageGeometry(stroked_segments(page)) if include_geometry else None
    # Nearby text is drawn from the runs that are NOT annotation occurrences. An occurrence
    # near another occurrence would be an occurrence-to-occurrence link, and this layer makes
    # no statement about how two annotations relate.
    occurrence_texts = {id(run) for run, _mark, _readable in classified}
    others = [run for run in runs if id(run) not in occurrence_texts]

    occurrences: list[AnnotationOccurrence] = []
    for run, mark, readable in classified:
        if geometry is not None:
            box = tag_box_for_mark(geometry, run.x, run.y)
            if box is None:
                # No box was found, so the leader test has nothing to test against. The
                # proof's leader test is defined relative to the box, so this is UNKNOWN and
                # is reported as unknown rather than as False.
                leader, leader_present = None, None
            else:
                leader = trace_leader(geometry, box)
                leader_present = leader is not None
        else:
            box, leader, leader_present = None, None, None

        if not columns:
            schedule: bool | None = None
        else:
            schedule = round(run.x, COORDINATE_PRECISION) in columns

        nearby = tuple(sorted(
            (NearbyText(
                text=other.text,
                x=other.x,
                y=other.y,
                y_top=other.y_top,
                dx=round(other.x - run.x, COORDINATE_PRECISION),
                dy=round(other.y - run.y, COORDINATE_PRECISION),
                distance_pt=round(_distance((other.x, other.y), (run.x, run.y)),
                                  COORDINATE_PRECISION),
             )
             for other in others
             if abs(other.x - run.x) <= NEARBY_WINDOW_X_PT
             and abs(other.y - run.y) <= NEARBY_WINDOW_Y_PT),
            key=lambda item: (item.distance_pt, item.text, item.x, item.y),
        ))

        occurrences.append(AnnotationOccurrence(
            page_number=page_number,
            mark_candidate=mark,
            mark_readable=readable,
            annotation_x=round(run.x, COORDINATE_PRECISION),
            annotation_y=round(run.y_top, COORDINATE_PRECISION),
            raw_text_run=run.text,
            font=run.font,
            font_size=run.font_size,
            text_op_index=run.first_text_op_index,
            operator_count=run.operator_count,
            duplicate_operator_count=run.duplicate_operator_count,
            text_matrix=run.text_matrix,
            ctm=run.ctm,
            tag_box_present=box is not None,
            tag_box_geometry=box,
            leader_present=leader_present,
            leader=leader,
            schedule_row_candidate=schedule,
            nearby_text_runs=nearby,
        ))

    occurrences.sort(key=lambda item: (item.annotation_y, item.annotation_x, item.text_op_index))
    return tuple(occurrences)


from app.drawing_reading.source_document import source_document_sha256_of_bytes


def source_bytes(source: Any) -> bytes:
    """The document's own bytes, from a path or from bytes already in hand.

    The single place this module opens a source document. A path is read once here; bytes
    that are already in memory are returned as they are rather than re-read. An unreadable
    source is refused with the same code every other unreadable source is.
    """
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    try:
        with open(source, "rb") as handle:
            return handle.read()
    except OSError as error:
        raise AnnotationExtractionRefused(
            f"the source PDF could not be read ({error.__class__.__name__})."
        ) from error


def source_document_sha256(source: Any) -> str:
    """The SHA-256 of a source document's own bytes. The ONE hashing rule in this codebase.

    Milestone J28 computed this over the bytes the annotation reading was taken from, and it
    is recorded beside every occurrence as `source_pdf_sha256`. J61 uses the SAME rule, on
    the same bytes, to give a document its durable identity — so there is exactly one
    definition of "the hash of this document" and no second hashing implementation exists to
    disagree with this one.

    It is a property of the file and not of the machine: no timestamp, no path and no UUID
    enters it, and the same bytes produce the same digest wherever they are read.

    E2E-001N moved the ARITHMETIC to `app.drawing_reading.source_document`, because the
    ingestion path needed the same rule and taking it from here would have made ingestion an
    annotation-layer consumer — the boundary J28 guards. What remains here is not a second
    implementation: this function reads a source that may be a PATH through `source_bytes`
    (the layer that owns "this source could not be read") and then delegates the hashing to
    the one rule. The digest is identical for identical bytes.
    """
    return source_document_sha256_of_bytes(source_bytes(source))


def extract_annotations(
    pdf: Any,
    *,
    pages: Sequence[int] | None = None,
    include_geometry: bool = True,
) -> DocumentAnnotationEvidence:
    """Every annotation occurrence in a PDF. The document's bytes are the only input.

    `pages` selects 1-based pages; `None` means every page. `include_geometry` may be turned
    off to skip the path reading, in which case `tag_box_present` is False, `leader_present`
    is None and `tag_box_geometry` is None for every occurrence — absence of a reading, which
    is why the flag is carried with the evidence rather than left implicit.
    """
    payload = source_bytes(pdf)
    digest = source_document_sha256(payload)

    try:
        reader = pypdf.PdfReader(io.BytesIO(payload))
    except Exception as error:
        raise AnnotationExtractionRefused(
            f"the source PDF could not be opened as a PDF ({error.__class__.__name__})."
        ) from error

    count = len(reader.pages)
    wanted = tuple(range(1, count + 1)) if pages is None else tuple(int(page) for page in pages)
    for page_number in wanted:
        if page_number < 1 or page_number > count:
            raise AnnotationExtractionRefused(
                f"page {page_number} was asked for, and the document has {count} page(s)."
            )

    collected: list[AnnotationOccurrence] = []
    with_column: list[int] = []
    for page_number in wanted:
        found = page_evidence(reader.pages[page_number - 1], page_number,
                              include_geometry=include_geometry)
        if any(occurrence.schedule_row_candidate for occurrence in found):
            with_column.append(page_number)
        collected.extend(found)

    return DocumentAnnotationEvidence(
        extractor_version=EXTRACTOR_VERSION,
        source_pdf_sha256=digest,
        page_count=count,
        pages_extracted=wanted,
        pages_with_schedule_column=tuple(with_column),
        include_geometry=include_geometry,
        rule_set=RULE_SET,
        occurrences=tuple(collected),
    )


# ===========================================================================
# The canonical form
# ===========================================================================
def occurrence_payload(occurrence: AnnotationOccurrence) -> dict[str, Any]:
    """One occurrence as plain JSON data — the deterministic core of the evidence.

    Everything here is a property of the PDF and of the recorded rule set. There is no
    timestamp, no identifier, no path and no host-specific value anywhere in it, which is what
    makes two extractions of one document comparable byte for byte.
    """
    return {
        "page_number": occurrence.page_number,
        "mark_candidate": occurrence.mark_candidate,
        "mark_readable": occurrence.mark_readable,
        "annotation_x": occurrence.annotation_x,
        "annotation_y": occurrence.annotation_y,
        "raw_text_run": occurrence.raw_text_run,
        "font": occurrence.font,
        "font_size": occurrence.font_size,
        "text_op_index": occurrence.text_op_index,
        "operator_count": occurrence.operator_count,
        "duplicate_operator_count": occurrence.duplicate_operator_count,
        "text_matrix": list(occurrence.text_matrix),
        "ctm": list(occurrence.ctm),
        "tag_box_present": occurrence.tag_box_present,
        "tag_box_geometry": None if occurrence.tag_box_geometry is None else [
            occurrence.tag_box_geometry.x0, occurrence.tag_box_geometry.y0,
            occurrence.tag_box_geometry.x1, occurrence.tag_box_geometry.y1,
        ],
        "leader_present": occurrence.leader_present,
        "leader_endpoint": None if occurrence.leader is None
        else (None if occurrence.leader.endpoint is None else list(occurrence.leader.endpoint)),
        "leader_chain_nodes": None if occurrence.leader is None else occurrence.leader.chain_nodes,
        "leader_truncated": None if occurrence.leader is None else occurrence.leader.truncated,
        "schedule_row_candidate": occurrence.schedule_row_candidate,
        "nearby_text_runs": [
            {"text": item.text, "x": item.x, "y": item.y, "y_top": item.y_top,
             "dx": item.dx, "dy": item.dy, "distance_pt": item.distance_pt}
            for item in occurrence.nearby_text_runs
        ],
    }


def evidence_payload(evidence: DocumentAnnotationEvidence) -> dict[str, Any]:
    """The whole reading as plain JSON data."""
    return {
        "extractor_version": evidence.extractor_version,
        "source_pdf_sha256": evidence.source_pdf_sha256,
        "page_count": evidence.page_count,
        "pages_extracted": list(evidence.pages_extracted),
        "pages_with_schedule_column": list(evidence.pages_with_schedule_column),
        "include_geometry": evidence.include_geometry,
        "rule_set": [[name, value] for name, value in evidence.rule_set],
        "occurrences": [occurrence_payload(item) for item in evidence.occurrences],
    }


def canonical_json(evidence: DocumentAnnotationEvidence) -> str:
    """The reading as a byte-stable string. Two runs over one PDF produce one string."""
    return json.dumps(evidence_payload(evidence), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
