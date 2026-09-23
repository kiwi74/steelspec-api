"""
Milestone 7H — proving a single HUMAN-REVIEWED connection record can
explicitly associate TWO independently extracted real members, with no
reliance on list order, source-row order, mark similarity, or any
other positional/inferred signal:

    real member A row -> 7A -> ValidatedSteelMember A --\
                                                           +--> (identity only)
    real member B row -> 7A -> ValidatedSteelMember B --/
    reviewed connection record -> 7D -> ValidatedConnection
        .connected_members == [A's mark, B's mark]

INSPECTION FINDING (Step 1) — no production change was needed for this
milestone. app/cad_engine/real_connection_adapter.py's
_require_member_association() already:
  - accepts `connected_member_marks` as a list of ANY length (no
    hardcoded "exactly one" or "exactly two" assumption);
  - builds a NEW list via `[str(m).strip() for m in marks]` — never an
    alias to the caller's own list;
  - rejects a missing/empty list and a blank-or-duplicate entry, but
    performs NO existence check against any actual member records;
  - preserves input order exactly (a plain list comprehension), but
    nothing downstream ever reads that order: a project-wide grep
    confirms `connected_members` is read in exactly two places in the
    whole CAD engine — where real_connection_adapter.py builds it, and
    the one-line field declaration on ValidatedConnection itself in
    interface.py. Nothing in app/cad_engine/connections.py or
    interface.py's connection-resolution logic (which matches by
    `connection_id` via `member.connection_refs`, never by
    `connected_members`) reads this field at all today. So there is no
    existing "ordered list" semantics to violate, and no CAD assembly
    behaviour this field currently drives — exactly matching this
    milestone's Step 14 scope boundary.

RESPONSIBILITY BOUNDARY (Step 12) this file documents rather than
changes: 7D's job is to preserve whatever explicit member marks a
reviewed connection record states — never to verify those marks refer
to real, currently-known project members. An unresolved/unknown mark
is not rejected here (see
test_connection_does_not_silently_resolve_unrelated_mark_to_member_b);
resolving identity against actual project members is left to a later
pipeline layer, and this file does not add that logic.

SCOPE BOUNDARY (Step 14): this file never calls generate_geometry()
and never builds a combined two-member CAD assembly, never positions
Member A relative to Member B, and never assumes the connection's END
position applies to both members geometrically. The proof stops at
ValidatedSteelMember A / ValidatedSteelMember B / ValidatedConnection
— three independent, correctly-populated objects, nothing more.

FIXTURE PROVENANCE:
  - Member A and Member B are the exact same two real members
    established in Milestone 7G (tests/test_real_multi_member_cad.py):
    Member A is the established primary fixture (REAL-UB-CAD-001,
    310UB40, 4000mm); Member B's mark/section come from the real
    Arkles Strand extraction row for "L2" (250PFC, page 14) — L2 was
    chosen there because it is the sole real occurrence of that mark
    in the 54-row extraction, unlike marks with multiple conflicting
    real section candidates. Member B's length remains explicitly
    HUMAN-REVIEWED/SUPPLEMENTED, since no Arkles row carries a length.
  - The connection record reuses 7E/7F's exact reviewed connection
    geometry unchanged (180x250x12mm end plate, 4xO22mm holes, 90mm H
    / 140mm V spacing, explicit END position) — the only change from
    that established fixture is `connected_member_marks`, which now
    explicitly names both real members instead of 7E/7F's single-member
    association.
"""
import pytest

from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.real_connection_adapter import real_connection_to_validated_connection
from app.cad_engine.real_member_adapter import real_member_to_validated_member
from tests.test_real_connection_adapter import make_7e_connection_record
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row, make_multi_member_matcher


def make_multi_member_connection_record(**overrides) -> dict:
    record = make_7e_connection_record(
        connection_id="CONN-REAL-UB-CAD-001-L2",
        connected_member_marks=["REAL-UB-CAD-001", "L2"],
    )
    record.update(overrides)
    return record


