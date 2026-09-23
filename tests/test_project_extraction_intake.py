"""
Milestone 7Y — tests for the intake seam between the REAL project AI extraction
output and the 7X project review queue (app.cad_engine.project_extraction_intake).

WHAT IS REAL AND WHAT IS NOT (read this first):

  REAL (captured from the actual Arkles Strand project):
    - `ARKLES_STRAND_RAW` (tests/test_arkles_strand.py): 54 MEMBER rows pulled from the live
      Supabase project. It is the repository's only captured real AI extraction, and it contains
      MEMBERS ONLY — no connection data of any kind. It is used here for real known member marks,
      and nothing else.
    - The real PDF `24633 33 Arkles Strand - Plans 17.08.26 (lodged).pdf` (in ~/Downloads, outside
      the repository), used only to read its page count.

  NOT AVAILABLE: a captured real AI CONNECTION extraction. The repository has none, and it cannot
  be produced here: analyze_pdf_pages() needs ANTHROPIC_API_KEY, Supabase credentials and poppler,
  and writes every page image to Supabase storage. This file therefore does NOT claim the Arkles AI
  "found zero connections" — it says no connection extraction has been captured. It never creates
  connection candidates under the Arkles name.

  DROP-IN: once a real extraction is captured (see CAPTURE_HOWTO), the gated real-capture tests
  below run automatically against it, using only the captured values (nothing hard-coded).

  SYNTHETIC: every other page extraction here is an isolated unit-test fixture of the seam. Its pages
  and connections are labelled SYNTHETIC, use the actual AI schema's keys, were not produced by any AI,
  and contain no engineering data anyone should rely on.

CAPTURE_HOWTO (needs credentials + poppler; not run by the tests; will call the vision API and, unless
patched, upload page images to Supabase storage):

    import dataclasses, json
    from app.ai_analysis.pdf_vision_analyzer import analyze_pdf_pages
    pages = analyze_pdf_pages(PDF_PATH, user_id, project_id, drawing_id, max_pages)
    json.dump([dataclasses.asdict(p) for p in pages], open(CAPTURE_PATH, "w"), indent=2)
"""
import ast
import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pypdf
import pytest

import app.cad_engine.project_extraction_intake as intake_module
from app.cad_engine.connection_review_package import (
    REVIEW_STATE_AI_EXTRACTED,
    ai_connection_to_extraction,
    build_reviewed_connection_specification,
)
from app.cad_engine.project_connection_review import (
    STATE_BLOCKED,
    build_project_review_summary,
    ordered_candidates,
    ready_reviewed_connections,
)
from app.cad_engine.project_extraction_intake import (
    INTAKE_SCOPE_STATEMENT,
    intake_page_extractions,
    known_member_marks_from_validated_members,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)
from app.validation.rules import validate_extraction
from tests.test_arkles_strand import ARKLES_STRAND_RAW, RealisticFakeMatcher

REPO = Path(__file__).resolve().parent.parent
CAPTURE_PATH = REPO / "tests" / "data" / "arkles_strand_page_extractions.json"   # where a real capture belongs
REAL_PDF = Path("/Users/chad/Downloads/24633 33 Arkles Strand - Plans 17.08.26 (lodged).pdf")

needs_real_capture = pytest.mark.skipif(
    not CAPTURE_PATH.exists(),
    reason=f"No captured real Arkles AI CONNECTION extraction exists at {CAPTURE_PATH.relative_to(REPO)}. The "
           "repository holds real member rows only; producing a real connection extraction needs credentials, "
           "poppler and the vision API (see CAPTURE_HOWTO). This is NOT a claim that the AI found no connections.",
)
needs_real_pdf = pytest.mark.skipif(not REAL_PDF.exists(), reason="the real Arkles PDF is not present on this machine")


# -----------------------------------------------------------------------------
# Real member data -> real known member marks (used by real and synthetic tests)
# -----------------------------------------------------------------------------
def real_validated_members():
    return validate_extraction(copy.deepcopy(ARKLES_STRAND_RAW), RealisticFakeMatcher())["members"]


def real_known_marks():
    return known_member_marks_from_validated_members(real_validated_members())


def load_capture(path):
    data = json.loads(Path(path).read_text())
    assert isinstance(data, list) and all(isinstance(p, dict) for p in data), "a capture is a list of page objects"
    return data


