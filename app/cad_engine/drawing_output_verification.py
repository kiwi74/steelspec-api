"""
Milestone 7AG — REAL OUTPUT VERIFICATION: an independent, read-only
inspection of the ACTUAL generated fabrication drawing artifact that
7AF's dispatch recorded.

    7AF answered "the generator produced a file";
    7AG answers "the produced file is a real, readable drawing
    artifact and its recorded content/metadata corresponds to the
    authorized output".

    DrawingDispatchResult (7AF manifest, never re-executed)
            |
    verify_drawing_artifact()           (per connection)
            |   BLOCKED_REVIEW / BLOCKED_CONFIRMATION / GENERATION_FAILED
            |       -> NO_ARTIFACT (nothing parsed, nothing touched)
            |   GENERATED -> the recorded artifact is located, hashed,
            |       parsed with the real PDF parser (pypdf), and its
            |       observable content is compared with the genuine
            |       reviewed assembly's own plain-data fields
            v
    ArtifactVerificationResult (frozen; VERIFIED / FAILED /
                                NOT_VERIFIABLE / NO_ARTIFACT)
            |
    verify_project_drawing_outputs()    (per project, every 7AF
            v                            outcome preserved)
    ProjectArtifactVerificationResult (frozen; counts derived)

SCOPE — ARTIFACT INTEGRITY AND OBSERVABLE CORRESPONDENCE ONLY:

  - This is NOT an engineering sign-off. A valid PDF is not an
    engineering-correct drawing; a PDF containing dimension text is
    not structurally adequate steelwork; a PDF that opens is not
    fabrication-ready. 7AG verifies that the produced artifact is a
    real, readable drawing whose observable, deterministic fields
    correspond to the authorized assembly. Nothing more.
  - 7AG never re-runs 7AA/7Z/7AE, never re-decides permission and
    never calls the drawing generator again. The only input is the
    genuine 7AF DrawingDispatchResult (plus, for correspondence, the
    genuine reviewed assembly it was generated from).
  - 7AG never generates, regenerates, repairs, rewrites, annotates or
    substitutes anything. If an artifact is missing, empty, corrupt,
    truncated or content-less, verification FAILS and the failed check
    says why. There is NO fallback artifact, ever.
  - The SHA-256 hash is a deterministic ARTIFACT IDENTITY — it proves
    which bytes were verified and detects any later mutation. It is
    not an engineering verification mechanism and is never described
    as one.

WHAT THE CURRENT ARTIFACT GENUINELY CONTAINS (established by
inspection of app/drawing_generator/pdf_builder.py and the real
generated PDF, not assumed):

  The reviewed-connection PDF's extractable text layer carries these
  deterministic, assembly-derived fields:
    - "FAB-{connection.connection_id}"                    (drawing number)
    - "CONNECTION DETAIL — {connection_geometry.connection_id}" (detail title)
    - "MEMBER A {mark} / SECTION {section_name} / LENGTH {length_mm:g} mm"
    - "MEMBER B {mark} / SECTION {section_name} / LENGTH {length_mm:g} mm"
      (the two-member title block rows) — and for a multi-member
      assembly (7AV), one such row PER MEMBER, labelled
      "MEMBER A".."MEMBER Z" in the assembly's own member order, the
      exact labels member_label() assigns
    - "Ø{diameter:g}" for every generated hole diameter
      (the HOLES summary line, built from the genuine generated holes)
  and its content stream carries vector drawing operators (m/l/c/re
  path construction plus S/B/f painting) for the borders, elevation,
  detail and dimensions.

  The plate/pattern/bolts lines and the elevation dimension are
  DERIVED inside the generator from bounding-box measurements of the
  genuine CAD solids. 7AG deliberately re-derives NONE of those
  generator-internal geometry computations: it only re-formats plain
  data fields (ids, marks, section names, lengths, hole diameters)
  with the exact formatting the generator already applies. Fields the
  artifact does not represent are reported
  NOT_VERIFIABLE_FROM_ARTIFACT — never invented, never passed by
  assumption.

  Status/revision/date/material are presentation-only title-block
  kwargs of the dispatch, not assembly-derived identity, so they are
  not part of correspondence verification.
"""
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.cad_engine.automation_pipeline import ReferenceDataIdentity
from app.cad_engine.drawing_dispatch import (
    OUTPUT_STATUS_BLOCKED_CONFIRMATION,
    OUTPUT_STATUS_BLOCKED_REVIEW,
    OUTPUT_STATUS_GENERATED,
    OUTPUT_STATUS_GENERATION_FAILED,
    DrawingDispatchResult,
    ProjectDrawingDispatchResult,
)
from app.cad_engine.multi_member_connection import (
    ReviewedMultiMemberConnectionAssembly,
    member_label,
)
from app.cad_engine.reviewed_connection_assembly import ReviewedTwoMemberConnectionAssembly