# === Step 5-7: primary path — both members via 7A, connection via 7D,
# nothing hand-built, both exact marks present ===
def test_primary_connection_explicitly_associates_both_real_members():
    matcher = make_multi_member_matcher()
    validated_a = real_member_to_validated_member(make_member_a_row(), matcher)
    validated_b = real_member_to_validated_member(make_member_b_row(), matcher)

    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())

    assert validated_a.mark == "REAL-UB-CAD-001"
    assert validated_b.mark == "L2"
    assert len(validated_connection.connected_members) == 2
    assert set(validated_connection.connected_members) == {validated_a.mark, validated_b.mark}
    # Members were validated completely independently — never merged.
    assert validated_a is not validated_b
    assert validated_a.section != validated_b.section


def test_connection_does_not_silently_resolve_unrelated_mark_to_member_b():
    """
    A mark that doesn't match any real member (e.g. a typo, or a
    member not yet extracted) is preserved exactly as given, never
    "helpfully" resolved to a similarly-shaped known member. See
    module docstring's responsibility-boundary note.
    """
    record = make_multi_member_connection_record(
        connected_member_marks=["REAL-UB-CAD-001", "UNRELATED-MARK-XYZ"],
    )
    validated_connection = real_connection_to_validated_connection(record)

    assert "UNRELATED-MARK-XYZ" in validated_connection.connected_members
    assert "L2" not in validated_connection.connected_members


# === Step 8: order is not semantically significant ===
def test_reversed_member_order_preserves_same_association():
    record_ab = make_multi_member_connection_record(connected_member_marks=["REAL-UB-CAD-001", "L2"])
    record_ba = make_multi_member_connection_record(
        connection_id="CONN-L2-REAL-UB-CAD-001", connected_member_marks=["L2", "REAL-UB-CAD-001"],
    )

    validated_ab = real_connection_to_validated_connection(record_ab)
    validated_ba = real_connection_to_validated_connection(record_ba)

    assert set(validated_ab.connected_members) == set(validated_ba.connected_members) == {"REAL-UB-CAD-001", "L2"}
    # The adapter preserves input order exactly (a plain list build, not
    # a set) — but nothing in the CAD engine reads that order (see
    # module docstring), so membership here is identity-based, not
    # position-based.
    assert validated_ab.connected_members == ["REAL-UB-CAD-001", "L2"]
    assert validated_ba.connected_members == ["L2", "REAL-UB-CAD-001"]


# === Step 9: adversarial "no member inference" tests ===
def test_association_unaffected_by_member_conversion_order():
    matcher = make_multi_member_matcher()
    connection_record = make_multi_member_connection_record()

    # Member B converted before Member A this time.
    validated_b = real_member_to_validated_member(make_member_b_row(), matcher)
    validated_a = real_member_to_validated_member(make_member_a_row(), matcher)
    validated_connection = real_connection_to_validated_connection(connection_record)

    assert set(validated_connection.connected_members) == {validated_a.mark, validated_b.mark}


def test_association_is_exact_string_match_not_normalized():
    """
    The adapter strips whitespace but never case-normalizes or
    fuzzy-matches a mark — "l2" and "L2" are different strings to it.
    Silently treating them as the same member would be exactly the
    kind of inference this milestone forbids.
    """
    record = make_multi_member_connection_record(connected_member_marks=["REAL-UB-CAD-001", "l2"])
    validated_connection = real_connection_to_validated_connection(record)
    assert "l2" in validated_connection.connected_members
    assert "L2" not in validated_connection.connected_members


def test_association_unaffected_by_member_b_section_change():
    matcher = make_multi_member_matcher()
    connection_record = make_multi_member_connection_record()
    validated_connection = real_connection_to_validated_connection(connection_record)

    # Member B's own section changes (mark stays "L2"); the connection's
    # mark-based association was never derived from section data.
    validated_b_ub = real_member_to_validated_member(
        make_member_b_row(section_name="310UB40", section_family="UB"), matcher,
    )
    assert validated_b_ub.mark == "L2"
    assert set(validated_connection.connected_members) == {"REAL-UB-CAD-001", "L2"}


# === Step 10: source independence ===
def test_source_independence_connection_association_survives_record_clearing():
    record = make_multi_member_connection_record()
    validated_connection = real_connection_to_validated_connection(record)
    record.clear()
    assert set(validated_connection.connected_members) == {"REAL-UB-CAD-001", "L2"}


def test_source_independence_member_identity_survives_row_clearing():
    matcher = make_multi_member_matcher()
    row_a = make_member_a_row()
    row_b = make_member_b_row()
    validated_a = real_member_to_validated_member(row_a, matcher)
    validated_b = real_member_to_validated_member(row_b, matcher)

    row_a.clear()
    row_b.clear()

    assert validated_a.mark == "REAL-UB-CAD-001"
    assert validated_b.mark == "L2"


