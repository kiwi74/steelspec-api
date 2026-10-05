"""
E2E-001A — THE WIRE FORM OF A PRODUCTION REVIEW.

WHAT THIS MODULE IS

The JSON rendering of the composition `app.production_connection_review.build_workflow_review`
already produces. It is the data twin of `app.review_ui.render.render_workflow_review_page`:
the same `WorkflowReview`, the same values, rendered as data rather than as markup.

WHY IT IS A MODULE OF ITS OWN

Because of a boundary J25 drew and this milestone must not weaken. The HTTP layer composes
nothing and names nothing below the route: `tests/test_real_world_j25_connection_review_surface.py`
proves that the ONLY names `app/main.py` takes from `app.cad_engine` are 7AJ's refusal
TYPES, and that a second cad_engine import — of a builder, a state, or a vocabulary — is
refused there. Naming an answer type in `main.py` would be exactly that second import.

So the route imports this module and this module owns the rendering, the same way
`app/production_connection_review` owns the composition the page route calls. Nothing here
is an authority: it decides nothing, validates nothing, refuses nothing, reads nothing and
writes nothing. Every value it emits is one the composition already produced, copied
field for field.

THE ONE THING IT ADDS

`answer_payload` — a DESCRIPTION of the `answer` payload each answer type carries, keyed
by the contract's own constants from `app/cad_engine/exception_resolution.py`, whose
comments beside those constants are its only source. It exists because a native client
must build a payload to submit a resolution and cannot read a Python comment. It is a
description and never a gate: the resolution contract remains the only thing that decides
whether an answer stands, so a client that ignores this and sends something else is
refused by that contract rather than corrected here. An answer type absent from the table
is reported as `UNKNOWN` rather than guessed at, and a client that cannot build a payload
must not send one.

WHAT IT DOES NOT DO

It takes no claim, records no revision, opens no review, generates no artifact, reads no
working directory and calls no model. `ARTIFACT_WORKING_DIR` is not mentioned and not
read anywhere in this file.
"""
from __future__ import annotations

from typing import Any

from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_FIELD_DECISION,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_PLATE_VALUE,
    ANSWER_POSITION_VALUE,
)

__all__ = ["workflow_wire", "PAYLOAD_SHAPES"]

#: What an `answer` payload for each answer type is, taken from that type's own comment in
#: `app/cad_engine/exception_resolution.py`. A type absent from this table is described as
#: UNKNOWN rather than guessed at.
PAYLOAD_SHAPES: dict[str, tuple[str, tuple[str, ...]]] = {
    ANSWER_APPROVE_REVIEW: ("none", ()),
    ANSWER_AUTOMATION_CONFIRMATION: ("none", ()),
    ANSWER_ACKNOWLEDGMENT: ("text", ()),
    ANSWER_CONNECTION_IDENTITY: ("text", ()),
    ANSWER_POSITION_VALUE: ("text", ()),
    ANSWER_MATERIAL_VALUE: ("text", ()),
    ANSWER_MEMBER_SELECTION: ("texts", ()),
    ANSWER_CONFIRMED_FIELDS: ("texts", ()),
    ANSWER_PLATE_VALUE: ("mapping", ("type", "thickness_mm", "width_mm", "depth_mm")),
    ANSWER_HOLES_VALUE: ("mapping", ("quantity", "diameter_mm", "spacings")),
    ANSWER_LOCATION_VALUE: (
        "mapping",
        ("x", "y", "z", "rotation_x", "rotation_y", "rotation_z"),
    ),
    ANSWER_ATTACHMENTS_VALUE: ("attachments", ("member_mark", "surface_reference")),
    ANSWER_MEMBER_POSITION_ATTACHMENTS: (
        "member_position_attachments",
        ("marks", "position", "attachments"),
    ),
    ANSWER_FIELD_DECISION: ("field_decision", ("field", "action", "value")),
}


def _task_wire(task) -> dict[str, Any]:
    """One task as data. Every field is one the rendered review page already shows."""
    kind, fields = PAYLOAD_SHAPES.get(task.answer_type, ("UNKNOWN", ()))
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "title": task.title,
        "description": task.description,
        "field": task.field,
        "required": task.required,
        "resolved": task.resolved,
        "current_value": task.current_value,
        "allowed_options": list(task.allowed_options),
        "evidence_requirement": task.evidence_requirement,
        "resolution_value": task.resolution_value,
        "resolution_evidence": task.resolution_evidence,
        "answer_type": task.answer_type,
        "answer_payload": {"kind": kind, "fields": list(fields)},
    }


