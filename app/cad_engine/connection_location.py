"""
Milestone 7J — the smallest explicit contract for locating a reviewed
multi-member connection in project coordinates:

    HUMAN-REVIEWED CONNECTION record (x, y, z, rotation_x, rotation_y, rotation_z)
            |
       real_connection_location_to_connection_location()
            |
      ConnectionLocation
            |
    (assembly metadata only — see module docstring's scope boundary)

THE PROBLEM (Step 2): a ValidatedConnection's existing `position`
field ("START"/"END") is single-member-relative — it says which end
of ONE member a plate attaches to, not where the connection sits in
shared project space, and not how two members relate to each other
spatially. Knowing Member A is "END" and Member B is "START" says
nothing about their relative project coordinates, the connection's own
X/Y/Z location, its orientation, or which face/axis is involved. This
module exists to hold that missing, explicitly-supplied information —
it does not compute it from anything else.

ARCHITECTURE (Step 1 finding): reused, not duplicated. app/cad_engine/
placement.py already established the validation conventions this
module needs (reject non-numeric/boolean/NaN/infinite, never default
a missing value to zero) for MemberPlacement — a conceptually
DIFFERENT thing (a rigid transform applied to a member's own solid).
A connection location is not a member placement: it has no solid to
transform, no section/length to preserve, and (per Step 8) must never
duplicate connection identity or the member list already owned by
ValidatedConnection. So this module defines its own small, separate
type — ConnectionLocation — rather than reusing MemberPlacement
directly or bolting new fields onto ValidatedConnection.

SCOPE BOUNDARY — this module and its tests
(tests/test_real_connection_location.py) do not:
  - generate connection CAD geometry;
  - attach/fuse a connection to either member's solid;
  - position a connection relative to a member's local frame;
  - infer location from member placement, member length/section,
    END/START position, plate dimensions, hole positions, member
    intersection, connection ID, mark, page number, or drawing/list
    order;
  - perform clash detection, midpoint/intersection calculation, or any
    other geometry math.
It stores exactly one thing: an explicit, reviewed rigid-body location
(x, y, z, rotation_x, rotation_y, rotation_z), validated and nothing
more. Where and how that location eventually participates in real
connection geometry is a later assembly-geometry milestone's decision.

IDENTITY (Step 8): ConnectionLocation intentionally carries no
connection_id and no member list — those already live on
ValidatedConnection, and duplicating them here would create two
sources of truth for the same identity. A caller associates a
ConnectionLocation with its connection externally (e.g. a
{connection_id: ConnectionLocation} mapping), the same way
GeneratedAssembly (app/cad_engine/assembly.py, Milestone 7I) already
associates a MemberPlacement with a member via PlacedMember rather
than embedding placement fields on ValidatedSteelMember itself.
"""
import math
from dataclasses import dataclass
from typing import Any

from app.cad_engine.errors import GeometryValidationError

__all__ = ["ConnectionLocation", "real_connection_location_to_connection_location"]


@dataclass(frozen=True)
class ConnectionLocation:
    """
    A minimal, explicit, immutable rigid-body location: translation
    (x, y, z) in mm and rotation (rotation_x, rotation_y, rotation_z)
    in degrees, in the same shared project coordinate system
    app.cad_engine.placement.MemberPlacement values are expressed in.

    Deliberately NOT a general transformation framework and
    deliberately carries no identity (see module docstring) — a plain
    geometry-only data container, nothing more.
    """
    x: float
    y: float
    z: float
    rotation_x: float
    rotation_y: float
    rotation_z: float


def _require_finite_numeric(value: Any, field_name: str, label: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryValidationError(
            f"Connection {label}: location field '{field_name}' must be a real number "
            f"(got {value!r}). A missing coordinate is never defaulted to zero."
        )
    try:
        number = float(value)
    except OverflowError:
        # e.g. 10**400: an int with no finite float representation — rejected,
        # never clamped, truncated or allowed to escape as an OverflowError.
        raise GeometryValidationError(
            f"Connection {label}: location field '{field_name}' must be finite; the supplied "
            "integer is too large to be represented as a finite number."
        ) from None
    if math.isnan(number) or math.isinf(number):
        raise GeometryValidationError(
            f"Connection {label}: location field '{field_name}' must be finite, not "
            f"NaN/infinite (got {number})."
        )
    return number


def real_connection_location_to_connection_location(record: dict) -> ConnectionLocation:
    """
    Converts a reviewed connection record's explicit x/y/z/rotation_x/
    rotation_y/rotation_z fields into a ConnectionLocation, or raises
    GeometryValidationError explaining exactly which field is missing
    or invalid. Deterministic and side-effect free: never touches
    CadQuery, never inspects generated geometry, never computes a
    coordinate from anything else in `record` (plates, bolts,
    connected_member_marks, position, ...) — those fields, if present,
    are ignored entirely by this function.

    `record.get("connection_id")` is read ONLY to make an error message
    readable when present — it is never validated or required by this
    function (that is real_connection_adapter.py's job), and it is
    never stored on the returned ConnectionLocation (see module
    docstring's identity note).
    """
    label = str(record.get("connection_id") or "").strip() or "(unlabelled)"

    x = _require_finite_numeric(record.get("x"), "x", label)
    y = _require_finite_numeric(record.get("y"), "y", label)
    z = _require_finite_numeric(record.get("z"), "z", label)
    rotation_x = _require_finite_numeric(record.get("rotation_x"), "rotation_x", label)
    rotation_y = _require_finite_numeric(record.get("rotation_y"), "rotation_y", label)
    rotation_z = _require_finite_numeric(record.get("rotation_z"), "rotation_z", label)

    return ConnectionLocation(
        x=x, y=y, z=z, rotation_x=rotation_x, rotation_y=rotation_y, rotation_z=rotation_z,
    )