# === Step 11: association mutation isolation ===
def test_connected_members_list_is_not_aliased_to_source_list():
    record = make_multi_member_connection_record()
    original_marks_list = record["connected_member_marks"]
    validated_connection = real_connection_to_validated_connection(record)

    original_marks_list.append("INJECTED-MARK")

    assert "INJECTED-MARK" not in validated_connection.connected_members
    assert set(validated_connection.connected_members) == {"REAL-UB-CAD-001", "L2"}


def test_connected_members_mutation_does_not_affect_member_source_rows():
    matcher = make_multi_member_matcher()
    row_a = make_member_a_row()
    row_b = make_member_b_row()
    validated_a = real_member_to_validated_member(row_a, matcher)
    validated_b = real_member_to_validated_member(row_b, matcher)

    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())
    validated_connection.connected_members.append("SOMETHING-ELSE")

    assert validated_a.mark == "REAL-UB-CAD-001"
    assert validated_b.mark == "L2"
    assert row_a["mark"] == "REAL-UB-CAD-001"
    assert row_b["mark"] == "L2"


def test_changing_member_b_source_row_after_validation_does_not_change_connection():
    """
    Structurally guaranteed by the architecture (the connection
    adapter never reads a member's source row at all), but proven
    explicitly here per Step 11's requirement.
    """
    matcher = make_multi_member_matcher()
    row_b = make_member_b_row()
    real_member_to_validated_member(row_b, matcher)

    validated_connection = real_connection_to_validated_connection(make_multi_member_connection_record())

    row_b["mark"] = "MUTATED-MARK"
    row_b["section_name"] = "310UB40"

    assert set(validated_connection.connected_members) == {"REAL-UB-CAD-001", "L2"}


# === Step 12: missing/invalid member association, and the documented
# non-rejection of non-string marks ===
def test_missing_member_list_is_rejected():
    record = make_multi_member_connection_record()
    del record["connected_member_marks"]
    with pytest.raises(GeometryValidationError, match="member association"):
        real_connection_to_validated_connection(record)


def test_empty_member_list_is_rejected():
    record = make_multi_member_connection_record(connected_member_marks=[])
    with pytest.raises(GeometryValidationError, match="member association"):
        real_connection_to_validated_connection(record)


def test_blank_member_mark_is_rejected():
    record = make_multi_member_connection_record(connected_member_marks=["REAL-UB-CAD-001", "   "])
    with pytest.raises(GeometryValidationError, match="blank or duplicate"):
        real_connection_to_validated_connection(record)


def test_duplicate_member_mark_within_same_connection_is_rejected():
    record = make_multi_member_connection_record(
        connected_member_marks=["REAL-UB-CAD-001", "REAL-UB-CAD-001"],
    )
    with pytest.raises(GeometryValidationError, match="blank or duplicate"):
        real_connection_to_validated_connection(record)


def test_non_string_member_mark_is_coerced_not_rejected_documented_behaviour():
    """
    Documents existing behaviour rather than a new rule: the adapter's
    member-association check runs `str(m).strip()` on every entry, so
    a non-string mark (e.g. an int) is silently coerced to its string
    form rather than rejected. Flagged as a finding in the milestone
    report — a stray numeric mark from an upstream bug currently
    passes through as a plausible-looking string instead of failing
    loudly. Not changed here: it is a candidate worth flagging, not a
    clear-cut contract defect, and Step 16 forbids production changes
    without one.
    """
    record = make_multi_member_connection_record(connected_member_marks=["REAL-UB-CAD-001", 12345])
    validated_connection = real_connection_to_validated_connection(record)
    assert "12345" in validated_connection.connected_members


# === Step 13: multi-member count — the contract has no hardcoded "exactly 2" ===
def test_more_than_two_members_is_preserved_generically_no_new_cad_behaviour():
    record = make_multi_member_connection_record(
        connected_member_marks=["REAL-UB-CAD-001", "L2", "THIRD-MEMBER-MARK"],
    )
    validated_connection = real_connection_to_validated_connection(record)
    assert set(validated_connection.connected_members) == {"REAL-UB-CAD-001", "L2", "THIRD-MEMBER-MARK"}
