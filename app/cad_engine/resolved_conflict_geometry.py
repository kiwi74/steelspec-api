"""
Milestone E3 — CONFLICT RESOLUTION INTO THE MEMBER PIPELINE: an
explicitly human-resolved catalogue conflict feeds the SELECTED source
geometry into the existing member-adapter -> CAD geometry path, and
nothing else.

Lifecycle proven here (additive — nothing is wired into the production
pipeline, matcher, DXF path or any connection gate):

    SOURCE CONFLICT  -> 7AC TASK -> 7AD HUMAN RESOLUTION
        -> 7Z RE-DECISION                 (Milestone E2,
                                          catalogue_conflict_resolution)
        -> RESOLVED GEOMETRY              build_resolved_section_row():
                                          a NEW detached row assembled
                                          from the selected source's
                                          own evidence on the record
        -> MEMBER ADAPTER                 real_member_to_validated_member(),
                                          the genuine existing adapter,
                                          fed the source-pinned matcher
        -> generate_geometry()            the genuine existing CAD
                                          engine, unchanged

Unresolved: SOURCE CONFLICT -> 7AC -> no human resolution -> 7Z blocked
-> no resolved row -> the member adapter is never invoked -> no
geometry. KEEP_BOTH_REQUIRES_STRONGER_EVIDENCE stays blocked (it is
not a source selection).

THE NO-RELOOKUP RULE (the reason this module exists): the existing
member adapter re-looks-up section_name through whatever matcher its
caller supplies. If E3 handed it the ambient CatalogueMatcher, a
live-authoritative decision for "250PFC" would hit the weight-only
local row and silently lose the selected geometry. So this module
never takes a matcher parameter and never consults a catalogue: it
assembles the resolved row from the conflict record's selected source
values (captured values for CAPTURE_AUTHORITATIVE, catalogue values
for LIVE_CATALOGUE_AUTHORITATIVE — both preserved verbatim on the
record) and pins a matcher to exactly that row. A relookup against
any other source is structurally impossible: there is nowhere for an
alternative row to enter.

HARD RULES:

  - THE SELECTED SOURCE IS THE ONLY GEOMETRY. The resolved row is a
    NEW detached dict built field-by-field from the selected source's
    evidence on the conflict record; no catalogue is ever queried, no
    other row is merged in, and the two original source rows on the
    record are never mutated.
  - BLOCKED MEANS NO GEOMETRY PATH. Every entry point raises before
    any adapter call while the re-decision has not applied one source
    — unresolved, un-re-decided and KEEP_BOTH conflicts never reach
    the adapter and never produce geometry.
  - DETERMINISTIC AND READ-ONLY. No network calls, no database
    access, no clock, no nondeterminism; inputs are never mutated;
    repeated runs over the same record produce the same resolved row,
    the same validated fields and the same generated dimensions.
  - SYNTHETIC DECISIONS ARE TEST INPUTS, NEVER REAL ENGINEERING
    DECISIONS. This module never decides which source is actually
    authoritative for the project; it only consumes a decision the
    E2 boundary recorded.
"""
import copy
from dataclasses import dataclass
from typing import Any

from app.cad_engine.catalogue_conflict_resolution import (
    RESULTING_DECISION_CAPTURE_GEOMETRY,
    RESULTING_DECISION_CATALOGUE_GEOMETRY,
    CatalogueConflict,
)
from app.cad_engine.interface import (
    GeneratedMemberGeometry,
    ValidatedSteelMember,
    generate_geometry,
)
from app.cad_engine.real_member_adapter import real_member_to_validated_member

__all__ = [
    "SELECTED_SOURCE_CAPTURE",
    "SELECTED_SOURCE_CATALOGUE",
    "SELECTED_SOURCES",
    "ResolvedSectionMatcher",
    "ResolvedMemberGeometry",
    "selected_source",
    "build_resolved_section_row",
    "build_resolved_matcher",
    "build_synthetic_member_row",
    "resolve_member_geometry",
]

# The two selected sources, as recorded on the result. Exactly two —
# a blocked conflict selects neither.
SELECTED_SOURCE_CAPTURE = "CAPTURE"
SELECTED_SOURCE_CATALOGUE = "CATALOGUE"
SELECTED_SOURCES = (SELECTED_SOURCE_CAPTURE, SELECTED_SOURCE_CATALOGUE)


