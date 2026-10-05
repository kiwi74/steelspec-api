"""
Unit tests for the validation module, one per rule, using small
synthetic fixtures so a failure points at exactly which rule broke —
separate from test_arkles_strand.py, which validates against the
real dataset end to end.
"""
from app.validation.rules import (
    validate_extraction, classify_member, fold_mark_format_anomalies,
    is_timber_or_non_steel, consolidate_members, check_missing_lengths,
)


class FakeMatcher:
    """A SectionMatcher test double — no Supabase connection required."""
    _sections = {
        "250UB37.3": {"name": "250UB37.3", "family": "UB", "weight_per_metre": 37.3},
        "310UB40.4": {"name": "310UB40.4", "family": "UB", "weight_per_metre": 40.4},
        "200UC46.2": {"name": "200UC46.2", "family": "UC", "weight_per_metre": 46.2},
        "250PFC": {"name": "250PFC", "family": "PFC", "weight_per_metre": 35.5},
        "100PFC": {"name": "100PFC", "family": "PFC", "weight_per_metre": 8.3},
        "300PFC": {"name": "300PFC", "family": "PFC", "weight_per_metre": 40.1},
    }

    def match(self, raw_name: str):
        key = raw_name.strip().upper().replace(" ", "")
        if key in self._sections:
            return self._sections[key]
        # Mimic the real matcher's trailing-decimal completion (e.g. "250UB37" -> "250UB37.3")
        for k, v in self._sections.items():
            if k.replace(".", "").startswith(key.replace(".", "")):
                return v
        return None


# === Clean case: unique marks, everything matches, nothing to flag ===
def test_clean_extraction_produces_no_issues():
    raw = [
        {"mark": "B1", "section": "250UB37", "source_page": 1, "length_mm": 5000, "confidence": 95},
        {"mark": "B2", "section": "310UB40", "source_page": 1, "length_mm": 6000, "confidence": 95},
    ]
    result = validate_extraction(raw, FakeMatcher())
    assert len(result["members"]) == 2
    assert len(result["excluded"]) == 0
    # No conflicts, no contamination, no missing-length warning (0% missing here)
    assert not any(i.rule == "CONFLICTING_MEMBER_DEFINITION" for i in result["issues"])
    assert not any(i.rule == "MATERIAL_CONTAMINATION_WARNING" for i in result["issues"])


# === Rule: timber contamination ===
def test_timber_keywords_are_excluded_not_deleted_silently():
    raw = [
        {"mark": "M10", "section": "2/190x45 SG8 H3.2", "source_page": 7, "confidence": 75},
        {"mark": "RB1", "section": "300x90 Hyone", "source_page": 28, "confidence": 82},
        {"mark": "B1", "section": "250UB37", "source_page": 1, "length_mm": 5000, "confidence": 95},
    ]
    result = validate_extraction(raw, FakeMatcher())
    excluded_marks = {e["mark"] for e in result["excluded"]}
    assert excluded_marks == {"M10", "RB1"}
    # Excluded from the steel schedule entirely
    member_marks = {m["mark"] for m in result["members"]}
    assert "M10" not in member_marks and "RB1" not in member_marks
    assert "B1" in member_marks
    # But surfaced as a warning, not silently dropped
    assert any(i.rule == "MATERIAL_CONTAMINATION_WARNING" for i in result["issues"])


# === Rule: timber contamination — AS/NZS softwood stress grades (F-grades) ===
def test_f_graded_timber_is_excluded_not_persisted_as_steel():
    """A graded softwood callout states none of the keyword list, so it used to
    read as unmatched STEEL and persist as a member. It is timber."""
    raw = [
        {"mark": "RB3", "section": "3/240x45 F27", "source_page": 21, "confidence": 70},
        {"mark": "RB4", "section": "2/200x45 F27", "source_page": 21, "confidence": 70},
        {"mark": "B1", "section": "250UB37", "source_page": 1, "length_mm": 5000, "confidence": 95},
    ]
    result = validate_extraction(raw, FakeMatcher())
    assert {e["mark"] for e in result["excluded"]} == {"RB3", "RB4"}
    member_marks = {m["mark"] for m in result["members"]}
    assert "RB3" not in member_marks and "RB4" not in member_marks
    assert "B1" in member_marks
    # Surfaced as a warning, exactly as the keyword path already does.
    contamination = [i for i in result["issues"] if i.rule == "MATERIAL_CONTAMINATION_WARNING"]
    assert len(contamination) == 1
    assert "RB3" in contamination[0].message and "RB4" in contamination[0].message