def flatten_reference(pages):
    """The reference flattening — deliberately independent of the module under test (app/pipeline.py's expression)."""
    flat = []
    for p in pages:
        get = (lambda k, p=p: p.get(k)) if isinstance(p, dict) else (lambda k, p=p: getattr(p, k, None))
        for c in get("raw_connections") or []:
            if isinstance(c, dict):
                flat.append({**c, "page_num": get("page_number")})
    return flat


def assert_honest_queue(pages, known):
    """
    Data-driven invariants for ANY page-extraction stream (a real capture, or a synthetic one in a unit test).
    Nothing here is hard-coded: every expectation is computed from the supplied pages.
    """
    before = copy.deepcopy(pages) if isinstance(pages, list) and pages and isinstance(pages[0], dict) else None
    intake = intake_page_extractions(pages, project_id="P-INTAKE", source_drawing_id="D-INTAKE", known_member_marks=known)
    summary = build_project_review_summary(intake.collection)
    expected = flatten_reference(pages)

    assert summary.total_candidates == len(expected)                     # exactly the AI's candidates, none added
    assert len(intake.collection.candidates) == len(expected)
    for candidate, raw in zip(intake.collection.candidates, expected):
        assert candidate.extraction == ai_connection_to_extraction(
            raw, project_id="P-INTAKE", source_drawing_id="D-INTAKE",
            drawing_number=next((p.get("drawing_number") if isinstance(p, dict) else getattr(p, "drawing_number", None)
                                 for p in pages if (p.get("page_number") if isinstance(p, dict)
                                                    else getattr(p, "page_number", None)) == 1), None))
        assert candidate.source_identity is None                          # no connection identity invented
        spec = build_reviewed_connection_specification(candidate.package)
        assert spec.connection_id is None and spec.position is None      # no START/END, no id
        assert spec.location is None and spec.attachments == [] and spec.holes is None
        assert set(spec.provenance.values()) <= {PROVENANCE_AI_EXTRACTED}  # no human provenance on AI values
    for r in summary.rows:
        assert r.state in {REVIEW_STATE_AI_EXTRACTED, STATE_BLOCKED}
        assert {"position", "holes", "location", "attachments"} <= set(r.report.missing)
    assert summary.ready_for_7v == 0 and ready_reviewed_connections(intake.collection) == ()
    assert summary.human_supplemented == summary.human_reviewed == 0
    assert summary.provenance_counts[PROVENANCE_HUMAN_REVIEWED] == summary.provenance_counts[
        PROVENANCE_HUMAN_SUPPLEMENTED] == 0
    assert summary.member_context_supplied is (known is not None)
    if before is not None:
        assert pages == before                                            # the extraction record is unchanged
    return intake, summary


# =============================================================================
# REAL DATA — what genuinely exists for Arkles Strand
# =============================================================================
def test_real_arkles_member_extraction_supplies_real_known_member_marks():
    real_rows = ARKLES_STRAND_RAW
    members = real_validated_members()
    known = known_member_marks_from_validated_members(members)

    assert known and len(set(known)) == len(known)                        # real marks, no duplicates
    assert set(known) == {m["mark"] for m in members if m.get("mark")}    # exactly the validated members' marks
    real_raw_marks = [row["mark"] for row in real_rows]
    for mark in known:                                                    # nothing invented: each traces to a real row
        assert any(raw_mark.startswith(mark) for raw_mark in real_raw_marks), mark
    assert all(isinstance(mark, str) for mark in known)                   # marks, not a member count


def test_the_real_known_marks_use_the_pipelines_own_linking_convention():
    """Pins known-mark derivation to app/pipeline.py's mark_to_id (read as text; importing it needs SUPABASE_URL)."""
    source = (REPO / "app" / "pipeline.py").read_text()
    assert 'mark_to_id = {row["mark"]: row["id"] for row in inserted_members if row.get("mark")}' in source
    assert 'linked_ids = [mark_to_id[mark] for mark in (c.get("connects_members") or []) if mark in mark_to_id]' in source


def test_the_repository_holds_real_member_rows_only_and_no_captured_connection_extraction():
    """States the real situation plainly, so it cannot be mistaken for 'the AI found no connections'."""
    for row in ARKLES_STRAND_RAW:
        assert not {"connects_members", "bolts", "plates", "welds", "connection_type"} & set(row)   # members only
    if not CAPTURE_PATH.exists():
        # No real connection candidates exist to review. Nothing is manufactured to fill the gap:
        # the queue built from the captured real extraction is empty because NO CONNECTION EXTRACTION WAS CAPTURED.
        summary = build_project_review_summary(intake_page_extractions(
            [], known_member_marks=real_known_marks()).collection)
        assert summary.total_candidates == 0 and summary.member_context_supplied is True
        assert "does not claim" in summary.scope_statement
    else:
        assert load_capture(CAPTURE_PATH), "a capture file exists but is empty"


