"""
Milestone E5 — RESOLVED CONFLICT -> FABRICATOR-FACING DELIVERABLE ->
INDEPENDENT ACCEPTANCE: an explicitly human-resolved catalogue conflict,
already carried through E3 geometry and an E4-verified fabrication
drawing, is gathered into a deterministic, fabricator-facing deliverable
whose conflict provenance is recoverable from the deliverable itself —
and an INDEPENDENT acceptance evaluation inspects that deliverable on
disk and returns a truthful verdict.

Lifecycle proven here (additive — nothing is wired into the production
pipeline, matcher, DXF path, review UI, Supabase layer or any project
workflow):

    SOURCE CONFLICT -> 7AC -> 7AD human resolution -> 7Z re-decision (E2)
        -> E3 RESOLVED GEOMETRY      resolved_conflict_geometry
        -> E4 VERIFIED PDF           resolved_conflict_drawing
        -> E5 DELIVERABLE            build_resolved_conflict_deliverable():
                                     a byte copy of the RECORDED 7AG-
                                     verified artifact plus a
                                     deterministic manifest.json
        -> E5 ACCEPTANCE             evaluate_resolved_conflict_deliverable():
                                     independent evidence from the real
                                     files on disk, reconciled against the
                                     reviewer's answers through the
                                     existing 7AS matrix
        -> ACCEPTED / NOT_ACCEPTED / REFUSED

WHAT THIS MODULE DELIBERATELY IS NOT. It is a SEPARATE, INDEPENDENT
resolved-conflict deliverable/acceptance path. It does NOT enter, call,
extend or imitate the genuine production job chain
(7AJ -> 7AQ -> 7AR -> 7AS): that chain consumes a real
`ProjectWorkflowState`, a real `ProductionJobAcceptance` and a real
`FabricationDrawingPackage`, and a resolved catalogue conflict carries
none of those — a catalogue-level section conflict is not a connection
blocker (E2's own documented boundary). Fabricating any of those three
objects to force this path through the production entries would
misrepresent a synthetic test decision as a real production acceptance,
so this module never constructs them, never imports their builders, and
has no parameter by which one could be injected. What it reuses is the
existing VOCABULARY AND SEMANTICS — statuses, evidence classes, finding
kinds, checklist items, check codes and the 7AS reconciliation matrix —
imported, never redefined.

HARD RULES:

  - THE RECORDED ARTIFACT IS COPIED, NEVER REGENERATED. The packaged PDF
    is a byte-for-byte copy of the artifact 7AG recorded and verified.
    This module never calls the drawing generator, never repairs,
    re-renders or re-verifies a drawing, and never creates a second one.
    7AG remains the only authority on what VERIFIED means.
  - NOTHING IS INVENTED. Every manifest value is carried verbatim from
    the conflict record, the E3 resolved geometry or the E4 recorded
    stage results, or is reported absent. A value the record does not
    carry is omitted or reported MISSING — never defaulted to something
    plausible, never inferred. Material that the drawing does not state
    stays "NOT SPECIFIED".
  - THE DECISION IS SYNTHETIC TEST EVIDENCE. The recorded human decision
    is an explicit test input recorded by the E2 boundary; it is not a
    real engineering decision, confers no production authority, and is
    labelled as such on every deliverable this module produces.
  - FAIL-CLOSED, AND NOTHING PARTIAL. Every check runs BEFORE any output
    is created. A refused or blocked deliverable writes no directory, no
    drawing and no manifest — there is no partial deliverable that could
    be mistaken for one.
  - DETERMINISTIC AND READ-ONLY ON EVERYTHING ELSE. Same E4 result ->
    identical manifest bytes; no timestamps, no clock, no randomness, no
    network, no database. Inputs are never mutated; the real catalogue
    rows and the real capture evidence are never touched.

DELIVERABLE LAYOUT (written only on READY):

    <output_dir>/drawings/<connection_id>-fabrication.pdf
    <output_dir>/resolved-conflict-manifest.json

The manifest is the authoritative provenance projection: both original
source rows, both names, the conflicting fields with both values, the E1
verdict, the E2 human decision and rationale, the selected source, the
resolved geometry, the drawing identity and the recorded 7AG
verification evidence — plus an explicit declaration that the decision
is synthetic test evidence rather than a real engineering decision.
"""

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from pypdf import PdfReader

from app.cad_engine.catalogue_conflict_resolution import (
    HUMAN_DECISIONS,
    ConflictAcceptance,
    accept_resolved_conflict,
    build_conflict_audit,
)
from app.cad_engine.drawing_output_verification import (
    CHECK_PASSED as VERIFICATION_CHECK_PASSED,
    VERIFICATION_STATUS_VERIFIED,
    ArtifactCheck,
)
from app.cad_engine.fabrication_package import (
    CHECK_ACCEPTANCE_FIELDS_CONSISTENT,
    CHECK_CONNECTION_IDENTITY_PRESENT,
    CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION,
    CHECK_DRAWING_NUMBER_PRESENT,
    CHECK_MEMBER_IDENTITIES_PRESENT,
    CHECK_NO_CONTRADICTORY_CONNECTION_IDS,
    CHECK_NO_NONE_VALUES,
    CHECK_NO_TRACEBACK_TEXT,
    CHECK_PAGE_COUNT_MATCHES_RECORDED,
    CHECK_FAILED,
    CHECK_PASSED,
    CHECK_PDF_OPENS,
    CHECK_RECORDED_CHECKS_PASSED,
    CHECK_SOURCE_ARTIFACT_PRESENT,
    CHECK_SOURCE_SHA256_MATCHES_RECORDED,
    CHECK_TITLE_BLOCK_PRESENT,
    PACKAGE_STATUS_BLOCKED,
    PACKAGE_STATUS_READY,
    PACKAGE_STATUS_REFUSED,
)
from app.cad_engine.fabricator_acceptance import (
    ANSWER_FAIL,
    ANSWER_NOT_APPLICABLE,
    ANSWER_PASS,
    ANSWER_VALUES,
    CHECKLIST_ITEM_CONNECTION_IDENTIFIED,
    CHECKLIST_ITEM_DRAWING_IDENTITY,
    CHECKLIST_ITEM_HOLE_INFORMATION,
    CHECKLIST_ITEM_MEMBER_A_IDENTIFIED,
    CHECKLIST_ITEM_MEMBER_B_IDENTIFIED,
    CHECKLIST_ITEM_PLATE_INFORMATION,
    CHECKLIST_ITEM_TRACEABILITY,
    EVIDENCE_CONTRADICTED,
    EVIDENCE_MISSING,
    EVIDENCE_PRESENT,
    FINDING_CONTRADICTION,
    FINDING_MISSING_INFORMATION,
    FabricatorAcceptanceFinding,
    FabricatorChecklistAnswer,
    FabricatorChecklistResult,
    checklist_question,
)
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
)
from app.cad_engine.resolved_conflict_drawing import ResolvedConflictDrawingResult
from app.cad_engine.resolved_conflict_geometry import (
    SELECTED_SOURCES,
    SELECTED_SOURCE_CAPTURE,
    SELECTED_SOURCE_CATALOGUE,
)

