"""
Milestone E1 — the source-of-truth boundary for section evidence.

Adopts the SteelSpec source-of-truth rule (SOURCE_OF_TRUTH_RULE):

  * The engineer's drawing is authoritative for what was actually
    specified on the project.
  * The section catalogue is authoritative for standard catalogue
    properties only where those properties do not conflict with the
    engineer's drawing.
  * If a catalogue value conflicts with a geometry-defining value
    extracted from the engineer's drawing, SteelSpec MUST NOT silently
    choose one — it must remain REVIEW/BLOCKED until the conflict is
    resolved by an explicit human decision or stronger authoritative
    evidence.

This module supplies the two explicit, deterministic schema mappings
evidenced by the Milestone E live reconciliation, and the conflict
boundary itself. It is ADDITIVE infrastructure: nothing here is wired
into the production pipeline, the production matcher, the DXF path or
any existing gate. It never chooses a side, never merges records, and
never mutates its inputs.

  * map_live_section_to_local(): the explicit live-schema -> local
    canonical representation adapter.
      - family FLAT -> FL (the local canonical family; the live
        source row is never renamed — a fresh detached dict is
        returned);
      - EA leg_size -> width (the local builder field for the leg
        dimension; the live field is never renamed in place);
      - everything else passes through verbatim, detached.
    The geometry builders keep receiving the local canonical fields
    (family "FL", EA "width"/"thickness"). The adapter never guesses:
    a live EA row without a leg_size is refused.

  * evaluate_section_evidence(): the conflict boundary. It compares
    geometry-defining fields only (depth, flange_width,
    flange_thickness, web_thickness, width, thickness, leg_size,
    outside_diameter) with exact equality. Semantics:

      - a field present on BOTH sides with DIFFERENT values is a
        CONFLICT — the verdict is CONFLICT_BLOCKED with resolution
        BLOCKED_REVIEW, and both recorded values ride on the verdict
        verbatim; nothing is averaged, merged or chosen;
      - a field absent (None) on either side is never a conflict:
        absent evidence fills, it never overrides;
      - weight_per_metre is deliberately NOT a geometry-defining
        field: a weight difference alone is a representation /
        precision discrepancy (see the 100PFC E1 decision in
        app/engineering_data/section_catalogue.py), never an
        engineering conflict.

  Name resolution (the SectionMatcher / CatalogueMatcher ".0"-".9"
  fallback included) is a separate mechanism and MUST flow through
  this boundary wherever capture-side and catalogue-side evidence
  meet: a fallback name match never, by itself, makes the matched
  geometry authoritative for a captured drawing (the 310UB40 /
  310UB40.4 flange-thickness conflict is the exact case).
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

__all__ = [
    "SOURCE_OF_TRUTH_RULE",
    "GEOMETRY_DEFINING_FIELDS",
    "LIVE_TO_LOCAL_FAMILY",
    "VERDICT_AGREEMENT",
    "VERDICT_CONFLICT_BLOCKED",
    "RESOLUTION_NO_CONFLICT",
    "RESOLUTION_BLOCKED_REVIEW",
    "SourceOfTruthVerdict",
    "map_live_section_to_local",
    "evaluate_section_evidence",
]

SOURCE_OF_TRUTH_RULE = (
    "The engineer's drawing is authoritative for what was actually specified on the project. "
    "The section catalogue is authoritative for standard catalogue properties only where those "
    "properties do not conflict with the engineer's drawing. If a catalogue value conflicts "
    "with a geometry-defining value extracted from the engineer's drawing, SteelSpec MUST NOT "
    "silently choose one — it must remain REVIEW/BLOCKED until the conflict is resolved by an "
    "explicit human decision or stronger authoritative evidence."
)

GEOMETRY_DEFINING_FIELDS = (
    "depth", "flange_width", "flange_thickness", "web_thickness",
    "width", "thickness", "leg_size", "outside_diameter",
)

# The one explicit controlled family mapping evidenced by Milestone E:
# the live table's family value "FLAT" is the local canonical "FL".
# Nothing else is mapped — every other live family passes through
# verbatim, and no family is ever renamed in the source record.
LIVE_TO_LOCAL_FAMILY: Mapping[str, str] = MappingProxyType({"FLAT": "FL"})

VERDICT_AGREEMENT = "AGREEMENT"
VERDICT_CONFLICT_BLOCKED = "CONFLICT_BLOCKED"

RESOLUTION_NO_CONFLICT = "NO_CONFLICT"
RESOLUTION_BLOCKED_REVIEW = "BLOCKED_REVIEW"


@dataclass(frozen=True)
class SourceOfTruthVerdict:
    """
    The frozen outcome of comparing capture-side section evidence
    against catalogue-side section evidence. `conflicting_fields` is a
    tuple of (field, capture_value, catalogue_value) triples — both
    values recorded verbatim, never chosen between, never merged.
    `resolution` is BLOCKED_REVIEW on a conflict and NO_CONFLICT
    otherwise: a conflict blocks, it never silently resolves.
    """
    status: str
    conflicting_fields: tuple[tuple[str, Any, Any], ...]
    resolution: str


def map_live_section_to_local(row: Mapping[str, Any]) -> dict[str, Any]:
    """
    The explicit, deterministic live-schema -> local canonical
    representation adapter. Returns a NEW detached plain dict; the
    source mapping is never mutated and its fields are never renamed
    in place.

      - family FLAT -> FL (the local canonical family);
      - EA leg_size -> width (the local builder field for the leg
        dimension; the adapted row carries width/thickness, exactly
        the local EA row shape, and no leg_size);
      - all other fields and families pass through verbatim.

    Fail-closed: a live EA row without a leg_size is refused — the
    adapter never guesses a leg dimension. Names are carried verbatim
    (live names keep their own spelling; naming conventions are a
    separate, deliberately unresolved decision).
    """
    if not isinstance(row, Mapping):
        raise TypeError(
            f"row must be a mapping of live steel_sections fields (got "
            f"{type(row).__name__})."
        )
    out = dict(row)
    family = row.get("family")
    if family == "EA":
        leg_size = row.get("leg_size")
        if leg_size is None:
            raise ValueError(
                "a live EA row without a leg_size cannot be adapted; the adapter "
                "never guesses a leg dimension."
            )
        out["width"] = leg_size
        out.pop("leg_size", None)
    elif family == "FLAT":
        out["family"] = "FL"
    return out


def evaluate_section_evidence(
    capture: Mapping[str, Any],
    catalogue: Mapping[str, Any],
) -> SourceOfTruthVerdict:
    """
    The source-of-truth conflict boundary: compares geometry-defining
    fields (GEOMETRY_DEFINING_FIELDS) between the capture-side and the
    catalogue-side row with exact equality.

      - both sides state a field with different values  -> CONFLICT:
        verdict CONFLICT_BLOCKED / resolution BLOCKED_REVIEW, both
        values recorded verbatim on the verdict;
      - a field is absent (None) on either side             -> never a
        conflict (absent evidence fills, it never overrides);
      - weight_per_metre is not geometry-defining and is deliberately
        never compared here: a weight difference alone is a
        representation/precision discrepancy, not an engineering
        conflict.

    Never chooses a side, never merges, never mutates its inputs. The
    caller decides what an AGREEMENT verdict proceeds with (its own
    canonical handling) and must route a CONFLICT_BLOCKED verdict to
    REVIEW/BLOCKED rather than resolve it programmatically.
    """
    if not isinstance(capture, Mapping):
        raise TypeError(f"capture must be a mapping (got {type(capture).__name__}).")
    if not isinstance(catalogue, Mapping):
        raise TypeError(f"catalogue must be a mapping (got {type(catalogue).__name__}).")
    conflicts: list[tuple[str, Any, Any]] = []
    for field in GEOMETRY_DEFINING_FIELDS:
        capture_value = capture.get(field)
        catalogue_value = catalogue.get(field)
        if capture_value is None or catalogue_value is None:
            continue
        if capture_value != catalogue_value:
            conflicts.append((field, capture_value, catalogue_value))
    if conflicts:
        return SourceOfTruthVerdict(
            status=VERDICT_CONFLICT_BLOCKED,
            conflicting_fields=tuple(conflicts),
            resolution=RESOLUTION_BLOCKED_REVIEW,
        )
    return SourceOfTruthVerdict(
        status=VERDICT_AGREEMENT,
        conflicting_fields=(),
        resolution=RESOLUTION_NO_CONFLICT,
    )
