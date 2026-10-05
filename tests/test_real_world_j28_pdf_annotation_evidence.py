"""
Milestone J28 — DETERMINISTIC PDF ANNOTATION EVIDENCE
(tests/test_real_world_j28_pdf_annotation_evidence.py)

WHAT THIS FILE IS

The proof that SteelSpec now has a PDF evidence layer that records ANNOTATION
OCCURRENCES — what a drawing actually says and where it actually says it —
without ever recording a physical member:

  * `app/drawing_reading/pdf_annotation_extractor.py` reads the PDF's own
    content stream: text-showing operators, their text matrices and CTMs, and
    the stroked vector geometry. No AI output, no database row and no network
    call is consulted, and the proofs below install tripwires so that the claim
    is checked rather than asserted.
  * `app/engineering_data/pdf_annotation_evidence.py` lays that reading down in
    rows and decides which reading stands for a page.
  * `supabase/migrations/20260927000000_j28_pdf_annotation_occurrences.sql` is
    the append-only table. IT IS NOT APPLIED and J28 wires the layer into no
    route — a claim this file proves structurally, not by assertion.

Section G1 (Milestone J37B) closes the one incompatibility J37 found between this
layer and its own migration: the extractor may report a negative coordinate, and
the table records only a non-negative one. The refusal was moved to the storage
layer, where it is the non-fatal refusal the extraction paths already handle
rather than a database constraint violation that fails a whole run. The extractor
is deliberately unchanged, and section G1 proves it can still report the
condition.

WHAT IS REAL, AND WHAT IS DOUBLED

REAL: the source document. `/Users/chad/Downloads/24633 33 Arkles Strand -
Plans 17.08.26 (lodged).pdf` is a genuine lodged drawing set, and the acceptance
section below reads its actual bytes with the pinned `pypdf`. Where it is absent
the section SKIPS — it never substitutes a fixture and never claims a pass it did
not earn.

DOUBLED: everything in the hermetic sections. Those PDFs are built by this file
with `reportlab`, one text-showing operator at a time, so that the expected
coordinate, operator count and merged text are computed independently here rather
than read back out of the extractor. A hermetic PDF is evidence about the CODE; it
is not evidence about the Arkles drawings, and nothing in the hermetic sections
is reported as if it were.

TWO HONEST LIMITS

1. THE GRAMMAR IS A SHAPE TEST, NOT A READING. `^[A-Z]{1,3}\\d{1,3}[A-Z]?$` admits
   sheet codes, grid references and product codes. The real set proves it: `S207`
   appears 14 times on one page, and `X4`, `W01`, `SG8`, `D19` and `SDM049` all
   match. This file asserts they SURVIVE AS CANDIDATES, and it asserts no field
   promotes any of them — it never asserts a candidate is a member.

2. AN OCCURRENCE IS NOT A MEMBER. Nothing here claims two occurrences of one mark
   are one member, or two members, or that a leader endpoint is a member's end. The
   real set has one mark appearing 18 times at 18 distinct coordinates, and the
   most this layer may say is that it found 18 annotations.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import io
import json
import re
from pathlib import Path

import pytest

from app.drawing_reading import pdf_annotation_extractor as extractor
from app.engineering_data import pdf_annotation_evidence as storage

REPO = Path(__file__).resolve().parent.parent
REAL_PDF = Path("/Users/chad/Downloads/24633 33 Arkles Strand - Plans 17.08.26 (lodged).pdf")

needs_real_pdf = pytest.mark.skipif(
    not REAL_PDF.exists(),
    reason=f"the real Arkles PDF is not at {REAL_PDF}. This is NOT a claim that the drawings "
           "contain no annotations — it is the absence of the document.",
)

# The 11 pages the J28 brief names for the real-document acceptance proof.
ACCEPTANCE_PAGES = (6, 8, 9, 10, 11, 15, 21, 23, 29, 35, 36)


# -----------------------------------------------------------------------------
# Building a PDF whose content is exactly the operators this file intends
# -----------------------------------------------------------------------------
PAGE_SIZE = (600.0, 400.0)


def build_pdf(operations, page_size=PAGE_SIZE) -> bytes:
    """A one-page PDF holding exactly `operations`, and nothing else.

    `("text", font, size, x, y, string)` emits one text-showing operator at a
    position written here. Two calls with the same arguments therefore emit two
    byte-identical operators — which is how the overprint tests build a real
    overprint rather than describing one.

    Coordinates are the PDF's own (origin bottom-left), because that is what
    `canvas.drawString` takes. The extractor reports the top-left convention, so
    the expected `annotation_y` of a mark drawn at y is `page_height - y`; the
    tests below compute that in the open rather than copying a number back.
    """
    from reportlab.pdfgen import canvas as _canvas

    buffer = io.BytesIO()
    canvas = _canvas.Canvas(buffer, pagesize=page_size, pageCompression=0)

    # Text is emitted through a PDFTextObject rather than `canvas.drawString`,
    # because `drawString` closes every string with a `T*` and pypdf surfaces that
    # as a newline INSIDE the operator's text — so `P` then `4` would arrive as
    # `P\n4`, which no run-reassembly rule could join. A text object emits
    # `1 0 0 1 x y Tm (s) Tj` and nothing else, which is the operator stream this
    # file intends and the shape the real drawings use.
    state = {"text": None}

    def flush():
        if state["text"] is not None:
            canvas.drawText(state["text"])
            state["text"] = None

    for operation in operations:
        kind = operation[0]
        if kind == "text":
            _, font, size, x, y, string = operation
            if state["text"] is None:
                state["text"] = canvas.beginText(x, y)
            else:
                state["text"].setTextOrigin(x, y)
            state["text"].setFont(font, size)
            state["text"].textOut(string)
        elif kind == "rect":
            flush()
            _, x, y, width, height = operation
            canvas.setLineWidth(0.5)
            canvas.rect(x, y, width, height, stroke=1, fill=0)
        elif kind == "line":
            flush()
            _, x0, y0, x1, y1 = operation
            canvas.setLineWidth(0.5)
            canvas.line(x0, y0, x1, y1)
        else:  # pragma: no cover - a typo in a test, not a product state
            raise AssertionError(f"unknown operation {kind!r}")
    flush()
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def mark(x, y, text, font="Helvetica", size=10):
    return ("text", font, size, x, y, text)


def top(y, page_height=PAGE_SIZE[1]):
    """The top-left y the extractor reports for a mark drawn at PDF y."""
    return round(page_height - y, extractor.COORDINATE_PRECISION)


def read(pdf, **kwargs):
    return extractor.extract_annotations(pdf, **kwargs)


def only_occurrence(pdf, **kwargs):
    evidence = read(pdf, **kwargs)
    assert len(evidence.occurrences) == 1, (
        f"expected exactly one occurrence, got {len(evidence.occurrences)}: "
        f"{[(o.mark_candidate, o.raw_text_run) for o in evidence.occurrences]}"
    )
    return evidence.occurrences[0]


def run_reads(occurrence) -> str:
    """What the run reads, with the whitespace its own operators carried removed.

    `raw_text_run` is the operators' text VERBATIM, so it keeps whatever the PDF
    emitted — and `reportlab` closes every `drawString` with a `T*`, which `pypdf`
    surfaces as a trailing newline. Comparisons about what a run READS strip;
    `test_raw_text_run_keeps_the_operators_own_whitespace` is the one place the
    verbatim value itself is asserted.
    """
    return occurrence.raw_text_run.strip()


def code_only(path) -> str:
    """A module's executable text, with every docstring and comment removed.

    Scanning the raw file would find the claims a module makes about ITSELF — "there
    is deliberately no `is_steel_member` column here" is a sentence containing the
    very token it denies. `ast.unparse` drops comments and the docstrings are
    removed from the tree first, so what remains is code.
    """
    tree = ast.parse(Path(path).read_text())
    docstring_holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, docstring_holders):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
    return ast.unparse(tree)


# =============================================================================
# A. The reading contract: what a coordinate, a run and a token actually are
# =============================================================================
def test_coordinate_is_the_first_glyph_origin_in_the_top_left_convention():
    """The J28 coordinate definition, checked against a position written here."""
    occurrence = only_occurrence(build_pdf([mark(100.0, 300.0, "P4")]))

    assert occurrence.annotation_x == 100.0
    assert occurrence.annotation_y == top(300.0) == 100.0
    assert occurrence.mark_candidate == "P4"
    assert occurrence.mark_readable is True
    # The PDF's own bottom-left value is carried too, so nobody has to guess which
    # convention a number is in.
    assert occurrence.text_matrix[5] == 300.0


def test_a_mark_split_across_operators_is_reassembled_from_its_own_advance():
    """`P` + `4` at a 3.4 pt advance is one run, and one occurrence, not two.

    The gap is under the extractor's `RUN_X_GAP_PT` because that is the advance
    the real Arkles set uses when it draws a mark one character at a time.
    """
    pdf = build_pdf([mark(100.0, 300.0, "P"), mark(103.4, 300.0, "4")])
    occurrence = only_occurrence(pdf)

    assert occurrence.mark_candidate == "P4"
    assert run_reads(occurrence) == "P4"
    assert occurrence.operator_count == 2
    assert occurrence.duplicate_operator_count == 0
    # The coordinate is the FIRST operator's origin, not the second's.
    assert occurrence.annotation_x == 100.0
    assert occurrence.annotation_y == top(300.0)


def test_a_split_wider_than_the_merge_threshold_is_not_reassembled():
    """The 4.8 pt threshold is real, and this is its cost: a wider split is lost.

    Stated as a test rather than as a limitation in prose, because a threshold
    that only ever appears to succeed is not a measured threshold.
    """
    pdf = build_pdf([mark(100.0, 300.0, "P"), mark(110.0, 300.0, "4")])
    assert extractor.RUN_X_GAP_PT < 10.0
    assert read(pdf).occurrences == ()


def test_a_run_of_distinct_operators_reports_no_duplicates():
    """`duplicate_operator_count` counts copies the PDF drew twice, not operators."""
    occurrence = only_occurrence(build_pdf([mark(100.0, 300.0, "P"), mark(103.4, 300.0, "4")]))
    assert occurrence.operator_count == 2
    assert occurrence.duplicate_operator_count == 0


def test_body_text_is_not_an_annotation_and_is_not_reported_as_unreadable():
    """Ordinary drawing text is neither a candidate nor an unreadable mark.

    A run of capital letters is not a designation-shaped fragment. Reporting
    `STEEL POSTS` as an unreadable mark would flood the evidence with note
    headings and room names and make the one genuine unreadable row unfindable.
    """
    pdf = build_pdf([mark(50.0, 300.0, "STEEL POSTS"), mark(50.0, 280.0, "GARAGE"),
                     mark(50.0, 260.0, "NOTES:")])
    assert read(pdf).occurrences == ()


def test_a_designation_shaped_fragment_the_grammar_cannot_tokenise_is_recorded():
    """`CCAH3.2treated` is a treated-timber grade: designation-shaped, not tokenisable.

    It is emitted with `mark_readable=False` and NO candidate, so the fact that
    the drawing said something the grammar could not read is preserved rather
    than dropped on the floor.
    """
    occurrence = only_occurrence(build_pdf([mark(50.0, 300.0, "CCAH3.2treated")]))
    assert occurrence.mark_readable is False
    assert occurrence.mark_candidate is None
    assert run_reads(occurrence) == "CCAH3.2treated"


def test_raw_text_run_keeps_the_operators_own_whitespace():
    """`raw_text_run` is verbatim, and this is the one place that is asserted.

    The real set draws some marks inside their operator as `' BF2'`, with a leading
    space. Nothing is trimmed on the way in: the evidence is what the content stream
    said, and a reader comparing runs strips for itself — which is why every other
    assertion in this file goes through `run_reads`.
    """
    occurrence = only_occurrence(build_pdf([mark(100.0, 300.0, " BF2")]))
    assert occurrence.raw_text_run == " BF2"
    assert run_reads(occurrence) == "BF2"
    assert occurrence.mark_candidate == "BF2"


def test_a_run_whose_operator_opens_with_a_space_reports_that_operator_origin():
    """The coordinate definition's one limitation, made explicit.

    The occurrence coordinate is the first glyph origin of the mark TOKEN. When the
    token shares an operator with leading whitespace, resolving past the space needs
    the font's width table, which is not read — so the operator's own origin is
    reported. That is the PDF's own number for that string's first glyph, it is
    deterministic, and it is at most one space wide.
    """
    spaced = only_occurrence(build_pdf([mark(100.0, 300.0, " BF2")]))
    plain = only_occurrence(build_pdf([mark(100.0, 300.0, "BF2")]))

    assert spaced.annotation_x == plain.annotation_x == 100.0
    assert spaced.annotation_y == plain.annotation_y == top(300.0)


# =============================================================================
# B. The order that matters: exact duplicates collapse BEFORE runs are merged
# =============================================================================
def test_an_overprint_is_one_occurrence_with_the_duplicate_counted():
    """The J28 proof's finding, as an executable claim.

    The real document draws `BF2` twice at one point and `S202` twice at another.
    Two byte-identical operators are ONE annotation drawn twice, so the count is
    preserved and the row is not doubled.
    """
    pdf = build_pdf([mark(100.0, 300.0, "BF2"), mark(100.0, 300.0, "BF2")])
    occurrence = only_occurrence(pdf)

    assert run_reads(occurrence) == "BF2"
    assert occurrence.operator_count == 1
    assert occurrence.duplicate_operator_count == 1


def test_the_phantom_token_does_not_appear_when_deduplication_runs_first():
    """`BF2` + `BF2` must never surface as `BF2BF2`.

    This is the whole reason the operation order is fixed. The second half of the
    test shows what the opposite order would have produced, by performing it:
    reassembling the RAW operators merges the two into one run reading `BF2BF2`,
    and the duplicate disappears into that phantom string.
    """
    pdf = build_pdf([mark(100.0, 300.0, "BF2"), mark(100.0, 300.0, "BF2")])

    extracted = read(pdf).occurrences
    assert [run_reads(occurrence) for occurrence in extracted] == ["BF2"]
    assert "BF2BF2" not in "".join(occurrence.raw_text_run for occurrence in extracted)

    # The rejected order, performed deliberately, on the same page.
    import pypdf

    page = pypdf.PdfReader(io.BytesIO(pdf)).pages[0]
    raw_operators = extractor.text_operators(page, 1)
    merged_first = extractor.reassemble_runs(raw_operators)
    assert [run.text.strip() for run in merged_first] == ["BF2BF2"], (
        "the test's premise is that merging before deduplication produces the phantom "
        "token; if that is no longer true the ordering proof has no subject"
    )


def test_an_overprint_of_a_multi_operator_run_keeps_every_copy_counted():
    """A whole run drawn twice is one occurrence with two duplicate copies."""
    pdf = build_pdf([
        mark(100.0, 300.0, "P"), mark(103.4, 300.0, "4"),
        mark(100.0, 300.0, "P"), mark(103.4, 300.0, "4"),
    ])
    occurrence = only_occurrence(pdf)

    assert run_reads(occurrence) == "P4"
    assert occurrence.operator_count == 2
    assert occurrence.duplicate_operator_count == 2


def test_deduplication_is_positional_not_textual():
    """The same mark drawn at two points is two occurrences, not an overprint.

    Deduplication compares the whole operator tuple — font, size, text, Tm AND CTM
    — so a `P4` at one point and a `P4` at another are two things the drawing says.
    Collapsing them on the token alone would be exactly the merge this layer exists
    to avoid, and it would destroy the count the next milestone needs.
    """
    pdf = build_pdf([mark(100.0, 300.0, "P4"), mark(100.0, 250.0, "P4")])
    extracted = read(pdf).occurrences

    assert len(extracted) == 2
    assert {o.mark_candidate for o in extracted} == {"P4"}
    assert all(o.duplicate_operator_count == 0 for o in extracted)
    assert all(o.operator_count == 1 for o in extracted)


def test_a_mark_and_its_section_size_are_one_run_with_one_token():
    """`P4 300PFC` yields the token `P4`, and `300PFC` stays in the run.

    This is the real shape the J28 proof found on the Arkles set, and the reason the
    token is called a candidate: the run is what the drawing wrote, the token is what
    the grammar could build from it, and nothing claims the two are the same length.
    """
    occurrence = only_occurrence(build_pdf([mark(100.0, 300.0, "P4 300PFC")]))

    assert occurrence.mark_candidate == "P4"
    assert run_reads(occurrence) == "P4 300PFC"
    assert occurrence.operator_count == 1


def test_the_separator_is_what_stops_the_token_picking_up_the_next_word():
    """Without a separator the trailing letters join the token, and that is honest.

    `P4` and `P5` written with no advance are one run reading `P4P5`, and the token
    the grammar builds is `P4P` — the trailing-letter form of the pattern. A space
    stops it, which is why `P4 300PFC` gives `P4`. Recording the token the grammar
    actually builds, rather than the token a reader expects, is the whole point.
    """
    occurrence = only_occurrence(build_pdf([mark(100.0, 300.0, "P4"), mark(100.0, 300.0, "P5")]))

    assert run_reads(occurrence) == "P4P5"
    assert occurrence.mark_candidate == "P4P"
    assert occurrence.operator_count == 2


# =============================================================================
# C. Heuristic classifications, kept separate from deterministic facts
# =============================================================================
def test_a_schedule_column_is_classified_from_its_median_pitch():
    """A column of marks at a table-like pitch is schedule evidence."""
    column = [mark(100.0, 300.0 - index * 5.76, f"P{index + 1}") for index in range(5)]
    plan = mark(400.0, 100.0, "P9")
    evidence = read(build_pdf(column + [plan]))

    in_column = [o for o in evidence.occurrences if o.annotation_x == 100.0]
    assert len(in_column) == 5
    assert all(o.schedule_row_candidate is True for o in in_column)

    # The page HAS a schedule column, so a mark outside it is classified False —
    # which is a statement about the page, not a claim about a member.
    elsewhere = [o for o in evidence.occurrences if o.annotation_x == 400.0]
    assert len(elsewhere) == 1
    assert elsewhere[0].schedule_row_candidate is False


def test_a_section_heading_gap_does_not_lose_the_column():
    """MEDIAN pitch, not uniform pitch — the real set's own structure decides this.

    A schedule's mark column is interrupted by a section heading with a much
    larger gap. A pitch-variance test rejects the very column it is looking for.
    """
    rows = [mark(100.0, 300.0 - index * 5.76, f"P{index + 1}") for index in range(6)]
    heading = mark(100.0, 300.0 - 6 * 5.76 - 22.68, "P7")   # the 22.68 pt section gap
    tail = [mark(100.0, 300.0 - 6 * 5.76 - 22.68 - (index + 1) * 5.76, f"P{index + 8}")
            for index in range(3)]
    evidence = read(build_pdf(rows + [heading] + tail))

    assert len(evidence.occurrences) == 10
    assert all(o.schedule_row_candidate is True for o in evidence.occurrences)


def test_one_page_may_carry_two_schedule_columns():
    """'One schedule per page' is false: the real pages 11 and 21 each have two.

    No caller may assume one column per page, so the classifier returns a tuple and
    this asserts that two distinct columns are found and both are classified.
    """
    import pypdf

    left = [mark(100.0, 300.0 - index * 5.76, f"P{index + 1}") for index in range(4)]
    right = [mark(223.0, 300.0 - index * 5.76, f"B{index + 1}") for index in range(4)]
    pdf = build_pdf(left + right)
    evidence = read(pdf)

    page = pypdf.PdfReader(io.BytesIO(pdf)).pages[0]
    kept, counts = extractor.deduplicate_operators(extractor.text_operators(page, 1))
    runs = extractor.reassemble_runs(kept, {op.text_op_index: count
                                            for op, count in zip(kept, counts)})
    marks = [run for run in runs if (extractor.classify_run(run) or (None,))[0] is not None]

    columns = extractor.schedule_columns(marks)
    assert columns == (100.0, 223.0)
    assert all(o.schedule_row_candidate is True for o in evidence.occurrences)


def test_a_page_with_no_schedule_column_returns_unknown_not_false():
    """Absence of a schedule is not evidence that a mark is not a schedule row.

    The three-valued classification is the point: `None` says the question was not
    put, and a page that has no schedule must never be reported as if it had been
    answered.
    """
    pdf = build_pdf([mark(100.0, 300.0, "P4"), mark(400.0, 100.0, "P5")])
    evidence = read(pdf)

    assert evidence.pages_with_schedule_column == ()
    assert all(o.schedule_row_candidate is None for o in evidence.occurrences)


# =============================================================================
# D. The physical-member boundary — the hard acceptance criterion
# =============================================================================
def test_the_same_mark_at_different_coordinates_is_several_occurrences():
    """Three `P4`s are three annotations. Nothing says how many members that is."""
    pdf = build_pdf([mark(100.0, 300.0, "P4"), mark(100.0, 250.0, "P4"),
                     mark(400.0, 100.0, "P4")])
    evidence = read(pdf)

    assert len(evidence.occurrences) == 3
    assert {o.mark_candidate for o in evidence.occurrences} == {"P4"}
    assert len({(o.annotation_x, o.annotation_y) for o in evidence.occurrences}) == 3


def test_no_payload_field_relates_two_annotations_or_names_a_member():
    """The boundary is enforced by a closed field set, not by a comment.

    A field added later is what a future milestone would reach for, so the set is
    frozen here. Nothing in it may name a member, a placement, or a relation
    between two occurrences.
    """
    occurrence = only_occurrence(build_pdf([mark(100.0, 300.0, "P4")]))
    payload = extractor.occurrence_payload(occurrence)

    assert set(payload) == {
        "page_number", "mark_candidate", "mark_readable", "annotation_x", "annotation_y",
        "raw_text_run", "font", "font_size", "text_op_index", "operator_count",
        "duplicate_operator_count", "text_matrix", "ctm", "tag_box_present",
        "tag_box_geometry", "leader_present", "leader_endpoint", "leader_chain_nodes",
        "leader_truncated", "schedule_row_candidate", "nearby_text_runs",
    }
    forbidden = ("member", "placement", "physical", "steel", "same_", "different_")
    assert not [name for name in payload if any(word in name for word in forbidden)]

    source = code_only(extractor.__file__)
    assert "is_steel_member" not in source
    assert "member_endpoint" not in source


def test_the_real_arkles_mark_grammar_false_positives_survive_as_candidates():
    """The grammar's false positives, reproduced hermetically.

    Every one of these is mark-shaped and none is a member mark; the layer's whole
    contract is that they arrive as candidates and stop there.
    """
    shapes = ["S207", "X4", "W01", "SG8", "D19", "SDM049", "SET153", "UB37"]
    pdf = build_pdf([mark(50.0, 300.0 - index * 20.0, shape) for index, shape in enumerate(shapes)])
    evidence = read(pdf)

    assert sorted(o.mark_candidate for o in evidence.occurrences) == sorted(shapes)
    assert all(o.mark_readable is True for o in evidence.occurrences)


def test_a_four_letter_prefix_cannot_be_tokenised_and_is_recorded_as_unreadable():
    """`ABCD1234` matches no token, so it is recorded as a fragment rather than dropped."""
    occurrence = only_occurrence(build_pdf([mark(50.0, 340.0, "ABCD1234")]))
    assert occurrence.mark_candidate is None
    assert occurrence.mark_readable is False


def test_the_token_is_a_prefix_and_the_remainder_is_kept_in_the_run():
    """`P1234` yields the candidate `P123`, and `4` stays in the raw run.

    The grammar matches a PREFIX of the run, not the whole of it. That is what lets
    the real `SET153Flashingtapeas`, `SDM049with304S/Sdomed` and `Y3Y1` runs yield a
    token at all — and it is why a candidate is never the whole of what was drawn.
    Nothing in the payload claims the token accounts for the run.
    """
    occurrence = only_occurrence(build_pdf([mark(50.0, 320.0, "P1234")]))
    assert occurrence.mark_candidate == "P123"
    assert occurrence.mark_readable is True
    assert run_reads(occurrence) == "P1234"


def test_nearby_text_records_distance_and_claims_nothing_about_applicability():
    """`nearby != applicable`, and the payload makes that structural."""
    pdf = build_pdf([mark(100.0, 300.0, "P4"), mark(120.0, 295.0, "LEVEL 3")])
    occurrence = only_occurrence(pdf)

    assert len(occurrence.nearby_text_runs) == 1
    near = occurrence.nearby_text_runs[0]
    assert near.text.strip() == "LEVEL 3"
    assert near.dx == 20.0
    assert near.dy == -5.0
    assert near.distance_pt == round((20.0 ** 2 + 5.0 ** 2) ** 0.5, extractor.COORDINATE_PRECISION)
    payload = extractor.occurrence_payload(occurrence)
    assert not [name for name in payload if name.startswith("applies_to")]


def test_a_leader_endpoint_is_an_annotation_fact_and_is_named_as_one():
    """A drawn chain leaving a tag box is traced to a free end — nothing more.

    The field is `leader_endpoint`. It is not a member end, and the payload has no
    name that could be read as one.
    """
    pdf = build_pdf([
        ("rect", 90.0, 290.0, 20.0, 20.0),      # the tag box around the mark
        mark(95.0, 297.0, "P4"),
        ("line", 110.0, 300.0, 160.0, 340.0),   # a leader leaving the box
    ])
    occurrence = only_occurrence(pdf)

    assert occurrence.tag_box_present is True
    assert occurrence.tag_box_geometry is not None
    assert occurrence.leader_present is True
    assert occurrence.leader.endpoint is not None
    payload = extractor.occurrence_payload(occurrence)
    assert "member_endpoint" not in payload


def test_a_mark_with_no_box_reports_the_leader_question_as_unknown():
    """The leader test is defined against a box, so without one the answer is unknown.

    Reported as unknown rather than as False: 'no leader was found' and 'there was
    no box to look for a leader from' are different states.
    """
    occurrence = only_occurrence(build_pdf([mark(100.0, 300.0, "P4")]))

    assert occurrence.tag_box_present is False
    assert occurrence.leader_present is None
    assert occurrence.leader is None


# =============================================================================
# E. Provenance
# =============================================================================
def test_every_occurrence_carries_the_provenance_to_reproduce_where_it_came_from():
    pdf = build_pdf([mark(100.0, 300.0, "P4")])
    evidence = read(pdf)
    occurrence = evidence.occurrences[0]

    assert evidence.source_pdf_sha256 == hashlib.sha256(pdf).hexdigest()
    assert evidence.extractor_version == extractor.EXTRACTOR_VERSION
    assert occurrence.page_number == 1
    assert occurrence.text_op_index == 0
    assert len(occurrence.text_matrix) == 6
    assert len(occurrence.ctm) == 6
    rules = dict(evidence.rule_set)
    assert rules["run_x_gap_pt"] == "4.8"
    assert rules["run_y_tolerance_pt"] == "0.75"
    assert rules["schedule_median_pitch_pt"] == "8.0"


def test_the_rule_set_records_every_threshold_the_reading_depends_on():
    """A reading is only reproducible if the thresholds it used are recorded.

    Every named constant that changes what is found must appear, so a reading can
    never be attributed to a rule set that is not the one that produced it.
    """
    rules = dict(extractor.RULE_SET)
    for name in ("run_x_gap_pt", "run_y_tolerance_pt", "schedule_median_pitch_pt",
                 "schedule_min_rows", "tag_box_reach_pt", "leader_snap_pt",
                 "leader_max_reach_pt", "leader_max_segments", "coordinate_precision",
                 "nearby_window_x_pt", "nearby_window_y_pt",
                 "mark_candidate_pattern", "mark_fragment_pattern"):
        assert name in rules, f"the rule set does not record {name}"
    assert extractor.EXTRACTOR_VERSION in ("j28.1",)
    assert isinstance(extractor.EXTRACTOR_VERSION, str)


# =============================================================================
# F. Determinism
# =============================================================================
def test_two_readings_of_one_pdf_are_byte_identical(tmp_path):
    pdf = build_pdf([
        mark(100.0, 300.0, "P4"), mark(100.0, 300.0, "P4"),
        mark(100.0, 250.0, "P"), mark(103.4, 250.0, "5"),
        ("rect", 90.0, 290.0, 20.0, 20.0), ("line", 110.0, 300.0, 160.0, 340.0),
    ])
    first = extractor.canonical_json(read(pdf))
    second = extractor.canonical_json(read(pdf))
    assert first == second


def test_the_reading_does_not_depend_on_where_the_file_lives(tmp_path):
    """No path, hostname or filesystem fact may enter the evidence.

    The same bytes are read from two different paths, so a path leaking into the
    payload shows up as a byte difference rather than as a raised eyebrow.
    """
    pdf = build_pdf([mark(100.0, 300.0, "P4")])
    first = tmp_path / "one.pdf"
    second = tmp_path / "nested" / "two.pdf"
    second.parent.mkdir()
    first.write_bytes(pdf)
    second.write_bytes(pdf)

    assert extractor.canonical_json(read(first)) == extractor.canonical_json(read(second))


def test_no_timestamp_or_identifier_enters_the_evidence_payload():
    """An audit timestamp belongs outside the evidence identity, and it is outside.

    The payload is scanned for the shapes a timestamp or generated identifier
    would take, so a later addition cannot pass unnoticed.
    """
    pdf = build_pdf([mark(100.0, 300.0, "P4")])
    payload = extractor.occurrence_payload(read(pdf).occurrences[0])
    text = json.dumps(payload, sort_keys=True)

    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", text)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", text)
    assert "tmp" not in text


def test_the_canonical_form_is_stable_under_key_ordering():
    """The canonical form sorts keys, so dict construction order cannot leak in."""
    pdf = build_pdf([mark(100.0, 300.0, "P4"), mark(100.0, 250.0, "B5")])
    evidence = read(pdf)
    rebuilt = dataclasses.replace(evidence, occurrences=tuple(reversed(evidence.occurrences)))
    assert extractor.canonical_json(evidence) != extractor.canonical_json(rebuilt)
    assert extractor.canonical_json(rebuilt) == extractor.canonical_json(
        dataclasses.replace(evidence, occurrences=tuple(reversed(evidence.occurrences)))
    )


# =============================================================================
# G. The storage layer
# =============================================================================
def synthetic_evidence(**overrides):
    occurrence = extractor.AnnotationOccurrence(
        page_number=overrides.get("page_number", 1),
        mark_candidate=overrides.get("mark_candidate", "P4"),
        mark_readable=overrides.get("mark_readable", True),
        annotation_x=overrides.get("annotation_x", 100.0),
        annotation_y=overrides.get("annotation_y", 100.0),
        raw_text_run=overrides.get("raw_text_run", "P4"),
        font="/F1", font_size=10.0, text_op_index=0, operator_count=1,
        duplicate_operator_count=overrides.get("duplicate_operator_count", 0),
        text_matrix=(1.0, 0.0, 0.0, 1.0, 100.0, 300.0), ctm=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        tag_box_present=False, tag_box_geometry=None, leader_present=None, leader=None,
        schedule_row_candidate=overrides.get("schedule_row_candidate", None),
        nearby_text_runs=(),
    )
    return extractor.DocumentAnnotationEvidence(
        extractor_version=overrides.get("extractor_version", "j28.1"),
        source_pdf_sha256=overrides.get(
            "source_pdf_sha256", "a" * 64),
        page_count=11, pages_extracted=(1,), pages_with_schedule_column=(),
        include_geometry=True, rule_set=extractor.RULE_SET,
        occurrences=overrides.get("occurrences", (occurrence,)),
    )


def test_rows_carry_exactly_the_declared_columns():
    rows = storage.occurrence_rows(synthetic_evidence(), drawing_id="d-1", project_id="p-1", analysis_run_id="run-1")
    assert len(rows) == 1
    # Every declared column but `extracted_at`, which belongs to the database's own
    # default and is asserted absent below.
    assert tuple(sorted(rows[0])) == tuple(
        sorted(name for name in storage.ANNOTATION_COLUMNS if name != "extracted_at"))
    assert rows[0]["drawing_id"] == "d-1"
    assert rows[0]["project_id"] == "p-1"
    assert rows[0]["extractor_version"] == "j28.1"
    # `extracted_at` belongs to the database's own clock, never to the caller.
    assert "extracted_at" not in rows[0]


def test_the_promoted_columns_are_the_same_values_the_json_holds():
    """The two copies may not drift, so they are built together and compared here."""
    row = storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p", analysis_run_id="run-1")[0]
    for name in storage._PROMOTED:
        assert row[name] == row["evidence"][name], f"{name} disagrees with the evidence json"


def test_no_column_could_be_read_as_a_physical_member():
    forbidden = ("member", "placement", "physical", "steel", "same_", "different_")
    assert not [name for name in storage.ANNOTATION_COLUMNS
                if any(word in name for word in forbidden)]


def test_an_overprint_row_keeps_its_duplicate_count():
    """The count is a column, not a discarded detail."""
    evidence = synthetic_evidence(occurrences=(extractor.AnnotationOccurrence(
        page_number=1, mark_candidate="BF2", mark_readable=True, annotation_x=10.0,
        annotation_y=20.0, raw_text_run="BF2", font="/F1", font_size=10.0, text_op_index=0,
        operator_count=1, duplicate_operator_count=1,
        text_matrix=(1.0, 0.0, 0.0, 1.0, 10.0, 300.0), ctm=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        tag_box_present=False, tag_box_geometry=None, leader_present=None, leader=None,
        schedule_row_candidate=None, nearby_text_runs=(),
    ),))
    row = storage.occurrence_rows(evidence, drawing_id="d", project_id="p", analysis_run_id="run-1")[0]
    assert row["duplicate_operator_count"] == 1
    assert row["operator_count"] == 1


def test_two_occurrences_that_would_collide_on_the_key_are_refused_not_merged():
    """Silently keeping one would be silently dropping an occurrence that was found."""
    first = synthetic_evidence(annotation_x=100.0, annotation_y=100.0)
    clash = dataclasses.replace(
        first, occurrences=(first.occurrences[0], dataclasses.replace(
            first.occurrences[0], mark_candidate="P5", raw_text_run="P5")))
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(clash, drawing_id="d", project_id="p", analysis_run_id="run-1")


def test_the_storage_layer_refuses_a_contradictory_reading():
    """Each refusal is a state this layer must never record as if it were a reading."""
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(synthetic_evidence(mark_readable=False, mark_candidate="P4"),
                                drawing_id="d", project_id="p", analysis_run_id="run-1")
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(synthetic_evidence(annotation_x="100.0"),
                                drawing_id="d", project_id="p", analysis_run_id="run-1")
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(synthetic_evidence(page_number=0), drawing_id="d", project_id="p", analysis_run_id="run-1")
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(synthetic_evidence(source_pdf_sha256=""),
                                drawing_id="d", project_id="p", analysis_run_id="run-1")
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(synthetic_evidence(), drawing_id="", project_id="p", analysis_run_id="run-1")


# -----------------------------------------------------------------------------
# G1. The coordinate domain — the storage contract the migration already declares
#     (Milestone J37B)
#
# `annotation_x` and `annotation_y` are `numeric(9,2)` columns whose CHECKs admit only a
# non-negative value. J37A established that the extractor can legitimately report a negative
# one — it is a faithful producer, and content drawn outside the page's own box is a real
# thing a real PDF can say — while this layer accepted it. The refusal therefore landed on
# the database, where it is a constraint violation that fails the WHOLE extraction run:
# deterministically, on every retry, leaving the page permanently unretryable. The boundary
# now sits here, and the refusal is the `AnnotationEvidenceRefused` the extraction paths
# already treat as non-fatal.
#
# These tests fix the DOMAIN, not a page. `x = 900` on a 600-point page is still accepted,
# because this layer enforces what the COLUMN enforces and nothing more; page-locality and
# MediaBox-origin semantics are separate open questions (J37A) and are not claimed here.
# -----------------------------------------------------------------------------
def test_a_negative_stored_coordinate_is_refused_on_either_axis():
    """Both columns independently: the domain is a property of a coordinate, not of x."""
    for name in ("annotation_x", "annotation_y"):
        with pytest.raises(storage.AnnotationEvidenceRefused):
            storage.occurrence_rows(synthetic_evidence(**{name: -1.0}),
                                    drawing_id="d", project_id="p", analysis_run_id="run-1")


def test_zero_is_inside_the_domain_on_either_axis():
    rows = storage.occurrence_rows(synthetic_evidence(annotation_x=0.0, annotation_y=0.0),
                                   drawing_id="d", project_id="p", analysis_run_id="run-1")
    assert (rows[0]["annotation_x"], rows[0]["annotation_y"]) == (0.0, 0.0)


def test_the_boundary_is_the_columns_own_rounding_and_not_an_epsilon():
    """`numeric(9,2)` rounds on assignment, and the CHECK sees the ROUNDED value.

    `-0.0042` is stored as `0.00` and is therefore inside the domain; `-0.005` is stored as
    `-0.01` and is outside it. A guard built on the offered value would wrongly refuse the
    first, and a guard carrying an epsilon of its own would agree with the column only by
    luck — which is why the rule is stated as the column states it.
    """
    for value in (-0.0042, -0.0):
        rows = storage.occurrence_rows(synthetic_evidence(annotation_x=value),
                                       drawing_id="d", project_id="p",
                                       analysis_run_id="run-1")
        assert rows[0]["annotation_x"] == value
    for value in (-0.005, -0.01):
        with pytest.raises(storage.AnnotationEvidenceRefused):
            storage.occurrence_rows(synthetic_evidence(annotation_x=value),
                                    drawing_id="d", project_id="p", analysis_run_id="run-1")


def test_the_guard_decides_on_the_stored_form_and_never_rewrites_the_emitted_one():
    """The reading is carried across, not repaired: an accepted value is stored as reported."""
    row = storage.occurrence_rows(synthetic_evidence(annotation_x=-0.0042),
                                  drawing_id="d", project_id="p", analysis_run_id="run-1")[0]
    assert row["annotation_x"] == -0.0042
    assert row["evidence"]["annotation_x"] == -0.0042


def test_a_positive_coordinate_is_still_accepted():
    rows = storage.occurrence_rows(synthetic_evidence(annotation_x=1168.68, annotation_y=821.4),
                                   drawing_id="d", project_id="p", analysis_run_id="run-1")
    assert (rows[0]["annotation_x"], rows[0]["annotation_y"]) == (1168.68, 821.4)


def test_the_refusal_names_the_coordinate_that_failed_and_its_value():
    """Deterministic and useful: which axis, and the value it was offered."""
    with pytest.raises(storage.AnnotationEvidenceRefused) as refused:
        storage.occurrence_rows(synthetic_evidence(annotation_y=-12.5),
                                drawing_id="d", project_id="p", analysis_run_id="run-1")
    message = str(refused.value)
    assert "annotation_y" in message
    assert "-12.5" in message
    assert "annotation_x" not in message


def test_one_out_of_domain_occurrence_refuses_the_whole_reading():
    """Not a filter and not a partial write: the in-domain occurrence is not recorded either.

    Keeping the rows that pass and dropping the one that does not would write an incomplete
    reading as though it were the reading — the same silent loss the key-collision branch
    above refuses, for the same reason.
    """
    good = synthetic_evidence().occurrences[0]
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(
            synthetic_evidence(occurrences=(good, dataclasses.replace(good, annotation_x=-30.0))),
            drawing_id="d", project_id="p", analysis_run_id="run-1")


def test_the_domain_guard_is_what_refuses_it(monkeypatch):
    """Mutation control, in the catching direction.

    With the guard's own stored-value helper bypassed, the identical reading the tests above
    refuse is ACCEPTED — so those tests fail when the guard is removed or weakened, rather
    than passing for some other reason. The helper is the guard's only use, so the bypass is
    surgical: nothing else about the validation changes.
    """
    monkeypatch.setattr(storage, "_stored_coordinate", lambda value: 0.0)
    rows = storage.occurrence_rows(synthetic_evidence(annotation_x=-30.0),
                                   drawing_id="d", project_id="p", analysis_run_id="run-1")
    assert rows[0]["annotation_x"] == -30.0


def test_the_extractor_still_reports_a_coordinate_outside_the_pages_box():
    """The boundary is here because the PRODUCER is unchanged, and this proves it.

    A mark drawn outside the page, through this file's own reportlab builder, is still found
    and still reported at its raw coordinate — so the refusal demonstrated below is this
    layer's decision, not an extractor that had been made incapable of the condition. There
    are two ways to leave the domain and both are exercised: to the left of the origin, and
    above the page's own height (the extractor reports the top-left convention, so a mark
    above the top flips to a negative y).
    """
    outside_the_origin = build_pdf((mark(-30.0, 300.0, "P4"),))
    assert only_occurrence(outside_the_origin).annotation_x == -30.0
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(read(outside_the_origin), drawing_id="d", project_id="p",
                               analysis_run_id="run-1")

    above_the_page = build_pdf((mark(100.0, 900.0, "P4"),))
    assert only_occurrence(above_the_page).annotation_y == top(900.0) == -500.0
    with pytest.raises(storage.AnnotationEvidenceRefused):
        storage.occurrence_rows(read(above_the_page), drawing_id="d", project_id="p",
                               analysis_run_id="run-1")


def test_the_domain_is_the_columns_domain_and_the_guard_does_not_pretend_otherwise():
    """Not page-locality. A mark beyond the page's width is stored, because the column has no
    opinion about where a page ends — and saying otherwise would be a claim this layer cannot
    keep. Page-locality and MediaBox-origin semantics remain recorded open questions."""
    beyond_the_page = build_pdf((mark(900.0, 300.0, "P4"),))
    rows = storage.occurrence_rows(read(beyond_the_page), drawing_id="d", project_id="p",
                                   analysis_run_id="run-1")
    assert rows[0]["annotation_x"] == 900.0


def test_the_reading_that_stands_for_a_page_is_the_latest_and_the_earlier_one_survives():
    """An append-only ledger appends; it never rewrites what it recorded before."""
    older = {**storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p", analysis_run_id="run-1")[0],
             "extracted_at": "2026-09-27T00:00:00+00:00", "extractor_version": "j28.1",
             "mark_candidate": "OLD"}
    newer = {**older, "extracted_at": "2026-09-27T01:00:00+00:00", "extractor_version": "j28.2",
             "mark_candidate": "NEW"}

    standing = storage.authoritative_occurrences([older, newer])
    assert [row["mark_candidate"] for row in standing] == ["NEW"]
    # The earlier reading is still in the input; nothing was rewritten or removed.
    assert [row["mark_candidate"] for row in [older, newer]] == ["OLD", "NEW"]


def test_the_standing_reading_is_chosen_per_page_not_per_drawing():
    rows = []
    for page in (1, 2):
        for stamp, mark_value in (("2026-09-27T00:00:00+00:00", "OLD"),
                                  ("2026-09-27T01:00:00+00:00", "NEW")):
            rows.append({"drawing_id": "d", "page_number": page, "annotation_x": 1.0,
                         "annotation_y": 2.0, "extractor_version": "j28.1",
                         "extracted_at": stamp, "mark_candidate": mark_value})
    standing = storage.authoritative_occurrences(rows)
    assert len(standing) == 2
    assert all(row["mark_candidate"] == "NEW" for row in standing)
    assert [row["page_number"] for row in standing] == [1, 2]


def test_a_page_with_no_recorded_occurrences_does_not_appear():
    """Absence is absence, never a fabricated empty page."""
    assert storage.authoritative_occurrences([]) == ()


# =============================================================================
# G2. The attempt dimension (Milestone J33)
#
# J28's occurrence identity was (drawing, page, coordinate, version) and had no attempt
# in it. The extractor is deterministic, so a re-read of one page under one version
# reproduces those columns exactly — which made the ordinary retry of a parse-failed page
# unwritable. J33 adds the identity J23 already uses for a read event, `analysis_run_id`,
# and nothing else.
# =============================================================================
def test_a_second_reading_of_one_page_is_a_new_row_and_not_a_collision():
    """Determinism is why the attempt has to be in the key, not a reason to refuse it."""
    reading = synthetic_evidence()
    first = storage.occurrence_rows(reading, drawing_id="d", project_id="p",
                                    analysis_run_id="run-a")[0]
    second = storage.occurrence_rows(reading, drawing_id="d", project_id="p",
                                     analysis_run_id="run-b")[0]

    # Every key column but the attempt is IDENTICAL — the same bytes under the same rules
    # produce the same coordinates, which is exactly what the extractor guarantees.
    for name in ("drawing_id", "page_number", "annotation_x", "annotation_y",
                 "extractor_version"):
        assert first[name] == second[name], name
    assert first["analysis_run_id"] != second["analysis_run_id"]

    # Two distinct keys, so the pair can be recorded. Before J33 they were one key and the
    # second reading could not be written at all.
    assert len({(row["drawing_id"], row["page_number"], row["analysis_run_id"],
                 row["annotation_x"], row["annotation_y"], row["extractor_version"])
                for row in (first, second)}) == 2
    # And it IS the same reading: nothing but the attempt and the table's own instant differ.
    assert first["evidence"] == second["evidence"]
    assert first["rule_set"] == second["rule_set"]
    assert first["source_pdf_sha256"] == second["source_pdf_sha256"]


def test_the_attempt_is_required_and_is_never_defaulted():
    """A refused attempt has no run, so a caller that does not hold one records nothing."""
    with pytest.raises(TypeError):
        storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p")
    for absent in ("", "   ", None, 7):
        with pytest.raises(storage.AnnotationEvidenceRefused):
            storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p",
                                    analysis_run_id=absent)


def test_the_deterministic_payload_is_independent_of_the_attempt():
    """The attempt is provenance. It may not enter the evidence, or comparison is lost."""
    reading = synthetic_evidence()
    first = storage.occurrence_rows(reading, drawing_id="d", project_id="p",
                                    analysis_run_id="run-a")[0]
    second = storage.occurrence_rows(reading, drawing_id="d", project_id="p",
                                     analysis_run_id="run-b")[0]
    assert first["evidence"] == second["evidence"]

    for name in ("analysis_run_id", "extracted_at", "drawing_id", "project_id"):
        assert name not in first["evidence"], name
        assert name not in storage._PROMOTED, name
        assert name not in extractor.occurrence_payload(reading.occurrences[0]), name


def test_the_standing_reading_is_the_later_attempt_and_never_the_larger_uuid():
    """An id is a name, not a time. Ranking by one would invent a chronology.

    The runs below are chosen so that lexical order and chronological order DISAGREE:
    `zz…` is the earlier attempt and `aa…` the later one.
    """
    row = storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p",
                                  analysis_run_id="zzzzzzzz-0000-0000-0000-000000000000")[0]
    older = {**row, "extracted_at": "2026-09-27T00:00:00+00:00", "mark_candidate": "OLD"}
    newer = {**row, "analysis_run_id": "aaaaaaaa-0000-0000-0000-000000000000",
             "extracted_at": "2026-09-27T01:00:00+00:00", "mark_candidate": "NEW"}

    standing = storage.authoritative_occurrences([older, newer])
    assert [item["mark_candidate"] for item in standing] == ["NEW"]
    assert [item["analysis_run_id"] for item in standing] == [newer["analysis_run_id"]]
    # The same answer whichever order the rows arrive in.
    assert storage.authoritative_occurrences([newer, older])[0]["mark_candidate"] == "NEW"
    # Neither reading was removed: both are still in the input the authority was given.
    assert {item["mark_candidate"] for item in (older, newer)} == {"OLD", "NEW"}


def test_the_standing_rule_reads_the_instant_and_the_version_and_not_the_attempt():
    """Proven behaviourally: two different runs with one standing tuple rank equally."""
    row = storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p",
                                  analysis_run_id="run-a")[0]
    stamp = "2026-09-27T00:00:00+00:00"
    left = {**row, "extracted_at": stamp, "extractor_version": "j28.1",
            "analysis_run_id": "zzzzzzzz-0000-0000-0000-000000000000"}
    right = {**row, "extracted_at": stamp, "extractor_version": "j28.1",
             "analysis_run_id": "aaaaaaaa-0000-0000-0000-000000000000"}
    assert storage._standing(left) == storage._standing(right)


def test_both_identical_attempts_survive_the_authority():
    """The standing set is one reading; the ledger is not. Nothing was overwritten."""
    row = storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p",
                                  analysis_run_id="run-a")[0]
    first = {**row, "extracted_at": "2026-09-27T00:00:00+00:00"}
    second = {**row, "analysis_run_id": "run-b",
              "extracted_at": "2026-09-27T01:00:00+00:00"}

    ledger = [first, second]
    standing = storage.authoritative_occurrences(ledger)

    assert len(standing) == 1
    assert standing[0]["analysis_run_id"] == "run-b"
    # The earlier attempt is still in the ledger and was never rewritten.
    assert len(ledger) == 2
    assert ledger[0] == first
    assert ledger[0]["analysis_run_id"] == "run-a"


def test_two_rule_sets_over_one_page_stay_distinguishable():
    """A re-extraction under new rules APPENDS; it does not replace the earlier reading."""
    row = storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p",
                                  analysis_run_id="run-a")[0]
    old_rules = {**row, "extracted_at": "2026-09-27T00:00:00+00:00",
                 "extractor_version": "j28.1"}
    new_rules = {**row, "analysis_run_id": "run-b",
                 "extracted_at": "2026-09-27T01:00:00+00:00",
                 "extractor_version": "j28.2"}

    standing = storage.authoritative_occurrences([old_rules, new_rules])
    assert [item["extractor_version"] for item in standing] == ["j28.2"]
    # The older rule set is still recorded, and still readable as its own reading.
    assert {item["extractor_version"] for item in (old_rules, new_rules)} == {"j28.1", "j28.2"}


def test_the_layer_consults_no_parse_state_and_carries_no_attempt_counter():
    """A reading is recorded because it was taken, not because a page parsed.

    Annotation extraction is independent of the model's reading: a page whose AI response
    could not be parsed is still a page whose drawn marks can be read, and the retry that
    re-reads it brings its own run. Nothing in this layer asks whether the page parsed.
    """
    source = code_only(storage.__file__)
    for forbidden in ("parse_failed", "parse_failure", "is_current", "superseded",
                      "supersedes", "attempt_number", "is_latest"):
        assert forbidden not in source, f"the storage layer's code names {forbidden}"


def test_no_mutable_authority_field_was_introduced():
    """Standing is a read-time selection. A stored flag would be a second authority."""
    sql = (REPO / "supabase" / "migrations"
           / "20260927000000_j28_pdf_annotation_occurrences.sql").read_text()
    statements = "\n".join(line for line in sql.splitlines()
                           if not line.strip().startswith("--"))
    for forbidden in ("is_current", "superseded_by", "supersedes", "attempt_number",
                      "is_latest"):
        assert forbidden not in statements, f"the migration's SQL names {forbidden}"


def test_the_attempt_is_provenance_and_not_a_second_document_identity():
    """The run names the attempt; the drawing and the digest still name the document."""
    row = storage.occurrence_rows(synthetic_evidence(), drawing_id="d", project_id="p",
                                  analysis_run_id="run-a")[0]
    assert row["drawing_id"] == "d"
    assert row["project_id"] == "p"
    assert row["analysis_run_id"] == "run-a"
    assert row["source_pdf_sha256"] == "a" * 64
    # No drawing set, no model, no parse flag and no owner column was added alongside it.
    for name in ("drawing_set_id", "model", "parse_failed", "user_id", "owner", "is_current"):
        assert name not in row, name
        assert name not in storage.ANNOTATION_COLUMNS, name


def test_the_storage_layer_is_pure_and_holds_no_client():
    source = code_only(storage.__file__)
    for forbidden in ("supabase", "requests", "httpx", "openai", "anthropic", "socket",
                      "environ", "urllib"):
        assert forbidden not in source, f"the storage layer's code names {forbidden}"


# =============================================================================
# H. Who the layer is wired to — and, after J36, that it is exactly one caller
# =============================================================================
# J28 built this boundary with no consumer at all and these two tests asserted that
# absence. J36 is the milestone that consumed it, so the assertions below state the
# CONSUMERS the wiring produced instead: one read surface, one write transport and exactly
# one caller of it. The point of pinning them exactly is unchanged — a further consumer
# cannot appear without being declared here.
def test_the_storage_layers_consumers_are_exactly_these_three():
    """Who reaches the annotation evidence, by import graph rather than by prose.

    J28 left this boundary with no consumer and this test asserted that. J36 changed it:
    the extraction pipeline now reads the PDF's own annotations beside the AI's, so
    `app/pipeline.py` appears in both lists — once for the storage layer it builds rows
    with, once for the extractor it reads them from. The assertion stays exact, so any
    further consumer still has to be declared here, and the writer's single caller is
    proved separately below.
    """
    app = REPO / "app"
    importers = []
    extractor_importers = []
    for path in sorted(app.rglob("*.py")):
        text = path.read_text()
        relative = str(path.relative_to(REPO))
        if "pdf_annotation_evidence" in text:
            importers.append(relative)
        if "pdf_annotation_extractor" in text:
            extractor_importers.append(relative)

    # The two WRITE-side consumers: `repository.py` owns the table's transport, and
    # `pipeline.py` is the one place a run goes through. `production_annotation_evidence.py`
    # is the J29 READ surface — it takes the table, the column list and the rule for which
    # reading stands for a page from the storage layer rather than restating them, and
    # writes nothing.
    assert importers == [
        "app/engineering_data/repository.py",
        "app/pipeline.py",
        "app/production_annotation_evidence.py",
    ], f"the storage layer is imported by {importers}"

    # The extractor has exactly two importers: the layer that records what it reports, and
    # the pipeline that asks it for a reading. No route and no other production module
    # reaches it directly — the only thing that has ever seen an occurrence is one of these.
    assert extractor_importers == [
        "app/engineering_data/pdf_annotation_evidence.py",
        "app/pipeline.py",
    ], extractor_importers


def test_the_write_transport_has_exactly_one_caller():
    """The storage boundary is complete and callable, and J36 gave it one caller."""
    repository = (REPO / "app" / "engineering_data" / "repository.py").read_text()
    assert "def insert_pdf_annotation_occurrences" in repository

    callers = []
    for path in sorted((REPO / "app").rglob("*.py")):
        if path.name == "repository.py":
            continue
        if "insert_pdf_annotation_occurrences" in path.read_text():
            callers.append(str(path.relative_to(REPO)))
    # Exactly one, and it is the sidecar the four extraction paths call. A second caller
    # would be a second place a reading is written from, which is what this pins against.
    assert callers == ["app/pipeline.py"], f"the writer is called from {callers}"

    # And that caller holds the only reference: the pipeline reaches the writer through the
    # `repo` module object, never by importing the function itself, so the call cannot be
    # smuggled past a double.
    pipeline = (REPO / "app" / "pipeline.py").read_text(encoding="utf-8")
    assert "repo.insert_pdf_annotation_occurrences(" in pipeline
    assert "insert_pdf_annotation_occurrences,\n" not in pipeline


def test_the_migration_is_the_append_only_house_shape_and_grants_no_browser_role():
    """The migration mirrors J23's shape exactly, and the risks it closes are named."""
    sql = (REPO / "supabase" / "migrations"
           / "20260927000000_j28_pdf_annotation_occurrences.sql").read_text()

    assert "create table if not exists public.pdf_annotation_occurrences" in sql
    assert "enable row level security" in sql
    assert "revoke all on table public.pdf_annotation_occurrences from public, anon, authenticated" in sql
    assert "grant insert, select on table public.pdf_annotation_occurrences to service_role" in sql
    assert "revoke update, delete, truncate on table public.pdf_annotation_occurrences from service_role" in sql
    assert "create policy" not in sql, "no policy may admit a browser role"
    assert "errcode = 'P0001'" in sql
    assert "on delete restrict" in sql
    assert "notify pgrst, 'reload schema'" in sql

    # The occurrence key, and the absence of any member identity. Comments are removed
    # first: the header SAYS there is no `is_steel_member` column, in those words.
    statements = "\n".join(line for line in sql.splitlines()
                           if not line.strip().startswith("--"))
    # Milestone J33 — the attempt is part of the identity, and it is the identity J23
    # already uses rather than a second one. Declared here as one line so a key that lost
    # the run, or grew a member column, could not read as a pass.
    assert ("primary key (drawing_id, page_number, analysis_run_id, annotation_x, "
            "annotation_y,\n                     extractor_version)") in statements
    assert ("foreign key (analysis_run_id) references public.analysis_runs (id) "
            "on delete restrict") in statements
    assert "analysis_run_id     uuid        not null" in statements
    for forbidden in ("member_id", "placement_id", "is_steel_member", "same_member",
                      "steel_members"):
        assert forbidden not in statements, f"the migration's SQL names {forbidden}"


