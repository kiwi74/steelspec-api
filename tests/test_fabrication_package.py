"""7AR Fabrication Drawing Package tests — the production deliverable proof.

Every READY package exercised here is derived from the GENUINE 7AJ project
workflow (start_project_workflow -> resolve_project_connection, rev 0 -> 3)
running on the real captured Arkles extraction, accepted by the genuine 7AQ
accept_production_job(), and packaged by build_fabrication_package() — never
from a synthetic acceptance object, never hard-coded to a revision. The
negative and tamper cases mutate the real acceptance AFTER the fact and prove
the package layer still refuses: missing, changed, substituted, duplicated,
injected, or unresolved state never becomes a fabricator-facing deliverable.

Sections (per the 7AR brief):
  * type guards and request validity (REFUSED semantics: stale state is
    never packaged; an unaccepted job produces nothing)
  * the production proof: the real Arkles job -> READY package with the
    three verified drawings under deterministic drawing numbers, a
    deterministic manifest, and a backward audit trail per drawing
  * the artifact proof: byte copies (hashed again after copying), real
    PDF text (identity, title block, members, project/source rows),
    observed fields, no secrets
  * negative paths A-J: unaccepted, missing artifact, changed bytes,
    failed recorded verification, wrong-connection artifact, substituted
    artifact, duplicate identity, missing engineering value, unresolved
    connection, stale revision — every one BLOCKED/REFUSED with nothing
    written
  * tamper proof: garbage bytes, copied-under-new-name, injected statuses,
    tampered on-disk manifest, deleted package files — never trusted
  * repeatability: same acceptance -> byte-identical packages; independent
    runs -> identical deterministic semantic fields (PDF bytes themselves
    embed a creation timestamp — a documented generator property, not
    hidden here)
  * purity: no invented engineering information, no second verification,
    no read-back of the manifest, no caller-editable status fields
"""

import ast
import dataclasses
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.cad_engine.automation_gate import AUTOMATION_DECISION_REVIEW
from app.cad_engine.drawing_output_verification import (
    CHECK_FAILED,
    VERIFICATION_STATUS_FAILED,
    ArtifactCheck,
)
from app.cad_engine.fabrication_package import (
    CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION,
    CHECK_MEMBER_IDENTITIES_PRESENT,
    CHECK_NO_CONTRADICTORY_CONNECTION_IDS,
    CHECK_NO_DUPLICATE_IDENTITY,
    CHECK_NO_UNRESOLVED_CONNECTION_IN_PACKAGE,
    CHECK_PROJECT_IDENTITY_IN_ARTIFACT,
    CHECK_SOURCE_ARTIFACT_PRESENT,
    CHECK_SOURCE_SHA256_MATCHES_RECORDED,
    ISSUE_BLOCKER,
    ISSUE_NOTE,
    PACKAGE_STATUS_BLOCKED,
    PACKAGE_STATUS_READY,
    PACKAGE_STATUS_REFUSED,
    build_fabrication_package,
)
from app.cad_engine.production_acceptance import (
    ACCEPTANCE_STATUS_NOT_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
    accept_production_job,
)
from app.cad_engine.reviewed_connection_specification import (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)
from tests.test_project_workflow import (
    CANDIDATE_IDS,
    CONNECTION_IDENTITIES,
    PROJECT_ID,
    SOURCE_DRAWING_ID,
    _full_sequence,
    _sha256,
)

MODULE_PATH = Path("app/cad_engine/fabrication_package.py")
MODULE_SOURCE = MODULE_PATH.read_text()

