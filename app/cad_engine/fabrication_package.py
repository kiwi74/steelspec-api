"""Milestone 7AR — Real Fabrication Drawing Package / Production Deliverable.

7AQ proved that one accepted production job is traceable end to end,
from the drawing evidence through every gate to a verified fabrication
drawing artifact. 7AR proves that the accepted job produces a real,
coherent, fabricator-facing drawing package: the correct independently
verified artifacts gathered under one deterministic project manifest,
with package-level deliverable checks a fabricator (or an audit) can
read without opening any internal workflow object.

    ProductionJobAcceptance (7AQ — the authoritative record)
            |
    build_fabrication_package()                 (per accepted job)
            |   READY    -> output_dir/drawings/STEELSPEC-00N.pdf
            |               (byte copies of the recorded, 7AG-verified
            |                artifacts only — never regenerated) plus
            |               output_dir/project-manifest.json
            |               (deterministic projection of the records)
            |   BLOCKED  -> nothing written; the issues say exactly why
            |   REFUSED  -> stale caller revision (or a refused
            |               acceptance); nothing written
            v
    FabricationDrawingPackage (frozen; READY / BLOCKED / REFUSED)

HARD RULES (never broken):

  * 7AG REMAINS AUTHORITATIVE FOR ARTIFACT VERIFICATION. This module
    never re-runs 7AG, never re-decides 7AA/7AE permission, never
    calls the drawing generator, never regenerates, repairs or
    substitutes anything. The package checks below are deliverable-
    quality checks over the acceptance's recorded evidence and the
    artifact bytes as they exist now — never a second engineering
    sign-off (7AG's own scope statement stays the only authority on
    what VERIFIED means).
  * THE RECORD IS THE TRUTH. An artifact's identity is the acceptance's
    recorded 7AG sha256 plus the recorded dispatch path — never file
    existence, never filenames, never a caller's claim. Changed bytes
    after verification, a substituted file, a copied artifact under a
    new name and an injected status are all detected from the recorded
    evidence, not trusted from the disk.
  * NOTHING IS INVENTED. Every engineering value in the manifest is
    either observed from the artifact's own text or carried verbatim
    from the acceptance record. A value the artifact does not carry is
    reported absent (omitted from `observed`, or an explicit issue) —
    never guessed, never defaulted to something plausible.
  * PROVENANCE VOCABULARY IS THE EXISTING ONE. AI_EXTRACTED /
    HUMAN_REVIEWED / HUMAN_SUPPLEMENTED only (7W); no new provenance
    type is invented here.
  * DETERMINISTIC. Same acceptance + same output_dir -> same files,
    same manifest bytes, same drawing numbers, same ordering
    (submission order — never filesystem order, never object
    identity). The manifest carries no build timestamps. (The copied
    PDF bytes are whatever the recorded artifacts are: the generator's
    PDF metadata embeds a creation date, so artifacts generated on
    different days differ in bytes even when every drawn value is
    identical — a documented generator property, not hidden here.)
  * NO TRUST IN USER-EDITABLE PACKAGE FIELDS. The package has no
    `status`, `approved` or `decision` parameter; the manifest is a
    projection written once and never read back; a package is always
    rebuilt from the acceptance record.
  * NO SECOND SOURCE OF TRUTH. Every manifest value is projected from
    the acceptance (and the artifact text); nothing here re-derives
    automation, gate, dispatch or verification decisions.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.cad_engine.automation_gate import AUTOMATION_DECISION_AUTO
from app.cad_engine.drawing_dispatch import OUTPUT_STATUS_GENERATED
from app.cad_engine.drawing_output_verification import CHECK_FAILED, VERIFICATION_STATUS_VERIFIED
from app.cad_engine.multi_member_connection import MEMBER_LABEL_LETTERS, member_label
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
    ProductionJobAcceptance,
)

__all__ = [
    "PACKAGE_STATUS_READY", "PACKAGE_STATUS_BLOCKED", "PACKAGE_STATUS_REFUSED",
    "PACKAGE_STATUSES", "PACKAGE_SCOPE_STATEMENT",
    "ISSUE_BLOCKER", "ISSUE_NOTE", "ISSUE_SEVERITIES",
    "CHECK_PASSED", "CHECK_FAILED", "CHECK_NOT_PRESENT_IN_ARTIFACT", "CHECK_STATUSES",
    "CHECK_SOURCE_ARTIFACT_PRESENT", "CHECK_SOURCE_SHA256_MATCHES_RECORDED",
    "CHECK_RECORDED_CHECKS_PASSED", "CHECK_ACCEPTANCE_FIELDS_CONSISTENT",
    "CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION", "CHECK_PDF_OPENS",
    "CHECK_PAGE_COUNT_MATCHES_RECORDED", "CHECK_DRAWING_NUMBER_PRESENT",
    "CHECK_CONNECTION_IDENTITY_PRESENT", "CHECK_TITLE_BLOCK_PRESENT",
    "CHECK_PROJECT_IDENTITY_IN_ARTIFACT", "CHECK_MEMBER_IDENTITIES_PRESENT",
    "CHECK_NO_TRACEBACK_TEXT", "CHECK_NO_EXCEPTION_REPR", "CHECK_NO_NONE_VALUES",
    "CHECK_NO_RAW_AI_DUMP", "CHECK_NO_CONTRADICTORY_CONNECTION_IDS",
    "CHECK_NO_DUPLICATE_IDENTITY", "CHECK_MANIFEST_COHERENCE",
    "CHECK_NO_UNRESOLVED_CONNECTION_IN_PACKAGE",
    "FabricationPackageCheck", "FabricationPackageIssue", "FabricationPackageItem",
    "FabricationPackageSummary", "FabricationDrawingPackage",
    "build_fabrication_package",
]

# Package outcomes. BLOCKED: the package was evaluated and one or more
# package-level checks failed — nothing is written (no partial package
# can be mistaken for a deliverable). REFUSED: the request itself
# cannot be evaluated (stale caller revision, or an acceptance 7AQ
# already refused). READY: every check passed and the package was
# written.
PACKAGE_STATUS_READY = "READY"
PACKAGE_STATUS_BLOCKED = "BLOCKED"
PACKAGE_STATUS_REFUSED = "REFUSED"
PACKAGE_STATUSES = (PACKAGE_STATUS_READY, PACKAGE_STATUS_BLOCKED, PACKAGE_STATUS_REFUSED)

PACKAGE_SCOPE_STATEMENT = (
    "This package gathers the already-accepted, 7AG-verified fabrication drawing artifacts of one "
    "accepted production job under a deterministic project manifest. 7AG remains the authority on "
    "artifact verification; the package checks are deliverable-quality checks over the recorded "
    "evidence and the artifact bytes as they exist now. The package never regenerates a drawing, "
    "never invents engineering information, and never includes an unresolved connection."
)

ISSUE_BLOCKER = "BLOCKER"
ISSUE_NOTE = "NOTE"
ISSUE_SEVERITIES = (ISSUE_BLOCKER, ISSUE_NOTE)

# Per-check statuses. NOT_PRESENT_IN_ARTIFACT (7AG's existing
# third-state vocabulary, re-used): the check could not observe the
# information in the artifact — reported honestly, never passed by
# assumption, and never treated as a failure.
CHECK_PASSED = "PASSED"
CHECK_FAILED = "FAILED"
CHECK_NOT_PRESENT_IN_ARTIFACT = "NOT_PRESENT_IN_ARTIFACT"
CHECK_STATUSES = (CHECK_PASSED, CHECK_FAILED, CHECK_NOT_PRESENT_IN_ARTIFACT)

# Per-item checks, in the execution order of _evaluate_item().
CHECK_ACCEPTANCE_FIELDS_CONSISTENT = "ACCEPTANCE_FIELDS_CONSISTENT"
CHECK_SOURCE_ARTIFACT_PRESENT = "SOURCE_ARTIFACT_PRESENT"
CHECK_SOURCE_SHA256_MATCHES_RECORDED = "SOURCE_SHA256_MATCHES_RECORDED"
CHECK_RECORDED_CHECKS_PASSED = "RECORDED_CHECKS_PASSED"
CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION = "DISPATCH_IDENTITY_MATCHES_CONNECTION"
CHECK_PDF_OPENS = "PDF_OPENS"
CHECK_PAGE_COUNT_MATCHES_RECORDED = "PAGE_COUNT_MATCHES_RECORDED"
CHECK_DRAWING_NUMBER_PRESENT = "DRAWING_NUMBER_PRESENT"
CHECK_CONNECTION_IDENTITY_PRESENT = "CONNECTION_IDENTITY_PRESENT"
CHECK_TITLE_BLOCK_PRESENT = "TITLE_BLOCK_PRESENT"
CHECK_PROJECT_IDENTITY_IN_ARTIFACT = "PROJECT_IDENTITY_IN_ARTIFACT"
CHECK_MEMBER_IDENTITIES_PRESENT = "MEMBER_IDENTITIES_PRESENT"
CHECK_NO_TRACEBACK_TEXT = "NO_TRACEBACK_TEXT"
CHECK_NO_EXCEPTION_REPR = "NO_EXCEPTION_REPR"
CHECK_NO_NONE_VALUES = "NO_NONE_VALUES"
CHECK_NO_RAW_AI_DUMP = "NO_RAW_AI_DUMP"
CHECK_NO_CONTRADICTORY_CONNECTION_IDS = "NO_CONTRADICTORY_CONNECTION_IDS"

# Project-level checks.
CHECK_NO_DUPLICATE_IDENTITY = "NO_DUPLICATE_IDENTITY"
CHECK_MANIFEST_COHERENCE = "MANIFEST_COHERENCE"
CHECK_NO_UNRESOLVED_CONNECTION_IN_PACKAGE = "NO_UNRESOLVED_CONNECTION_IN_PACKAGE"

# The existing generator's drawing-number convention (7T) and the
# existing 7AF dispatch naming rule (restated here, 7AG's precedent,
# so the recorded filename can be checked against the connection
# identity without importing new rules).
_DRAWING_NUMBER_PREFIX = "FAB"
_FABRICATION_SUFFIX = "-fabrication"

# The package's own deterministic drawing numbering: STEELSPEC-001,
# STEELSPEC-002, ... in the acceptance's submission order (7X order),
# never filesystem order and never object identity. The width constant
# is spelled len("001") so the module carries no bare numeric literal
# beyond the counts it genuinely needs.
_PACKAGE_NUMBER_PREFIX = "STEELSPEC"
_PACKAGE_NUMBER_WIDTH = len("001")

# Title-block labels the generator draws as label line + value line
# pairs (SECTION/LENGTH appear once per member). The member rows are
# labelled "MEMBER A".."MEMBER Z" by member_label() — the 7T
# two-member block draws the first two; 7AV's multi-member block draws
# one per member. Recognizing the full label vocabulary here lets a
# multi-member artifact's own rows be observed, never truncated to two.
_TITLE_BLOCK_LABELS = (
    "DRAWING NO.", "REV", "DATE", "PROJECT", "SOURCE DRAWING",
    "SECTION", "LENGTH", "MATERIAL", "UNITS", "SCALE", "STATUS",
) + tuple(member_label(index) for index in range(len(MEMBER_LABEL_LETTERS)))
# Single-line info-block labels drawn with a trailing colon.
_INFO_BLOCK_LABELS = ("PLATE", "HOLES", "PATTERN")

# Negative presentation scans: a fabricator-facing drawing never
# carries these. "AIExtracted" is the real label prefix of the AI
# schema's value names — a raw AI dump would carry it; the drawing
# carries accepted engineering values only.
_FORBIDDEN_SUBSTRINGS = ("Traceback", "<class", " at 0x", "AIExtracted")
_CONNECTION_ID_PATTERN = re.compile(r"\bCONN-[A-Z0-9-]+\b")
_NONE_VALUE_LINES = ("None", "NONE", "null")

_MANIFEST_SCHEMA = "steelspec-fabrication-package-1"
_MANIFEST_FILENAME = "project-manifest.json"
_DRAWINGS_SUBDIR = "drawings"


@dataclass(frozen=True)
class FabricationPackageCheck:
    """
    One deterministic package-level observation. Every FAILED check
    explains what was wrong; every NOT_PRESENT_IN_ARTIFACT check
    explains which information the artifact does not carry.
    """
    code: str
    status: str
    detail: str


@dataclass(frozen=True)
class FabricationPackageIssue:
    """One package finding. BLOCKER issues fail the package; NOTE issues are
    recorded observations (e.g. a value the artifact does not carry, supplied
    by the manifest instead) and never invent a substitute."""
    code: str
    severity: str
    detail: str


@dataclass(frozen=True)
class FabricationPackageItem:
    """
    One packaged drawing. `drawing_number` is the package's own
    deterministic number (STEELSPEC-00N); `source_artifact` is the
    recorded 7AG-verified artifact this drawing is a byte copy of;
    `recorded_sha256` is the acceptance's recorded artifact hash and
    `artifact_sha256` is the hash recomputed over the packaged bytes;
    `observed` is the fabricator-facing title-block information
    observed from the artifact's own text (absent values are omitted,
    never invented); `audit` is the concise backward trail to the
    drawing evidence and human decisions; `source_bytes` is the exact
    evaluated artifact content (the packaged copy is written from it,
    so it can never diverge from what was hashed and inspected).
    """
    drawing_number: str
    connection_id: str
    package_id: str
    filename: str
    source_artifact: str
    source_filename: str
    artifact_sha256: str | None
    recorded_sha256: str | None
    page_count: int | None
    verification_status: str | None
    dispatch_output_status: str | None
    checks: tuple[FabricationPackageCheck, ...]
    observed: tuple[tuple[str, str], ...]
    audit: tuple[tuple[str, object], ...]
    packaged_path: str | None
    source_bytes: bytes | None = None


@dataclass(frozen=True)
class FabricationPackageSummary:
    """Project-level counts, computed from the items (never asserted)."""
    total_accepted_connections: int
    total_drawing_artifacts: int
    verified_artifacts: int
    missing_artifacts: int
    failed_artifacts: int
    package_issues: int
    final_package_status: str


@dataclass(frozen=True)
class FabricationDrawingPackage:
    """
    The frozen package result. `manifest` is the deterministic
    ordered projection of exactly what was (READY) or would have been
    (BLOCKED) written to `manifest_path` — inspectable without opening
    any internal workflow object, and never read back from disk.
    """
    status: str
    reason: str
    project_id: str | None
    source_drawing_id: str | None
    workflow_revision: int
    acceptance_status: str
    caller_revision: int | None
    items: tuple[FabricationPackageItem, ...]
    manifest_path: str | None
    summary: FabricationPackageSummary
    issues: tuple[FabricationPackageIssue, ...]
    manifest: tuple[tuple[str, object], ...]


# --------------------------------------------------------------------------------------
# Small deterministic helpers.
# --------------------------------------------------------------------------------------
def _check(code: str, status: str, detail: str) -> FabricationPackageCheck:
    return FabricationPackageCheck(code=code, status=status, detail=detail)


def _issue(code: str, severity: str, detail: str) -> FabricationPackageIssue:
    return FabricationPackageIssue(code=code, severity=severity, detail=detail)


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonable(value: object) -> object:
    """A JSON-serializable projection of one recorded value. Plain data passes
    through unchanged; anything without a JSON shape is represented by its
    deterministic string form (the acceptance's frozen trace still carries the
    verbatim value — the manifest never replaces it)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(entry) for entry in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(entry) for key, entry in value.items()}
    return str(value)