def test_the_extractor_reads_the_pdf_and_nothing_else():
    """No network, no database, no model. Checked against the module's own code."""
    source = code_only(extractor.__file__)
    for forbidden in ("supabase", "requests", "httpx", "openai", "anthropic", "socket",
                      "urllib", "environ"):
        assert forbidden not in source, f"the extractor's code names {forbidden}"


def test_the_extractor_uses_the_pinned_pdf_dependency_only():
    """pypdf, and none of the PDF libraries the brief forbids."""
    source = code_only(extractor.__file__)
    for forbidden in ("fitz", "PyMuPDF", "pdfplumber", "pikepdf", "PyPDF2"):
        assert forbidden not in source, f"the extractor's code names {forbidden}"


# =============================================================================
# I. The real Arkles set — the acceptance proof
# =============================================================================
@pytest.fixture(scope="module")
def arkles():
    """One genuine reading of the 11 pages the J28 brief names.

    Read once and shared: the extraction takes about a minute and a half on a real
    11-page lodged set, so re-reading per test would cost more than it proves.
    """
    if not REAL_PDF.exists():  # pragma: no cover - guarded by needs_real_pdf
        pytest.skip("the real Arkles PDF is not present on this machine")
    return read(REAL_PDF, pages=ACCEPTANCE_PAGES)