__all__ = [
    "VERIFICATION_STATUS_VERIFIED", "VERIFICATION_STATUS_FAILED",
    "VERIFICATION_STATUS_NOT_VERIFIABLE", "VERIFICATION_STATUS_NO_ARTIFACT",
    "VERIFICATION_STATUSES",
    "CHECK_PASSED", "CHECK_FAILED", "CHECK_NOT_VERIFIABLE_FROM_ARTIFACT", "CHECK_STATUSES",
    "CHECK_FILE_EXISTS", "CHECK_FILE_REGULAR", "CHECK_FILE_NONEMPTY", "CHECK_ARTIFACT_HASH",
    "CHECK_FORMAT_READABLE", "CHECK_FORMAT_STRUCTURE_VALID", "CHECK_PAGE_COUNT_VALID",
    "CHECK_DRAWING_CONTENT_PRESENT", "CHECK_IDENTITY_VERIFIABLE",
    "CHECK_GEOMETRY_FIELDS_VERIFIABLE", "VERIFICATION_CHECK_CODES",
    "ARTIFACT_VERIFICATION_SCOPE_STATEMENT", "PROJECT_VERIFICATION_SCOPE_STATEMENT",
    "ArtifactCheck", "ArtifactVerificationResult",
    "ProjectConnectionArtifactVerificationOutcome", "ProjectArtifactVerificationResult",
    "verify_drawing_artifact", "verify_project_drawing_outputs",
]

# Verification outcomes. NO_ARTIFACT: 7AF recorded no generated output
# (blocked or failed generation) — there is nothing to verify and
# nothing was parsed. NOT_VERIFIABLE: the artifact is a real readable
# drawing, but correspondence to the authorized assembly could not be
# established from the evidence available (never reported as VERIFIED).
VERIFICATION_STATUS_VERIFIED = "VERIFIED"
VERIFICATION_STATUS_FAILED = "FAILED"
VERIFICATION_STATUS_NOT_VERIFIABLE = "NOT_VERIFIABLE"
VERIFICATION_STATUS_NO_ARTIFACT = "NO_ARTIFACT"
VERIFICATION_STATUSES = (
    VERIFICATION_STATUS_VERIFIED,
    VERIFICATION_STATUS_FAILED,
    VERIFICATION_STATUS_NOT_VERIFIABLE,
    VERIFICATION_STATUS_NO_ARTIFACT,
)

# Per-check statuses. NOT_VERIFIABLE_FROM_ARTIFACT means the artifact
# (or the manifest) does not expose the information the check needs —
# the check is reported honestly as unevaluable, never passed.
CHECK_PASSED = "PASSED"
CHECK_FAILED = "FAILED"
CHECK_NOT_VERIFIABLE_FROM_ARTIFACT = "NOT_VERIFIABLE_FROM_ARTIFACT"
CHECK_STATUSES = (CHECK_PASSED, CHECK_FAILED, CHECK_NOT_VERIFIABLE_FROM_ARTIFACT)

# Individual checks, each with a deterministic identity and an
# explanation of exactly what it observed (or why it could not).
CHECK_FILE_EXISTS = "FILE_EXISTS"
CHECK_FILE_REGULAR = "FILE_REGULAR"
CHECK_FILE_NONEMPTY = "FILE_NONEMPTY"
CHECK_ARTIFACT_HASH = "ARTIFACT_HASH"
CHECK_FORMAT_READABLE = "FORMAT_READABLE"
CHECK_FORMAT_STRUCTURE_VALID = "FORMAT_STRUCTURE_VALID"
CHECK_PAGE_COUNT_VALID = "PAGE_COUNT_VALID"
CHECK_DRAWING_CONTENT_PRESENT = "DRAWING_CONTENT_PRESENT"
CHECK_IDENTITY_VERIFIABLE = "IDENTITY_VERIFIABLE"
CHECK_GEOMETRY_FIELDS_VERIFIABLE = "GEOMETRY_FIELDS_VERIFIABLE"
# In the exact execution order of verify_drawing_artifact(): the hash is
# computed for every located artifact (even an empty one), then the
# remaining checks stop at the first failure.
VERIFICATION_CHECK_CODES = (
    CHECK_FILE_EXISTS,
    CHECK_FILE_REGULAR,
    CHECK_ARTIFACT_HASH,
    CHECK_FILE_NONEMPTY,
    CHECK_FORMAT_READABLE,
    CHECK_FORMAT_STRUCTURE_VALID,
    CHECK_PAGE_COUNT_VALID,
    CHECK_DRAWING_CONTENT_PRESENT,
    CHECK_IDENTITY_VERIFIABLE,
    CHECK_GEOMETRY_FIELDS_VERIFIABLE,
)

