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
not decide sections (it asks the supplied matcher — the same one
app.validation.rules.validate_extraction() already takes as a
parameter, never instantiated in here, so this module never hard-wires
a live Supabase dependency and stays trivially testable with a fake),
and does not duplicate app/cad_engine/sections.py's per-family
required-geometry-field checks. It has exactly one job: decide whether
a real member row is trustworthy enough to even ATTEMPT CAD geometry,
and fail loudly and specifically when it is not.

THE CAD AUTHORITY RULE (see _authorised_catalogue_row below): this
module CONSUMES the section identity a member already has; it never
re-derives it. The member's section was decided upstream from the
engineer's drawing, and this boundary's one catalogue lookup may
CONFIRM that identity but may never CHANGE it — so a lookup that would
hand back a different catalogue section (the SectionMatcher's
".0"..".9" suffix fallback is the one route by which that happens:
"310UB40" is answered with the 310UB40.4 row) is refused, exactly like
every other untrustworthy input here, rather than silently generating a
different member than the drawing specified.

INPUT SHAPE: the real `steel_members` row shape, exactly as
app/pipeline.py's `_run_pipeline()` currently builds it before calling
repo.insert_members() — inspected directly from that function, not
guessed. Relevant keys this module reads: `mark`, `section_name` (the
member's authoritative section identity, as decided by
app/validation/rules.py — never re-derived from the raw OCR/vision
text in `section_name_raw`), `length_mm`, `grade`, `review_status`,
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

SECTION GEOMETRY: this module confirms `section_name` is answered for
by a real steel_sections row (via the supplied matcher, and only when
that answer IS the section `section_name` names — see the authority
rule above) AND that the matched row's family is admissible to CAD —
reusing app.cad_engine.sections.cad_family_for() as the single source
of truth for that decision, never re-listing family names here.
Milestone J1: that decision is made from the AUTHORITATIVE source
family via one explicit table (sections.CAD_FAMILY_PROJECTION), so the
catalogue's own "FLAT" is admitted as this engine's "FL" — while the
row itself is left exactly as the catalogue gave it. It deliberately does
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
import re
from typing import Any

from app.cad_engine import sections
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import ValidatedSteelMember

__all__ = ["real_member_to_validated_member"]

# The matcher's own resolution vocabulary, defined in
# app/engineering_data/section_matcher.py (RESOLUTION_EXACT /
# RESOLUTION_SUFFIX_FALLBACK / RESOLUTION_NONE, added with the SectionMatch
# contract). Compared here as a plain string and deliberately NOT imported:
# that module reaches app.supabase_client -> app.config, which reads
# os.environ at import time, so importing it would make this CAD module
# unusable without a configured environment — including in every CAD test.
# tests/test_real_world_cad_authority_boundary.py asserts the two spellings
# are the same value, so they cannot drift apart silently.
RESOLUTION_EXACT = "EXACT"


def _identity_key(name: Any) -> str:
    """
    The identity key of a section name: whitespace collapsed, case folded.

    Deliberately the SAME expression app/engineering_data/section_matcher.py's
    SectionMatcher._normalise() uses to build its lookup keys, so "is this the
    same section, spelled differently?" is decided here exactly as the
    catalogue decides it. This is an EQUALITY relation, not a matching rule:
    it generates no candidate and applies no fallback, so it can only ever
    make this module MORE refusing, never less. The two expressions are pinned
    together by a guard test.
    """
    return re.sub(r"\s+", "", str(name).strip().upper())


def _denotes_the_same_section(returned_name: Any, section_name: str) -> bool:
    """
    True when the row the catalogue answered with IS the section this member
    was decided to be — however each side spells it (the catalogue's own house
    spelling of an exact match is frequently not the matcher's canonical form,
    e.g. "25x25x3EA"). False when the catalogue answered with a DIFFERENT
    section, which no lookup at this boundary may ever do.
    """
    if returned_name is None or not str(returned_name).strip():
        return False
    return _identity_key(returned_name) == _identity_key(section_name)


def _authorised_catalogue_row(matcher: Any, section_name: str, mark: str) -> dict:
    """
    The catalogue row for this member's ALREADY-DECIDED section identity.

    THE CAD AUTHORITY RULE: this lookup may CONFIRM the identity the member
    already has; it may never CHANGE it. The member's section was decided
    upstream (app/validation/rules.py) from the engineer's drawing, and this
    module consumes that decision — it does not re-open it, and it does not
    re-resolve the drawing token. A lookup that would hand back a different
    catalogue section is refused outright, with the same
    GeometryValidationError vocabulary every other precondition here uses,
    rather than silently generating a different member than the drawing said.

    Two independent refusals, because neither one alone is sufficient:

      - the matcher's own resolution report, when it can give one: only an
        EXACT resolution is accepted. That is what catches the ".0"..".9"
        suffix fallback — the one route by which the real SectionMatcher
        answers "310UB40" with a different section's row — and it fails
        closed on any resolution this module does not recognise.
      - the identity of the row actually returned: its own name must denote
        the requested section. This holds whatever the matcher claims, and it
        is what protects callers supplying a match()-only matcher. The
        pre-existing test doubles are unaffected (they answer only for the
        name asked), and so is the source-pinned matcher the resolved-conflict
        path supplies: it returns its row for its own name and nothing for any
        other, so it passes the rule untouched.
    """
    resolve = getattr(matcher, "resolve", None)
    resolution = None
    if resolve is None:
        row = matcher.match(section_name)
    else:
        outcome = resolve(section_name)
        resolution, row = outcome.resolution, outcome.catalogue_row

    if not row:
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: section '{section_name}' is not a "
            "recognised entry in the steel section reference table. Refusing to substitute or "
            "approximate another section."
        )

    returned_name = row.get("name") if isinstance(row, dict) else None

    if resolution is not None and resolution != RESOLUTION_EXACT:
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: the reference table answered section "
            f"'{section_name}' with a {resolution} resolution, not an exact one — it offered "
            f"{returned_name!r} instead. A lookup at this boundary may confirm this member's "
            "section, never change it. Refusing to substitute or approximate another section."
        )

    if not _denotes_the_same_section(returned_name, section_name):
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: this member's section identity is "
            f"'{section_name}', but the reference table answered with a DIFFERENT section "
            f"({returned_name!r}). A lookup at this boundary may confirm this member's section, "
            "never change it. Refusing to substitute or approximate another section."
        )

    return row


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

    # The one lookup this boundary performs: it must answer FOR this member's
    # decided section, never with a different one. See _authorised_catalogue_row.
    section_properties = _authorised_catalogue_row(matcher, section_name, mark)

    # THE SAME ADMISSION DECISION interface.generate_geometry() makes, from the
    # same one explicit table — not a second, parallel rule. The row's family is
    # the AUTHORITATIVE catalogue value and is left exactly as it is: admitting
    # a source family here derives a CAD family, it never rewrites the row.
    family = section_properties.get("family")
    if sections.cad_family_for(family) is None:
        raise GeometryValidationError(
            f"Member '{mark}' cannot generate CAD geometry: section '{section_name}' has family "
            f"{family!r}, which has no supported CAD profile builder yet. Supported families: "
            f"{sorted(sections.PROFILE_BUILDERS)}; source families projected onto them: "
            f"{sorted(sections.CAD_FAMILY_PROJECTION)}."
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
      - the supplied matcher answers section_name with a row that is NOT
        that section — via its suffix fallback or by any other route — or
        reports that it resolved it by anything other than an exact hit
        (THE CAD AUTHORITY RULE: this lookup may confirm a member's section,
        never change it);
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