@needs_real_pdf
def test_real_the_document_reads_as_one_reading_with_its_own_identity(arkles):
    assert arkles.source_pdf_sha256 == hashlib.sha256(REAL_PDF.read_bytes()).hexdigest()
    assert arkles.pages_extracted == ACCEPTANCE_PAGES
    assert arkles.extractor_version == extractor.EXTRACTOR_VERSION
    assert len(arkles.occurrences) > 300


@needs_real_pdf
def test_real_p4_is_repeated_across_the_set_at_distinct_coordinates(arkles):
    """(1) and (2) of the brief: P4 recurs, and every recurrence is its own occurrence."""
    p4 = [o for o in arkles.occurrences if o.mark_candidate == "P4"]
    pages = sorted({o.page_number for o in p4})
    by_x = {}
    for occurrence in p4:
        by_x.setdefault(occurrence.annotation_x, []).append(occurrence.annotation_y)

    assert len(p4) >= 10, f"only {len(p4)} P4 occurrences"
    assert len(pages) >= 3, f"P4 appears on {pages} only"
    shared = {x: ys for x, ys in by_x.items() if len(ys) > 1}
    assert shared, "P4's occurrences share no x, so the repeated-position pattern is not present"
    assert len(p4) == len({(o.page_number, o.annotation_x, o.annotation_y) for o in p4})