def test_the_graded_timber_family_is_recognised():
    for section in [
        "3/240x45 F27", "2/200x45 F27", "240x45 F27", "200x45F27",
        "190x45 F8", "140x45 F17", "190x45 F11", "90x45 F4", "240x45 F34",
        "2/190x45 F8 KDHW",
    ]:
        assert is_timber_or_non_steel(section) is True, section


def test_an_f_number_alone_is_a_member_mark_not_a_timber_grade():
    """The reason the grade must be anchored to a section size: this project's
    own corpus carries STEEL members marked F1, F2, F3, F6, F11 and F12, and an
    unanchored F-number would turn every one of them into timber."""
    for token in ["F1", "F2", "F3", "F6", "F11", "F12", "F27", "F34",
                  "mark F11", "F11 (typ)"]:
        assert is_timber_or_non_steel(token) is False, token
        assert classify_member(token, None) == "unmatched_steel", token


def test_an_unanchored_grade_stays_conservative():
    """A section stating only the grade, with no size, is not enough to call it
    timber — the same conservatism the keyword list has always had."""
    assert classify_member("F27", None) == "unmatched_steel"
    assert classify_member("Grade F27", None) == "unmatched_steel"


def test_steel_callouts_containing_f_like_tokens_are_not_timber():
    for section in [
        "250UB37", "310UB46.2", "200UC46.2", "89x5 SHS", "250PFC",
        "125x75x6 EA", "150x100x10 UA", "300FC", "220",
        # The anchor is deliberately tight: an open middle would read a steel
        # hollow section followed by a grade-shaped token as timber.
        "200x200x6 SHS F17", "100x100x5 SHS F8", "150x90x6 RHS F11",
    ]:
        assert is_timber_or_non_steel(section) is False, section


def test_a_catalogue_matched_section_is_never_reclassified_as_timber():
    """The F-grade signal is an addition to the unmatched path only: a section
    the catalogue resolved stays steel, whatever it contains."""
    assert classify_member("240x45 F27", FakeMatcher().match("250UB37")) == "steel_confirmed"


def test_existing_timber_keywords_still_match():
    """The keyword list is untouched by the F-grade addition — every form it
    recognised before it still recognises, and they still exclude."""
    for section in [
        "2/190x45 SG8 H3.2", "140x45 H4 SG8", "125x125 H5", "190x45 SG8 H2.0",
        "300x90 Hyone", "200x90 spotted gum beam", "240x45 hyONE", "246x90 hy90",
        "LVL 240x45", "HySPAN 300x63", "140x45 SG6", "190x45 SG10 H3.2",
        "KWILA 100x50", "MERBAU", "PLYWOOD 12mm", "TREATED PINE 90x45",
    ]:
        assert is_timber_or_non_steel(section) is True, section


def test_bare_dimension_without_timber_keyword_is_not_classified_as_timber():
    # "220" alone is genuinely ambiguous — must NOT be auto-classified as timber.
    assert classify_member("220", None) == "unmatched_steel"
    assert classify_member("300FC", None) == "unmatched_steel"
    # But an explicit timber keyword is unambiguous.
    assert classify_member("125x125 H5", None) == "non_steel_material"
    assert is_timber_or_non_steel("140x45 H4 SG8") is True
    assert is_timber_or_non_steel("250UB37") is False


# === Rule: conflicting member definitions across pages ===
def test_conflicting_definitions_are_flagged_not_silently_resolved():
    raw = [
        {"mark": "P2", "section": "250PFC", "source_page": 8, "confidence": 85},
        {"mark": "P2", "section": "100PFC", "source_page": 10, "confidence": 85},
        {"mark": "P2", "section": "250UB37", "source_page": 6, "confidence": 85},
    ]
    result = validate_extraction(raw, FakeMatcher())
    conflict_issues = [i for i in result["issues"] if i.rule == "CONFLICTING_MEMBER_DEFINITION"]
    assert len(conflict_issues) == 1
    assert conflict_issues[0].severity == "HIGH"
    # All three conflicting rows are kept — none silently dropped or auto-picked
    assert len(result["members"]) == 3
    assert all(m["review_status"] == "review_required" for m in result["members"])