@needs_real_pdf
def test_the_real_pdf_has_more_pages_than_the_pipeline_analyses_and_the_real_members_cite_real_pages():
    page_count = len(pypdf.PdfReader(str(REAL_PDF)).pages)
    config_text = (REPO / "app" / "config.py").read_text()      # read as text: importing app.config needs SUPABASE_URL
    assert 'MAX_PDF_PAGES = int(os.environ.get("MAX_PDF_PAGES", "30"))' in config_text
    default_cap = 30
    assert max(row["source_page"] for row in ARKLES_STRAND_RAW) <= page_count       # cited pages exist in the PDF
    assert page_count > default_cap                                                 # the real PDF outruns the default cap
    # Coverage honesty: with `default_cap` pages analysed, the rest are reported as never read, not as empty.
    analysed = [{"page_number": n, "raw_connections": []} for n in range(1, default_cap + 1)]
    intake = intake_page_extractions(analysed, drawing_set_page_count=page_count)
    assert intake.pages_not_analysed == page_count - default_cap > 0
    assert "never analysed" in intake.scope_statement


# =============================================================================
# REAL CAPTURE — runs automatically once a real connection extraction is captured
# =============================================================================
@needs_real_capture
def test_real_capture_reaches_the_review_queue_honestly():
    pages = load_capture(CAPTURE_PATH)
    intake, summary = assert_honest_queue(pages, real_known_marks())
    assert intake.pages_received == len(pages)
    assert summary.total_candidates == sum(
        1 for c in flatten_reference(pages))          # however many the AI found — zero is a valid real result


@needs_real_capture
def test_real_capture_without_project_member_context_uses_the_existing_missing_context_behaviour():
    intake, summary = assert_honest_queue(load_capture(CAPTURE_PATH), None)
    assert summary.member_context_supplied is False
    assert all(r.report.current_unknown_member_marks == () for r in summary.rows)


@needs_real_capture
def test_real_capture_preserves_every_ai_field_it_contains():
    pages = load_capture(CAPTURE_PATH)
    intake = intake_page_extractions(pages, known_member_marks=real_known_marks())
    for candidate, raw in zip(intake.collection.candidates, flatten_reference(pages)):
        ex = candidate.extraction
        assert ex.source_page == raw["page_num"] and ex.confidence == raw.get("confidence")
        assert ex.detail_reference == raw.get("detail_reference") and ex.grid_reference == raw.get("grid_reference")
        assert list(ex.connected_member_references) == list(raw.get("connects_members") or []) or \
            "connects_members" in ex.malformed_fields
        for bolt in ex.bolts:                                            # nominal sizes stay nominal strings/values as reported
            assert bolt.size == next((b.get("size") for b in raw.get("bolts", []) if b.get("size") == bolt.size), None)


# =============================================================================
# THE SEAM — unit tests on SYNTHETIC page extractions (never named Arkles)
# =============================================================================
def synthetic_connection(marks, detail, grid=None, size="M20", confidence=72, **extra):
    """A SYNTHETIC AI-shaped connection object (actual schema keys). Not produced by any AI."""
    conn = {"detail_reference": detail, "grid_reference": grid, "connects_members": list(marks),
            "connection_type": "bolted", "bolts": [{"quantity": 4, "size": size, "grade": "8.8"}],
            "plates": [{"type": "end_plate", "thickness_mm": 12, "width_mm": 180, "depth_mm": 250}],
            "confidence": confidence}
    conn.update(extra)
    return conn


def synthetic_pages():
    """Three SYNTHETIC pages: attribute-style (like PageExtraction), a mapping, and a parse-failed page."""
    return [
        SimpleNamespace(page_number=1, drawing_number="S101", drawing_title="T", revision="A", raw_members=[],
                        raw_connections=[synthetic_connection(["B8", "P1"], "D1", "A/1"),
                                         synthetic_connection(["P2", "P3"], "D2", "B/2", size="M16", confidence=40)],
                        parse_failed=False),
        {"page_number": 2, "drawing_number": None, "raw_members": [], "raw_connections": None, "parse_failed": True},
        SimpleNamespace(page_number=5, drawing_number=None, raw_members=[],
                        raw_connections=[synthetic_connection(["L2", "L3"], "D3", None, size="M24", surprise="kept")],
                        parse_failed=False),
    ]