@needs_real_pdf
def test_real_the_known_overprints_are_single_occurrences_with_the_copy_counted(arkles):
    """(3), (4) and (5) of the brief, at the coordinates the J28 proof measured."""
    overprints = [o for o in arkles.occurrences if o.duplicate_operator_count > 0]
    assert len(overprints) >= 2
    for occurrence in overprints:
        assert occurrence.operator_count >= 1

    on_page_11 = [o for o in overprints if o.page_number == 11]
    marks = sorted(o.raw_text_run for o in on_page_11)
    assert "BF2" in marks and "S202" in marks

    joined = "".join(o.raw_text_run for o in arkles.occurrences)
    assert "BF2BF2" not in joined
    assert "S202S202" not in joined


@needs_real_pdf
def test_real_a_mark_split_across_operators_is_reassembled(arkles):
    """(6) of the brief: `P` followed by `4` is recorded as P4."""
    split = [o for o in arkles.occurrences
             if o.operator_count > 1 and o.mark_candidate is not None]
    assert split, "no run on these pages was assembled from more than one operator"
    assert any(o.mark_candidate == "P4" and o.operator_count == 2 for o in split)
    # A split run's raw text is the whole run, so the operator structure is visible.
    assert all(o.raw_text_run for o in split)


@needs_real_pdf
def test_real_a_mark_and_its_section_size_are_not_merged_into_one_token(arkles):
    """(7) of the brief: `P4 300PFC` keeps P4 as its token and 300PFC in its run."""
    with_pfc = [o for o in arkles.occurrences if "PFC" in o.raw_text_run]
    assert with_pfc, "the section-size pairs the proof found are not present"
    assert not [o for o in arkles.occurrences if o.mark_candidate and "PFC" in o.mark_candidate]
    for occurrence in with_pfc:
        assert occurrence.mark_candidate is not None
        assert occurrence.mark_candidate.strip() in occurrence.raw_text_run