def test_agreeing_multi_page_definitions_consolidate_to_one_confirmed_row():
    raw = [
        {"mark": "P5", "section": "250UB37", "source_page": 6, "confidence": 85},
        {"mark": "P5", "section": "250UB37", "source_page": 8, "confidence": 80},
        {"mark": "P5", "section": "250UB37", "source_page": 9, "confidence": 72},
        {"mark": "P5", "section": "250UB37", "source_page": 10, "confidence": 85},
    ]
    result = validate_extraction(raw, FakeMatcher())
    p5_rows = [m for m in result["members"] if m["mark"] == "P5"]
    assert len(p5_rows) == 1  # consolidated, not four separate rows
    assert p5_rows[0]["review_status"] == "extracted"
    assert p5_rows[0]["source_pages_confirmed"] == [6, 8, 9, 10]


# === Rule: mark format anomaly (section folded into the mark string) ===
def test_section_size_folded_into_mark_gets_corrected():
    fixed = fold_mark_format_anomalies([
        {"mark": "P1 89x5 SHS", "section": "89x5 SHS"},
        {"mark": "P2 250PFC", "section": "250PFC"},
        {"mark": "P3 200UC46", "section": "200UC46"},
        {"mark": "B1", "section": "250UB37"},  # unaffected — no anomaly
    ])
    assert fixed[0]["mark"] == "P1"
    assert fixed[1]["mark"] == "P2"
    assert fixed[2]["mark"] == "P3"
    assert fixed[3]["mark"] == "B1"
    assert fixed[0]["_mark_was_corrected"] is True
    assert "_mark_was_corrected" not in fixed[3]


def test_corrected_mark_folds_into_existing_group_across_pages():
    raw = [
        {"mark": "P1", "section": "89x5 SHS", "source_page": 6, "confidence": 85},
        {"mark": "P1 89x5 SHS", "section": "89x5 SHS", "source_page": 15, "confidence": 80},
    ]
    result = validate_extraction(raw, FakeMatcher())
    p1_rows = [m for m in result["members"] if m["mark"] == "P1"]
    assert len(p1_rows) == 1
    assert p1_rows[0]["source_pages_confirmed"] == [6, 15]


# === Rule: missing length must never silently become zero ===
def test_missing_length_triggers_project_warning_not_zero_tonnage():
    raw = [
        {"mark": "B1", "section": "250UB37", "source_page": 1, "length_mm": None, "confidence": 90},
        {"mark": "B2", "section": "310UB40", "source_page": 1, "length_mm": None, "confidence": 90},
        {"mark": "B3", "section": "200UC46", "source_page": 1, "length_mm": 4000, "confidence": 90},
    ]
    result = validate_extraction(raw, FakeMatcher())
    length_issues = [i for i in result["issues"] if i.rule == "MISSING_MEMBER_LENGTH"]
    assert len(length_issues) == 1  # 2 of 3 missing = 67% >= 50% threshold
    assert length_issues[0].status == "WARNING"
    # And critically: total_weight_kg must be None for members with no length, never 0
    b1 = next(m for m in result["members"] if m["mark"] == "B1")
    assert b1.get("total_weight_kg") is None or "total_weight_kg" not in b1


def test_missing_length_below_threshold_does_not_warn():
    raw = [
        {"mark": "B1", "section": "250UB37", "source_page": 1, "length_mm": 5000, "confidence": 90},
        {"mark": "B2", "section": "310UB40", "source_page": 1, "length_mm": 6000, "confidence": 90},
        {"mark": "B3", "section": "200UC46", "source_page": 1, "length_mm": None, "confidence": 90},
    ]
    result = validate_extraction(raw, FakeMatcher())
    assert not any(i.rule == "MISSING_MEMBER_LENGTH" for i in result["issues"])


# === Direct check_missing_lengths tests (isolated from the full pipeline) ===
def test_check_missing_lengths_directly():
    assert check_missing_lengths([]) is None
    assert check_missing_lengths([{"length_mm": 1000}, {"length_mm": 2000}]) is None
    issue = check_missing_lengths([{"length_mm": None}, {"length_mm": None}, {"length_mm": 1000}])
    assert issue is not None
    assert issue.rule == "MISSING_MEMBER_LENGTH"
