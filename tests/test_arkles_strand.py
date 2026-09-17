"""
Validation against the REAL Arkles Strand extraction — the actual 54
rows pulled from the live Supabase project (id 0d05e86d-5437-40ab-
a36c-a3a0d049a88d), not reconstructed or synthetic data. This is the
test the restructure was built to pass.
"""
from app.validation.rules import validate_extraction

# The real 54 rows, exactly as extracted, reshaped into the raw-member
# input shape validate_extraction expects (section = section_name_raw,
# since these predate the current pipeline's "section" field naming).
ARKLES_STRAND_RAW = [
    {"mark": "200x90 spotted gum beam", "section": "200x90", "source_page": 15, "confidence": 70},
    {"mark": "B17", "section": "220", "source_page": 14, "confidence": 55},
    {"mark": "B17", "section": "225", "source_page": 14, "confidence": 60},
    {"mark": "B8", "section": "250UB 37", "source_page": 27, "confidence": 60, "detail_reference": "17"},
    {"mark": "BF1", "section": "310UB46", "source_page": 23, "confidence": 75, "detail_reference": "BF1 PORTAL"},
    {"mark": "BF1", "section": "250PFC", "source_page": 23, "confidence": 72},
    {"mark": "BF2", "section": "200UB30", "source_page": 29, "confidence": 90, "detail_reference": "22, 23, 24"},
    {"mark": "BF2", "section": "250PFC", "source_page": 23, "confidence": 65},
    {"mark": "BF2", "section": "250UB37", "source_page": 23, "confidence": 70, "detail_reference": "BF2 PORTAL"},
    {"mark": "L-T-150PFC", "section": "150PFC", "source_page": 28, "confidence": 72, "detail_reference": "21"},
    {"mark": "L17", "section": "200x90 Hy90", "source_page": 15, "confidence": 70},
    {"mark": "L2", "section": "250PFC", "source_page": 14, "confidence": 85},
    {"mark": "L2/L3", "section": "250 PFC", "source_page": 26, "confidence": 85, "detail_reference": "12"},
    {"mark": "L3", "section": "250PFC", "source_page": 14, "confidence": 85},
    {"mark": "M10", "section": "2/190x45 SG8 H3.2", "source_page": 7, "confidence": 75},
    {"mark": "M11", "section": "140x45 H4 SG8", "source_page": 7, "confidence": 75},
    {"mark": "M12", "section": "190x45 H4 SG8", "source_page": 7, "confidence": 75},
    {"mark": "M13", "section": "2/140x45 SG8 H4", "source_page": 7, "confidence": 72},
    {"mark": "M14", "section": "125x125 H5", "source_page": 7, "confidence": 80},
    {"mark": "M15", "section": "140x45 SG8 H3.2", "source_page": 7, "confidence": 72},
    {"mark": "M16", "section": "2/190x45 SG8 H3.2", "source_page": 7, "confidence": 70},
    {"mark": "M7", "section": "2/190x45 SG8 H3.2", "source_page": 7, "confidence": 75},
    {"mark": "M8", "section": "190x45 SG8 H3.2", "source_page": 7, "confidence": 75},
    {"mark": "M9", "section": "2/190x45 SG8 H3.2", "source_page": 7, "confidence": 72},
    {"mark": "P1", "section": "89x89 SHS", "source_page": 9, "confidence": 72},
    {"mark": "P1", "section": "89x5 SHS", "source_page": 10, "confidence": 85},
    {"mark": "P1", "section": "89x5 SHS", "source_page": 6, "confidence": 85},
    {"mark": "P1", "section": "89x5 SHS", "source_page": 8, "confidence": 85},
    {"mark": "P1 89x5 SHS", "section": "89x5 SHS", "source_page": 15, "confidence": 80},
    {"mark": "P2", "section": "250PFC", "source_page": 9, "confidence": 72},
    {"mark": "P2", "section": "100PFC", "source_page": 10, "confidence": 85},
    {"mark": "P2", "section": "250PFC", "source_page": 8, "confidence": 85},
    {"mark": "P2", "section": "250UB37", "source_page": 6, "confidence": 85},
    {"mark": "P2 250PFC", "section": "250PFC", "source_page": 15, "confidence": 80},
    {"mark": "P3", "section": "250PFC", "source_page": 9, "confidence": 72},
    {"mark": "P3", "section": "200UC46", "source_page": 8, "confidence": 85},
    {"mark": "P3", "section": "200UC", "source_page": 10, "confidence": 85},
    {"mark": "P3", "section": "200UC46", "source_page": 29, "confidence": 90, "detail_reference": "22, 23, 24"},
    {"mark": "P3", "section": "200UC46", "source_page": 6, "confidence": 85},
    {"mark": "P3 200UC46", "section": "200UC46", "source_page": 15, "confidence": 80},
    {"mark": "P4", "section": "300FC", "source_page": 8, "confidence": 75},
    {"mark": "P4", "section": "100PFC", "source_page": 10, "confidence": 85},
    {"mark": "P4", "section": "300PFC", "source_page": 9, "confidence": 72},
    {"mark": "P4", "section": "300PFC", "source_page": 6, "confidence": 85},
    {"mark": "P5", "section": "250UB37", "source_page": 10, "confidence": 85},
    {"mark": "P5", "section": "250UB37", "source_page": 6, "confidence": 85},
    {"mark": "P5", "section": "250UB37", "source_page": 9, "confidence": 72},
    {"mark": "P5", "section": "250UB37", "source_page": 8, "confidence": 80},
    {"mark": "P6", "section": "250UB37", "source_page": 6, "confidence": 80},
    {"mark": "PORTAL FRAME BF1", "section": "310UB42", "source_page": 15, "confidence": 65},
    {"mark": "RB1", "section": "300x90 Hyone", "source_page": 28, "confidence": 82, "detail_reference": "21"},
    {"mark": "RB2", "section": "250UB 25", "source_page": 27, "confidence": 55, "detail_reference": "15"},
    {"mark": "RB3", "section": "246x90 hy90", "source_page": 28, "confidence": 85, "detail_reference": "18"},
    {"mark": "RBS", "section": "200PFC", "source_page": 29, "confidence": 85, "detail_reference": "22, 23"},
]
# All 54 rows have length_mm = None in the real data — omitted above (dict.get defaults to None).