@needs_real_pdf
def test_real_y3y1_is_not_declared_a_designation(arkles):
    """(8) of the brief: the raw run exists and no token claims to be the whole string."""
    runs = [o for o in arkles.occurrences if o.raw_text_run.strip() == "Y3Y1"]
    assert runs, "the Y3Y1 run the proof found is not present on these pages"
    assert not [o for o in arkles.occurrences if o.mark_candidate == "Y3Y1"]


@needs_real_pdf
def test_real_grammar_false_positives_are_candidates_and_nothing_more(arkles):
    """(9) of the brief: these are present, and no field promotes any of them."""
    candidates = {o.mark_candidate for o in arkles.occurrences}
    for shape in ("S207", "X4", "W01", "SG8", "SDM049"):
        assert shape in candidates, f"{shape} is not recorded as a candidate at all"

    source = code_only(extractor.__file__)
    assert "is_steel_member" not in source
    assert "steel_members" not in source


@needs_real_pdf
def test_real_schedule_columns_are_evidence_and_schedule_less_pages_are_unknown(arkles):
    """(10) and (11) of the brief."""
    assert arkles.pages_with_schedule_column == (6, 8, 9, 10, 11, 21)

    in_column = [o for o in arkles.occurrences if o.schedule_row_candidate is True]
    assert in_column
    assert sorted({o.page_number for o in in_column}) == [6, 8, 9, 10, 11, 21]

    for page in (15, 23, 29, 35, 36):
        on_page = [o for o in arkles.occurrences if o.page_number == page]
        assert on_page
        assert all(o.schedule_row_candidate is None for o in on_page), (
            f"page {page} has no schedule column, so its occurrences must be unknown"
        )