__all__ = [
    "RESOLVED_CONFLICT_DELIVERABLE_SCOPE_STATEMENT",
    "DECISION_PROVENANCE_SYNTHETIC",
    "DELIVERABLE_MATERIAL_NOT_SPECIFIED",
    "CHECKLIST_ITEM_CONFLICT_DISCLOSED",
    "CHECKLIST_ITEM_SELECTED_AUTHORITY",
    "CHECKLIST_ITEM_HUMAN_DECISION",
    "CHECKLIST_ITEM_SYNTHETIC_PROVENANCE",
    "CHECKLIST_ITEM_MATERIAL_STATED",
    "DELIVERABLE_CHECKLIST_ITEMS",
    "REQUIRED_DELIVERABLE_ITEMS",
    "ResolvedConflictAcceptanceAnswers",
    "ResolvedConflictDeliverable",
    "ResolvedConflictAcceptance",
    "build_resolved_conflict_deliverable",
    "evaluate_resolved_conflict_deliverable",
]

RESOLVED_CONFLICT_DELIVERABLE_SCOPE_STATEMENT = (
    "This is an INDEPENDENT resolved-conflict deliverable — not a production job package. It "
    "gathers the already-recorded, 7AG-verified fabrication drawing artifact of one explicitly "
    "human-resolved catalogue conflict under a deterministic manifest, and never regenerates, "
    "repairs or re-verifies a drawing. The conflict resolution recorded here is synthetic test "
    "evidence recorded by the E2 boundary: it is not a real engineering decision, it confers no "
    "production authority, and no production acceptance is created, implied or fabricated. The "
    "acceptance evaluation below is an independent, read-only inspection of the actual deliverable "
    "on disk, reusing the existing 7AS evidence vocabulary and reconciliation rules and inventing "
    "nothing: a value the deliverable does not carry is reported missing."
)

# The one provenance label this deliverable ever records for the human
# decision. It is deliberately NOT a production provenance label: the
# existing vocabulary (AI_EXTRACTED / HUMAN_REVIEWED /
# HUMAN_SUPPLEMENTED) describes how an engineering value reached a
# record; this value says the opposite — that the decision is test
# evidence and carries no engineering authority.
DECISION_PROVENANCE_SYNTHETIC = "SYNTHETIC_TEST_EVIDENCE"

# What the deliverable records when the drawing states no material. The
# existing generator already renders this exact text; restating it here
# keeps the module's own projection honest without re-reading the
# generator's rules.
DELIVERABLE_MATERIAL_NOT_SPECIFIED = "NOT SPECIFIED"

# ---------------------------------------------------------------------------
# The acceptance checklist. The connection/drawing items are the
# existing 7AS items, reused verbatim by import; the conflict items are
# genuinely new concerns that only a resolved-conflict deliverable has
# (a production package has no conflict to disclose), so they are added
# here rather than redefining anything that already exists.
# ---------------------------------------------------------------------------
CHECKLIST_ITEM_CONFLICT_DISCLOSED = "CONFLICT_DISCLOSED"
CHECKLIST_ITEM_SELECTED_AUTHORITY = "SELECTED_AUTHORITY_RECORDED"
CHECKLIST_ITEM_HUMAN_DECISION = "HUMAN_DECISION_RECORDED"
CHECKLIST_ITEM_SYNTHETIC_PROVENANCE = "DECISION_PROVENANCE_SYNTHETIC"
CHECKLIST_ITEM_MATERIAL_STATED = "MATERIAL_STATED"

DELIVERABLE_CHECKLIST_ITEMS = (
    CHECKLIST_ITEM_CONFLICT_DISCLOSED,
    CHECKLIST_ITEM_SELECTED_AUTHORITY,
    CHECKLIST_ITEM_HUMAN_DECISION,
    CHECKLIST_ITEM_SYNTHETIC_PROVENANCE,
    CHECKLIST_ITEM_CONNECTION_IDENTIFIED,
    CHECKLIST_ITEM_MEMBER_A_IDENTIFIED,
    CHECKLIST_ITEM_MEMBER_B_IDENTIFIED,
    CHECKLIST_ITEM_PLATE_INFORMATION,
    CHECKLIST_ITEM_HOLE_INFORMATION,
    CHECKLIST_ITEM_DRAWING_IDENTITY,
    CHECKLIST_ITEM_MATERIAL_STATED,
    CHECKLIST_ITEM_TRACEABILITY,
)

# Items whose absence genuinely prevents fabrication acceptance. Material
# is deliberately NOT one of them: whether an unstated material blocks
# fabrication is the reviewer's engineering judgement (the established
# 7AS rule that a reviewer's FAIL — which must carry a finding — is what
# blocks), so an unstated material is REPORTED as missing evidence in
# every result and is never silently filled in. Every other item either
# is carried by the deliverable or the deliverable does not exist.
REQUIRED_DELIVERABLE_ITEMS = tuple(
    item for item in DELIVERABLE_CHECKLIST_ITEMS
    if item != CHECKLIST_ITEM_MATERIAL_STATED
)

_DELIVERABLE_CHECKLIST_QUESTIONS = {
    CHECKLIST_ITEM_CONFLICT_DISCLOSED: (
        "Does the deliverable show that a genuine source-of-truth conflict existed, and name "
        "both sources?"
    ),
    CHECKLIST_ITEM_SELECTED_AUTHORITY: (
        "Does the deliverable record which source the human resolution made authoritative, "
        "consistently with the drawing?"
    ),
    CHECKLIST_ITEM_HUMAN_DECISION: (
        "Does the deliverable record the explicit human decision and its rationale?"
    ),
    CHECKLIST_ITEM_SYNTHETIC_PROVENANCE: (
        "Does the deliverable declare that the recorded decision is synthetic test evidence "
        "and not a real engineering decision?"
    ),
    CHECKLIST_ITEM_MATERIAL_STATED: "Does the drawing state the material to be used?",
}

# The deliverable's own deterministic file layout (7AR's precedent: a
# drawings subdirectory plus one manifest, written only on READY).
_DRAWINGS_SUBDIR = "drawings"
_MANIFEST_FILENAME = "resolved-conflict-manifest.json"
_MANIFEST_SCHEMA = "steelspec-resolved-conflict-deliverable-1"
_MANIFEST_INDENT = len("  ")

# Identity conventions. The connection identity is E4's own
# (build_connection_identity), the artifact filename is 7AF's recorded
# naming rule, and the drawing number is the existing generator's 7T
# convention — restated here so the recorded identity can be checked
# without importing new rules, exactly as 7AR does.
_DRAWING_NUMBER_PREFIX = "FAB"
_FABRICATION_SUFFIX = "-fabrication"
_CONNECTION_ID_PATTERN = re.compile(r"[A-Za-z0-9]+-CONN-[A-Za-z0-9.\-]+")
_DRAWING_NUMBER_PATTERN = re.compile(r"FAB-[A-Za-z0-9.\-]+")
_FORBIDDEN_SUBSTRINGS = ("Traceback", "<class", " at 0x", "AIExtracted")
_NONE_VALUE_LINES = ("None", "NONE", "null")
_NOT_SPECIFIED_VALUES = (DELIVERABLE_MATERIAL_NOT_SPECIFIED, "NONE", "None", "null", "")
_TITLE_BLOCK_LABELS = (
    "DRAWING NO.", "REV", "DATE", "PROJECT", "SOURCE DRAWING", "SECTION", "LENGTH",
    "MATERIAL", "UNITS", "SCALE", "STATUS", "MEMBER A", "MEMBER B",
)