EXISTING_PROVENANCE = (
    PROVENANCE_AI_EXTRACTED,
    PROVENANCE_HUMAN_REVIEWED,
    PROVENANCE_HUMAN_SUPPLEMENTED,
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _genuine_acceptance(tmp: Path):
    """The real Arkles job, rev 3, accepted by the genuine 7AQ layer."""
    states = _full_sequence(tmp / "job")
    return states[-1], accept_production_job(states[-1])


def _conn(acceptance, package_id):
    return next(c for c in acceptance.connections if c.package_id == package_id)


def _with_connections(acceptance, connections):
    return dataclasses.replace(acceptance, connections=tuple(connections))


def _with_connection(acceptance, package_id, **changes):
    return _with_connections(
        acceptance,
        [dataclasses.replace(c, **changes) if c.package_id == package_id else c
         for c in acceptance.connections],
    )


def _write_minimal_pdf(path, lines):
    """A real, parseable one-page PDF whose text is exactly `lines`."""
    pdf = canvas.Canvas(str(path))
    y = 780
    for line in lines:
        pdf.drawString(72, y, line)
        y -= 14
    pdf.save()


def _pdf_text(path):
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def _normalized(text):
    return " ".join(text.split())


def _blocker_codes(issues):
    return {issue.code for issue in issues if issue.severity == ISSUE_BLOCKER}


def _note_codes(issues):
    return {issue.code for issue in issues if issue.severity == ISSUE_NOTE}


def _read_manifest(package):
    return json.loads(Path(package.manifest_path).read_text())


# ---------------------------------------------------------------------------
# Module-scoped genuine package: one real workflow run shared by all tests.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def genuine(tmp_path_factory):
    tmp = Path(tmp_path_factory.mktemp("genuine"))
    workflow, acceptance = _genuine_acceptance(tmp)
    pkg_dir = tmp / "pkg"
    package = build_fabrication_package(acceptance, output_dir=pkg_dir)
    return SimpleNamespace(
        tmp=tmp, workflow=workflow, acceptance=acceptance,
        pkg_dir=pkg_dir, package=package,
    )


# ---------------------------------------------------------------------------
# 1. Request validity: type guards and REFUSED semantics.
# ---------------------------------------------------------------------------
class TestRequestValidity:
    def test_type_guards_raise_before_anything_runs(self, genuine, tmp_path):
        for bad_acceptance in (None, object(), "ACCEPTED", genuine.workflow):
            with pytest.raises(TypeError):
                build_fabrication_package(bad_acceptance, output_dir=tmp_path / "x")
        with pytest.raises(TypeError):
            build_fabrication_package(genuine.acceptance, output_dir=123)
        with pytest.raises(TypeError):
            build_fabrication_package(
                genuine.acceptance, output_dir=tmp_path / "x", expected_revision="3",
            )

    def test_unrecognized_acceptance_status_is_rejected(self, genuine, tmp_path):
        bogus = dataclasses.replace(genuine.acceptance, status="BOGUS")
        with pytest.raises(ValueError):
            build_fabrication_package(bogus, output_dir=tmp_path / "x")

    def test_stale_caller_revision_is_refused_and_writes_nothing(self, genuine, tmp_path):
        out = tmp_path / "out"
        package = build_fabrication_package(
            genuine.acceptance, output_dir=out, expected_revision=2,
        )
        assert package.status == PACKAGE_STATUS_REFUSED
        assert package.items == ()
        assert package.manifest_path is None
        assert "revision" in package.reason
        assert not out.exists()

    def test_refused_acceptance_is_refused(self, genuine, tmp_path):
        refused = accept_production_job(genuine.workflow, expected_revision=2)
        assert refused.status == ACCEPTANCE_STATUS_REFUSED
        out = tmp_path / "out"
        package = build_fabrication_package(refused, output_dir=out)
        assert package.status == PACKAGE_STATUS_REFUSED
        assert not out.exists()

    def test_unaccepted_job_is_blocked_with_no_package(self, genuine, tmp_path):
        unaccepted = accept_production_job(_full_sequence(tmp_path / "job")[0])
        assert unaccepted.status == ACCEPTANCE_STATUS_NOT_ACCEPTED
        out = tmp_path / "out"
        package = build_fabrication_package(unaccepted, output_dir=out)
        assert package.status == PACKAGE_STATUS_BLOCKED
        assert package.items == ()
        assert _blocker_codes(package.issues) == {"ACCEPTANCE_NOT_ACCEPTED"}
        assert not out.exists()

    def test_no_status_approved_or_decision_parameter(self):
        signature = inspect.signature(build_fabrication_package)
        assert set(signature.parameters) == {"acceptance", "output_dir", "expected_revision"}


# ---------------------------------------------------------------------------
# 2. The production proof: the real Arkles job becomes a READY package.
# ---------------------------------------------------------------------------
class TestRealArklesPackage:
    def test_package_is_ready_with_three_numbered_drawings(self, genuine):
        package = genuine.package
        assert package.status == PACKAGE_STATUS_READY
        assert package.project_id == PROJECT_ID
        assert package.source_drawing_id == SOURCE_DRAWING_ID
        assert package.workflow_revision == 3
        assert package.acceptance_status == "ACCEPTED"
        assert package.issues == ()
        assert [item.drawing_number for item in package.items] == [
            "STEELSPEC-001", "STEELSPEC-002", "STEELSPEC-003",
        ]
        assert [item.connection_id for item in package.items] == [
            CONNECTION_IDENTITIES["RP-0001"],
            CONNECTION_IDENTITIES["RP-0002"],
            CONNECTION_IDENTITIES["RP-0003"],
        ]
        assert [item.filename for item in package.items] == [
            "STEELSPEC-001.pdf", "STEELSPEC-002.pdf", "STEELSPEC-003.pdf",
        ]

    def test_summary_counts_are_derived(self, genuine):
        summary = genuine.package.summary
        assert summary.total_accepted_connections == 3
        assert summary.total_drawing_artifacts == 3
        assert summary.verified_artifacts == 3
        assert summary.missing_artifacts == 0
        assert summary.failed_artifacts == 0
        assert summary.package_issues == 0
        assert summary.final_package_status == PACKAGE_STATUS_READY

    def test_only_the_package_files_are_written(self, genuine):
        files = sorted(p.relative_to(genuine.pkg_dir).as_posix()
                       for p in genuine.pkg_dir.rglob("*") if p.is_file())
        assert files == [
            "drawings/STEELSPEC-001.pdf",
            "drawings/STEELSPEC-002.pdf",
            "drawings/STEELSPEC-003.pdf",
            "project-manifest.json",
        ]

    def test_packaged_artifacts_are_byte_copies_and_rehashed(self, genuine):
        for item in genuine.package.items:
            packaged = genuine.pkg_dir / "drawings" / item.filename
            assert packaged.read_bytes() == Path(item.source_artifact).read_bytes()
            assert item.artifact_sha256 == _sha256(packaged)
            assert item.artifact_sha256 == item.recorded_sha256
            assert item.packaged_path == f"drawings/{item.filename}"
            assert Path(genuine.pkg_dir / item.packaged_path) == packaged

    def test_every_item_check_passed(self, genuine):
        for item in genuine.package.items:
            assert item.checks
            assert all(check.status == "PASSED" for check in item.checks)
            assert {check.code for check in item.checks} == {
                "ACCEPTANCE_FIELDS_CONSISTENT", "SOURCE_ARTIFACT_PRESENT",
                "SOURCE_SHA256_MATCHES_RECORDED", "RECORDED_CHECKS_PASSED",
                "DISPATCH_IDENTITY_MATCHES_CONNECTION", "PDF_OPENS",
                "PAGE_COUNT_MATCHES_RECORDED", "DRAWING_NUMBER_PRESENT",
                "CONNECTION_IDENTITY_PRESENT", "TITLE_BLOCK_PRESENT",
                "PROJECT_IDENTITY_IN_ARTIFACT", "MEMBER_IDENTITIES_PRESENT",
                "NO_TRACEBACK_TEXT", "NO_EXCEPTION_REPR", "NO_NONE_VALUES",
                "NO_RAW_AI_DUMP", "NO_CONTRADICTORY_CONNECTION_IDS",
            }

    def test_manifest_corresponds_exactly_to_included_artifacts(self, genuine):
        manifest = _read_manifest(genuine.package)
        assert manifest["package_schema"] == "steelspec-fabrication-package-1"
        assert manifest["project"] == {
            "project_id": PROJECT_ID,
            "source_drawing_id": SOURCE_DRAWING_ID,
            "workflow_revision": 3,
            "acceptance_status": "ACCEPTED",
            "package_status": PACKAGE_STATUS_READY,
            "reason": genuine.package.reason,
        }
        assert manifest["summary"]["final_package_status"] == PACKAGE_STATUS_READY
        assert manifest["issues"] == []
        drawings = manifest["drawings"]
        assert len(drawings) == 3
        for drawing, item in zip(drawings, genuine.package.items):
            assert drawing["drawing_number"] == item.drawing_number
            assert drawing["connection_id"] == item.connection_id
            assert drawing["project_id"] == PROJECT_ID
            assert drawing["filename"] == item.filename
            assert drawing["source_artifact"] == item.source_artifact
            assert drawing["source_filename"] == item.source_filename
            assert drawing["page_count"] == 1
            assert drawing["verification_status"] == "VERIFIED"
            assert drawing["dispatch_output_status"] == "GENERATED"
            assert drawing["artifact_sha256"] == item.artifact_sha256
            assert drawing["recorded_sha256"] == item.recorded_sha256
            assert drawing["observed"] == dict(item.observed)
            assert len(drawing["checks"]) == len(item.checks)
            assert "audit" in drawing

    def test_manifest_carries_no_secrets(self, genuine):
        dumped = json.dumps(_read_manifest(genuine.package)).lower()
        for forbidden in ("supabase", "password", "secret", "token", "api_key", "postgres"):
            assert forbidden not in dumped

    def test_observed_fields_are_the_real_title_block(self, genuine):
        first = genuine.package.items[0]
        observed = dict(first.observed)
        assert observed["drawing_number_observed"] == "FAB-CONN-ARKLES-001"
        assert observed["revision"] == "A"
        assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", observed["date"])
        assert observed["project"] == PROJECT_ID
        assert observed["source_drawing"] == SOURCE_DRAWING_ID
        assert observed["member_a_mark"] == "L2"
        assert observed["member_a_section"] == "310UB40"
        assert observed["member_a_length"] == "4000 mm"
        assert observed["member_b_mark"] == "L3"
        assert observed["member_b_section"] == "250PFC"
        assert observed["member_b_length"] == "3000 mm"
        assert observed["plate"] == "180 × 250 × 12 mm"
        assert observed["holes"] == "4 × Ø22"
        assert observed["pattern"] == "90 H × 140 V"
        assert observed["material"] == "NOT SPECIFIED"
        assert observed["units"] == "mm"
        assert observed["scale"] == "NTS"
        # MILESTONE 7AW: the artifact carries no STATUS field at all — visible
        # absence, never a fabricated default (no approval state was recorded)
        assert "status" not in observed

    def test_packaged_pdf_text_asserts_real_identities(self, genuine):
        """The real PDF text, not the model — the fabricator reads the PDF."""
        text = _normalized(_pdf_text(genuine.pkg_dir / "drawings" / "STEELSPEC-001.pdf"))
        for expected in (
            "CONN-ARKLES-001", "FAB-CONN-ARKLES-001",
            PROJECT_ID, SOURCE_DRAWING_ID,
            "MEMBER A", "L2", "310UB40", "4000 mm",
            "MEMBER B", "L3", "250PFC", "3000 mm",
            "PLATE: 180 × 250 × 12 mm", "HOLES: 4 × Ø22", "PATTERN: 90 H × 140 V",
            "STEELSPEC", "FABRICATION DRAWING",
        ):
            assert expected in text, expected

    def test_project_rows_are_in_the_real_artifact(self, genuine):
        lines = _pdf_text(genuine.pkg_dir / "drawings" / "STEELSPEC-001.pdf").splitlines()
        assert "PROJECT" in lines and "PROJ-7AJ" in lines
        assert "SOURCE DRAWING" in lines and "ARKLES-STRAND" in lines

    def test_audit_trail_reaches_human_decisions_and_ai_observations(self, genuine):
        for item in genuine.package.items:
            audit = dict(item.audit)
            assert set(audit) == {
                "package_id", "acceptance", "drawing_evidence", "ai_observations",
                "field_provenance", "human_decisions", "verification",
            }
            evidence = audit["drawing_evidence"]
            assert evidence["source_drawing_id"] == SOURCE_DRAWING_ID
            assert evidence["drawing_number"] is None
            decisions = audit["human_decisions"]
            assert len(decisions) == 8
            for decision in decisions:
                assert decision["applied"] is True
                assert decision["task_id"]
            # the three approval tasks (COMPLETE_REVIEW, REVIEW_SPECIFICATION,
            # REVIEW_VALIDATION) are recorded with answer None by design;
            # every engineering question carries its supplied answer
            assert sum(1 for d in decisions if d["answer"] is not None) == 5
            labels = {entry[1] for entry in audit["field_provenance"]}
            assert labels and labels <= set(EXISTING_PROVENANCE)
            assert audit["ai_observations"]["member_references"]
            assert audit["verification"]["status"] == "VERIFIED"
            assert audit["verification"]["recorded_sha256"] == item.recorded_sha256

    def test_package_is_built_from_the_accepted_state_only(self, genuine, monkeypatch):
        """No generator, verifier, dispatcher, rerun or acceptance call runs
        while packaging — the package only copies what is already recorded."""
        forbidden = [
            ("app.cad_engine.drawing_dispatch", "dispatch_fabrication_drawing"),
            ("app.cad_engine.drawing_dispatch", "dispatch_project_fabrication_drawings"),
            ("app.cad_engine.drawing_output_verification", "verify_drawing_artifact"),
            ("app.cad_engine.drawing_output_verification", "verify_project_drawing_outputs"),
            ("app.cad_engine.resolution_rerun", "rerun_connection_after_resolutions"),
            ("app.cad_engine.production_acceptance", "accept_production_job"),
        ]
        for module_name, function_name in forbidden:
            monkeypatch.setattr(
                f"{module_name}.{function_name}",
                lambda *a, **k: pytest.fail(f"{function_name} was invoked during packaging"),
            )
        out = genuine.tmp / "pkg-spied"
        package = build_fabrication_package(genuine.acceptance, output_dir=out)
        assert package.status == PACKAGE_STATUS_READY


# ---------------------------------------------------------------------------
# 3. Negative paths: every violation blocks the package, nothing is written.
# ---------------------------------------------------------------------------
class TestNegativePaths:
    def _blocked_with(self, acceptance, out, codes):
        package = build_fabrication_package(acceptance, output_dir=out)
        assert package.status == PACKAGE_STATUS_BLOCKED
        assert codes <= _blocker_codes(package.issues)
        assert not out.exists() or not any(out.rglob("*"))
        return package

    def test_missing_artifact_blocks_the_package(self, genuine, tmp_path):
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001",
            verified_artifact=str(tmp_path / "gone.pdf"),
        )
        package = self._blocked_with(acceptance, tmp_path / "out", {CHECK_SOURCE_ARTIFACT_PRESENT})
        assert any("never hunts for a substitute" in issue.detail for issue in package.issues)

    def test_changed_bytes_after_verification_block_the_package(self, genuine, tmp_path):
        copy = tmp_path / "CONN-ARKLES-001-fabrication.pdf"
        copy.write_bytes(Path(genuine.acceptance.verified_artifacts[0]).read_bytes() + b"tampered")
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001", verified_artifact=str(copy),
        )
        self._blocked_with(acceptance, tmp_path / "out", {CHECK_SOURCE_SHA256_MATCHES_RECORDED})

    def test_recorded_failed_verification_blocks_the_package(self, genuine, tmp_path):
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001", verification_status=VERIFICATION_STATUS_FAILED,
        )
        self._blocked_with(acceptance, tmp_path / "out", {"ACCEPTANCE_FIELDS_CONSISTENT"})

    def test_tampered_decision_blocks_the_package(self, genuine, tmp_path):
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001", decision=AUTOMATION_DECISION_REVIEW,
        )
        self._blocked_with(acceptance, tmp_path / "out", {"ACCEPTANCE_FIELDS_CONSISTENT"})

    def test_injected_verified_status_over_failed_checks_blocks(self, genuine, tmp_path):
        injected = ArtifactCheck(code="INJECTED", status=CHECK_FAILED, detail="tampered")
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001",
            checks=_conn(genuine.acceptance, "RP-0001").checks + (injected,),
        )
        self._blocked_with(acceptance, tmp_path / "out", {"RECORDED_CHECKS_PASSED"})

    def test_wrong_connection_artifact_by_name_blocks(self, genuine, tmp_path):
        other = _conn(genuine.acceptance, "RP-0002").verified_artifact
        renamed = tmp_path / "OTHER-NAME.pdf"
        renamed.write_bytes(Path(other).read_bytes())
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001",
            verified_artifact=str(renamed), sha256=_sha256(renamed),
        )
        self._blocked_with(acceptance, tmp_path / "out", {CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION})

    def test_substituted_artifact_under_correct_name_blocks(self, genuine, tmp_path):
        fake = tmp_path / "CONN-ARKLES-001-fabrication.pdf"
        _write_minimal_pdf(fake, [
            "STEELSPEC", "FABRICATION DRAWING",
            "FAB-CONN-ARKLES-001", "CONNECTION DETAIL — CONN-ARKLES-002",
            "MEMBER A", "L2", "SECTION", "310UB40", "LENGTH", "4000 mm",
            "MEMBER B", "L3", "SECTION", "250PFC", "LENGTH", "3000 mm",
            "MATERIAL", "NOT SPECIFIED", "UNITS", "mm", "SCALE", "NTS", "STATUS", "TEST",
        ])
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001",
            verified_artifact=str(fake), sha256=_sha256(fake),
        )
        self._blocked_with(acceptance, tmp_path / "out", {CHECK_NO_CONTRADICTORY_CONNECTION_IDS})

    def test_missing_engineering_value_is_blocked_never_invented(self, genuine, tmp_path):
        fake = tmp_path / "CONN-ARKLES-001-fabrication.pdf"
        _write_minimal_pdf(fake, [
            "STEELSPEC", "FABRICATION DRAWING",
            "FAB-CONN-ARKLES-001", "CONNECTION DETAIL — CONN-ARKLES-001",
            "MEMBER A", "L2", "MEMBER B", "L3",
            "SECTION", "310UB40",  # only ONE section — the second is missing
            "LENGTH", "4000 mm",
            "MATERIAL", "NOT SPECIFIED", "UNITS", "mm", "SCALE", "NTS", "STATUS", "TEST",
        ])
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001",
            verified_artifact=str(fake), sha256=_sha256(fake),
        )
        package = self._blocked_with(acceptance, tmp_path / "out", {CHECK_MEMBER_IDENTITIES_PRESENT})
        assert CHECK_PROJECT_IDENTITY_IN_ARTIFACT in _note_codes(package.issues)
        item = package.items[0]
        # the blocked item observes nothing — nothing is ever invented to
        # fill the gap the issue explains
        assert dict(item.observed) == {}
        assert any("not every member section present" in issue.detail for issue in package.issues)

    def test_garbage_bytes_at_the_recorded_path_block(self, genuine, tmp_path):
        fake = tmp_path / "CONN-ARKLES-001-fabrication.pdf"
        fake.write_bytes(b"this is not a pdf at all")
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001",
            verified_artifact=str(fake), sha256=_sha256(fake),
        )
        package = self._blocked_with(acceptance, tmp_path / "out", {"PDF_OPENS"})
        assert any("never repairs" in issue.detail for issue in package.issues)

    def test_duplicate_drawing_identity_blocks(self, genuine, tmp_path):
        c1 = _conn(genuine.acceptance, "RP-0001")
        c3 = _conn(genuine.acceptance, "RP-0003")
        acceptance = _with_connections(genuine.acceptance, [c1, c1, c3])
        self._blocked_with(acceptance, tmp_path / "out", {CHECK_NO_DUPLICATE_IDENTITY})

    def test_unaccepted_connection_presented_as_completed_blocks(self, genuine, tmp_path):
        c1 = _conn(genuine.acceptance, "RP-0001")
        c2 = _conn(genuine.acceptance, "RP-0002")
        c3 = _conn(genuine.acceptance, "RP-0003")
        unaccepted_twin = dataclasses.replace(c2, accepted=False)
        acceptance = _with_connections(genuine.acceptance, [c1, c2, c3, unaccepted_twin])
        self._blocked_with(acceptance, tmp_path / "out", {CHECK_NO_UNRESOLVED_CONNECTION_IN_PACKAGE})

    def test_extra_files_are_never_silently_included(self, genuine):
        extra_pdf = genuine.pkg_dir / "drawings" / "EXTRA.pdf"
        junk = genuine.pkg_dir / "junk.txt"
        original_extra = b"extra file that must never be listed"
        extra_pdf.write_bytes(original_extra)
        junk.write_text("junk")
        # a pre-existing drawing with wrong bytes is replaced by the recorded artifact
        genuine.pkg_dir.joinpath("drawings", "STEELSPEC-001.pdf").write_bytes(b"stale")

        package = build_fabrication_package(genuine.acceptance, output_dir=genuine.pkg_dir)
        assert package.status == PACKAGE_STATUS_READY
        assert extra_pdf.read_bytes() == original_extra  # untouched, unlisted
        assert junk.read_text() == "junk"
        manifest = _read_manifest(package)
        assert [d["filename"] for d in manifest["drawings"]] == [
            "STEELSPEC-001.pdf", "STEELSPEC-002.pdf", "STEELSPEC-003.pdf",
        ]
        restored = genuine.pkg_dir / "drawings" / "STEELSPEC-001.pdf"
        assert _sha256(restored) == package.items[0].recorded_sha256