@needs_real_pdf
def test_real_pages_11_and_21_carry_more_than_one_schedule_column(arkles):
    """'One schedule per page' is false, and the real set says so."""
    import pypdf

    reader = pypdf.PdfReader(str(REAL_PDF))
    for page_number in (11, 21):
        runs = extractor.reassemble_runs(
            extractor.deduplicate_operators(
                extractor.text_operators(reader.pages[page_number - 1], page_number))[0])
        marks = [run for run in runs if extractor.classify_run(run) is not None
                 and extractor.classify_run(run)[0] is not None]
        assert len(extractor.schedule_columns(marks)) >= 2, (
            f"page {page_number} was expected to carry two schedule columns"
        )


@needs_real_pdf
def test_real_no_occurrence_claims_anything_about_a_member(arkles):
    """The hard acceptance criterion, over every genuine occurrence."""
    forbidden = ("member", "placement", "physical", "steel", "same_", "different_")
    for occurrence in arkles.occurrences:
        payload = extractor.occurrence_payload(occurrence)
        assert not [name for name in payload if any(word in name for word in forbidden)]
        assert occurrence.annotation_x >= 0
        assert occurrence.annotation_y >= 0
        assert occurrence.page_number in ACCEPTANCE_PAGES


@needs_real_pdf
def test_real_the_pages_and_occurrences_are_what_the_reading_reports(arkles):
    """(14) in structural form: the reading touches no member row, because it can reach none."""
    assert sorted({o.page_number for o in arkles.occurrences}) == list(ACCEPTANCE_PAGES)
    # Every occurrence's coordinate is distinct on its page: the reading did not
    # collapse two positions into one, and it did not fabricate a position.
    coordinates = [(o.page_number, o.annotation_x, o.annotation_y) for o in arkles.occurrences]
    assert len(coordinates) == len(set(coordinates))


@needs_real_pdf
def test_real_the_same_pdf_reads_byte_identically_twice(arkles):
    """Determinism on the genuine document, not only on a fixture."""
    again = read(REAL_PDF, pages=ACCEPTANCE_PAGES)
    assert extractor.canonical_json(arkles) == extractor.canonical_json(again)