def test_synthetic_stream_goes_through_the_same_honest_invariants_as_the_real_capture_would(tmp_path):
    """Proves the shared assertion helper and the capture loader work, so the gated real tests are not untested code."""
    capture = tmp_path / "synthetic_capture.json"
    capture.write_text(json.dumps([dataclasses.asdict(p) if dataclasses.is_dataclass(p) else
                                   (vars(p) if isinstance(p, SimpleNamespace) else p) for p in synthetic_pages()]))
    intake, summary = assert_honest_queue(load_capture(capture), real_known_marks())
    assert summary.total_candidates == 3 and intake.parse_failed_pages == (2,)


def test_flattening_matches_the_pipelines_own_expression_and_the_real_page_extraction_fields():
    pipeline = (REPO / "app" / "pipeline.py").read_text()
    assert 'connections_raw.append({**c, "page_num": p.page_number})' in pipeline
    assert "for c in p.raw_connections" in pipeline
    tree = ast.parse((REPO / "app" / "ai_analysis" / "pdf_vision_analyzer.py").read_text())
    page_extraction = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "PageExtraction")
    fields = {n.target.id for n in page_extraction.body if isinstance(n, ast.AnnAssign)}
    assert {"page_number", "drawing_number", "raw_connections", "parse_failed"} <= fields   # the names the seam reads


def test_actual_ai_connection_candidates_become_independent_candidates_in_page_then_ai_order():
    intake = intake_page_extractions(synthetic_pages(), project_id="P", source_drawing_id="D")
    c = intake.collection.candidates
    assert [x.review_package_id for x in c] == ["RP-0001", "RP-0002", "RP-0003"]
    assert [x.extraction.detail_reference for x in c] == ["D1", "D2", "D3"]
    assert [x.extraction.source_page for x in c] == [1, 1, 5]
    assert len({id(x.package) for x in c}) == 3                                   # independent packages


def test_ai_fields_are_preserved_including_confidence_nominal_sizes_and_extra_fields():
    ex = intake_page_extractions(synthetic_pages()).collection.candidates[2].extraction
    assert ex.detail_reference == "D3" and ex.grid_reference is None
    assert ex.connected_member_references == ("L2", "L3") and ex.generic_connection_type == "bolted"
    assert ex.bolts[0].size == "M24" and ex.bolts[0].quantity == 4 and ex.bolts[0].grade == "8.8"
    assert ex.plates[0].thickness_mm == 12 and ex.confidence == 72
    assert ex.unrecognised_fields == {"surprise": "kept"}
    low = intake_page_extractions(synthetic_pages()).collection.candidates[1].extraction
    assert low.confidence == 40 and low.bolts[0].size == "M16"


def test_the_pipelines_page_number_wins_over_any_page_num_the_ai_itself_emitted():
    page = SimpleNamespace(page_number=7, drawing_number=None,
                           raw_connections=[synthetic_connection(["A", "B"], "D1", page_num=999)], parse_failed=False)
    assert intake_page_extractions([page]).collection.candidates[0].extraction.source_page == 7


def test_absent_source_information_stays_absent_not_invented():
    page = {"page_number": None, "raw_connections": [{"connects_members": ["A", "B"]}]}   # no detail, grid, page, anything
    intake = intake_page_extractions([page])
    ex = intake.collection.candidates[0].extraction
    assert (ex.source_page, ex.detail_reference, ex.grid_reference, ex.confidence) == (None, None, None, None)
    assert (ex.project_id, ex.source_drawing_id, ex.drawing_number) == (None, None, None)
    row = build_project_review_summary(intake.collection).rows[0]
    assert set(row.source_gaps) == {"source_page", "detail_reference", "grid_reference"}


def test_identity_comes_from_the_caller_and_the_drawing_number_from_page_one_only():
    intake = intake_page_extractions(synthetic_pages(), project_id="PROJECT-X", source_drawing_id="DRAWING-X")
    ex = intake.collection.candidates[2].extraction                                 # a candidate from page 5
    assert (ex.project_id, ex.source_drawing_id, ex.drawing_number) == ("PROJECT-X", "DRAWING-X", "S101")
    no_page_one = [SimpleNamespace(page_number=4, drawing_number="S999",
                                   raw_connections=[synthetic_connection(["A", "B"], "D1")], parse_failed=False)]
    assert intake_page_extractions(no_page_one).collection.candidates[0].extraction.drawing_number is None