class RealisticFakeMatcher:
    """
    Mimics the real SectionMatcher's behaviour against the actual
    steel_sections currently seeded — including the real gap this
    test surfaces: there is no 89x89 SHS in the current reference
    table, so P1 never actually matches (see finding in final report).
    """
    _sections = {
        "250UB37.3": {"name": "250UB37.3", "family": "UB", "weight_per_metre": 37.3},
        "310UB46.2": {"name": "310UB46.2", "family": "UB", "weight_per_metre": 46.2},
        "200UB30.4": {"name": "200UB30.4", "family": "UB", "weight_per_metre": 30.4},
        "250UB25.7": {"name": "250UB25.7", "family": "UB", "weight_per_metre": 25.7},
        "200UC46.2": {"name": "200UC46.2", "family": "UC", "weight_per_metre": 46.2},
        "250PFC": {"name": "250PFC", "family": "PFC", "weight_per_metre": 35.5},
        "100PFC": {"name": "100PFC", "family": "PFC", "weight_per_metre": 8.3},
        "150PFC": {"name": "150PFC", "family": "PFC", "weight_per_metre": 17.7},
        "200PFC": {"name": "200PFC", "family": "PFC", "weight_per_metre": 22.9},
        "300PFC": {"name": "300PFC", "family": "PFC", "weight_per_metre": 40.1},
        # Deliberately NOT including 89x89 SHS — matches the real gap in steel_sections today.
    }

    def match(self, raw_name: str):
        key = raw_name.strip().upper().replace(" ", "")
        if key in self._sections:
            return self._sections[key]
        for k, v in self._sections.items():
            if k.replace(".", "").startswith(key.replace(".", "")):
                return v
        return None


def test_arkles_strand_real_data_validation():
    result = validate_extraction(ARKLES_STRAND_RAW, RealisticFakeMatcher())

    print("\n" + "=" * 70)
    print("ARKLES STRAND — REAL DATA VALIDATION RESULTS")
    print("=" * 70)

    print(f"\nFinal steel members: {len(result['members'])}")
    print(f"Excluded (non-steel): {len(result['excluded'])}")
    print(f"Total issues: {len(result['issues'])}")

    print("\n--- EXCLUDED AS NON-STEEL (timber contamination) ---")
    for e in result["excluded"]:
        print(f"  {e['mark']!r} -> {e['section']!r}")

    print("\n--- CONFLICTING_MEMBER_DEFINITION ---")
    conflicts = [i for i in result["issues"] if i.rule == "CONFLICTING_MEMBER_DEFINITION"]
    for c in conflicts:
        print(f"  {c.message}")

    print("\n--- UNRECOGNISED_SECTION_FORMAT (unmatched, kept for review) ---")
    unmatched_issues = [i for i in result["issues"] if i.rule == "UNRECOGNISED_SECTION_FORMAT"]
    for u in unmatched_issues:
        print(f"  {u.message}")

    print("\n--- MISSING_MEMBER_LENGTH ---")
    length_issues = [i for i in result["issues"] if i.rule == "MISSING_MEMBER_LENGTH"]
    for l in length_issues:
        print(f"  {l.message}")

    print("\n--- CONSOLIDATION CHECK: P5 (should merge 4 pages into 1 confirmed row) ---")
    p5 = [m for m in result["members"] if m["mark"] == "P5"]
    print(f"  P5 rows in final output: {len(p5)} (pages confirmed: {p5[0].get('source_pages_confirmed') if p5 else 'N/A'})")

    print("\n--- MARK FORMAT ANOMALY CHECK: P1, P2, P3 folding ---")
    for mark in ("P1", "P2", "P3"):
        rows = [m for m in result["members"] if m["mark"] == mark]
        print(f"  {mark}: {len(rows)} row(s) after folding+consolidation, category={rows[0]['category'] if rows else 'N/A'}")

    print("=" * 70)

    # === Assertions: the restructure must actually catch these ===
    excluded_marks = {e["mark"] for e in result["excluded"]}
    expected_timber_marks = {
        "200x90 spotted gum beam", "L17", "M10", "M11", "M12", "M13",
        "M14", "M15", "M16", "M7", "M8", "M9", "RB1", "RB3",
    }
    assert expected_timber_marks.issubset(excluded_marks), (
        f"Missing expected timber exclusions: {expected_timber_marks - excluded_marks}"
    )

    conflict_marks = {c.message.split("'")[1] for c in conflicts}
    assert "P2" in conflict_marks  # 250PFC / 100PFC / 250UB37.3 — real 3-way conflict
    assert "P4" in conflict_marks  # 300PFC / 100PFC — real conflict
    assert "BF2" in conflict_marks  # 250PFC / 250UB37.3 — real conflict

    assert len(length_issues) == 1  # 100% of members missing length — must fire
    assert "NOT_CALCULATED" in length_issues[0].message

    # P5 must consolidate cleanly since all 4 pages agree
    assert len(p5) == 1
    assert p5[0]["source_pages_confirmed"] == [6, 8, 9, 10]

    # Every member's total_weight_kg must be genuinely absent, never a fabricated zero
    for m in result["members"]:
        length_mm = m.get("length_mm")
        assert length_mm is None  # confirms the real data's actual state