ARTIFACT_VERIFICATION_SCOPE_STATEMENT = (
    "This result inspects the actual artifact 7AF's dispatch recorded: it verifies artifact "
    "integrity and observable correspondence with the authorized reviewed assembly. It is NOT "
    "an engineering sign-off — a valid readable PDF is not fabrication-ready steelwork."
)
PROJECT_VERIFICATION_SCOPE_STATEMENT = (
    "This manifest verifies every artifact 7AF's dispatch recorded, one outcome per connection. "
    "It never claims the project is verified or fabrication-ready; a single failed artifact "
    "fails the corresponding connection, and blocked connections stay visible."
)

# The 7AF dispatch naming rule, restated here so the recorded filename
# can be checked against the manifest's connection identity.
_FABRICATION_SUFFIX = "-fabrication"

# Reading a file in fixed chunks for the SHA-256 digest; the %%EOF
# trailer marker must sit at the very end of the bytes (trailing
# whitespace tolerated) for the PDF not to count as truncated.
_HASH_READ_CHUNK_BYTES = 65536
_TRAILING_BYTES_TO_IGNORE = b"\r\n \t\x00"

# Vector drawing evidence: the existing generator's content stream
# contains path-construction operators (m/l/c/re) and a painting
# operator (S/B/f/s/b) for every drawn shape. A valid-but-blank page
# has neither.
_PATH_OPERATOR_PATTERN = re.compile(rb"\b(?:m|l|c|re)\b")
_PAINT_OPERATOR_PATTERN = re.compile(rb"\b(?:S|B|f|s|b)\b")


@dataclass(frozen=True)
class ArtifactCheck:
    """
    One deterministic verification observation. Every FAILED check
    explains what was wrong; every NOT_VERIFIABLE_FROM_ARTIFACT check
    explains which information the artifact or manifest does not
    expose.
    """
    code: str
    status: str
    detail: str


@dataclass(frozen=True)
class ArtifactVerificationResult:
    """
    The immutable verification manifest of one 7AF dispatch: the
    dispatch outcome carried verbatim, the verification status, and —
    when an artifact was actually located — its recorded path, format,
    size, SHA-256 identity and page count, plus every executed check.
    `failures` is derived from `checks`, never asserted.
    `reference_identity` is the genuine 7AF dispatch manifest's own
    reference-data identity, carried through unchanged (None exactly
    when the dispatch recorded none) — this stage preserves it and never
    recomputes it from anywhere else. It describes the REFERENCE DATA a
    run consulted; it is not part of artifact verification, and no
    verification outcome above depends on it.
    """
    connection_id: str | None
    dispatch_output_status: str
    verification_status: str
    artifact_path: Path | None
    artifact_format: str | None
    file_size_bytes: int | None
    sha256: str | None
    page_count: int | None
    checks: tuple[ArtifactCheck, ...]
    failures: tuple[ArtifactCheck, ...]
    summary: tuple[str, ...]
    reference_identity: ReferenceDataIdentity | None = None


@dataclass(frozen=True)
class ProjectConnectionArtifactVerificationOutcome:
    """
    One connection's verification outcome in the project's own
    submission order: the 7X identity plus its verification manifest.
    """
    review_package_id: str
    submission_index: int
    source_identity: str | None
    verification_result: ArtifactVerificationResult


@dataclass(frozen=True)
class ProjectArtifactVerificationResult:
    """
    The immutable project verification manifest. Counts are DERIVED
    from the connection outcomes, never asserted; every 7AF outcome is
    preserved, and the project is never described as verified.
    """
    project_id: str | None
    connections_total: int
    generated_count: int
    verified_count: int
    failed_count: int
    not_verifiable_count: int
    blocked_count: int
    connection_results: tuple[ProjectConnectionArtifactVerificationOutcome, ...]
    summary: tuple[str, ...]


