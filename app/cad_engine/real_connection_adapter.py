"""
Milestone 7D — the boundary between a REAL extracted/persisted
connection detail and the CAD engine's ValidatedConnection contract:

    real connection record (engineering_data)
            |
       real_connection_to_validated_connection()
            |
      ValidatedConnection
            |
    existing CAD connection engine (Milestone 7E, NOT this one)

This module does not build geometry (it never calls
app.cad_engine.connections or generate_geometry) and does not
duplicate the CAD engine's own field-requirement lists — it imports
and re-checks against the exact same public registries
app.cad_engine.connections and app.cad_engine.interface already
expose (REQUIRED_PLATE_FIELDS, REQUIRED_HOLE_FIELDS,
SUPPORTED_HOLE_QUANTITIES, SUPPORTED_CONNECTION_POSITIONS,
SUPPORTED_CONNECTION_TYPES), never a second hardcoded copy of them.

INPUT SHAPE: real connection data is stored across FOUR tables, not
one — inspected directly from app/pipeline.py's `_run_pipeline()`:
`connections` (connection_type, grid_reference, detail_reference,
review_status, source_page/drawing_id), `connection_plates`
(plate_type, thickness, width, depth — note pipeline.py's own
`p.get("thickness_mm") or 0` means a real stored row can already have
a FALSE ZERO where the source drawing actually said nothing; this
module treats thickness <= 0 as missing, never as a real dimension),
`bolt_groups` (bolt_size as a STRING like "M20", bolt_grade, quantity
— no numeric hole diameter, no spacing, anywhere), and
`connection_members` (a link table to the member(s) a connection
joins). There is no existing repository function that joins these
back into one record, so this module's input is the natural assembled
shape a caller (or a future repository query) would hand it: one dict
with a `plates` list and a `bolts` list, mirroring
app.ai_analysis.pdf_vision_analyzer.py's own EXTRACTION_SYSTEM_PROMPT
output shape (`connects_members`, `connection_type`, `bolts[]`,
`plates[]`, `welds[]`) plus the few fields (`connection_id`,
`review_status`) that only exist once persisted.

THE HONEST FINDING THIS MODULE SURFACES: as of today, the real
extraction schema (see pdf_vision_analyzer.py's own prompt/example)
has NO field for connection position (START/END) and NO field for a
numeric hole-void diameter or hole spacing — bolts are only ever
captured as a nominal size string ("M20") plus a grade. This means a
connection built from exactly what Claude's vision extraction
currently reports can NEVER reach a complete ValidatedConnection: it
will always be rejected for missing position and missing hole
geometry, regardless of how good the plate data is. That is not a bug
in this module — see tests/test_real_connection_adapter.py's primary
fixture, built directly from the real extraction schema, which proves
exactly this rejection path. A second, clearly-labelled fixture in
that same file proves the adapter DOES succeed once a record actually
supplies the fields the CAD contract needs (e.g. after a human
reviewer supplements position and dimensions) — proving the success
path is real, not merely untested.

BOLT SIZE IS NOT HOLE DIAMETER: `bolts[0]['size']` (e.g. "M20") is a
nominal bolt designation, not a hole-void diameter in mm — a real M20
bolt commonly sits in a slightly larger clearance hole, and this
module has no reliable, source-stated conversion between the two. It
never reads `size` (or `grade`) as a source for
ValidatedConnection.bolts[0]['diameter']; an explicit numeric
`diameter_mm` is required instead. Likewise, this module never
populates ValidatedConnection.physical_bolts — nothing in the current
extraction schema captures shaft/head hardware dimensions, and
fabricating them from a nominal bolt size would be exactly the kind of
invented physical-hardware geometry this milestone forbids.
"""
import math
from typing import Any

from app.cad_engine import connections as connection_geometry
from app.cad_engine import interface as cad_interface
from app.cad_engine.errors import GeometryValidationError
from app.cad_engine.interface import ValidatedConnection

__all__ = ["real_connection_to_validated_connection"]

# The only plate `type` string (from the real extraction schema's own
# `plates[].type` field — see pdf_vision_analyzer.py's example) this
# module treats as evidence of an END_PLATE connection. A generic
# connection_type of "bolted"/"welded" is too coarse to mean END_PLATE
# specifically (a bolted connection could just as easily be a cleat, a
# gusset, or a base plate) — the plate's own stated type is the only
# explicit signal specific enough to trust.
_END_PLATE_TYPE_VALUE = "end_plate"


