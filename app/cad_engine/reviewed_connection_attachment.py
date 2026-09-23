"""
Milestone 7N — the smallest bridge from a HUMAN-REVIEWED connection
detail record to the existing 7M `ConnectionAttachment` contract:

    reviewed connection-detail record (attachments: [...])
            |
       reviewed_connection_detail_to_attachments()
            |
      list[ConnectionAttachment]
            |
    existing 7M resolve_connection_attachment_surfaces() (unchanged)

STEP 1 FINDING — the real AI extraction schema, quoted directly from
app/ai_analysis/pdf_vision_analyzer.py's own EXTRACTION_SYSTEM_PROMPT
(not assumed): a connection object contains exactly
`detail_reference`, `grid_reference`, `connects_members`,
`connection_type`, `bolts[]`, `plates[]`, `welds[]`, `confidence`.
There is no `connection_id` (assigned only once persisted — see
real_connection_adapter.py's own docstring), no `position`
(START/END), and — the gap this milestone fills — NOTHING resembling
an attachment surface or a per-member surface reference anywhere in
the schema. The vision model has never been asked for this
information and does not provide it today. This module does not
change that: it only defines what a REVIEWER supplies once they
provide it, and the boundary stays exactly where 7D/7J already drew
it:

    AI extraction  = proposed/extracted information (no attachment data)
    Human review   = verified/supplemented information (attachment data lives here)
    CAD            = consumes only validated information

STEP 3 — WHAT THIS RECORD DOES AND DOES NOT CONTAIN: a reviewed
connection-detail record is expected to look like:

    {
        "connection_id": "CONN-TEST-001",   # cross-references an EXISTING
                                             # ValidatedConnection's own id —
                                             # never a new identity
        "review_status": "approved",
        "attachments": [
            {"member_mark": "REAL-UB-CAD-001", "surface_reference": "END"},
            {"member_mark": "L2", "surface_reference": "START"},
        ],
    }

It deliberately does NOT carry connected_member_marks,
connection_type, plate dimensions, or hole geometry — those already
have one authoritative source (ValidatedConnection, from 7D/7H) and
duplicating them here would create a second, competing representation
of the same facts. Instead, `reviewed_connection_detail_to_attachments()`
takes the authoritative `connected_member_marks` as an explicit
parameter (sourced from the caller's already-validated
ValidatedConnection), and validates attachments against it — never
re-deriving or storing its own copy.

STEP 6 — REUSES, NEVER DUPLICATES: the output is a plain
`list[app.cad_engine.connection_attachment.ConnectionAttachment]` —
the exact same type 7M's resolve_connection_attachment_surfaces()
already consumes. No new "ReviewedConnectionAttachment" or
"DrawingAttachment" type is introduced; this module's only job is
producing the existing type from a raw reviewed dict, the same
adapter shape as real_connection_adapter.py (7D) and
connection_location.py's real_connection_location_to_connection_location()
(7J).

STEP 17 — NO INFERENCE: this module never reads connection.position,
never reads member order, never reads geometry, and never defaults a
missing surface_reference to "END" or "START". Every attachment must
be explicitly present in the reviewed record with a valid, supported
surface_reference string, or the whole record is rejected.
"""
from typing import Any

from app.cad_engine import connections as connection_geometry
from app.cad_engine.connection_attachment import ConnectionAttachment
from app.cad_engine.errors import GeometryValidationError

__all__ = ["reviewed_connection_detail_to_attachments"]


def reviewed_connection_detail_to_attachments(
    record: dict, connected_member_marks: list[str],
) -> list[ConnectionAttachment]:
    """
    Converts a reviewed connection-detail record's explicit
    `attachments` list into `ConnectionAttachment` objects, or raises
    GeometryValidationError explaining exactly why it can't.
    Deterministic and side-effect free — never touches CadQuery, never
    calls resolve_connection_attachment_surfaces() itself, never reads
    connection_type/plates/bolts/position from `record` even if
    present.

    `connected_member_marks` is the caller's already-validated
    ValidatedConnection.connected_members — the sole authority for
    "which members must this attachment list cover", never re-derived
    from `record` itself.

    Raises GeometryValidationError when:
      - `attachments` is missing, not a list, or empty;
      - any entry is not an object, or has a missing/blank/non-string
        member_mark or surface_reference;
      - any surface_reference is not a supported value (only "END"/
        "START" — see app.cad_engine.connections.SUPPORTED_CONNECTION_POSITIONS);
      - two entries reference the same member_mark (a duplicate attachment);
      - an entry's member_mark is not among `connected_member_marks`;
      - the resulting attachment marks do not exactly cover
        `connected_member_marks` (a missing attachment for a connected member).
    """
    connection_id = record.get("connection_id")
    label = str(connection_id).strip() if connection_id else "(unlabelled)"

    raw_attachments = record.get("attachments")
    if not raw_attachments or not isinstance(raw_attachments, list):
        raise GeometryValidationError(
            f"Connection detail {label}: no attachment information is recorded (attachments is "
            "missing or empty). A surface reference is never defaulted to END or START."
        )

    attachments: list[ConnectionAttachment] = []
    seen_marks: set[str] = set()
    for i, entry in enumerate(raw_attachments):
        if not isinstance(entry, dict):
            raise GeometryValidationError(
                f"Connection detail {label}: attachment entry {i} is malformed (expected an "
                f"object with member_mark/surface_reference, got {entry!r})."
            )

        mark: Any = entry.get("member_mark")
        if not isinstance(mark, str) or not mark.strip():
            raise GeometryValidationError(
                f"Connection detail {label}: attachment entry {i} has no trustworthy "
                f"member_mark (got {mark!r})."
            )
        mark = mark.strip()

        ref: Any = entry.get("surface_reference")
        if not isinstance(ref, str) or not ref.strip():
            raise GeometryValidationError(
                f"Connection detail {label}: attachment for member '{mark}' has no trustworthy "
                f"surface_reference (got {ref!r}). Never defaulted to END or START."
            )
        ref = ref.strip()
        if ref not in connection_geometry.SUPPORTED_CONNECTION_POSITIONS:
            raise GeometryValidationError(
                f"Connection detail {label}: attachment for member '{mark}' has unsupported "
                f"surface_reference {ref!r}. Supported: "
                f"{sorted(connection_geometry.SUPPORTED_CONNECTION_POSITIONS)}."
            )

        if mark in seen_marks:
            raise GeometryValidationError(
                f"Connection detail {label}: multiple attachments reference member '{mark}' — a "
                "duplicate attachment. Each connected member must have exactly one."
            )
        seen_marks.add(mark)

        if mark not in connected_member_marks:
            raise GeometryValidationError(
                f"Connection detail {label}: attachment references member '{mark}', which is not "
                f"among this connection's connected_member_marks {sorted(connected_member_marks)}."
            )

        attachments.append(ConnectionAttachment(member_mark=mark, surface_reference=ref))

    if seen_marks != set(connected_member_marks):
        raise GeometryValidationError(
            f"Connection detail {label}: attachments {sorted(seen_marks)} do not exactly cover "
            f"connected_member_marks {sorted(connected_member_marks)} — every connected member "
            "needs exactly one explicit attachment."
        )

    return attachments