def _check(code: str, status: str, detail: str) -> ArtifactCheck:
    return ArtifactCheck(code=code, status=status, detail=detail)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _no_artifact_result(dispatch_result: DrawingDispatchResult, explanation: str) -> ArtifactVerificationResult:
    return ArtifactVerificationResult(
        connection_id=dispatch_result.connection_id,
        dispatch_output_status=dispatch_result.output_status,
        verification_status=VERIFICATION_STATUS_NO_ARTIFACT,
        artifact_path=None,
        artifact_format=None,
        file_size_bytes=None,
        sha256=None,
        page_count=None,
        checks=(),
        failures=(),
        summary=(
            ARTIFACT_VERIFICATION_SCOPE_STATEMENT,
            f"verification_status = {VERIFICATION_STATUS_NO_ARTIFACT}",
            f"dispatch_output_status = {dispatch_result.output_status}",
            explanation,
        ),
        reference_identity=dispatch_result.reference_identity,
    )


def _extract_pdf_text(reader) -> str:
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _vector_drawing_present(reader) -> bool:
    for page in reader.pages:
        contents = page.get_contents()
        if contents is None:
            continue
        data = contents.get_data()
        if _PATH_OPERATOR_PATTERN.search(data) and _PAINT_OPERATOR_PATTERN.search(data):
            return True
    return False


def _expected_geometry_strings(
    assembly: ReviewedTwoMemberConnectionAssembly | ReviewedMultiMemberConnectionAssembly,
) -> tuple[str, ...]:
    """
    The exact deterministic strings the existing generator embeds in
    the artifact, reformatted from the assembly's own plain data
    fields with the generator's own formatting — never from any
    generator-internal geometry computation, and never invented. For
    a multi-member assembly (7AV) this is the same field set PER
    MEMBER, with the per-member labels ("MEMBER A", "MEMBER B", ...)
    the multi-member title block draws, in the assembly's own member
    order — a multi-member artifact must carry EVERY member's row.
    """
    if isinstance(assembly, ReviewedMultiMemberConnectionAssembly):
        strings = [
            f"FAB-{assembly.connection.connection_id}",
            f"CONNECTION DETAIL — {assembly.connection_geometry.connection_id}",
        ]
        for index, member in enumerate(assembly.members):
            geometry = member.geometry
            strings.extend([
                f"{member_label(index)} {geometry.mark}",
                f"SECTION {geometry.section_name}",
                f"LENGTH {geometry.length_mm:g} mm",
            ])
        for diameter in sorted({hole.diameter for hole in assembly.connection_geometry.holes}):
            strings.append(f"Ø{diameter:g}")
        return tuple(strings)

    member_a = assembly.member_a.geometry
    member_b = assembly.member_b.geometry
    strings = [
        f"FAB-{assembly.connection.connection_id}",
        f"CONNECTION DETAIL — {assembly.connection_geometry.connection_id}",
        f"MEMBER A {member_a.mark}",
        f"SECTION {member_a.section_name}",
        f"LENGTH {member_a.length_mm:g} mm",
        f"MEMBER B {member_b.mark}",
        f"SECTION {member_b.section_name}",
        f"LENGTH {member_b.length_mm:g} mm",
    ]
    for diameter in sorted({hole.diameter for hole in assembly.connection_geometry.holes}):
        strings.append(f"Ø{diameter:g}")
    return tuple(strings)


def _artifact_result(
    dispatch_result: DrawingDispatchResult,
    verification_status: str,
    path: Path | None,
    file_size: int | None,
    sha256: str | None,
    page_count: int | None,
    checks: tuple[ArtifactCheck, ...],
    recorded_file_count: int,
) -> ArtifactVerificationResult:
    summary_lines = [
        ARTIFACT_VERIFICATION_SCOPE_STATEMENT,
        f"verification_status = {verification_status}",
        f"dispatch_output_status = {dispatch_result.output_status}",
    ]
    if path is None:
        summary_lines.append("artifact = none")
    else:
        parts = [f"artifact = {path}"]
        if file_size is not None:
            parts.append(f"{file_size} bytes")
        if sha256 is not None:
            parts.append(f"sha256 = {sha256}")
        if page_count is not None:
            parts.append(f"{page_count} page(s)")
        summary_lines.append(", ".join(parts))
    if recorded_file_count > 1:
        summary_lines.append(
            f"note: the manifest records {recorded_file_count} generated files; verification "
            "inspected the first recorded artifact"
        )
    return ArtifactVerificationResult(
        connection_id=dispatch_result.connection_id,
        dispatch_output_status=dispatch_result.output_status,
        verification_status=verification_status,
        artifact_path=path,
        artifact_format=path.suffix.lstrip(".") if path is not None else None,
        file_size_bytes=file_size,
        sha256=sha256,
        page_count=page_count,
        checks=checks,
        failures=tuple(check for check in checks if check.status == CHECK_FAILED),
        summary=tuple(summary_lines),
        reference_identity=dispatch_result.reference_identity,
    )