def _finite_float_or_none(value: Any) -> float | None:
    """
    `value` as a finite float, or None if it is not a real (non-boolean)
    number or cannot be represented as a finite float. The conversion
    itself is what decides representability: a Python int beyond float
    range (e.g. 10**400) makes float() raise OverflowError, which is
    translated here into "not a valid number" instead of escaping. No
    maximum dimension is imposed and nothing is clamped or truncated —
    any value that IS a finite float (e.g. 10**300) is returned as-is;
    whether it is a sensible dimension is not this function's concern.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _is_positive_finite_number(value: Any) -> bool:
    """
    A real, non-boolean number that is representable as a FINITE float
    and greater than zero. The original `value <= 0` check accepted NaN
    and +infinity (every NaN comparison is False), and a later
    oversized-integer review found ints such as 10**400 passing here and
    then raising OverflowError at the next float() — see
    _finite_float_or_none().
    """
    number = _finite_float_or_none(value)
    return number is not None and number > 0


def _is_positive_finite_integer(value: Any) -> bool:
    """Like _is_positive_finite_number, and additionally integral (4 or 4.0, never 4.5)."""
    number = _finite_float_or_none(value)
    return number is not None and number > 0 and number.is_integer()


def _require_trustworthy_connection_id(record: dict) -> str:
    connection_id = record.get("connection_id")
    if connection_id is None or not str(connection_id).strip():
        raise GeometryValidationError(
            f"Connection cannot generate CAD geometry: no trustworthy connection_id is recorded "
            f"(got {connection_id!r})."
        )
    return str(connection_id).strip()


def _require_member_association(record: dict, connection_id: str) -> list[str]:
    marks = record.get("connected_member_marks")
    if not marks or not isinstance(marks, list):
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: no member association "
            "is recorded (connected_member_marks is missing or empty)."
        )
    cleaned = [str(m).strip() for m in marks]
    if any(not m for m in cleaned) or len(cleaned) != len(set(cleaned)):
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: connected_member_marks "
            f"is ambiguous (got {marks!r}) — it contains a blank or duplicate entry."
        )
    return cleaned


def _require_supported_connection_type(record: dict, connection_id: str) -> str:
    """
    The CAD engine only builds one connection type (END_PLATE) — see
    app.cad_engine.interface.SUPPORTED_CONNECTION_TYPES. The real
    extraction's own connection_type field ("bolted", "welded", ...) is
    too generic to mean END_PLATE specifically; the plate's own `type`
    sub-field is the only explicit signal this module trusts for that.
    """
    plates = record.get("plates")
    if not plates:
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: no plate information is "
            "recorded, so no supported connection type can be established."
        )
    plate_type = str(plates[0].get("type") or "").strip().lower()
    if plate_type != _END_PLATE_TYPE_VALUE:
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: plate type "
            f"{plates[0].get('type')!r} does not correspond to a supported connection type. "
            f"Supported: {sorted(cad_interface.SUPPORTED_CONNECTION_TYPES)}."
        )
    connection_type = "END_PLATE"
    if connection_type not in cad_interface.SUPPORTED_CONNECTION_TYPES:
        raise GeometryValidationError(  # defensive; unreachable while END_PLATE is the only type
            f"Connection '{connection_id}' cannot generate CAD geometry: connection type "
            f"{connection_type!r} has no supported CAD builder."
        )
    return connection_type


def _require_explicit_position(record: dict, connection_id: str) -> str:
    """
    Never inferred from list order, connection ID numbering, or any
    other proxy — see this module's docstring. The real extraction
    schema has no position field at all today, so this will reject
    every as-extracted connection; that is the correct, honest
    behaviour until the extraction schema (or a human reviewer)
    actually supplies one.
    """
    position = record.get("position")
    if position not in connection_geometry.SUPPORTED_CONNECTION_POSITIONS:
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: no explicit position "
            f"(START/END) is recorded (got {position!r}). Position is never inferred from list "
            "order or connection numbering — it must be an explicit, source-stated value."
        )
    return position


def _require_plate_geometry(record: dict, connection_id: str) -> dict:
    plate = record["plates"][0]  # presence already confirmed by _require_supported_connection_type

    width = plate.get("width_mm")
    height = plate.get("depth_mm")  # extraction schema's "depth" = the CAD schema's plate "height"
    thickness = plate.get("thickness_mm")

    missing = [
        name for name, value in (("width_mm", width), ("depth_mm", height), ("thickness_mm", thickness))
        if not _is_positive_finite_number(value)
    ]
    if missing:
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: plate {missing} "
            f"{'is' if len(missing) == 1 else 'are'} not available (or not a positive finite number) from "
            "the source drawing. Refusing to substitute a typical/standard plate dimension."
        )

    return {"width": float(width), "height": float(height), "thickness": float(thickness)}


def _require_hole_geometry(record: dict, connection_id: str) -> dict:
    bolts = record.get("bolts")
    if not bolts:
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: no hole/bolt information "
            "is recorded for this connection."
        )
    bolt_group = bolts[0]

    quantity = bolt_group.get("quantity")
    diameter = bolt_group.get("diameter_mm")  # NEVER bolt_group['size'] — see module docstring
    vertical_spacing = bolt_group.get("vertical_spacing_mm")
    horizontal_spacing = bolt_group.get("horizontal_spacing_mm")

    missing = []
    if not _is_positive_finite_integer(quantity):
        missing.append("quantity")
    if not _is_positive_finite_number(diameter):
        missing.append("diameter_mm")
    if not _is_positive_finite_number(vertical_spacing):
        missing.append("vertical_spacing_mm")
    if quantity in connection_geometry.SUPPORTED_HOLE_QUANTITIES and quantity == 4:
        if not _is_positive_finite_number(horizontal_spacing):
            missing.append("horizontal_spacing_mm")
    if missing:
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: hole {missing} "
            f"{'is' if len(missing) == 1 else 'are'} not explicitly available from the source "
            "drawing. A nominal bolt size (e.g. 'M20') is not a hole-void diameter and is never "
            "used as a substitute."
        )
    if int(quantity) not in connection_geometry.SUPPORTED_HOLE_QUANTITIES:
        raise GeometryValidationError(
            f"Connection '{connection_id}' cannot generate CAD geometry: hole quantity {quantity} "
            f"is not a supported pattern. Supported: {sorted(connection_geometry.SUPPORTED_HOLE_QUANTITIES)}."
        )

    hole_spec = {"diameter": float(diameter), "quantity": int(quantity), "vertical_spacing": float(vertical_spacing)}
    if quantity == 4:
        hole_spec["horizontal_spacing"] = float(horizontal_spacing)
    return hole_spec


def real_connection_to_validated_connection(record: dict) -> ValidatedConnection:
    """
    Converts one real, assembled connection record into a
    ValidatedConnection, or raises GeometryValidationError explaining
    exactly why it can't. Deterministic and side-effect free — this
    function never calls generate_geometry() or any function in
    app.cad_engine.connections; it only validates and reshapes data.

    Raises GeometryValidationError when:
      - connection_id is missing or blank;
      - the member association (connected_member_marks) is missing,
        empty, or ambiguous (a blank or duplicate entry);
      - no plate is recorded, or the plate's own `type` does not
        indicate a supported connection type (only "end_plate" is
        recognised — see this module's docstring for why the generic
        connection_type field isn't used for this);
      - no explicit position (START/END) is recorded;
      - plate width/height(depth)/thickness are missing, non-numeric,
        or not positive;
      - hole quantity/diameter/vertical_spacing (and
        horizontal_spacing, when quantity is 4) are missing,
        non-numeric, or not positive — a nominal bolt size string
        ("M20") is never read as a hole diameter;
      - hole quantity is not one of
        app.cad_engine.connections.SUPPORTED_HOLE_QUANTITIES.

    Never populates `physical_bolts` — see this module's docstring for
    why: nothing in the current extraction schema captures physical
    hardware dimensions, so this field is always left at its default
    (None).
    """
    connection_id = _require_trustworthy_connection_id(record)
    connected_members = _require_member_association(record, connection_id)
    connection_type = _require_supported_connection_type(record, connection_id)
    position = _require_explicit_position(record, connection_id)
    plate_spec = _require_plate_geometry(record, connection_id)
    hole_spec = _require_hole_geometry(record, connection_id)

    source_refs: list[dict[str, Any]] = []
    if record.get("source_page") is not None:
        source_refs.append({
            "page": record["source_page"],
            "drawing_id": record.get("source_drawing_id"),
        })

    return ValidatedConnection(
        connection_id=connection_id,
        connected_members=connected_members,
        plates=[plate_spec],
        bolts=[hole_spec],
        welds=[],
        dimensions=None,
        source_refs=source_refs,
        validation_status="extracted",
        connection_type=connection_type,
        position=position,
        physical_bolts=None,
    )