# ---------------------------------------------------------------------------
# Small deterministic helpers.
# ---------------------------------------------------------------------------
def _format_dim(value: Any) -> str:
    """One measured value as the drawing prints it (no trailing '.0' on a
    whole number) — the same presentation rule the existing checks use."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _rows_to_dict(rows: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    """A frozen (field, value) projection back to a plain detached dict."""
    return {field: copy.deepcopy(value) for field, value in rows}


def _freeze_rows(rows: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """A mapping as a sorted, detached tuple projection — the frozen shape
    carried on the result (never a live reference into a stage object)."""
    return tuple(sorted((key, copy.deepcopy(value)) for key, value in rows.items()))


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """The one canonical manifest serialization: sorted keys, fixed indent,
    a trailing newline, no timestamps and no clock values — so the same
    logical deliverable always produces byte-identical output."""
    return (json.dumps(dict(manifest), indent=_MANIFEST_INDENT, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _check(code: str, passed: bool, detail: str) -> ArtifactCheck:
    return ArtifactCheck(
        code=code, status=CHECK_PASSED if passed else CHECK_FAILED, detail=detail,
    )


def _read_text(path: Path) -> str:
    return PdfReader(str(path)).pages[0].extract_text() or ""


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def _label_value(lines: Sequence[str], label: str) -> str | None:
    """The value line following a title-block label line, or None."""
    for index, line in enumerate(lines):
        if line == label and index + 1 < len(lines):
            return lines[index + 1]
    return None


def _info_value(lines: Sequence[str], label: str) -> str | None:
    """The value on a single-line info block ('PLATE: ...'), or None."""
    prefix = f"{label}:"
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _member_block(lines: Sequence[str], letter: str) -> dict[str, str] | None:
    """The label->value pairs of one member's title-block row
    ('MEMBER A' .. the next member row), or None when absent."""
    header = f"MEMBER {letter}"
    try:
        start = lines.index(header)
    except ValueError:
        return None
    block: dict[str, str] = {}
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if line.startswith("MEMBER ") and line != header:
            break
        if line in ("SECTION", "LENGTH", "ATTACH") and index + 1 < len(lines):
            block[line] = lines[index + 1]
            index += 2
            continue
        if block == {} and line and line not in _TITLE_BLOCK_LABELS:
            block["MARK"] = line
        index += 1
    return block or None


def _connection_tokens(text: str) -> set[str]:
    return {match.group(0) for match in _CONNECTION_ID_PATTERN.finditer(text)}


def _drawing_number_tokens(text: str) -> set[str]:
    return {match.group(0) for match in _DRAWING_NUMBER_PATTERN.finditer(text)}


# ---------------------------------------------------------------------------
# Frozen records.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResolvedConflictAcceptanceAnswers:
    """
    The reviewer's verdicts for one deliverable, one per checklist item
    (DELIVERABLE_CHECKLIST_ITEMS). Each verdict is the existing
    FabricatorChecklistAnswer record — a FAIL must carry its finding.
    """
    answers: tuple[FabricatorChecklistAnswer, ...] = ()


@dataclass(frozen=True)
class ResolvedConflictDeliverable:
    """
    The complete E5 deliverable record for one re-decided conflict: the
    conflict identity, the E2 acceptance, the selected source, the frozen
    deterministic manifest projection, the per-check results, and the
    recorded artifact identity (filename, SHA-256, size) and paths.

    READY carries every field; BLOCKED (a check failed) and REFUSED (the
    request itself could not be evaluated) carry the reason and write
    nothing at all.
    """
    status: str
    reason: str
    conflict_id: str | None
    connection_id: str | None
    selected_source: str | None
    acceptance: ConflictAcceptance | None
    manifest: tuple[tuple[str, Any], ...]
    checks: tuple[ArtifactCheck, ...]
    artifact_filename: str | None
    artifact_sha256: str | None
    artifact_size_bytes: int | None
    deliverable_dir: str | None
    artifact_path: str | None
    manifest_path: str | None
    scope_statement: str = RESOLVED_CONFLICT_DELIVERABLE_SCOPE_STATEMENT


@dataclass(frozen=True)
class ResolvedConflictAcceptance:
    """
    The frozen independent evaluation of one deliverable. ACCEPTED /
    NOT_ACCEPTED are the reviewer-reconciled verdicts over evidence
    derived from the actual deliverable on disk; REFUSED means the
    evaluation itself could not proceed (incoherent or tampered
    deliverable, invalid or incomplete answers) and carries no verdict.
    """
    status: str
    reason: str
    conflict_id: str | None
    connection_id: str | None
    deliverable_status: str
    results: tuple[FabricatorChecklistResult, ...]
    findings: tuple[FabricatorAcceptanceFinding, ...]
    refusal_reasons: tuple[str, ...]
    summary: tuple[str, ...]
    scope_statement: str = RESOLVED_CONFLICT_DELIVERABLE_SCOPE_STATEMENT


# ---------------------------------------------------------------------------
# The manifest — the authoritative provenance projection.
# ---------------------------------------------------------------------------
def _build_manifest(
    result: ResolvedConflictDrawingResult,
    *,
    resolution_evidence: str | None,
) -> dict[str, Any]:
    """
    The deterministic manifest projection of one E4 result. Every value is
    carried verbatim from the conflict record, the E3 resolved geometry or
    the recorded E4 stage results; nothing is derived, defaulted or
    invented. `resolution_evidence` is the evidence note the caller states
    was recorded with the human resolution (E2 does not carry it on the
    conflict record) — when it is not supplied, the manifest reports it
    absent rather than inventing one.
    """
    conflict = result.conflict
    audit = build_conflict_audit(conflict)
    supplement = result.package.supplement
    verification = result.verification_result
    geometry = result.resolved_geometry

    decision = accept_resolved_conflict(conflict)
    material = supplement.material
    if not isinstance(material, str) or not material.strip():
        material = DELIVERABLE_MATERIAL_NOT_SPECIFIED

    return {
        "schema": _MANIFEST_SCHEMA,
        "scope_statement": RESOLVED_CONFLICT_DELIVERABLE_SCOPE_STATEMENT,
        "conflict_scope_statement": conflict.scope_statement,
        "conflict": {
            "conflict_id": conflict.conflict_id,
            "captured_name": conflict.captured_name,
            "catalogue_name": conflict.catalogue_name,
            "captured_values": _rows_to_dict(conflict.captured_values),
            "catalogue_values": _rows_to_dict(conflict.catalogue_values),
            "conflicting_fields": [
                {"field": field, "captured_value": captured, "catalogue_value": catalogue}
                for field, captured, catalogue in audit.conflicting_fields
            ],
            "original_verdict": audit.original_verdict,
            "original_resolution": audit.original_resolution,
            "human_decision": audit.human_decision,
            "human_rationale": audit.human_rationale,
            "resulting_decision": audit.resulting_decision,
            "resolution_status": audit.resolution_status,
            "conflict_state": audit.conflict_state,
            "resolved_geometry_fields": _rows_to_dict(audit.resolved_geometry_fields),
        },
        "acceptance": {"accepted": decision.accepted, "reason": decision.reason},
        "selected_source": result.selected_source,
        "resolved_geometry": {
            "section_name": geometry.geometry.section_name,
            "mark": geometry.geometry.mark,
            "resolved_section_row": _rows_to_dict(geometry.resolved_section_row),
        },
        "decision_provenance": {
            "kind": DECISION_PROVENANCE_SYNTHETIC,
            "evidence_note": resolution_evidence,
            "statement": (
                "The recorded human decision is an explicit synthetic test input recorded by the "
                "E2 boundary. It is not a real engineering decision, it confers no engineering "
                "authority, and no production acceptance is created or implied."
            ),
        },
        "drawing": {
            "connection_id": result.connection_id,
            "drawing_number": f"{_DRAWING_NUMBER_PREFIX}-{result.connection_id}",
            "artifact_filename": Path(result.dispatch_result.generated_files[0]).name,
            "artifact_sha256": verification.sha256,
            "artifact_size_bytes": verification.file_size_bytes,
            "verification_status": verification.verification_status,
            "page_count": verification.page_count,
            # The relevant 7AG checks, projected as code + status only: the
            # check DETAILS quote the absolute paths of the workspace the
            # drawing happened to be generated in, and a fabricator-facing
            # deliverable must carry no filesystem-dependent value beyond
            # the artifact identity it deliberately records.
            "verification_checks": [
                {"code": check.code, "status": check.status}
                for check in verification.checks
            ],
        },
        "connection": {
            "position": supplement.position,
            "plate": copy.deepcopy(dict(supplement.plate)) if supplement.plate else None,
            "holes": copy.deepcopy(dict(supplement.holes)) if supplement.holes else None,
            "location": copy.deepcopy(dict(supplement.location)) if supplement.location else None,
            "attachments": [
                copy.deepcopy(dict(attachment)) for attachment in (supplement.attachments or ())
            ],
            "member_rows": {
                mark: _rows_to_dict(rows) for mark, rows in result.member_rows
            },
            "member_placements": {
                mark: {
                    "x": placement.x, "y": placement.y, "z": placement.z,
                    "rotation_x": placement.rotation_x,
                    "rotation_y": placement.rotation_y,
                    "rotation_z": placement.rotation_z,
                }
                for mark, placement in result.member_placements
            },
        },
        "material": material,
    }


# ---------------------------------------------------------------------------
# E5 DELIVERABLE — build the package from an E4 result.
# ---------------------------------------------------------------------------
def _refused(reason: str, *, conflict_id: Any = None, connection_id: Any = None
             ) -> ResolvedConflictDeliverable:
    return ResolvedConflictDeliverable(
        status=PACKAGE_STATUS_REFUSED, reason=reason,
        conflict_id=conflict_id, connection_id=connection_id,
        selected_source=None, acceptance=None, manifest=(), checks=(),
        artifact_filename=None, artifact_sha256=None, artifact_size_bytes=None,
        deliverable_dir=None, artifact_path=None, manifest_path=None,
    )


def _blocked(reason: str, *, conflict_id: Any = None, connection_id: Any = None,
             checks: Sequence[ArtifactCheck] = ()) -> ResolvedConflictDeliverable:
    return ResolvedConflictDeliverable(
        status=PACKAGE_STATUS_BLOCKED, reason=reason,
        conflict_id=conflict_id, connection_id=connection_id,
        selected_source=None, acceptance=None, manifest=(), checks=tuple(checks),
        artifact_filename=None, artifact_sha256=None, artifact_size_bytes=None,
        deliverable_dir=None, artifact_path=None, manifest_path=None,
    )


def _identity_consistent(conflict_id: Any, connection_id: Any) -> str | None:
    """The reason a deliverable's identity pair cannot be trusted, or None.
    The connection identity is E4's own derivation from the conflict
    record; a pair that does not agree, or an absent one, is refused."""
    if not isinstance(conflict_id, str) or not conflict_id.strip():
        return ("the conflict record carries no usable conflict identity; a deliverable is "
                "never built for an unnamed conflict.")
    if not isinstance(connection_id, str) or not connection_id.strip():
        return ("the recorded connection identity is absent; a deliverable is never built "
                "without the drawing identity it must carry.")
    if connection_id != f"E4-CONN-{conflict_id}":
        return (f"the recorded connection identity {connection_id!r} does not derive from the "
                f"conflict identity {conflict_id!r}; contradictory provenance is refused.")
    return None


def build_resolved_conflict_deliverable(
    result: ResolvedConflictDrawingResult,
    *,
    output_dir: Path,
    resolution_evidence: str | None = None,
    expected_artifact_sha256: str | None = None,
) -> ResolvedConflictDeliverable:
    """
    The E5 integration: an E4-verified resolved-conflict drawing is
    gathered into a deterministic fabricator-facing deliverable — a byte
    copy of the recorded artifact plus a deterministic manifest carrying
    the complete conflict provenance — and nothing is written unless
    every check passes first.

    This function NEVER regenerates the drawing. The packaged PDF is the
    bytes 7AG recorded and verified, copied verbatim; a byte that differs
    from the recorded SHA-256 is a refusal, never something to repair.

    `resolution_evidence` is the evidence note the caller states was
    recorded with the human resolution (the E2 conflict record does not
    carry it). It is preserved verbatim when supplied and reported absent
    when not — never invented.

    `expected_artifact_sha256` is this path's staleness token. E5 has no
    project workflow and therefore no revision counter; the recorded
    artifact identity is what a caller holds and what can go stale, so a
    caller that holds an older identity is refused rather than served
    state it did not see.

    SOURCE AUTHORITY: this module decides nothing about the conflict. It
    consumes the E2 acceptance the record already earned and the E4
    result 7AG already verified.
    """
    if not isinstance(result, ResolvedConflictDrawingResult):
        raise TypeError(
            f"result must be a ResolvedConflictDrawingResult (got "
            f"{type(result).__name__}); a deliverable is built from the genuine E4 "
            "resolved-conflict drawing result, never anything else."
        )
    if not isinstance(output_dir, Path):
        raise TypeError(
            f"output_dir must be a pathlib.Path (got {type(output_dir).__name__})."
        )
    if resolution_evidence is not None and not isinstance(resolution_evidence, str):
        raise TypeError(
            f"resolution_evidence must be a str or None (got "
            f"{type(resolution_evidence).__name__})."
        )
    if expected_artifact_sha256 is not None and not isinstance(expected_artifact_sha256, str):
        raise TypeError(
            f"expected_artifact_sha256 must be a str or None (got "
            f"{type(expected_artifact_sha256).__name__})."
        )

    conflict = result.conflict
    conflict_id = conflict.conflict_id
    connection_id = result.connection_id

    # ---- identity and acceptance: refuse before touching anything.
    inconsistent = _identity_consistent(conflict_id, connection_id)
    if inconsistent is not None:
        return _refused(inconsistent, conflict_id=conflict_id, connection_id=connection_id)

    acceptance = accept_resolved_conflict(conflict)
    if not acceptance.accepted:
        return _refused(
            f"the conflict is not an accepted human-resolved conflict ({acceptance.reason}); "
            "no deliverable exists for an unresolved, un-re-decided or KEEP_BOTH conflict.",
            conflict_id=conflict_id, connection_id=connection_id,
        )
    if result.selected_source not in SELECTED_SOURCES:
        return _refused(
            f"the recorded selected source {result.selected_source!r} is not one of "
            f"{list(SELECTED_SOURCES)}; contradictory provenance is refused.",
            conflict_id=conflict_id, connection_id=connection_id,
        )

    # ---- the recorded artifact: present, real, and the bytes 7AG verified.
    generated = tuple(result.dispatch_result.generated_files)
    if len(generated) != 1:
        return _blocked(
            f"the dispatch recorded {len(generated)} artifact(s) for this connection; exactly "
            "one verified drawing is required for a deliverable.",
            conflict_id=conflict_id, connection_id=connection_id,
        )
    recorded_path = Path(generated[0])
    verification = result.verification_result
    if verification.verification_status != VERIFICATION_STATUS_VERIFIED:
        return _refused(
            f"the recorded 7AG verification is {verification.verification_status!r}, not "
            f"{VERIFICATION_STATUS_VERIFIED!r}; only a genuinely verified artifact may be "
            "delivered.",
            conflict_id=conflict_id, connection_id=connection_id,
        )
    if expected_artifact_sha256 is not None and \
            expected_artifact_sha256 != verification.sha256:
        return _refused(
            f"the caller holds artifact identity {expected_artifact_sha256!r} but the recorded "
            f"artifact is {verification.sha256!r}; a stale deliverable request is never "
            "satisfied by state the caller did not see.",
            conflict_id=conflict_id, connection_id=connection_id,
        )
    if verification.sha256 is None or verification.artifact_path is None:
        return _refused(
            "the recorded 7AG verification carries no artifact identity; a deliverable is "
            "never built from an unidentifiable artifact.",
            conflict_id=conflict_id, connection_id=connection_id,
        )

    checks: list[ArtifactCheck] = []

    artifact_exists = recorded_path.is_file()
    checks.append(_check(
        CHECK_SOURCE_ARTIFACT_PRESENT, artifact_exists,
        f"the recorded artifact {recorded_path.name!r} "
        + ("is present" if artifact_exists else "is no longer present on disk"),
    ))
    if not artifact_exists:
        return _blocked(
            f"the recorded artifact {recorded_path.name!r} is no longer present; a deliverable "
            "is never built from a missing artifact.",
            conflict_id=conflict_id, connection_id=connection_id, checks=checks,
        )

    artifact_bytes = recorded_path.read_bytes()
    recorded_sha = hashlib.sha256(artifact_bytes).hexdigest()
    hash_matches = recorded_sha == verification.sha256
    checks.append(_check(
        CHECK_SOURCE_SHA256_MATCHES_RECORDED, hash_matches,
        "the artifact bytes hash to the identity 7AG recorded"
        if hash_matches else
        f"the artifact bytes hash to {recorded_sha}, not the recorded {verification.sha256}",
    ))
    if not hash_matches:
        return _blocked(
            "the artifact's bytes no longer match the SHA-256 7AG verified; a deliverable is "
            "never built from, or repaired onto, an artifact that has changed.",
            conflict_id=conflict_id, connection_id=connection_id, checks=checks,
        )

    recorded_checks_passed = bool(verification.checks) and all(
        check.status == VERIFICATION_CHECK_PASSED for check in verification.checks
    )
    checks.append(_check(
        CHECK_RECORDED_CHECKS_PASSED, recorded_checks_passed,
        f"{len(verification.checks)} recorded 7AG check(s), "
        + ("every one passed" if recorded_checks_passed
           else "at least one did not pass (" + ", ".join(
               f"{c.code}={c.status}" for c in verification.checks
               if c.status != VERIFICATION_CHECK_PASSED) + ")"),
    ))
    if not recorded_checks_passed:
        return _blocked(
            "the recorded 7AG verification carries failing checks; a deliverable is never built "
            "over a failed verification.",
            conflict_id=conflict_id, connection_id=connection_id, checks=checks,
        )

    filename = recorded_path.name
    expected_filename = f"{connection_id}{_FABRICATION_SUFFIX}.pdf"
    dispatch_matches = filename == expected_filename
    checks.append(_check(
        CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION, dispatch_matches,
        f"the recorded artifact filename {filename!r} "
        + ("matches the recorded connection identity"
           if dispatch_matches else f"does not match {expected_filename!r}"),
    ))
    if not dispatch_matches:
        return _blocked(
            f"the recorded artifact filename {filename!r} does not carry the connection identity "
            f"{connection_id!r}; a substituted artifact is refused.",
            conflict_id=conflict_id, connection_id=connection_id, checks=checks,
        )

    # ---- the artifact is parsed, never re-generated.
    try:
        text = _read_text(recorded_path)
        page_count = len(PdfReader(str(recorded_path)).pages)
        parsed = True
        parse_detail = f"the recorded artifact parses as a {page_count}-page PDF"
    except Exception as error:  # noqa: BLE001 - a malformed artifact is a refusal, not a crash
        text, page_count, parsed = "", 0, False
        parse_detail = f"the recorded artifact could not be parsed ({type(error).__name__})"
    checks.append(_check(CHECK_PDF_OPENS, parsed, parse_detail))
    if not parsed:
        return _blocked(
            "the recorded artifact is not a parsable PDF; an unreadable deliverable is refused.",
            conflict_id=conflict_id, connection_id=connection_id, checks=checks,
        )

    checks.append(_check(
        CHECK_PAGE_COUNT_MATCHES_RECORDED, page_count == verification.page_count,
        f"the artifact carries {page_count} page(s); 7AG recorded {verification.page_count}",
    ))

    lines = _lines(text)
    drawing_number = f"{_DRAWING_NUMBER_PREFIX}-{connection_id}"
    drawing_numbers = _drawing_number_tokens(text)
    checks.append(_check(
        CHECK_DRAWING_NUMBER_PRESENT, drawing_number in drawing_numbers,
        f"the drawing number {drawing_number!r} is "
        + ("present" if drawing_number in drawing_numbers else "absent"),
    ))
    connection_tokens = _connection_tokens(text)
    checks.append(_check(
        CHECK_CONNECTION_IDENTITY_PRESENT, connection_tokens == {connection_id},
        f"the artifact carries connection identities {sorted(connection_tokens)}",
    ))
    checks.append(_check(
        CHECK_NO_CONTRADICTORY_CONNECTION_IDS, connection_tokens == {connection_id},
        "the artifact carries exactly one connection identity",
    ))
    members_present = all(
        _member_block(lines, letter) is not None for letter in ("A", "B")
    )
    checks.append(_check(
        CHECK_MEMBER_IDENTITIES_PRESENT, members_present,
        "both member rows are present in the drawing"
        if members_present else "at least one member row is absent from the drawing",
    ))
    title_block_present = all(
        _label_value(lines, label) is not None
        for label in ("DRAWING NO.", "MATERIAL", "UNITS")
    )
    checks.append(_check(
        CHECK_TITLE_BLOCK_PRESENT, title_block_present,
        "the drawing carries its title block"
        if title_block_present else "the drawing's title block is incomplete",
    ))
    forbidden = sorted({token for token in _FORBIDDEN_SUBSTRINGS if token in text})
    checks.append(_check(
        CHECK_NO_TRACEBACK_TEXT, not forbidden,
        "the drawing carries no error or raw-AI presentation"
        if not forbidden else f"the drawing carries {forbidden}",
    ))
    none_lines = sorted({line for line in lines if line in _NONE_VALUE_LINES})
    checks.append(_check(
        CHECK_NO_NONE_VALUES, not none_lines,
        "the drawing carries no bare None value"
        if not none_lines else f"the drawing carries bare None value(s) {none_lines}",
    ))

    # ---- the recorded stage evidence must agree with the record itself.
    pipeline = result.pipeline_result
    acceptance_consistent = (
        result.gate_result.decision == pipeline.automation_gate_result.decision
        and result.gate_result.connection_id == connection_id
        and result.dispatch_result.connection_id == connection_id
        and verification.connection_id == connection_id
    )
    checks.append(_check(
        CHECK_ACCEPTANCE_FIELDS_CONSISTENT, acceptance_consistent,
        "every recorded stage result agrees on the connection and the gate decision"
        if acceptance_consistent else
        "the recorded stage results disagree with each other",
    ))

    failed = [check for check in checks if check.status != CHECK_PASSED]
    if failed:
        return _blocked(
            "the deliverable checks did not all pass: "
            + "; ".join(f"{check.code} ({check.detail})" for check in failed),
            conflict_id=conflict_id, connection_id=connection_id, checks=checks,
        )

    # ---- everything validated: only now is anything written.
    manifest = _build_manifest(result, resolution_evidence=resolution_evidence)
    manifest_bytes = _manifest_bytes(manifest)
    drawings_dir = output_dir / _DRAWINGS_SUBDIR
    artifact_target = drawings_dir / filename
    manifest_target = output_dir / _MANIFEST_FILENAME

    written: list[Path] = []
    try:
        drawings_dir.mkdir(parents=True, exist_ok=True)
        artifact_target.write_bytes(artifact_bytes)
        written.append(artifact_target)
        manifest_target.write_bytes(manifest_bytes)
        written.append(manifest_target)
    except OSError as error:
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
        try:
            drawings_dir.rmdir()
        except OSError:
            pass
        return _blocked(
            f"the deliverable could not be written ({type(error).__name__}); nothing partial is "
            "left behind.",
            conflict_id=conflict_id, connection_id=connection_id, checks=checks,
        )

    return ResolvedConflictDeliverable(
        status=PACKAGE_STATUS_READY,
        reason="every deliverable check passed and the deliverable was written",
        conflict_id=conflict_id,
        connection_id=connection_id,
        selected_source=result.selected_source,
        acceptance=acceptance,
        manifest=_freeze_rows(manifest),
        checks=tuple(checks),
        artifact_filename=filename,
        artifact_sha256=verification.sha256,
        artifact_size_bytes=len(artifact_bytes),
        deliverable_dir=str(output_dir),
        artifact_path=str(artifact_target),
        manifest_path=str(manifest_target),
    )


# ---------------------------------------------------------------------------
# E5 ACCEPTANCE — independent evaluation of the deliverable on disk.
# ---------------------------------------------------------------------------
def _question(item_code: str) -> str:
    if item_code in _DELIVERABLE_CHECKLIST_QUESTIONS:
        return _DELIVERABLE_CHECKLIST_QUESTIONS[item_code]
    return checklist_question(item_code)


def _verdict(item_code: str, status: str, visible: bool,
             findings: Sequence[FabricatorAcceptanceFinding] = ()) -> tuple:
    return status, visible, tuple(findings)


def _evidence_status(
    item_code: str,
    *,
    deliverable: ResolvedConflictDeliverable,
    manifest: Mapping[str, Any],
    lines: Sequence[str],
    text: str,
) -> tuple:
    """
    Derives PRESENT / MISSING / CONTRADICTED for one checklist item from
    the ACTUAL packaged artifact text and the deliverable's own recorded
    evidence — never from filenames alone, never from the manifest's mere
    presence, and never by inventing a value the deliverable does not
    carry.
    """
    conflict = manifest.get("conflict") or {}
    drawing = manifest.get("drawing") or {}
    connection = manifest.get("connection") or {}

    if item_code == CHECKLIST_ITEM_CONFLICT_DISCLOSED:
        if not conflict:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        captured_name = conflict.get("captured_name")
        catalogue_name = conflict.get("catalogue_name")
        fields = conflict.get("conflicting_fields") or []
        if not captured_name or not catalogue_name or not fields:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        agreed = [
            entry for entry in fields
            if entry.get("captured_value") == entry.get("catalogue_value")
        ]
        names_both = (
            str(captured_name) in str(conflict.get("conflict_id"))
            and str(catalogue_name) in str(conflict.get("conflict_id"))
        )
        if agreed or not names_both:
            return _verdict(item_code, EVIDENCE_CONTRADICTED, False, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    "the deliverable does not disclose a genuine conflict: "
                    + ("the recorded sources agree on "
                       + ", ".join(sorted(entry.get("field", "?") for entry in agreed))
                       if agreed else
                       "the recorded conflict identity does not name both sources"),
                ),
            ))
        return _verdict(item_code, EVIDENCE_PRESENT, False)

    if item_code == CHECKLIST_ITEM_SELECTED_AUTHORITY:
        selected = manifest.get("selected_source")
        if selected not in SELECTED_SOURCES:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        expected_name = (
            conflict.get("captured_name")
            if selected == SELECTED_SOURCE_CAPTURE
            else conflict.get("catalogue_name")
        )
        sections = {
            (block or {}).get("SECTION")
            for block in (_member_block(lines, "A"), _member_block(lines, "B"))
            if block
        }
        sections.discard(None)
        if not sections:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        if expected_name and sections - {expected_name}:
            return _verdict(item_code, EVIDENCE_CONTRADICTED, True, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    f"the recorded selected source {selected!r} implies the section "
                    f"{expected_name!r}, but the drawing's section rows carry "
                    f"{sorted(sections)}",
                ),
            ))
        return _verdict(item_code, EVIDENCE_PRESENT, True)

    if item_code == CHECKLIST_ITEM_HUMAN_DECISION:
        if not conflict:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        decision = conflict.get("human_decision")
        rationale = conflict.get("human_rationale")
        if decision not in HUMAN_DECISIONS or not (
                isinstance(rationale, str) and rationale.strip()):
            return _verdict(item_code, EVIDENCE_MISSING, False)
        return _verdict(item_code, EVIDENCE_PRESENT, False)

    if item_code == CHECKLIST_ITEM_SYNTHETIC_PROVENANCE:
        provenance = manifest.get("decision_provenance") or {}
        scope = conflict.get("resolved_geometry_fields")
        if provenance.get("kind") != DECISION_PROVENANCE_SYNTHETIC:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        if not manifest.get("conflict_scope_statement"):
            return _verdict(item_code, EVIDENCE_MISSING, False)
        statement = str(provenance.get("statement") or "")
        if "not a real engineering decision" not in statement:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        blob = json.dumps(dict(manifest), sort_keys=True)
        if "HUMAN_CONFIRMED" in blob or "PRODUCTION_PROVEN" in blob:
            return _verdict(item_code, EVIDENCE_CONTRADICTED, False, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    "the deliverable carries production provenance vocabulary; a synthetic test "
                    "decision is never presented as engineering authority",
                ),
            ))
        if scope is None:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        return _verdict(item_code, EVIDENCE_PRESENT, False)

    if item_code == CHECKLIST_ITEM_CONNECTION_IDENTIFIED:
        tokens = _connection_tokens(text)
        if not tokens:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        if tokens != {deliverable.connection_id}:
            return _verdict(item_code, EVIDENCE_CONTRADICTED, True, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    f"the drawing carries connection identities {sorted(tokens)} but the "
                    f"deliverable records {deliverable.connection_id!r}",
                ),
            ))
        return _verdict(item_code, EVIDENCE_PRESENT, True)

    if item_code in (CHECKLIST_ITEM_MEMBER_A_IDENTIFIED,
                     CHECKLIST_ITEM_MEMBER_B_IDENTIFIED):
        letter = "A" if item_code == CHECKLIST_ITEM_MEMBER_A_IDENTIFIED else "B"
        block = _member_block(lines, letter)
        if block is None or not block.get("MARK") or not block.get("SECTION"):
            return _verdict(item_code, EVIDENCE_MISSING, False)
        recorded_marks = sorted(
            (connection.get("member_rows") or {}).keys()
        )
        recorded_mark = recorded_marks[0 if letter == "A" else 1] \
            if len(recorded_marks) > (0 if letter == "A" else 1) else None
        if recorded_mark is not None and block["MARK"] != recorded_mark:
            return _verdict(item_code, EVIDENCE_CONTRADICTED, True, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    f"the drawing identifies MEMBER {letter} as {block['MARK']!r} but the "
                    f"deliverable records the member mark {recorded_mark!r}",
                ),
            ))
        return _verdict(item_code, EVIDENCE_PRESENT, True)

    if item_code == CHECKLIST_ITEM_PLATE_INFORMATION:
        plate_line = _info_value(lines, "PLATE")
        if not plate_line:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        recorded = connection.get("plate")
        if not recorded:
            return _verdict(item_code, EVIDENCE_PRESENT, True)
        dims = {
            "width_mm": recorded.get("width_mm"),
            "depth_mm": recorded.get("depth_mm"),
            "thickness_mm": recorded.get("thickness_mm"),
        }
        if any(value is None for value in dims.values()):
            return _verdict(item_code, EVIDENCE_PRESENT, True)
        for needle, name in (
            (f"{_format_dim(dims['width_mm'])} ×", "width"),
            (f"× {_format_dim(dims['depth_mm'])} ×", "depth"),
            (f"× {_format_dim(dims['thickness_mm'])} mm", "thickness"),
        ):
            if needle not in plate_line:
                return _verdict(item_code, EVIDENCE_CONTRADICTED, True, (
                    FabricatorAcceptanceFinding(
                        item_code, FINDING_CONTRADICTION,
                        f"the drawing's plate line {plate_line!r} does not carry the recorded "
                        f"plate {name} {_format_dim(dims[name + '_mm'])}",
                    ),
                ))
        return _verdict(item_code, EVIDENCE_PRESENT, True)

    if item_code == CHECKLIST_ITEM_HOLE_INFORMATION:
        holes_line = _info_value(lines, "HOLES")
        pattern_line = _info_value(lines, "PATTERN")
        if not holes_line or not pattern_line:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        recorded = connection.get("holes")
        if not recorded:
            return _verdict(item_code, EVIDENCE_PRESENT, True)
        quantity = recorded.get("quantity")
        diameter = recorded.get("diameter_mm")
        horizontal = recorded.get("horizontal_spacing_mm")
        vertical = recorded.get("vertical_spacing_mm")
        if None in (quantity, diameter, horizontal, vertical):
            return _verdict(item_code, EVIDENCE_PRESENT, True)
        for needle, source, name in (
            (f"{_format_dim(quantity)} ×", holes_line, "hole quantity"),
            (f"Ø{_format_dim(diameter)}", holes_line, "hole diameter"),
            (f"{_format_dim(horizontal)} H", pattern_line, "horizontal hole spacing"),
            (f"{_format_dim(vertical)} V", pattern_line, "vertical hole spacing"),
        ):
            if needle not in source:
                return _verdict(item_code, EVIDENCE_CONTRADICTED, True, (
                    FabricatorAcceptanceFinding(
                        item_code, FINDING_CONTRADICTION,
                        f"the drawing's line {source!r} does not carry the recorded {name}",
                    ),
                ))
        return _verdict(item_code, EVIDENCE_PRESENT, True)

    if item_code == CHECKLIST_ITEM_DRAWING_IDENTITY:
        numbers = _drawing_number_tokens(text)
        expected = drawing.get("drawing_number")
        if not expected:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        if not numbers:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        if numbers != {expected}:
            return _verdict(item_code, EVIDENCE_CONTRADICTED, True, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    f"the drawing carries drawing numbers {sorted(numbers)} but the deliverable "
                    f"records {expected!r}",
                ),
            ))
        return _verdict(item_code, EVIDENCE_PRESENT, True)

    if item_code == CHECKLIST_ITEM_MATERIAL_STATED:
        stated = _label_value(lines, "MATERIAL")
        if stated is None:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        if stated in _NOT_SPECIFIED_VALUES:
            return _verdict(item_code, EVIDENCE_MISSING, False, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_MISSING_INFORMATION,
                    f"the drawing's MATERIAL cell reads {stated!r}; no material is stated, and "
                    "none is invented",
                ),
            ))
        recorded = manifest.get("material")
        if isinstance(recorded, str) and recorded.strip() and recorded != stated:
            return _verdict(item_code, EVIDENCE_CONTRADICTED, True, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    f"the drawing states the material {stated!r} but the deliverable records "
                    f"{recorded!r}",
                ),
            ))
        return _verdict(item_code, EVIDENCE_PRESENT, True)

    if item_code == CHECKLIST_ITEM_TRACEABILITY:
        conflict_id = deliverable.conflict_id
        connection_id = deliverable.connection_id
        filename = deliverable.artifact_filename
        if not conflict_id or not connection_id or not filename:
            return _verdict(item_code, EVIDENCE_MISSING, False)
        consistent = (
            drawing.get("connection_id") == connection_id
            and conflict.get("conflict_id") == conflict_id
            and filename == f"{connection_id}{_FABRICATION_SUFFIX}.pdf"
            and drawing.get("artifact_sha256") == deliverable.artifact_sha256
        )
        if not consistent:
            return _verdict(item_code, EVIDENCE_CONTRADICTED, False, (
                FabricatorAcceptanceFinding(
                    item_code, FINDING_CONTRADICTION,
                    "the deliverable's conflict identity, connection identity, artifact filename "
                    "and recorded hash do not form one consistent chain",
                ),
            ))
        return _verdict(item_code, EVIDENCE_PRESENT, False)

    return _verdict(item_code, EVIDENCE_MISSING, False)


def _refused_acceptance(
    reason: str,
    *,
    deliverable: ResolvedConflictDeliverable,
    refusal_reasons: Sequence[str],
) -> ResolvedConflictAcceptance:
    return ResolvedConflictAcceptance(
        status=ACCEPTANCE_STATUS_REFUSED,
        reason=reason,
        conflict_id=deliverable.conflict_id,
        connection_id=deliverable.connection_id,
        deliverable_status=deliverable.status,
        results=(),
        findings=(),
        refusal_reasons=tuple(refusal_reasons),
        summary=(),
    )


def evaluate_resolved_conflict_deliverable(
    deliverable: ResolvedConflictDeliverable,
    *,
    acceptance_answers: ResolvedConflictAcceptanceAnswers,
) -> ResolvedConflictAcceptance:
    """
    The E5 acceptance boundary: an independent, read-only evaluation of
    the ACTUAL deliverable on disk, reconciled against the reviewer's
    answers through the existing 7AS rules.

    Evidence is derived from the packaged PDF's own text plus the
    deliverable's recorded evidence — never from the manifest's mere
    presence, never from filenames alone, never from the 7AR/7AR-style
    projection would-be. A tampered artifact, a tampered manifest, an
    unreadable PDF, an incomplete or invalid answer set, and every
    matrix violation all REFUSE the evaluation rather than return a
    verdict — invalid input is a refusal, never a silent continuation.

    A NOT_ACCEPTED result is a first-class, successful outcome: when the
    evidence genuinely does not support acceptance (required evidence
    missing or contradicted, or the reviewer's FAIL on an item the
    evidence cannot support), it is reported with named findings and
    nothing is filled in. This function never forces an ACCEPTED verdict.
    """
    if not isinstance(deliverable, ResolvedConflictDeliverable):
        raise TypeError(
            f"deliverable must be a ResolvedConflictDeliverable (got "
            f"{type(deliverable).__name__})."
        )
    if not isinstance(acceptance_answers, ResolvedConflictAcceptanceAnswers):
        raise TypeError(
            f"acceptance_answers must be a ResolvedConflictAcceptanceAnswers (got "
            f"{type(acceptance_answers).__name__})."
        )

    if deliverable.status != PACKAGE_STATUS_READY:
        return _refused_acceptance(
            f"the deliverable is {deliverable.status!r}, not {PACKAGE_STATUS_READY!r} — there is "
            f"no deliverable to accept ({deliverable.reason})",
            deliverable=deliverable,
            refusal_reasons=(deliverable.reason,),
        )
    if not deliverable.artifact_path or not deliverable.manifest_path:
        return _refused_acceptance(
            "the deliverable records no artifact or manifest path",
            deliverable=deliverable,
            refusal_reasons=("the deliverable records no artifact or manifest path",),
        )

    # ---- the artifact on disk must still be the artifact that was recorded.
    artifact_path = Path(deliverable.artifact_path)
    if not artifact_path.is_file():
        return _refused_acceptance(
            f"the delivered artifact {artifact_path.name!r} is no longer present",
            deliverable=deliverable,
            refusal_reasons=("the delivered artifact is missing",),
        )
    try:
        artifact_bytes = artifact_path.read_bytes()
    except OSError:
        return _refused_acceptance(
            "the delivered artifact could not be read",
            deliverable=deliverable,
            refusal_reasons=("the delivered artifact could not be read",),
        )
    actual_sha = hashlib.sha256(artifact_bytes).hexdigest()
    if actual_sha != deliverable.artifact_sha256:
        return _refused_acceptance(
            "the delivered artifact's bytes no longer match the deliverable's recorded SHA-256",
            deliverable=deliverable,
            refusal_reasons=(
                f"the artifact hashes to {actual_sha}, not the recorded "
                f"{deliverable.artifact_sha256}",
            ),
        )

    # ---- the manifest on disk must still be the manifest that was written.
    manifest_path = Path(deliverable.manifest_path)
    if not manifest_path.is_file():
        return _refused_acceptance(
            "the delivered manifest is no longer present",
            deliverable=deliverable,
            refusal_reasons=("the delivered manifest is missing",),
        )
    try:
        on_disk_manifest = manifest_path.read_bytes()
    except OSError:
        return _refused_acceptance(
            "the delivered manifest could not be read",
            deliverable=deliverable,
            refusal_reasons=("the delivered manifest could not be read",),
        )
    manifest = dict(deliverable.manifest)
    if on_disk_manifest != _manifest_bytes(manifest):
        return _refused_acceptance(
            "the delivered manifest does not match the deliverable's recorded evidence",
            deliverable=deliverable,
            refusal_reasons=(
                "the manifest bytes on disk differ from the deliverable's recorded projection",
            ),
        )

    # ---- the artifact must be readable as a drawing.
    try:
        text = _read_text(artifact_path)
        PdfReader(str(artifact_path)).pages
    except Exception as error:  # noqa: BLE001 - an unreadable deliverable is a refusal
        return _refused_acceptance(
            "the delivered artifact is not a parsable PDF",
            deliverable=deliverable,
            refusal_reasons=(f"the artifact could not be parsed ({type(error).__name__})",),
        )

    lines = _lines(text)

    # ---- validate the answers: complete, unambiguous and about real items.
    refusals: list[str] = []
    by_item: dict[str, FabricatorChecklistAnswer] = {}
    seen = set()
    for answer in acceptance_answers.answers:
        if not isinstance(answer, FabricatorChecklistAnswer):
            refusals.append(
                f"malformed acceptance answers: {type(answer).__name__} is not a "
                "FabricatorChecklistAnswer"
            )
            continue
        if answer.checklist_item not in DELIVERABLE_CHECKLIST_ITEMS:
            refusals.append(f"unknown checklist item {answer.checklist_item!r}")
            continue
        if answer.checklist_item in seen:
            refusals.append(f"duplicate answer for {answer.checklist_item}")
            continue
        seen.add(answer.checklist_item)
        if answer.answer not in ANSWER_VALUES:
            refusals.append(
                f"invalid answer {answer.answer!r} for {answer.checklist_item}"
            )
            continue
        if answer.answer == ANSWER_FAIL and not (answer.finding and answer.finding.strip()):
            refusals.append(f"FAIL without a finding for {answer.checklist_item}")
            continue
        by_item[answer.checklist_item] = answer
    missing = [item for item in DELIVERABLE_CHECKLIST_ITEMS if item not in by_item]
    if missing:
        refusals.append(f"missing acceptance answers for: {', '.join(missing)}")
    if refusals:
        return _refused_acceptance(
            "the acceptance answers cannot be reconciled", deliverable=deliverable,
            refusal_reasons=refusals,
        )

    # ---- derive evidence independently, then apply the reconciliation matrix.
    evidence: dict[str, tuple] = {}
    for item_code in DELIVERABLE_CHECKLIST_ITEMS:
        evidence[item_code] = _evidence_status(
            item_code, deliverable=deliverable, manifest=manifest, lines=lines, text=text,
        )

    for item_code, answer in by_item.items():
        status = evidence[item_code][0]
        if answer.answer == ANSWER_PASS and status in (EVIDENCE_MISSING, EVIDENCE_CONTRADICTED):
            refusals.append(
                f"PASS for {item_code} over {status.lower()} evidence — a PASS can never invent "
                "missing or contradicted evidence"
            )
        if answer.answer == ANSWER_NOT_APPLICABLE and status in (
                EVIDENCE_PRESENT, EVIDENCE_CONTRADICTED):
            refusals.append(
                f"NOT_APPLICABLE for {item_code} while the evidence is {status.lower()}"
            )
    if refusals:
        return _refused_acceptance(
            "the acceptance answers contradict the evidence", deliverable=deliverable,
            refusal_reasons=refusals,
        )

    # ---- the verdict: evidence and answers together, never forced.
    results: list[FabricatorChecklistResult] = []
    findings: list[FabricatorAcceptanceFinding] = []
    blocking: list[str] = []
    for item_code in DELIVERABLE_CHECKLIST_ITEMS:
        status, visible, evaluator_findings = evidence[item_code]
        answer = by_item[item_code]
        results.append(FabricatorChecklistResult(
            checklist_item=item_code,
            question=_question(item_code),
            answer=answer.answer,
            evidence_status=status,
            visible_in_drawing=visible,
            finding=answer.finding,
            evaluator_findings=evaluator_findings,
        ))
        findings.extend(evaluator_findings)
        if status == EVIDENCE_CONTRADICTED:
            blocking.append(f"{item_code} evidence is contradicted")
        elif status == EVIDENCE_MISSING and item_code in REQUIRED_DELIVERABLE_ITEMS:
            blocking.append(f"{item_code} evidence is missing")
        if answer.answer == ANSWER_FAIL:
            findings.append(FabricatorAcceptanceFinding(
                item_code, FINDING_MISSING_INFORMATION,
                f"the reviewer failed {item_code}: {answer.finding}",
            ))
            blocking.append(f"{item_code} failed review ({answer.finding})")

    summary = (
        f"checklist items: {len(DELIVERABLE_CHECKLIST_ITEMS)}",
        f"evidence present: "
        f"{sum(1 for r in results if r.evidence_status == EVIDENCE_PRESENT)}",
        f"evidence missing: "
        f"{sum(1 for r in results if r.evidence_status == EVIDENCE_MISSING)}",
        f"evidence contradicted: "
        f"{sum(1 for r in results if r.evidence_status == EVIDENCE_CONTRADICTED)}",
        f"reviewer failures: {sum(1 for r in results if r.answer == ANSWER_FAIL)}",
    )

    if blocking:
        return ResolvedConflictAcceptance(
            status=ACCEPTANCE_STATUS_NOT_ACCEPTED,
            reason="the deliverable's evidence and the reviewer's verdict do not support "
                   "acceptance: " + "; ".join(blocking),
            conflict_id=deliverable.conflict_id,
            connection_id=deliverable.connection_id,
            deliverable_status=deliverable.status,
            results=tuple(results),
            findings=tuple(findings),
            refusal_reasons=(),
            summary=summary,
        )

    return ResolvedConflictAcceptance(
        status=ACCEPTANCE_STATUS_ACCEPTED,
        reason="every required item's evidence is present and consistent with the reviewer's "
               "verdict",
        conflict_id=deliverable.conflict_id,
        connection_id=deliverable.connection_id,
        deliverable_status=deliverable.status,
        results=tuple(results),
        findings=tuple(findings),
        refusal_reasons=(),
        summary=summary,
    )
