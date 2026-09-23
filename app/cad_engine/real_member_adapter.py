"""
Milestone 7A — the boundary between REAL extracted/persisted member
data and the CAD engine's ValidatedSteelMember contract:

    real steel_members row (engineering_data)
            |
       real_member_to_validated_member()
            |
      ValidatedSteelMember
            |
    existing generate_geometry() (unchanged)

This module is deliberately small. It does not build geometry, does
not match sections itself (it calls the supplied matcher — the same
one app.validation.rules.validate_extraction() already takes as a
parameter, never instantiated in here, so this module never hard-wires
a live Supabase dependency and stays trivially testable with a fake),
and does not duplicate app/cad_engine/sections.py's per-family
required-geometry-field checks. It has exactly one job: decide whether
a real member row is trustworthy enough to even ATTEMPT CAD geometry,
and fail loudly and specifically when it is not.

INPUT SHAPE: the real `steel_members` row shape, exactly as
app/pipeline.py's `_run_pipeline()` currently builds it before calling
repo.insert_members() — inspected directly from that function, not
guessed. Relevant keys this module reads: `mark`, `section_name` (the
already-matched name from SectionMatcher, not the raw OCR/vision text
in `section_name_raw`), `length_mm`, `grade`, `review_status`,
`source_page`, `source_drawing_id`. Every other key on a real row
(quantity, weight_per_metre, confidence, notes, ...) is production/
report metadata this module has no reason to touch.

THE LENGTH RULE (the reason this module exists): length_mm must be a
genuine positive number already present on the row. It is never
guessed from section depth, drawing scale, page size, quantity, grid
spacing, a detail reference, or any other proxy. A member without a
trustworthy length is rejected outright — see test_arkles_strand.py's
real 54-row Arkles Strand extraction, where every single member has
length_mm = None today. That is not a bug in this module; it is this
module doing exactly what it is for.

SECTION GEOMETRY: this module confirms `section_name` matches a real
steel_sections row (via the supplied matcher) AND that the matched
row's family has a supported CAD profile builder
(app.cad_engine.sections.PROFILE_BUILDERS) — reusing that existing
registry as the single source of truth for "which families are
supported", never re-listing family names here. It deliberately does
NOT check whether that matched row carries every individual geometry
field a given family's builder needs (depth, flange_width, etc.) —
app.cad_engine.sections._require_fields() already performs that check,
with the same GeometryValidationError this module raises, the moment
generate_geometry() is actually called on the ValidatedSteelMember
this module returns. Re-implementing that check here would be the
exact kind of duplicated section-geometry logic this milestone
explicitly warns against; letting the existing, authoritative check
run downstream is the smaller, correct design.
"""
from typing import Any

from app.cad_engine import sections
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import ValidatedSteelMember

__all__ = ["real_member_to_validated_member"]


def _require_trustworthy_mark(member_row: dict) -> str:
    mark = member_row.get("mark")
    if mark is None or not str(mark).strip() or str(mark).strip() == "?":
        raise GeometryValidationError(
            f"Member cannot generate CAD geometry: no trustworthy mark is recorded (got {mark!r})."
        )
    return str(mark).strip()


def _require_trustworthy_length(member_row: dict, mark: str) -> float:
    length_mm = member_row.get("length_mm")
    # bool is technically an int subclass in Python — explicitly excluded so a
    # stray True/False in the data is never silently read as a length of 1/0.
    if length_mm is None or isinstance(length_mm, bool) or not isinstance(length_mm, (int, float)):
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: length_mm is missing or not numeric "
            f"(got {length_mm!r}). A length is never guessed from section depth, drawing scale, "
            "quantity, or any other proxy — it must be an explicit extracted dimension."
        )
    if length_mm <= 0:
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: length_mm must be a positive number "
            f"(got {length_mm})."
        )
    return float(length_mm)


def _require_matched_section(member_row: dict, mark: str, matcher: Any) -> tuple[str, dict]:
    section_name = member_row.get("section_name")
    if not section_name or not str(section_name).strip():
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: no matched section name is recorded "
            "for this member (its section could not be recognised during extraction)."
        )
    section_name = str(section_name).strip()

    section_properties = matcher.match(section_name)
    if not section_properties:
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: section '{section_name}' is not a "
            "recognised entry in the steel section reference table. Refusing to substitute or "
            "approximate another section."
        )

    family = section_properties.get("family")
    if family not in sections.PROFILE_BUILDERS:
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: section '{section_name}' has family "
            f"{family!r}, which has no supported CAD profile builder yet. Supported families: "
            f"{sorted(sections.PROFILE_BUILDERS)}."
        )

    return section_name, section_properties


def real_member_to_validated_member(member_row: dict, matcher: Any) -> ValidatedSteelMember:
    """
    Converts one real `steel_members` row into a ValidatedSteelMember,
    or raises GeometryValidationError explaining exactly why it can't.
    Deterministic and side-effect free: `matcher` is the caller's own
    SectionMatcher (or a fake, in tests) — this function never
    constructs one itself, so it never touches Supabase directly.

    Raises GeometryValidationError when:
      - the mark is missing, blank, or the "?" not-extracted sentinel
        app.pipeline.py itself uses for an unrecorded mark;
      - the member is already flagged review_required by earlier
        validation (e.g. a conflicting section definition across
        pages) — that flag means a human hasn't resolved this member
        yet, so CAD generation would be premature regardless of
        whether length/section individually look fine;
      - length_mm is missing, non-numeric, zero, or negative;
      - section_name is missing (the section was never matched during
        extraction);
      - section_name does not match a real steel_sections row via the
        supplied matcher;
      - the matched row's family has no supported CAD profile builder.

    Does NOT check whether the matched row carries every geometry
    field its family's builder needs — see this module's docstring for
    why that check is deliberately left to generate_geometry() itself.
    """
    mark = _require_trustworthy_mark(member_row)

    if member_row.get("review_status") == "review_required":
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: this member is flagged "
            "review_required and must be resolved by a human before CAD generation."
        )

    length_mm = _require_trustworthy_length(member_row, mark)
    section_name, section_properties = _require_matched_section(member_row, mark, matcher)

    source_refs = []
    if member_row.get("source_page") is not None:
        source_refs.append({
            "page": member_row["source_page"],
            "drawing_id": member_row.get("source_drawing_id"),
        })

    return ValidatedSteelMember(
        mark=mark,
        section=section_name,
        length_mm=length_mm,
        material=member_row.get("grade") or "UNKNOWN",
        orientation=None,
        connection_refs=[],
        source_refs=source_refs,
        validation_status="extracted",
        section_properties=section_properties,
    )