# ---------------------------------------------------------------------------
# 4. Tamper proof: user-editable state is never trusted over the record.
# ---------------------------------------------------------------------------
class TestTamperProof:
    def test_tampered_on_disk_manifest_and_deleted_drawing_are_restored(self, genuine, tmp_path):
        out = tmp_path / "out"
        first = build_fabrication_package(genuine.acceptance, output_dir=out)
        pristine_manifest = (out / "project-manifest.json").read_bytes()
        assert first.status == PACKAGE_STATUS_READY

        # tamper everything user-editable on disk
        (out / "project-manifest.json").write_text('{"package_schema": "tampered"}')
        (out / "drawings" / "STEELSPEC-001.pdf").unlink()
        (out / "drawings" / "STEELSPEC-002.pdf").write_bytes(b"tampered")

        rebuilt = build_fabrication_package(genuine.acceptance, output_dir=out)
        assert rebuilt.status == PACKAGE_STATUS_READY
        assert (out / "project-manifest.json").read_bytes() == pristine_manifest
        assert _sha256(out / "drawings" / "STEELSPEC-001.pdf") == rebuilt.items[0].recorded_sha256
        assert _sha256(out / "drawings" / "STEELSPEC-002.pdf") == rebuilt.items[1].recorded_sha256

    def test_copied_pdf_under_a_new_name_is_not_trusted(self, genuine, tmp_path):
        """A byte-identical copy renamed to the right name is fine (the record's
        hash governs); a renamed copy at the WRONG name is refused."""
        wrong = tmp_path / "copied-elsewhere.pdf"
        wrong.write_bytes(Path(_conn(genuine.acceptance, "RP-0001").verified_artifact).read_bytes())
        acceptance = _with_connection(
            genuine.acceptance, "RP-0001",
            verified_artifact=str(wrong), sha256=_sha256(wrong),
        )
        package = build_fabrication_package(acceptance, output_dir=tmp_path / "out")
        assert package.status == PACKAGE_STATUS_BLOCKED
        assert CHECK_DISPATCH_IDENTITY_MATCHES_CONNECTION in _blocker_codes(package.issues)

    def test_manifest_claims_are_never_read_back(self):
        assert "json.load" not in MODULE_SOURCE
        assert "read_text(" not in MODULE_SOURCE
        assert "open(" not in MODULE_SOURCE