def _extract_pdf_text(reader) -> str:
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _label_values(lines: list[str]) -> dict[str, list[str]]:
    """The title block's label -> value(s) pairs, read from the artifact's own
    text lines (the generator draws each label line directly above its value
    line). SECTION/LENGTH collect one value per member, in drawing order."""
    values: dict[str, list[str]] = {}
    for index, line in enumerate(lines):
        if line in _TITLE_BLOCK_LABELS:
            value = ""
            for candidate in lines[index + 1:]:
                if candidate:
                    value = candidate
                    break
            values.setdefault(line, []).append(value)
    return values


def _info_lines(lines: list[str]) -> dict[str, str]:
    """The connection info block's single-line entries (PLATE: / HOLES: /
    PATTERN:), keyed by label without the colon."""
    found: dict[str, str] = {}
    for line in lines:
        for label in _INFO_BLOCK_LABELS:
            if line.startswith(f"{label}:") and label not in found:
                found[label] = line[len(label) + 1:].strip()
    return found


def _observed_fields(
    label_values: dict[str, list[str]],
    info_lines: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    """The fabricator-facing fields observed from the artifact text, in a fixed
    deterministic order. Keys whose value the artifact does not carry are
    omitted — absence is visible absence, never a fabricated default."""
    observed: list[tuple[str, str]] = []
    single = {
        "DRAWING NO.": "drawing_number_observed",
        "REV": "revision",
        "DATE": "date",
        "PROJECT": "project",
        "SOURCE DRAWING": "source_drawing",
        "MATERIAL": "material",
        "UNITS": "units",
        "SCALE": "scale",
        "STATUS": "status",
    }
    # One observed mark per member row, letter-labelled in drawing
    # order (member_a_mark, member_b_mark, member_c_mark, ...) — the
    # two-member drawing produces exactly the original two keys.
    for index in range(len(MEMBER_LABEL_LETTERS)):
        letter = MEMBER_LABEL_LETTERS[index].lower()
        single[member_label(index)] = f"member_{letter}_mark"
    for label, key in single.items():
        values = label_values.get(label)
        if values and values[0]:
            observed.append((key, values[0]))
    sections = label_values.get("SECTION", [])
    lengths = label_values.get("LENGTH", [])
    for position, letter in enumerate(MEMBER_LABEL_LETTERS):
        key = f"member_{letter.lower()}_section"
        if len(sections) > position and sections[position]:
            observed.append((key, sections[position]))
    for position, letter in enumerate(MEMBER_LABEL_LETTERS):
        key = f"member_{letter.lower()}_length"
        if len(lengths) > position and lengths[position]:
            observed.append((key, lengths[position]))
    for label in _INFO_BLOCK_LABELS:
        if label in info_lines:
            observed.append((label.lower(), info_lines[label]))
    return tuple(observed)


def _connection_ids_in(text: str) -> set[str]:
    return set(_CONNECTION_ID_PATTERN.findall(text))


# --------------------------------------------------------------------------------------
# Per-item evaluation.
# --------------------------------------------------------------------------------------
def _evaluate_item(
    acceptance: ProductionJobAcceptance,
    conn,
    drawing_number: str,
) -> FabricationPackageItem:
    """
    One connection's package-level evaluation. The acceptance record is the
    evidence; the artifact is the recorded dispatch path, read once, hashed
    from those exact bytes, and parsed from those exact bytes (a file changed
    mid-read can only fail, never pass). Checks stop at the first failure
    (7AG's own convention); NOT_PRESENT_IN_ARTIFACT observations do not stop
    the sequence.
    """
    checks: list[FabricationPackageCheck] = []
    recorded_sha256 = conn.sha256
    page_count = conn.page_count
    artifact_sha256: str | None = None
    observed: tuple[tuple[str, str], ...] = ()
    packaged_path: str | None = None

    # ---- the recorded acceptance fields must agree with an accepted connection
    field_problems: list[str] = []
    if conn.decision != AUTOMATION_DECISION_AUTO:
        field_problems.append(f"recorded decision {conn.decision!r}, not {AUTOMATION_DECISION_AUTO!r}")
    if conn.output_status != OUTPUT_STATUS_GENERATED:
        field_problems.append(f"recorded dispatch status {conn.output_status!r}, not {OUTPUT_STATUS_GENERATED!r}")
    if conn.verification_status != VERIFICATION_STATUS_VERIFIED:
        field_problems.append(
            f"recorded verification status {conn.verification_status!r}, not "
            f"{VERIFICATION_STATUS_VERIFIED!r} (7AG stays authoritative)")
    if conn.connection_id is None:
        field_problems.append("the acceptance records no connection identity")
    if conn.verified_artifact is None:
        field_problems.append("the acceptance records no verified artifact")
    if conn.artifact_exists is not True:
        field_problems.append(f"the acceptance records artifact_exists = {conn.artifact_exists!r}")
    if recorded_sha256 is None:
        field_problems.append("the acceptance records no artifact sha256")
    if page_count is None:
        field_problems.append("the acceptance records no page count")
    if field_problems:
        checks.append(_check(
            CHECK_ACCEPTANCE_FIELDS_CONSISTENT, CHECK_FAILED,
            "an accepted connection's recorded fields disagree: " + "; ".join(field_problems) + ".",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id or "",
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=conn.verified_artifact or "",
            source_filename=Path(conn.verified_artifact).name if conn.verified_artifact else "",
            artifact_sha256=None, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_ACCEPTANCE_FIELDS_CONSISTENT, CHECK_PASSED,
        f"the acceptance records an accepted connection ({conn.package_id}) with decision AUTO, "
        "dispatch GENERATED and verification VERIFIED",
    ))

    source_path = Path(conn.verified_artifact)
    try:
        data = source_path.read_bytes()
    except OSError as error:
        checks.append(_check(
            CHECK_SOURCE_ARTIFACT_PRESENT, CHECK_FAILED,
            f"the recorded artifact {source_path} could not be read "
            f"({type(error).__name__}: {error}); the package never hunts for a substitute file.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=None, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    if not data:
        checks.append(_check(
            CHECK_SOURCE_ARTIFACT_PRESENT, CHECK_FAILED,
            f"the recorded artifact {source_path} is empty; an empty file is not a drawing.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=None, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_SOURCE_ARTIFACT_PRESENT, CHECK_PASSED,
        f"the recorded artifact {source_path} was read ({len(data)} bytes)",
    ))

    artifact_sha256 = _sha256_of_bytes(data)
    if artifact_sha256 != recorded_sha256:
        checks.append(_check(
            CHECK_SOURCE_SHA256_MATCHES_RECORDED, CHECK_FAILED,
            f"the artifact's bytes no longer match the verification manifest hash "
            f"({artifact_sha256!r} != recorded {recorded_sha256!r}); the bytes were changed "
            "after 7AG verified them.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_SOURCE_SHA256_MATCHES_RECORDED, CHECK_PASSED,
        f"sha256 = {artifact_sha256} (matches the recorded verification hash)",
    ))

    if not conn.checks or any(check.status == CHECK_FAILED for check in conn.checks):
        failed = [check.code for check in conn.checks if check.status == CHECK_FAILED]
        checks.append(_check(
            CHECK_RECORDED_CHECKS_PASSED, CHECK_FAILED,
            "the acceptance's recorded 7AG checks include a failure"
            + (f" ({', '.join(failed)})" if failed else ": the acceptance records no checks"),
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_RECORDED_CHECKS_PASSED, CHECK_PASSED,
        f"the acceptance records {len(conn.checks)} 7AG check(s), none failed",
    ))

    expected_stem = f"{conn.connection_id}{_FABRICATION_SUFFIX}"
    if source_path.stem != expected_stem:
        checks.append(_check(
            CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION, CHECK_FAILED,
            f"the recorded artifact {source_path.name} does not carry the connection identity "
            f"{conn.connection_id} (expected stem {expected_stem!r}); an artifact recorded for "
            "another connection can never be packaged under this one.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION, CHECK_PASSED,
        f"the recorded filename {source_path.name} carries the connection identity "
        f"{conn.connection_id}",
    ))

    try:
        # The same bytes that were hashed are parsed — the artifact can
        # never change between the hash and the inspection.
        reader = PdfReader(BytesIO(data))
        observed_page_count = len(reader.pages)
        text = _extract_pdf_text(reader)
    except Exception as error:  # noqa: BLE001 — recorded, never repaired
        checks.append(_check(
            CHECK_PDF_OPENS, CHECK_FAILED,
            f"the real PDF parser could not open the artifact ({type(error).__name__}: "
            f"{error}); the package never repairs or regenerates.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(CHECK_PDF_OPENS, CHECK_PASSED, "opened by the real PDF parser"))

    if observed_page_count != page_count:
        checks.append(_check(
            CHECK_PAGE_COUNT_MATCHES_RECORDED, CHECK_FAILED,
            f"the artifact has {observed_page_count} page(s) but the verification manifest "
            f"records {page_count}; the artifact does not correspond to the recorded verification.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_PAGE_COUNT_MATCHES_RECORDED, CHECK_PASSED, f"{observed_page_count} page(s)",
    ))

    normalized = " ".join(text.split())
    lines = [line.strip() for line in text.splitlines()]

    expected_drawing_number = f"{_DRAWING_NUMBER_PREFIX}-{conn.connection_id}"
    if expected_drawing_number not in normalized:
        checks.append(_check(
            CHECK_DRAWING_NUMBER_PRESENT, CHECK_FAILED,
            f"the artifact text does not carry the generator's drawing number "
            f"{expected_drawing_number!r} for connection {conn.connection_id}.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_DRAWING_NUMBER_PRESENT, CHECK_PASSED,
        f"drawing number {expected_drawing_number!r} present in the artifact text",
    ))

    if conn.connection_id not in normalized:
        checks.append(_check(
            CHECK_CONNECTION_IDENTITY_PRESENT, CHECK_FAILED,
            f"the artifact text does not name the connection {conn.connection_id}.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_CONNECTION_IDENTITY_PRESENT, CHECK_PASSED,
        f"the artifact text names connection {conn.connection_id}",
    ))

    if "STEELSPEC" not in normalized or "FABRICATION DRAWING" not in normalized:
        checks.append(_check(
            CHECK_TITLE_BLOCK_PRESENT, CHECK_FAILED,
            "the artifact text does not carry the generator's title block "
            "(\"STEELSPEC\" / \"FABRICATION DRAWING\").",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_TITLE_BLOCK_PRESENT, CHECK_PASSED,
        "title block present (\"STEELSPEC\" / \"FABRICATION DRAWING\")",
    ))

    project_id = acceptance.project_id
    if project_id is not None and project_id in normalized:
        checks.append(_check(
            CHECK_PROJECT_IDENTITY_IN_ARTIFACT, CHECK_PASSED,
            f"the artifact text names the project {project_id}",
        ))
    else:
        checks.append(_check(
            CHECK_PROJECT_IDENTITY_IN_ARTIFACT, CHECK_NOT_PRESENT_IN_ARTIFACT,
            f"the artifact text does not name the project"
            + (f" {project_id}" if project_id is not None else "")
            + "; the package manifest supplies the project identity.",
        ))

    label_values = _label_values(lines)
    member_problems: list[str] = []
    all_member_labels = tuple(member_label(index) for index in range(len(MEMBER_LABEL_LETTERS)))
    present_labels = [label for label in all_member_labels if label_values.get(label)]
    member_count = len(present_labels)
    # The generator draws one contiguous row per member, starting at
    # MEMBER A: a complete set of member identity rows is exactly the
    # first member_count labels, each with a mark value, plus one
    # SECTION and one LENGTH value per member. This is the two-member
    # check generalized (a two-member drawing yields exactly
    # MEMBER A/MEMBER B + 2 sections + 2 lengths, as before) — the
    # SPECIFIC marks are already verified against the genuine assembly
    # by 7AG at dispatch time; this check guards that the deliverable
    # carries a complete, coherent set of member rows.
    if member_count < 2:
        member_problems.append("fewer than two member rows present")
    elif present_labels != list(all_member_labels[:member_count]):
        member_problems.append(
            "the member rows are not the contiguous set "
            f"'MEMBER A'..'MEMBER {MEMBER_LABEL_LETTERS[member_count - 1]}'"
        )
    sections = label_values.get("SECTION", [])
    lengths = label_values.get("LENGTH", [])
    if len(sections) < member_count or not all(sections[:member_count]):
        member_problems.append("not every member section present")
    if len(lengths) < member_count or not all(lengths[:member_count]):
        member_problems.append("not every member length present")
    if member_problems:
        checks.append(_check(
            CHECK_MEMBER_IDENTITIES_PRESENT, CHECK_FAILED,
            "the artifact text does not carry a complete, coherent set of member identity "
            "rows: " + "; ".join(member_problems) + ".",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_MEMBER_IDENTITIES_PRESENT, CHECK_PASSED,
        f"{member_count} member row(s) present, each with mark, section and length",
    ))

    for forbidden in _FORBIDDEN_SUBSTRINGS:
        if forbidden in text:
            checks.append(_check(
                CHECK_NO_TRACEBACK_TEXT if forbidden == "Traceback" else
                CHECK_NO_EXCEPTION_REPR if forbidden in ("<class", " at 0x") else
                CHECK_NO_RAW_AI_DUMP,
                CHECK_FAILED,
                f"the artifact text contains {forbidden!r}; a fabricator-facing drawing "
                "carries none of these.",
            ))
            return FabricationPackageItem(
                drawing_number=drawing_number,
                connection_id=conn.connection_id,
                package_id=conn.package_id,
                filename=f"{drawing_number}.pdf",
                source_artifact=str(source_path), source_filename=source_path.name,
                artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
                verification_status=conn.verification_status,
                dispatch_output_status=conn.output_status,
                checks=tuple(checks), observed=(), audit=(), packaged_path=None,
            )
    checks.append(_check(
        CHECK_NO_TRACEBACK_TEXT, CHECK_PASSED, "no Python traceback text in the artifact",
    ))
    checks.append(_check(
        CHECK_NO_EXCEPTION_REPR, CHECK_PASSED, "no internal exception representation in the artifact",
    ))
    checks.append(_check(
        CHECK_NO_RAW_AI_DUMP, CHECK_PASSED, "no raw AI extraction dump in the artifact",
    ))

    none_lines = [line for line in lines if line in _NONE_VALUE_LINES]
    if none_lines:
        checks.append(_check(
            CHECK_NO_NONE_VALUES, CHECK_FAILED,
            "the artifact text carries a bare None value line; missing information must read "
            "NOT SPECIFIED or be absent — never None.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(CHECK_NO_NONE_VALUES, CHECK_PASSED, "no bare None value in the artifact"))

    ids = _connection_ids_in(normalized)
    if ids != {conn.connection_id}:
        checks.append(_check(
            CHECK_NO_CONTRADICTORY_CONNECTION_IDS, CHECK_FAILED,
            f"the artifact text carries connection identities {sorted(ids)} but the package "
            f"records {conn.connection_id}; a substituted artifact can never pass.",
        ))
        return FabricationPackageItem(
            drawing_number=drawing_number,
            connection_id=conn.connection_id,
            package_id=conn.package_id,
            filename=f"{drawing_number}.pdf",
            source_artifact=str(source_path), source_filename=source_path.name,
            artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
            verification_status=conn.verification_status,
            dispatch_output_status=conn.output_status,
            checks=tuple(checks), observed=(), audit=(), packaged_path=None,
        )
    checks.append(_check(
        CHECK_NO_CONTRADICTORY_CONNECTION_IDS, CHECK_PASSED,
        f"the artifact text carries exactly the connection identity {conn.connection_id}",
    ))

    observed = _observed_fields(label_values, _info_lines(lines))
    packaged_path = str(Path("drawings") / f"{drawing_number}.pdf")
    return FabricationPackageItem(
        drawing_number=drawing_number,
        connection_id=conn.connection_id,
        package_id=conn.package_id,
        filename=f"{drawing_number}.pdf",
        source_artifact=str(source_path), source_filename=source_path.name,
        artifact_sha256=artifact_sha256, recorded_sha256=recorded_sha256, page_count=page_count,
        verification_status=conn.verification_status,
        dispatch_output_status=conn.output_status,
        checks=tuple(checks), observed=observed,
        audit=_item_audit(conn),
        packaged_path=packaged_path,
        source_bytes=data,
    )


def _item_audit(conn) -> tuple[tuple[str, object], ...]:
    """The concise backward trail for one drawing: package -> artifact ->
    7AG verification -> dispatch -> acceptance -> human resolutions ->
    tasks/blocker codes -> AI observations -> drawing evidence. Every value is
    carried verbatim from the acceptance record; nothing is re-derived."""
    evidence = conn.trace.evidence
    contract = conn.trace.contract
    human_decisions = []
    for decision in conn.trace.human_decisions:
        human_decisions.append({
            "task_id": decision.task_id,
            "task_type": decision.task_type,
            "question": decision.question,
            "blocker_codes": list(decision.blocker_codes),
            "answer_type": decision.answer_type,
            "answer": _jsonable(decision.answer),
            "evidence": decision.evidence,
            "applied": decision.applied,
            "refusal_reason": decision.refusal_reason,
        })
    return (
        ("package_id", conn.package_id),
        ("acceptance", {
            "accepted": conn.accepted,
            "reason": conn.reason,
            "decision": conn.decision,
            "output_status": conn.output_status,
            "verification_status": conn.verification_status,
        }),
        ("drawing_evidence", {
            "source_drawing_id": evidence.source_drawing_id,
            "drawing_number": evidence.drawing_number,
            "source_page": evidence.source_page,
            "detail_reference": evidence.detail_reference,
            "grid_reference": evidence.grid_reference,
        }),
        ("ai_observations", {
            "note": "AI-extracted observations from the drawing — preserved verbatim, "
                    "not engineering facts",
            "member_references": list(contract.ai_member_references),
            "bolt_readings": list(contract.ai_bolt_readings),
            "plate_readings": list(contract.ai_plate_readings),
            "weld_readings": list(contract.ai_weld_readings),
            "malformed_readings": list(contract.ai_malformed_readings),
            "unrecognised_readings": list(contract.ai_unrecognised_readings),
            "connection_type": contract.ai_connection_type,
            "confidence": contract.ai_confidence,
            # Material (7AT): a key appears only when the AI genuinely observed a
            # grade — for drawings that state none, the key's absence is itself
            # the truthful audit record.
            **({"material": contract.ai_material} if contract.ai_material is not None else {}),
        }),
        ("field_provenance", [
            [entry.field, entry.provenance] for entry in contract.provenance
        ]),
        ("human_decisions", human_decisions),
        ("verification", {
            "status": conn.verification_status,
            "recorded_sha256": conn.sha256,
            "page_count": conn.page_count,
            "checks": [[check.code, check.status] for check in conn.checks],
        }),
    )


# --------------------------------------------------------------------------------------
# Project-level evaluation.
# --------------------------------------------------------------------------------------
def _project_checks(
    acceptance: ProductionJobAcceptance,
    items: tuple[FabricationPackageItem, ...],
) -> tuple[FabricationPackageCheck, ...]:
    """The package-level checks over the evaluated items: no duplicate drawing
    identity, coherent manifest projection, no unresolved connection present as
    a completed drawing."""
    checks: list[FabricationPackageCheck] = []

    connection_ids = [item.connection_id for item in items]
    drawing_numbers = [item.drawing_number for item in items]
    source_filenames = [item.source_filename for item in items]
    if len(set(connection_ids)) == len(items) and len(set(drawing_numbers)) == len(items) \
            and len(set(source_filenames)) == len(items):
        checks.append(_check(
            CHECK_NO_DUPLICATE_IDENTITY, CHECK_PASSED,
            f"{len(items)} distinct connection identities, drawing numbers and source "
            "filenames — no duplicate drawing identity",
        ))
    else:
        duplicates: list[str] = []
        for label, values in (
            ("connection id", connection_ids), ("drawing number", drawing_numbers),
            ("source filename", source_filenames),
        ):
            seen: set[str] = set()
            for value in values:
                if value in seen:
                    duplicates.append(f"{label} {value!r}")
                seen.add(value)
        checks.append(_check(
            CHECK_NO_DUPLICATE_IDENTITY, CHECK_FAILED,
            "duplicate drawing identity: " + ", ".join(sorted(set(duplicates))) + ".",
        ))

    accepted_by_record = tuple(item.package_id for item in items)
    coherence_problems: list[str] = []
    if acceptance.accepted_package_ids != accepted_by_record:
        coherence_problems.append(
            f"the acceptance records accepted packages {list(acceptance.accepted_package_ids)} "
            f"but the package derived {list(accepted_by_record)} from the connections"
        )
    if set(accepted_by_record) & set(acceptance.unresolved_package_ids):
        coherence_problems.append(
            "an unresolved package appears among the accepted connections"
        )
    if set(accepted_by_record) & set(acceptance.failed_package_ids):
        coherence_problems.append(
            "a failed package appears among the accepted connections"
        )
    summary = acceptance.summary
    if summary.connections_accepted != len(items):
        coherence_problems.append(
            f"the acceptance summary counts {summary.connections_accepted} accepted "
            f"connection(s) but the package derived {len(items)}"
        )
    if summary.connections_total != len(acceptance.connections):
        coherence_problems.append(
            f"the acceptance summary counts {summary.connections_total} connection(s) but "
            f"the acceptance carries {len(acceptance.connections)}"
        )
    if tuple(summary.verified_artifacts) != tuple(
        conn.verified_artifact for conn in acceptance.connections if conn.accepted
    ):
        coherence_problems.append(
            "the acceptance summary's verified-artifact list disagrees with the connections"
        )
    if coherence_problems:
        checks.append(_check(
            CHECK_MANIFEST_COHERENCE, CHECK_FAILED,
            "the acceptance's own projections disagree: " + "; ".join(coherence_problems) + ".",
        ))
    else:
        checks.append(_check(
            CHECK_MANIFEST_COHERENCE, CHECK_PASSED,
            "the package projection agrees with the acceptance's recorded lists and summary "
            "(accepted/unresolved/failed packages and artifact counts)",
        ))

    unresolved: list[str] = []
    for conn in acceptance.connections:
        in_package = conn.package_id in {item.package_id for item in items}
        if conn.accepted and not in_package:
            unresolved.append(f"accepted {conn.package_id} missing from the package")
        if not conn.accepted and in_package:
            unresolved.append(f"unaccepted {conn.package_id} present in the package")
    if unresolved:
        checks.append(_check(
            CHECK_NO_UNRESOLVED_CONNECTION_IN_PACKAGE, CHECK_FAILED,
            "an unresolved or unaccepted connection appears in the drawing package: "
            + "; ".join(unresolved) + ".",
        ))
    else:
        excluded = len(acceptance.connections) - len(items)
        checks.append(_check(
            CHECK_NO_UNRESOLVED_CONNECTION_IN_PACKAGE, CHECK_PASSED,
            f"every packaged drawing corresponds to an accepted connection; "
            f"{excluded} connection(s) excluded from the package stay excluded",
        ))

    return tuple(checks)


# --------------------------------------------------------------------------------------
# Manifest projection and writing.
# --------------------------------------------------------------------------------------
def _manifest_projection(
    package: "FabricationDrawingPackage",
) -> tuple[tuple[str, object], ...]:
    """The deterministic ordered manifest projection — exactly the content that
    would be written to project-manifest.json. Fabricator-facing field names
    only; no internal workflow objects, no secrets."""
    drawings = []
    for item in package.items:
        drawings.append({
            "drawing_number": item.drawing_number,
            "connection_id": item.connection_id,
            "project_id": package.project_id,
            "filename": item.filename,
            "source_artifact": item.source_artifact,
            "source_filename": item.source_filename,
            "page_count": item.page_count,
            "verification_status": item.verification_status,
            "dispatch_output_status": item.dispatch_output_status,
            "artifact_sha256": item.artifact_sha256,
            "recorded_sha256": item.recorded_sha256,
            "observed": dict(item.observed),
            "checks": [
                {"code": check.code, "status": check.status, "detail": check.detail}
                for check in item.checks
            ],
            "audit": dict(item.audit),
        })
    return (
        ("package_schema", _MANIFEST_SCHEMA),
        ("scope_statement", PACKAGE_SCOPE_STATEMENT),
        ("project", {
            "project_id": package.project_id,
            "source_drawing_id": package.source_drawing_id,
            "workflow_revision": package.workflow_revision,
            "acceptance_status": package.acceptance_status,
            "package_status": package.status,
            "reason": package.reason,
        }),
        ("drawings", drawings),
        ("summary", {
            "total_accepted_connections": package.summary.total_accepted_connections,
            "total_drawing_artifacts": package.summary.total_drawing_artifacts,
            "verified_artifacts": package.summary.verified_artifacts,
            "missing_artifacts": package.summary.missing_artifacts,
            "failed_artifacts": package.summary.failed_artifacts,
            "package_issues": package.summary.package_issues,
            "final_package_status": package.summary.final_package_status,
        }),
        ("issues", [
            {"code": issue.code, "severity": issue.severity, "detail": issue.detail}
            for issue in package.issues
        ]),
    )


def _assemble(
    acceptance: ProductionJobAcceptance,
    *,
    output_dir: Path,
    caller_revision: int | None,
) -> FabricationDrawingPackage:
    """Evaluates one accepted job and, only when every check passes, writes the
    deterministic package (drawing copies + manifest) to output_dir. BLOCKED
    packages are never written — no partial package can be mistaken for a
    deliverable."""
    issues: list[FabricationPackageIssue] = []
    items: list[FabricationPackageItem] = []

    for index, conn in enumerate(acceptance.connections):
        if not conn.accepted:
            continue
        drawing_number = (
            f"{_PACKAGE_NUMBER_PREFIX}-{str(index + 1).zfill(_PACKAGE_NUMBER_WIDTH)}"
        )
        items.append(_evaluate_item(acceptance, conn, drawing_number))

    item_checks = [check for item in items for check in item.checks]
    project_checks = _project_checks(acceptance, tuple(items))
    all_checks = item_checks + list(project_checks)

    failed = [check for check in all_checks if check.status == CHECK_FAILED]
    for check in failed:
        issues.append(_issue(check.code, ISSUE_BLOCKER, check.detail))
    for check in all_checks:
        if check.status == CHECK_NOT_PRESENT_IN_ARTIFACT:
            issues.append(_issue(check.code, ISSUE_NOTE, check.detail))

    missing = sum(
        True for item in items
        if any(check.code == CHECK_SOURCE_ARTIFACT_PRESENT and check.status == CHECK_FAILED
               for check in item.checks)
    )
    failed_items = sum(
        True for item in items if any(check.status == CHECK_FAILED for check in item.checks)
    )
    verified_items = len(items) - failed_items
    status = PACKAGE_STATUS_READY if not failed else PACKAGE_STATUS_BLOCKED
    reason = (
        "every packaged drawing corresponds to its accepted connection's recorded, "
        "7AG-verified artifact, and every package-level check passed."
        if not failed else failed[0].detail
    )

    summary = FabricationPackageSummary(
        total_accepted_connections=len(items),
        total_drawing_artifacts=len(items),
        verified_artifacts=verified_items,
        missing_artifacts=missing,
        failed_artifacts=failed_items,
        package_issues=len(issues),
        final_package_status=status,
    )

    source_drawing_id = None
    for item in items:
        audit = dict(item.audit)
        evidence = audit.get("drawing_evidence")
        if isinstance(evidence, dict) and evidence.get("source_drawing_id") is not None:
            source_drawing_id = evidence["source_drawing_id"]
            break

    package = FabricationDrawingPackage(
        status=status,
        reason=reason,
        project_id=acceptance.project_id,
        source_drawing_id=source_drawing_id,
        workflow_revision=acceptance.revision,
        acceptance_status=acceptance.status,
        caller_revision=caller_revision,
        items=tuple(items),
        manifest_path=None,
        summary=summary,
        issues=tuple(issues),
        manifest=(),
    )
    package = FabricationDrawingPackage(
        status=package.status, reason=package.reason, project_id=package.project_id,
        source_drawing_id=package.source_drawing_id, workflow_revision=package.workflow_revision,
        acceptance_status=package.acceptance_status, caller_revision=package.caller_revision,
        items=package.items, manifest_path=package.manifest_path, summary=package.summary,
        issues=package.issues, manifest=_manifest_projection(package),
    )

    if status != PACKAGE_STATUS_READY:
        return package

    # ---- write the package: drawing copies first, manifest last. A write
    # ---- failure removes what this call wrote (best effort) and reports
    # ---- BLOCKED — a partial package is never left behind.
    written: list[Path] = []
    try:
        drawings_dir = output_dir / _DRAWINGS_SUBDIR
        drawings_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / _MANIFEST_FILENAME
        for item in package.items:
            # The exact bytes that were hashed and inspected are written —
            # the packaged copy can never diverge from the evaluated artifact
            # (READY guarantees source_bytes is present for every item).
            target = drawings_dir / item.filename
            target.write_bytes(item.source_bytes)
            written.append(target)
        manifest_path.write_text(
            json.dumps(dict(package.manifest), indent=len("  "), sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
        try:
            (output_dir / _DRAWINGS_SUBDIR).rmdir()
        except OSError:
            pass
        write_issue = _issue(
            "PACKAGE_WRITE_FAILED", ISSUE_BLOCKER,
            f"the package could not be written ({type(error).__name__}: {error}); files "
            "written by this call were removed — no partial package remains.",
        )
        blocked_summary = FabricationPackageSummary(
            total_accepted_connections=summary.total_accepted_connections,
            total_drawing_artifacts=summary.total_drawing_artifacts,
            verified_artifacts=summary.verified_artifacts,
            missing_artifacts=summary.missing_artifacts,
            failed_artifacts=summary.failed_artifacts,
            package_issues=len(package.issues) + 1,
            final_package_status=PACKAGE_STATUS_BLOCKED,
        )
        return FabricationDrawingPackage(
            status=PACKAGE_STATUS_BLOCKED,
            reason=write_issue.detail,
            project_id=package.project_id,
            source_drawing_id=package.source_drawing_id,
            workflow_revision=package.workflow_revision,
            acceptance_status=package.acceptance_status,
            caller_revision=package.caller_revision,
            items=package.items,
            manifest_path=None,
            summary=blocked_summary,
            issues=package.issues + (write_issue,),
            manifest=package.manifest,
        )

    return FabricationDrawingPackage(
        status=package.status, reason=package.reason, project_id=package.project_id,
        source_drawing_id=package.source_drawing_id, workflow_revision=package.workflow_revision,
        acceptance_status=package.acceptance_status, caller_revision=package.caller_revision,
        items=package.items, manifest_path=str(manifest_path), summary=summary,
        issues=package.issues, manifest=package.manifest,
    )


def _refused(
    acceptance: ProductionJobAcceptance,
    reason: str,
    caller_revision: int | None,
) -> FabricationDrawingPackage:
    return FabricationDrawingPackage(
        status=PACKAGE_STATUS_REFUSED,
        reason=reason,
        project_id=acceptance.project_id,
        source_drawing_id=None,
        workflow_revision=acceptance.revision,
        acceptance_status=acceptance.status,
        caller_revision=caller_revision,
        items=(),
        manifest_path=None,
        summary=FabricationPackageSummary(
            total_accepted_connections=0, total_drawing_artifacts=0,
            verified_artifacts=0, missing_artifacts=0, failed_artifacts=0,
            package_issues=0, final_package_status=PACKAGE_STATUS_REFUSED,
        ),
        issues=(),
        manifest=(),
    )


def _blocked(
    acceptance: ProductionJobAcceptance,
    reason: str,
    caller_revision: int | None,
) -> FabricationDrawingPackage:
    issue = _issue("ACCEPTANCE_NOT_ACCEPTED", ISSUE_BLOCKER, reason)
    package = FabricationDrawingPackage(
        status=PACKAGE_STATUS_BLOCKED,
        reason=reason,
        project_id=acceptance.project_id,
        source_drawing_id=None,
        workflow_revision=acceptance.revision,
        acceptance_status=acceptance.status,
        caller_revision=caller_revision,
        items=(),
        manifest_path=None,
        summary=FabricationPackageSummary(
            total_accepted_connections=0, total_drawing_artifacts=0,
            verified_artifacts=0, missing_artifacts=0, failed_artifacts=0,
            package_issues=1, final_package_status=PACKAGE_STATUS_BLOCKED,
        ),
        issues=(issue,),
        manifest=(),
    )
    return FabricationDrawingPackage(
        status=package.status, reason=package.reason, project_id=package.project_id,
        source_drawing_id=package.source_drawing_id, workflow_revision=package.workflow_revision,
        acceptance_status=package.acceptance_status, caller_revision=package.caller_revision,
        items=package.items, manifest_path=package.manifest_path, summary=package.summary,
        issues=package.issues, manifest=_manifest_projection(package),
    )


# --------------------------------------------------------------------------------------
# The public API.
# --------------------------------------------------------------------------------------
def build_fabrication_package(
    acceptance,
    *,
    output_dir: str | Path,
    expected_revision: int | None = None,
) -> FabricationDrawingPackage:
    """
    Builds the deterministic project-level fabrication drawing package for an
    accepted production job (7AQ's frozen ProductionJobAcceptance — the only
    input; the package is never built from workflow internals, UI state, HTTP
    or a database).

      - READY: the acceptance is ACCEPTED and every package-level check
        passed — the recorded, 7AG-verified artifacts are copied verbatim to
        output_dir/drawings/STEELSPEC-00N.pdf (hashes recomputed over the
        packaged bytes) and the deterministic manifest is written to
        output_dir/project-manifest.json.
      - BLOCKED: the acceptance is not ACCEPTED, or any package-level check
        failed (missing/changed/substituted artifact, failed recorded
        verification, duplicate identity, incoherent projections, unresolved
        connection) — nothing is written.
      - REFUSED: `expected_revision` (when given) is not the acceptance's own
        revision — a stale caller never receives a package for state they did
        not see (7AQ's stale-request vocabulary, re-used) — or the acceptance
        itself was refused.

    Nothing is regenerated, re-verified, repaired or substituted; no
    engineering value is invented; no second source of truth is created. The
    package has no `status`, `approved` or `decision` parameter — package
    fields are projections of the acceptance record, never caller-editable.
    """
    if not isinstance(acceptance, ProductionJobAcceptance):
        raise TypeError(
            f"acceptance must be the genuine ProductionJobAcceptance (got "
            f"{type(acceptance).__name__}); a fabrication package is built from the "
            "accepted production job's recorded evidence, never anything else."
        )
    if not isinstance(output_dir, (str, Path)):
        raise TypeError("output_dir must be a path-like (str or Path).")
    if expected_revision is not None and (
            not isinstance(expected_revision, int) or isinstance(expected_revision, bool)):
        raise TypeError("expected_revision must be an int or None.")
    if acceptance.status not in (
        ACCEPTANCE_STATUS_ACCEPTED,
        ACCEPTANCE_STATUS_NOT_ACCEPTED,
        ACCEPTANCE_STATUS_REFUSED,
    ):
        raise ValueError(
            f"unrecognized acceptance status {acceptance.status!r}; the package consumes "
            "only genuine 7AQ acceptances."
        )

    output_dir = Path(output_dir)

    if expected_revision is not None and expected_revision != acceptance.revision:
        return _refused(
            acceptance,
            f"the acceptance was produced at revision {acceptance.revision} but the caller "
            f"holds revision {expected_revision}; a stale package request is never satisfied "
            "by state the caller did not see.",
            expected_revision,
        )
    if acceptance.status == ACCEPTANCE_STATUS_REFUSED:
        return _refused(
            acceptance,
            f"the acceptance itself was refused ({acceptance.reason}); nothing can be "
            "packaged from it.",
            expected_revision,
        )
    if acceptance.status == ACCEPTANCE_STATUS_NOT_ACCEPTED:
        return _blocked(
            acceptance,
            f"the production job was NOT_ACCEPTED ({acceptance.reason}); no fabrication "
            "drawing package is produced for an unaccepted job.",
            expected_revision,
        )

    return _assemble(acceptance, output_dir=output_dir, caller_revision=expected_revision)