def test_parse_failed_pages_are_recorded_and_never_treated_as_read_and_empty():
    intake = intake_page_extractions(synthetic_pages())
    assert intake.parse_failed_pages == (2,) and intake.pages_received == 3
    assert "was not read" in intake.scope_statement
    all_pages = [getattr(p, "page_number", None) if not isinstance(p, dict) else p["page_number"]
                 for p in synthetic_pages()]
    assert all_pages == [1, 2, 5]                                                   # page 2 contributed no candidate


def test_unusable_connection_entries_are_preserved_verbatim_and_never_become_candidates():
    pages = [{"page_number": 3, "raw_connections": [synthetic_connection(["A", "B"], "D1"), "junk", None, ["x"]]},
             {"page_number": 4, "raw_connections": "not-a-list"}]
    intake = intake_page_extractions(pages)
    assert len(intake.collection.candidates) == 1
    assert intake.unusable_connection_entries == ((3, "junk"), (3, None), (3, ["x"]), (4, "not-a-list"))


def test_pages_with_no_or_missing_connection_lists_yield_nothing_and_do_not_error():
    intake = intake_page_extractions([{"page_number": 1, "raw_connections": []}, {"page_number": 2},
                                      SimpleNamespace(page_number=3, raw_connections=None)])
    assert intake.collection.candidates == () and intake.pages_received == 3


def test_the_empty_extraction_is_an_honest_empty_queue():
    summary = build_project_review_summary(intake_page_extractions([], known_member_marks=["A"]).collection)
    assert summary.total_candidates == 0 and summary.ready_for_7v == 0
    assert "does not claim" in summary.scope_statement


def test_pages_not_analysed_is_only_reported_when_the_set_size_is_stated():
    assert intake_page_extractions(synthetic_pages()).pages_not_analysed is None            # never guessed
    assert intake_page_extractions(synthetic_pages(), drawing_set_page_count=41).pages_not_analysed == 38
    assert intake_page_extractions(synthetic_pages(), drawing_set_page_count=2).pages_not_analysed == 0  # never negative


def test_intake_scope_statement_makes_no_completeness_or_approval_claim():
    for phrase in ("never analysed", "does not mean", "does not claim", "connection-complete", "not engineering approval"):
        assert phrase in INTAKE_SCOPE_STATEMENT, phrase
    assert not [n for n in dir(intake_module) if "APPROVED" in n.upper() or "COMPLETE" in n.upper()]


# --- known project members ---
def test_known_member_marks_are_derived_by_the_pipelines_convention_and_never_invented():
    members = [{"mark": "B8"}, {"mark": "P1"}, {"mark": "P1"}, {"mark": ""}, {"mark": None}, {"section": "x"}, {"mark": "L2"}]
    assert known_member_marks_from_validated_members(members) == ("B8", "P1", "L2")
    assert known_member_marks_from_validated_members([]) == ()
    assert known_member_marks_from_validated_members(None) is None                # no context is not "no members"


def test_known_marks_reach_7x_and_7w_decides_which_ai_marks_are_unknown():
    known = real_known_marks()
    pages = [{"page_number": 1, "raw_connections": [synthetic_connection([known[0], known[1]], "D1"),
                                                     synthetic_connection([known[0], "GHOST"], "D2")]}]
    intake = intake_page_extractions(pages, known_member_marks=known)
    summary = build_project_review_summary(intake.collection)
    assert intake.collection.known_member_marks == known
    by_detail = {r.detail_reference: r for r in summary.rows}
    assert by_detail["D1"].report.current_unknown_member_marks == ()
    assert by_detail["D2"].report.current_unknown_member_marks == ("GHOST",) and by_detail["D2"].state == STATE_BLOCKED
    assert not hasattr(intake_module, "_unknown_marks")                             # no second copy of the rule


def test_missing_known_member_context_uses_the_existing_behaviour_and_flags_nothing_as_unknown():
    intake = intake_page_extractions(synthetic_pages(), known_member_marks=None)
    summary = build_project_review_summary(intake.collection)
    assert summary.member_context_supplied is False and summary.member_references_unchecked == 3
    assert all(r.report.current_unknown_member_marks == () for r in summary.rows)