# ---------------------------------------------------------------------------
# 5. Repeatability: deterministic package, honest PDF-byte caveat.
# ---------------------------------------------------------------------------
class TestRepeatability:
    def test_same_acceptance_builds_byte_identical_packages(self, genuine, tmp_path):
        out_a = tmp_path / "a"
        out_b = tmp_path / "b"
        package_a = build_fabrication_package(genuine.acceptance, output_dir=out_a)
        package_b = build_fabrication_package(genuine.acceptance, output_dir=out_b)
        assert (out_a / "project-manifest.json").read_bytes() == \
            (out_b / "project-manifest.json").read_bytes()
        for item_a, item_b in zip(package_a.items, package_b.items):
            assert item_a.drawing_number == item_b.drawing_number
            assert item_a.connection_id == item_b.connection_id
            assert item_a.artifact_sha256 == item_b.artifact_sha256
            packaged_a = out_a / "drawings" / item_a.filename
            packaged_b = out_b / "drawings" / item_b.filename
            assert packaged_a.read_bytes() == packaged_b.read_bytes()

    def test_independent_runs_agree_on_semantic_fields(self, tmp_path):
        """Two genuine runs produce byte-different PDFs (the generator embeds a
        creation timestamp in the PDF metadata — a documented generator
        property), so the manifest hashes differ. Every deterministic semantic
        field — numbering, identity, membership, statuses, observed title
        block — must agree exactly; hashes, paths and the drawn date are the
        only honest differences."""
        manifests = []
        for name in ("run-a", "run-b"):
            _, acceptance = _genuine_acceptance(tmp_path / name)
            out = tmp_path / name / "pkg"
            package = build_fabrication_package(acceptance, output_dir=out)
            assert package.status == PACKAGE_STATUS_READY
            manifests.append(json.loads((out / "project-manifest.json").read_text()))

        def normalize(manifest):
            manifest = json.loads(json.dumps(manifest))
            for drawing in manifest["drawings"]:
                drawing.pop("artifact_sha256")
                drawing.pop("recorded_sha256")
                drawing["source_artifact"] = Path(drawing["source_artifact"]).name
                drawing["observed"].pop("date", None)
                # check details and the audit's recorded hash embed the
                # run's sha256/temp paths; code+status are the semantic fields
                for check in drawing["checks"]:
                    check.pop("detail", None)
                drawing["audit"]["verification"].pop("recorded_sha256", None)
            return manifest

        assert normalize(manifests[0]) == normalize(manifests[1])