def verify_drawing_artifact(
    dispatch_result: DrawingDispatchResult,
    *,
    assembly: ReviewedTwoMemberConnectionAssembly | ReviewedMultiMemberConnectionAssembly | None = None,
) -> ArtifactVerificationResult:
    """
    Independently inspects the actual artifact recorded by the genuine
    7AF DrawingDispatchResult — never by re-dispatching, never by
    calling the drawing generator, never by re-running any gate.

      - BLOCKED_REVIEW / BLOCKED_CONFIRMATION -> NO_ARTIFACT: the
        generator was never invoked by 7AF, so nothing is located,
        opened or parsed.
      - GENERATION_FAILED -> NO_ARTIFACT: a failed generation is never
        reinterpreted as a produced artifact.
      - GENERATED -> the first recorded file is verified by executing,
        in order, the deterministic checks FILE_EXISTS, FILE_REGULAR,
        ARTIFACT_HASH, FILE_NONEMPTY, FORMAT_READABLE (real parser),
        FORMAT_STRUCTURE_VALID, PAGE_COUNT_VALID,
        DRAWING_CONTENT_PRESENT, IDENTITY_VERIFIABLE and
        GEOMETRY_FIELDS_VERIFIABLE, stopping at the first failure.
        Any FAILED check -> FAILED; otherwise any
        NOT_VERIFIABLE_FROM_ARTIFACT check -> NOT_VERIFIABLE; otherwise
        VERIFIED.

    `assembly` is the genuine reviewed assembly the artifact was
    generated from (the pipeline's reviewed_assembly). Without it the
    artifact-to-assembly correspondence check reports
    NOT_VERIFIABLE_FROM_ARTIFACT — correspondence is never asserted on
    faith.

    Read-only and deterministic: the artifact is hashed and parsed,
    never opened for writing, never repaired, never replaced.
    """
    if not isinstance(dispatch_result, DrawingDispatchResult):
        raise TypeError(
            f"dispatch_result must be the genuine DrawingDispatchResult (got "
            f"{type(dispatch_result).__name__}); verification inspects what 7AF actually "
            "recorded, never anything else."
        )
    if assembly is not None and not isinstance(
        assembly, (ReviewedTwoMemberConnectionAssembly, ReviewedMultiMemberConnectionAssembly)
    ):
        raise TypeError(
            f"assembly must be a ReviewedTwoMemberConnectionAssembly or a "
            f"ReviewedMultiMemberConnectionAssembly (got {type(assembly).__name__}); "
            "correspondence is verified against the genuine reviewed assembly only."
        )

    status = dispatch_result.output_status
    if status == OUTPUT_STATUS_BLOCKED_REVIEW:
        return _no_artifact_result(
            dispatch_result,
            "7AF recorded BLOCKED_REVIEW: the drawing generator was never invoked, no artifact "
            "exists and nothing was parsed.",
        )
    if status == OUTPUT_STATUS_BLOCKED_CONFIRMATION:
        return _no_artifact_result(
            dispatch_result,
            "7AF recorded BLOCKED_CONFIRMATION: the drawing generator was never invoked, no "
            "artifact exists and nothing was parsed.",
        )
    if status == OUTPUT_STATUS_GENERATION_FAILED:
        error = dispatch_result.generation_error or "no error detail recorded"
        return _no_artifact_result(
            dispatch_result,
            f"7AF recorded GENERATION_FAILED ({error}); a failed generation is never "
            "reinterpreted as a produced artifact.",
        )
    if status != OUTPUT_STATUS_GENERATED:
        raise ValueError(
            f"unrecognized 7AF output_status {status!r}; verification understands only the "
            "genuine 7AF statuses."
        )

    files = dispatch_result.generated_files
    if not files:
        checks = (
            _check(
                CHECK_FILE_EXISTS,
                CHECK_FAILED,
                "the 7AF manifest records GENERATED but no artifact file; there is nothing to "
                "verify",
            ),
        )
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, None, None, None, None, checks, 0,
        )

    path = files[0]
    checks: list[ArtifactCheck] = []
    file_size: int | None = None
    sha256: str | None = None
    page_count: int | None = None

    if not path.exists():
        checks.append(_check(
            CHECK_FILE_EXISTS, CHECK_FAILED,
            f"the recorded artifact {path} does not exist; 7AG never hunts for a substitute "
            "file and never regenerates.",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, 0, None, None, tuple(checks),
            len(files),
        )
    checks.append(_check(CHECK_FILE_EXISTS, CHECK_PASSED, f"{path} exists"))

    if not path.is_file():
        checks.append(_check(
            CHECK_FILE_REGULAR, CHECK_FAILED,
            f"{path} is not a regular file; nothing was opened.",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, 0, None, None, tuple(checks),
            len(files),
        )
    checks.append(_check(CHECK_FILE_REGULAR, CHECK_PASSED, f"{path} is a regular file"))

    try:
        sha256 = _sha256_of(path)
    except OSError as error:
        checks.append(_check(
            CHECK_ARTIFACT_HASH, CHECK_FAILED,
            f"the artifact could not be read for hashing: {type(error).__name__}: {error}",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, 0, None, None, tuple(checks),
            len(files),
        )
    checks.append(_check(
        CHECK_ARTIFACT_HASH, CHECK_PASSED,
        f"sha256 = {sha256} (a deterministic artifact identity, not an engineering "
        "verification)",
    ))

    try:
        file_size = path.stat().st_size
    except OSError as error:
        checks.append(_check(
            CHECK_FILE_NONEMPTY, CHECK_FAILED,
            f"the artifact's size could not be read: {type(error).__name__}: {error}",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, 0, sha256, None, tuple(checks),
            len(files),
        )
    if file_size <= 0:
        checks.append(_check(
            CHECK_FILE_NONEMPTY, CHECK_FAILED,
            "the artifact is empty (0 bytes); an empty file is not a drawing.",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, 0, sha256, None, tuple(checks),
            len(files),
        )
    checks.append(_check(CHECK_FILE_NONEMPTY, CHECK_PASSED, f"{file_size} bytes"))

    try:
        reader = PdfReader(path)
    except Exception as error:  # noqa: BLE001 — every parse failure is recorded, never repaired
        checks.append(_check(
            CHECK_FORMAT_READABLE, CHECK_FAILED,
            f"the real PDF parser could not open the artifact ({type(error).__name__}: "
            f"{error}); the file is not a readable PDF and was NOT repaired or regenerated.",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, file_size, sha256, None,
            tuple(checks), len(files),
        )
    if reader.is_encrypted:
        checks.append(_check(
            CHECK_FORMAT_READABLE, CHECK_FAILED,
            "the artifact is encrypted; its content cannot be read and it cannot be verified.",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, file_size, sha256, None,
            tuple(checks), len(files),
        )
    checks.append(_check(CHECK_FORMAT_READABLE, CHECK_PASSED, "opened by the real PDF parser"))

    data = path.read_bytes()
    header_ok = data.startswith(b"%PDF-")
    trailer_ok = data.rstrip(_TRAILING_BYTES_TO_IGNORE).endswith(b"%%EOF")
    if not header_ok and not trailer_ok:
        checks.append(_check(
            CHECK_FORMAT_STRUCTURE_VALID, CHECK_FAILED,
            "the artifact has no PDF header and no %%EOF trailer marker; the PDF appears "
            "truncated or structurally incomplete.",
        ))
    elif not header_ok:
        checks.append(_check(
            CHECK_FORMAT_STRUCTURE_VALID, CHECK_FAILED,
            "the artifact does not start with a PDF header; the PDF appears structurally "
            "incomplete.",
        ))
    elif not trailer_ok:
        checks.append(_check(
            CHECK_FORMAT_STRUCTURE_VALID, CHECK_FAILED,
            "the artifact has no %%EOF trailer marker at the end of the bytes; the PDF "
            "appears truncated.",
        ))
    else:
        checks.append(_check(
            CHECK_FORMAT_STRUCTURE_VALID, CHECK_PASSED,
            "PDF header present and %%EOF trailer marker present at the end of the bytes",
        ))

    if checks[-1].status == CHECK_FAILED:
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, file_size, sha256, None,
            tuple(checks), len(files),
        )

    try:
        page_count = len(reader.pages)
    except Exception as error:  # noqa: BLE001
        checks.append(_check(
            CHECK_PAGE_COUNT_VALID, CHECK_FAILED,
            f"the page count could not be read ({type(error).__name__}: {error}).",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, file_size, sha256, None,
            tuple(checks), len(files),
        )
    if page_count <= 0:
        checks.append(_check(
            CHECK_PAGE_COUNT_VALID, CHECK_FAILED,
            "the PDF contains zero pages; a drawing with no pages has no content.",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, file_size, sha256, None,
            tuple(checks), len(files),
        )
    checks.append(_check(CHECK_PAGE_COUNT_VALID, CHECK_PASSED, f"{page_count} page(s)"))

    try:
        text = _extract_pdf_text(reader)
    except Exception as error:  # noqa: BLE001
        checks.append(_check(
            CHECK_DRAWING_CONTENT_PRESENT, CHECK_FAILED,
            f"text could not be extracted from the artifact ({type(error).__name__}: {error}).",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, file_size, sha256, page_count,
            tuple(checks), len(files),
        )
    try:
        vector_present = _vector_drawing_present(reader)
    except Exception as error:  # noqa: BLE001
        checks.append(_check(
            CHECK_DRAWING_CONTENT_PRESENT, CHECK_FAILED,
            f"the content stream could not be inspected for vector linework "
            f"({type(error).__name__}: {error}).",
        ))
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, file_size, sha256, page_count,
            tuple(checks), len(files),
        )

    text_present = bool(text.strip())
    if text_present and vector_present:
        checks.append(_check(
            CHECK_DRAWING_CONTENT_PRESENT, CHECK_PASSED,
            "extractable text present and vector drawing operators present in the content "
            "stream — the artifact is a drawing, not a blank page",
        ))
    elif text_present:
        checks.append(_check(
            CHECK_DRAWING_CONTENT_PRESENT, CHECK_FAILED,
            "the artifact has extractable text but NO vector drawing operators in its content "
            "stream; the existing generator's output always carries vector linework, so this "
            "is not a drawing the generator produced.",
        ))
    elif vector_present:
        checks.append(_check(
            CHECK_DRAWING_CONTENT_PRESENT, CHECK_FAILED,
            "the artifact has vector linework but NO extractable text; the existing "
            "generator's output always carries its title-block and dimension text.",
        ))
    else:
        checks.append(_check(
            CHECK_DRAWING_CONTENT_PRESENT, CHECK_FAILED,
            "the artifact has neither extractable text nor vector drawing operators: it is a "
            "technically valid blank page, not a drawing.",
        ))

    if checks[-1].status == CHECK_FAILED:
        return _artifact_result(
            dispatch_result, VERIFICATION_STATUS_FAILED, path, file_size, sha256, page_count,
            tuple(checks), len(files),
        )

    if dispatch_result.connection_id is None:
        checks.append(_check(
            CHECK_IDENTITY_VERIFIABLE, CHECK_NOT_VERIFIABLE_FROM_ARTIFACT,
            "the 7AF manifest carries no connection identity, so the recorded filename can be "
            "checked against nothing; identity is NOT asserted.",
        ))
    else:
        expected_stem = f"{dispatch_result.connection_id}{_FABRICATION_SUFFIX}"
        if path.stem == expected_stem:
            checks.append(_check(
                CHECK_IDENTITY_VERIFIABLE, CHECK_PASSED,
                f"the recorded filename {path.name} carries the manifest's connection identity "
                f"{dispatch_result.connection_id}",
            ))
        else:
            checks.append(_check(
                CHECK_IDENTITY_VERIFIABLE, CHECK_FAILED,
                f"the recorded artifact {path.name} does not carry the manifest's connection "
                f"identity {dispatch_result.connection_id} (expected stem "
                f"{expected_stem!r}).",
            ))

    if assembly is None:
        checks.append(_check(
            CHECK_GEOMETRY_FIELDS_VERIFIABLE, CHECK_NOT_VERIFIABLE_FROM_ARTIFACT,
            "no authorized reviewed assembly was supplied for comparison; "
            "artifact-to-assembly correspondence was not evaluated and is NOT asserted.",
        ))
    else:
        expected = _expected_geometry_strings(assembly)
        normalized = " ".join(text.split())
        missing = [string for string in expected if string not in normalized]
        if missing:
            checks.append(_check(
                CHECK_GEOMETRY_FIELDS_VERIFIABLE, CHECK_FAILED,
                f"the artifact text does not contain the assembly's deterministic fields: "
                f"{missing}",
            ))
        else:
            checks.append(_check(
                CHECK_GEOMETRY_FIELDS_VERIFIABLE, CHECK_PASSED,
                f"all {len(expected)} assembly-derived fields are present in the artifact text "
                f"(connection ids, member marks, section names, lengths, hole diameters)",
            ))

    statuses = {check.status for check in checks}
    if CHECK_FAILED in statuses:
        verification_status = VERIFICATION_STATUS_FAILED
    elif CHECK_NOT_VERIFIABLE_FROM_ARTIFACT in statuses:
        verification_status = VERIFICATION_STATUS_NOT_VERIFIABLE
    else:
        verification_status = VERIFICATION_STATUS_VERIFIED

    return _artifact_result(
        dispatch_result, verification_status, path, file_size, sha256, page_count,
        tuple(checks), len(files),
    )


def verify_project_drawing_outputs(
    project_dispatch_result: ProjectDrawingDispatchResult,
    assemblies: Mapping[
        str, ReviewedTwoMemberConnectionAssembly | ReviewedMultiMemberConnectionAssembly
    ] | None = None,
) -> ProjectArtifactVerificationResult:
    """
    Verifies every artifact the genuine 7AF project dispatch recorded,
    one verification per connection outcome, in the project's own
    submission order — blocked and failed connections stay visible as
    NO_ARTIFACT outcomes and are never collapsed, omitted or counted
    as verified. Counts are derived from the outcomes, never
    asserted; the project is never described as verified.

    `assemblies` maps review_package_id to the genuine reviewed
    assembly that connection's artifact was generated from. Connections
    without an entry are verified for integrity only, with
    correspondence reported NOT_VERIFIABLE_FROM_ARTIFACT.
    """
    if not isinstance(project_dispatch_result, ProjectDrawingDispatchResult):
        raise TypeError(
            f"project_dispatch_result must be the genuine ProjectDrawingDispatchResult (got "
            f"{type(project_dispatch_result).__name__})."
        )
    if assemblies is not None and not isinstance(assemblies, Mapping):
        raise TypeError(
            f"assemblies must be a mapping of review_package_id -> "
            f"ReviewedTwoMemberConnectionAssembly or ReviewedMultiMemberConnectionAssembly "
            f"(got {type(assemblies).__name__})."
        )
    assembly_by_id = dict(assemblies) if assemblies is not None else {}

    outcomes = tuple(
        ProjectConnectionArtifactVerificationOutcome(
            review_package_id=outcome.review_package_id,
            submission_index=outcome.submission_index,
            source_identity=outcome.source_identity,
            verification_result=verify_drawing_artifact(
                outcome.dispatch_result,
                assembly=assembly_by_id.get(outcome.review_package_id),
            ),
        )
        for outcome in project_dispatch_result.connection_outputs
    )

    generated_count = len([
        o for o in outcomes
        if o.verification_result.dispatch_output_status == OUTPUT_STATUS_GENERATED
    ])
    verified_count = len([
        o for o in outcomes
        if o.verification_result.verification_status == VERIFICATION_STATUS_VERIFIED
    ])
    failed_count = len([
        o for o in outcomes
        if o.verification_result.verification_status == VERIFICATION_STATUS_FAILED
    ])
    not_verifiable_count = len([
        o for o in outcomes
        if o.verification_result.verification_status == VERIFICATION_STATUS_NOT_VERIFIABLE
    ])
    blocked_count = len([
        o for o in outcomes
        if o.verification_result.verification_status == VERIFICATION_STATUS_NO_ARTIFACT
    ])

    lines = [
        f"connections_total = {len(outcomes)}",
        f"generated_count = {generated_count}",
        f"verified_count = {verified_count}",
        f"failed_count = {failed_count}",
        f"not_verifiable_count = {not_verifiable_count}",
        f"blocked_count = {blocked_count}",
        PROJECT_VERIFICATION_SCOPE_STATEMENT,
    ]
    if failed_count:
        lines.append(
            f"the project is NOT fully verified: {failed_count} connection artifact(s) failed "
            "verification"
        )
    for outcome in outcomes:
        result = outcome.verification_result
        artifact = result.artifact_path.name if result.artifact_path is not None else "no artifact"
        lines.append(f"{outcome.review_package_id}: {result.verification_status} — {artifact}")

    return ProjectArtifactVerificationResult(
        project_id=project_dispatch_result.project_id,
        connections_total=len(outcomes),
        generated_count=generated_count,
        verified_count=verified_count,
        failed_count=failed_count,
        not_verifiable_count=not_verifiable_count,
        blocked_count=blocked_count,
        connection_results=outcomes,
        summary=tuple(lines),
    )