# --- the queue's honest state ---
def test_a_synthetic_ai_stream_reaches_the_queue_with_nothing_invented_and_nothing_ready():
    intake, summary = assert_honest_queue(synthetic_pages(), real_known_marks())
    assert summary.total_candidates == 3 and summary.ready_for_7v == 0
    assert summary.missing_information == 3 and summary.human_supplemented == 0


def test_nominal_bolt_sizes_never_become_hole_diameters_anywhere_in_the_queue():
    intake = intake_page_extractions(synthetic_pages())
    for candidate in intake.collection.candidates:
        spec = build_reviewed_connection_specification(candidate.package)
        assert spec.holes is None and "holes" not in spec.provenance
    for row in build_project_review_summary(intake.collection).rows:
        assert "hole_diameter" in row.report.missing or "holes" in row.report.missing
        assert "not a hole diameter" in row.report.advisories["bolts"]


def test_ordering_is_deterministic_and_carries_no_engineering_meaning():
    a = intake_page_extractions(synthetic_pages())
    b = intake_page_extractions(synthetic_pages())
    assert build_project_review_summary(a.collection) == build_project_review_summary(b.collection)
    shuffled = list(reversed(synthetic_pages()))
    forward = [c.extraction.detail_reference for c in ordered_candidates(a.collection)]
    backward = [c.extraction.detail_reference for c in ordered_candidates(intake_page_extractions(shuffled).collection)]
    assert forward == backward == ["D1", "D2", "D3"]                            # presentation order follows source facts
    for c in a.collection.candidates:                                            # ...never START/END, attachment or location
        spec = build_reviewed_connection_specification(c.package)
        assert spec.position is None and spec.attachments == [] and spec.location is None


def test_no_human_provenance_is_assigned_to_ai_values():
    summary = build_project_review_summary(intake_page_extractions(synthetic_pages(), known_member_marks=real_known_marks()).collection)
    assert summary.provenance_counts[PROVENANCE_HUMAN_REVIEWED] == 0
    assert summary.provenance_counts[PROVENANCE_HUMAN_SUPPLEMENTED] == 0
    assert summary.provenance_counts[PROVENANCE_AI_EXTRACTED] > 0
    assert {r.state for r in summary.rows} <= {REVIEW_STATE_AI_EXTRACTED, STATE_BLOCKED}


def test_the_real_extraction_record_is_never_mutated_and_later_edits_cannot_change_the_queue():
    pages = synthetic_pages()
    snapshot = copy.deepcopy([vars(p) if isinstance(p, SimpleNamespace) else p for p in pages])
    intake = intake_page_extractions(pages, known_member_marks=real_known_marks())
    build_project_review_summary(intake.collection)
    assert [vars(p) if isinstance(p, SimpleNamespace) else p for p in pages] == snapshot
    pages[0].raw_connections[0]["bolts"][0]["size"] = "M99"
    pages[0].raw_connections[0]["confidence"] = 1
    ex = intake.collection.candidates[0].extraction
    assert ex.bolts[0].size == "M20" and ex.confidence == 72


# --- no CAD, no drawings ---
def test_no_cad_or_drawing_code_is_called_by_the_intake(monkeypatch):
    import app.cad_engine.reviewed_connection_assembly as assembly_module
    import app.cad_engine.reviewed_connection_drawing_gate as gate_module
    import app.cad_engine.two_member_connection as two_member_module
    import app.drawing_generator.interface as drawing_interface
    import app.drawing_generator.pdf_builder as pdf_builder

    def forbidden(*args, **kwargs):
        raise AssertionError("7Y must not generate CAD or drawings")

    for module, name in [
        (assembly_module, "build_reviewed_two_member_connection_assembly"),
        (two_member_module, "build_two_member_connection_assembly"),
        (gate_module, "generate_fabrication_drawing_from_reviewed_assembly"),
        (drawing_interface, "generate_connection_fabrication_drawing_pdf"),
        (pdf_builder, "build_connection_pdf"),
    ]:
        monkeypatch.setattr(module, name, forbidden)
    intake = intake_page_extractions(synthetic_pages(), known_member_marks=real_known_marks())
    build_project_review_summary(intake.collection)
    assert ready_reviewed_connections(intake.collection) == ()


def test_the_intake_module_imports_only_the_7x_review_layer_and_the_standard_library():
    tree = ast.parse(Path(intake_module.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {"copy", "collections.abc", "dataclasses", "typing", "app.cad_engine.project_connection_review"}