def selected_source(conflict: CatalogueConflict) -> str:
    """
    The source the recorded re-decision applied, read straight from
    the conflict record: CAPTURE for CAPTURE_GEOMETRY_APPLIED,
    CATALOGUE for CATALOGUE_GEOMETRY_APPLIED. Raises while the
    conflict is blocked — an applied source exists only when the
    human explicitly chose one.
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    if conflict.resulting_decision == RESULTING_DECISION_CAPTURE_GEOMETRY:
        return SELECTED_SOURCE_CAPTURE
    if conflict.resulting_decision == RESULTING_DECISION_CATALOGUE_GEOMETRY:
        return SELECTED_SOURCE_CATALOGUE
    raise ValueError(
        f"conflict {conflict.conflict_id!r} is blocked "
        f"({conflict.resulting_decision or 'no re-decision has run'}); no geometry "
        "may proceed without an explicit recorded human decision that applied one source."
    )


def build_resolved_section_row(conflict: CatalogueConflict) -> dict[str, Any]:
    """
    The complete section row for the selected source, assembled
    field-by-field from that source's own evidence on the conflict
    record — a NEW detached dict with deep-copied values, never a
    reference into the record and never a catalogue lookup.
    CAPTURE_AUTHORITATIVE yields the captured evidence verbatim;
    LIVE_CATALOGUE_AUTHORITATIVE yields the catalogue evidence
    supplied to the resolution verbatim. Raises while the conflict is
    blocked: no resolved row exists without an applied decision, so
    nothing can flow into the member adapter.
    """
    source = selected_source(conflict)  # raises while blocked
    values = (
        conflict.captured_values
        if source == SELECTED_SOURCE_CAPTURE
        else conflict.catalogue_values
    )
    row = {field: copy.deepcopy(value) for field, value in values}
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            f"conflict {conflict.conflict_id!r}: the selected {source} evidence carries "
            "no usable section name; no geometry may proceed."
        )
    return row


@dataclass(frozen=True)
class ResolvedSectionMatcher:
    """
    A matcher pinned to exactly one resolved section row — the row
    assembled from the conflict's selected source evidence. match()
    returns a fresh detached copy of that row for its own name and
    nothing for every other name. It carries no catalogue, no
    fallback and no reference to any other row, so a lookup can never
    resolve to anything but the selected source.
    """
    resolved_name: str
    resolved_fields: tuple[tuple[str, Any], ...]

    def match(self, name: Any) -> dict[str, Any] | None:
        if name != self.resolved_name:
            return None
        return {field: copy.deepcopy(value) for field, value in self.resolved_fields}


def build_resolved_matcher(conflict: CatalogueConflict) -> ResolvedSectionMatcher:
    """
    The source-pinned matcher for one re-decided conflict, built only
    from the conflict record's selected source evidence. Raises while
    the conflict is blocked — a matcher carrying authorised geometry
    can only exist once the human decision applied one source.
    """
    row = build_resolved_section_row(conflict)
    return ResolvedSectionMatcher(
        resolved_name=row["name"],
        resolved_fields=tuple(
            sorted((field, copy.deepcopy(value)) for field, value in row.items())
        ),
    )


def build_synthetic_member_row(
    conflict: CatalogueConflict,
    *,
    mark: str,
    length_mm: float,
    grade: str = "300",
    source_page: Any = None,
    source_drawing_id: Any = None,
) -> dict[str, Any]:
    """
    A synthetic steel_members-shaped row for one re-decided conflict
    — every section dimension still comes from the resolved row,
    never from this helper (the same shape the existing
    dimensionless-row adapter proofs use). Its section_name is the
    SELECTED source's own name, so the pinned matcher — and only the
    pinned matcher — matches it. Raises while the conflict is
    blocked: a member row carrying an authorised section can only
    exist once the human decision applied one source.
    """
    resolved = build_resolved_section_row(conflict)  # raises while blocked
    return {
        "mark": mark,
        "section_name": resolved["name"],
        "section_name_raw": conflict.captured_name,
        "section_family": resolved.get("family"),
        "length_mm": length_mm,
        "grade": grade,
        "quantity": 1,
        "review_status": "approved",
        "source_page": source_page,
        "source_drawing_id": source_drawing_id,
    }


@dataclass(frozen=True)
class ResolvedMemberGeometry:
    """
    The complete end-to-end result for one re-decided conflict: the
    conflict record itself (unchanged — both original source rows,
    the conflicting fields, the E1 verdict, the E2 decision, the
    rationale and the 7Z result all recoverable), the selected
    source, the detached resolved section row, the validated member
    the genuine existing adapter produced, and the genuine generated
    geometry.
    """
    conflict: CatalogueConflict
    selected_source: str
    resolved_section_row: tuple[tuple[str, Any], ...]
    validated_member: ValidatedSteelMember
    geometry: GeneratedMemberGeometry


def resolve_member_geometry(
    conflict: CatalogueConflict,
    *,
    mark: str,
    length_mm: float,
    grade: str = "300",
    source_page: Any = None,
    source_drawing_id: Any = None,
) -> ResolvedMemberGeometry:
    """
    The E3 integration: an explicitly human-resolved conflict feeds
    the selected source geometry into the existing member-adapter ->
    generate_geometry path, end to end.

    The adapter receives ONLY the source-pinned matcher built from
    the conflict record — this function has no matcher parameter, no
    catalogue access and no relookup path, so the selected source
    cannot be replaced by any other row. While the conflict is
    blocked (unresolved, un-re-decided, or KEEP_BOTH) this raises
    before any adapter call: no resolved row exists and the adapter
    is never invoked with authorised geometry.
    """
    if not isinstance(conflict, CatalogueConflict):
        raise TypeError(
            f"conflict must be a CatalogueConflict (got {type(conflict).__name__})."
        )
    resolved_row = build_resolved_section_row(conflict)  # raises while blocked
    matcher = build_resolved_matcher(conflict)
    member_row = build_synthetic_member_row(
        conflict,
        mark=mark,
        length_mm=length_mm,
        grade=grade,
        source_page=source_page,
        source_drawing_id=source_drawing_id,
    )
    validated = real_member_to_validated_member(member_row, matcher)
    geometry = generate_geometry(validated)
    return ResolvedMemberGeometry(
        conflict=conflict,
        selected_source=selected_source(conflict),
        resolved_section_row=tuple(
            sorted((field, copy.deepcopy(value)) for field, value in resolved_row.items())
        ),
        validated_member=validated,
        geometry=geometry,
    )