# ---------------------------------------------------------------------------
# 6. Purity: the module itself never invents, imports, or decides.
# ---------------------------------------------------------------------------
class TestModulePurity:
    def test_no_engineering_literals_in_the_module(self):
        for forbidden in ("ARKLES", "RP-000", "PROJ-7AJ", "M12", "SQ4", "Ø",
                          "310UB40", "250PFC", "SYSTEM_DERIVED", "×"):
            assert forbidden not in MODULE_SOURCE, forbidden
        assert not re.search(r"\b(180|250|12|22|4000|3000|7000|140|90|45|55|41|310|30)\b",
                             MODULE_SOURCE)
        assert not re.search(r"\bmm\b", MODULE_SOURCE)
        assert not re.search(r"\bL2\b|\bL3\b", MODULE_SOURCE)

    def test_module_imports_only_cad_engine_pypdf_and_stdlib(self):
        tree = ast.parse(MODULE_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in {"dataclasses", "hashlib", "json", "re", "io", "pathlib"}
                           for alias in node.names), node.names
            elif isinstance(node, ast.ImportFrom):
                root = node.module.split(".")[0]
                assert root in {"app", "pypdf", "dataclasses", "hashlib", "json",
                                "re", "io", "pathlib"}, node.module

    def test_scope_statement_and_reason_are_honest(self, genuine):
        manifest = _read_manifest(genuine.package)
        assert "7AG" in manifest["scope_statement"]
        assert "never regenerates" in manifest["scope_statement"]
        assert "never invents" in manifest["scope_statement"]
        assert genuine.package.reason