def _finding_wire(finding) -> dict[str, Any]:
    """One blocker or warning, verbatim — the contract's own code, title and message."""
    return {
        "code": finding.code,
        "title": finding.title,
        "message": finding.message,
        "severity": finding.severity,
        "severity_label": finding.severity_label,
        "field": finding.field,
        "task_type": finding.task_type,
    }


def _connection_wire(item) -> dict[str, Any]:
    """One connection's review as data: identity, decision, evidence, tasks, output."""
    identity = item.identity
    return {
        "package_id": identity.package_id,
        "connection_id": identity.connection_id,
        "display_reference": identity.display_reference,
        "decision": item.decision,
        "decision_label": item.decision_label,
        "summary": item.summary,
        "attention": {
            "requires_attention": item.attention.requires_attention,
            "label": item.attention.label,
        },
        "blockers": [_finding_wire(finding) for finding in item.blockers],
        "warnings": [_finding_wire(finding) for finding in item.warnings],
        "evidence": {
            "source_drawing_id": item.evidence.source_drawing_id,
            "drawing_number": item.evidence.drawing_number,
            "source_page": item.evidence.source_page,
            "detail_reference": item.evidence.detail_reference,
            "grid_reference": item.evidence.grid_reference,
            "evidence_text": item.evidence.evidence_text,
        },
        "extracted": {
            "member_references": list(item.extracted.member_references),
            "bolt_readings": list(item.extracted.bolt_readings),
            "plate_readings": list(item.extracted.plate_readings),
            "weld_readings": list(item.extracted.weld_readings),
            "malformed_readings": list(item.extracted.malformed_readings),
            "unrecognised_readings": list(item.extracted.unrecognised_readings),
            "connection_type": item.extracted.connection_type,
            "confidence": item.extracted.confidence,
            "material": item.extracted.material,
        },
        "provenance": [
            {
                "field": record.field,
                "provenance": record.provenance,
                "provenance_label": record.provenance_label,
            }
            for record in item.provenance
        ],
        "tasks": [_task_wire(task) for task in item.tasks],
        "actions": [{"action": a.action, "label": a.label} for a in item.actions],
        "output": {
            "output_status": item.output.output_status,
            "output_label": item.output.output_label,
            "verification_status": item.output.verification_status,
            "verification_label": item.output.verification_label,
            "generated_files": list(item.output.generated_files),
        },
    }


def workflow_wire(review) -> dict[str, Any]:
    """The whole review as data, from the composition the page route already renders.

    `revision` is the revision the store HAS RECORDED — the same value `POST /open`
    reports and the value a client must send back as `expected_revision`. It is read from
    the persisted chain and never recomputed, and a project with nothing recorded reports
    0 with `revision_recorded` false, so a client can tell "not opened yet" from
    "opened at 0".

    A reconstruction that REFUSED is reported as that refusal, in the composition's own
    `refusal_code`/`refusal_detail`, so a client is never handed an empty review in place
    of a refused one.
    """
    view = review.view if review.view is not None else review.persisted_view
    recorded = tuple(review.persisted_revisions)

    return {
        "project_id": review.project_id,
        "revision": max(recorded) if recorded else 0,
        "revision_recorded": bool(recorded),
        "recorded_revisions": list(recorded),
        "persisted_code": review.persisted_code,
        "refusal_code": review.refusal_code,
        "refusal_detail": review.refusal_detail,
        "identity": [list(pair) for pair in review.identity],
        "coverage": [list(pair) for pair in review.coverage],
        "capture_runs": list(review.capture_runs),
        "limitations": [list(triple) for triple in review.limitations],
        "project_status": view.project_status if view is not None else None,
        "status_label": view.status_label if view is not None else None,
        "summary": view.summary if view is not None else None,
        "counts": (
            {
                "review": view.counts.review,
                "verified": view.counts.verified,
                "auto": view.counts.auto,
                "confirmation": view.counts.confirmation,
                "blocked": view.counts.blocked,
            }
            if view is not None
            else None
        ),
        "actions": (
            [{"action": a.action, "label": a.label} for a in view.actions]
            if view is not None
            else []
        ),
        "connections": (
            [_connection_wire(item) for item in view.review_items]
            + [_connection_wire(item) for item in view.completed_items]
        )
        if view is not None
        else [],
    }
