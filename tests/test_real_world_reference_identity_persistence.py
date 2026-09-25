"""
STEP B — CARRY REFERENCE IDENTITY THROUGH THE PRODUCTION DELIVERABLE.

Proves, on the GENUINE production chain (7AJ workflow -> 7AA -> 7AF -> 7AG ->
7AQ -> 7AR -> 7AS) running the real Arkles capture, that the accepted
reference-data identity of STEP A actually reaches the packaged deliverable,
and that the final package can say:

    "this connection was generated using reference data with
     source_kind = LIVE_SUPABASE, identity_status = UNVERSIONED,
     reference_data_digest = <digest>"

WITHOUT ever calling that digest a catalogue version.

WHAT IS PROVEN HERE, and where:

  1. THE VOCABULARY. The CAD chain's copied spellings are the matcher's own
     (pinned, so they cannot drift), there is exactly ONE identity status, and
     no CAD module can produce a "VERSIONED"-looking value.
  2. THE RECORD. ReferenceDataIdentity refuses an unknown source kind, an
     unknown identity status and a malformed digest; it is frozen and offers
     no method. Absence (None) and a recorded UNVERSIONED identity are two
     different values, at every level (object and JSON).
  3. THE GENUINE CHAIN. One real workflow run whose section matcher is the
     REAL SectionMatcher over captured rows: the identity is recorded at 7AA,
     carried unchanged through 7AF, 7AG and 7AQ, and appears PER DRAWING in the
     7AR manifest — never at project level, never recomputed, with the digest
     verified independently against the rows the matcher actually loaded.
  4. ABSENCE. The same chain with a matcher that declares no identity: every
     stage records None, the manifest entry is an explicit null, and 7AS still
     accepts the deliverable — absence is recorded, never filled in.
  5. THE REFUSED FALLBACK. A member whose section the catalogue answers only
     with a DIFFERENT section's row never reaches an artifact, and no
     deliverable claims that the refused row supplied anything.
  6. TAMPERING. A changed digest, source kind or identity status — on disk, or
     in a forged projection that matches the disk bytes — is refused by 7AS,
     as is provenance moved to project level or dropped from one drawing.
  7. MUTATIONS M1-M5, in throwaway copies, restored and re-verified.

The digest is a CONTENT FINGERPRINT of the reference rows a run observed. It
is never a catalogue version, and this file fails if anything presents it as
one.
"""
import ast
import copy
import dataclasses
import hashlib
import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.cad_engine.automation_pipeline as automation_pipeline
import app.cad_engine.drawing_dispatch as drawing_dispatch
import app.cad_engine.drawing_output_verification as drawing_output_verification
import app.cad_engine.fabrication_package as fabrication_package
import app.cad_engine.fabricator_acceptance as fabricator_acceptance
import app.cad_engine.production_acceptance as production_acceptance
import app.cad_engine.project_workflow as workflow_module
from app.cad_engine.automation_pipeline import (
    REFERENCE_IDENTITY_STATUSES,
    REFERENCE_IDENTITY_UNVERSIONED,
    REFERENCE_SOURCE_KINDS,
    REFERENCE_SOURCE_LIVE_SUPABASE,
    ReferenceDataIdentity,
    capture_reference_identity,
    reference_data_projection,
)
from app.cad_engine.drawing_output_verification import verify_drawing_artifact
from app.cad_engine.fabrication_package import (
    PACKAGE_STATUS_READY,
    build_fabrication_package,
)
from app.cad_engine.fabricator_acceptance import (
    ACCEPTANCE_STATUS_ACCEPTED,
    ACCEPTANCE_STATUS_REFUSED,
    ANSWER_PASS,
    CHECKLIST_ITEM_PACKAGE_COMPLETE,
    FabricatorAcceptanceAnswers,
    FabricatorChecklistAnswer,
    FabricatorDrawingAnswers,
    drawing_checklist_items_for,
    evaluate_fabricator_acceptance,
)
from app.cad_engine.production_acceptance import accept_production_job
from app.cad_engine.reviewed_connection_drawing_gate import (
    generate_fabrication_drawing_from_reviewed_assembly,
)
from tests.test_project_workflow import (
    CANDIDATE_IDS,
    _record,
    _resolutions_for,
    _workflow_built,
)
from tests.test_project_extraction_intake import needs_real_capture
from tests.test_real_member_adapter import FakeSectionMatcher
from tests.test_real_multi_member_cad import make_member_a_row, make_member_b_row
from tests.test_real_world_catalogue_identity_contract import (
    TEST_SERVICE_ROLE_KEY,
    TEST_SUPABASE_URL,
    LIVE_250PFC,
    LIVE_310UB40_4,
    LIVE_310UB46_2,
    _FakeSupabaseClient,
    _NetworkGuardClient,
    _independent_digest,
)
from tests.test_real_world_exception_proof import (
    ARKLES_REAL_AI_EXTRACTION,
    HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
    HUMAN_SUPPLIED_SECTION_MATCHER,
)

CHAIN_MODULES = {
    "7AA": automation_pipeline,
    "7AF": drawing_dispatch,
    "7AG": drawing_output_verification,
    "7AQ": production_acceptance,
    "7AR": fabrication_package,
    "7AS": fabricator_acceptance,
}
CHAIN_SOURCES = {name: Path(module.__file__).read_text() for name, module in CHAIN_MODULES.items()}
CHAIN_PATHS = {name: Path(module.__file__) for name, module in CHAIN_MODULES.items()}

REJECTED_ROW_NAME = LIVE_310UB40_4["name"]          # "310UB40.4" — the offered, refused row
MEMBER_SECTION_REAL = LIVE_310UB46_2["name"]        # "310UB46.2" — a real captured row


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _require_capture():
    if not ARKLES_REAL_AI_EXTRACTION:
        pytest.skip(
            "the real Arkles connection capture is not present in this environment; the "
            "genuine-chain proofs in this file require it and never substitute a synthetic one"
        )


def _all_pass_answers(package):
    """Every checklist item answered PASS — the reviewer's own input."""
    blocks = []
    for item in package.items:
        codes = drawing_checklist_items_for(dict(item.audit))
        blocks.append(FabricatorDrawingAnswers(
            item.drawing_number,
            tuple(FabricatorChecklistAnswer(code, ANSWER_PASS) for code in codes),
        ))
    return FabricatorAcceptanceAnswers(
        tuple(blocks),
        (FabricatorChecklistAnswer(CHECKLIST_ITEM_PACKAGE_COMPLETE, ANSWER_PASS),),
    )


def _manifest_of(package):
    return json.loads(Path(package.manifest_path).read_text())


def _drawings(manifest):
    return {entry["drawing_number"]: entry for entry in manifest["drawings"]}


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree_hashes(root):
    root = Path(root)
    return {
        str(path.relative_to(root)): _sha256_file(path)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _forged(package, manifest_dict):
    """A package whose recorded projection IS the supplied (tampered) manifest,
    with the same bytes written to disk — so the generic disk-vs-projection
    integrity check passes and the provenance check is what has to fire."""
    projection = tuple((key, value) for key, value in manifest_dict.items())
    forged = dataclasses.replace(package, manifest=projection)
    Path(package.manifest_path).write_text(
        json.dumps(manifest_dict, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return forged


def _refusal(result):
    assert result.status == ACCEPTANCE_STATUS_REFUSED, (
        f"expected REFUSED, got {result.status}: {result.refusal_reasons}"
    )
    return " ".join(result.refusal_reasons)


# ---------------------------------------------------------------------------
# The real matcher, under a test-only environment boundary (STEP A's harness).
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def section_matcher_module():
    saved = {key: os.environ.get(key) for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    before = set(sys.modules)
    os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = TEST_SERVICE_ROLE_KEY
    try:
        module = importlib.import_module("app.engineering_data.section_matcher")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    real_client = module.supabase
    module.supabase = _NetworkGuardClient()
    try:
        yield module
    finally:
        module.supabase = real_client
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)


def _real_matcher_over(module, rows):
    module.supabase = _FakeSupabaseClient(rows)
    try:
        return module.SectionMatcher()
    finally:
        module.supabase = _NetworkGuardClient()


@pytest.fixture(scope="module")
def identity_rows():
    """The reference rows this file's matcher loads — real captured rows, verbatim."""
    return [copy.deepcopy(LIVE_310UB46_2), copy.deepcopy(LIVE_250PFC)]


@pytest.fixture(scope="module")
def identity_matcher(section_matcher_module, identity_rows):
    return _real_matcher_over(section_matcher_module, identity_rows)


MEMBER_ROWS_REAL_SECTIONS = {
    "L2": {**make_member_a_row(), "mark": "L2", "section_name": MEMBER_SECTION_REAL,
           "section_name_raw": MEMBER_SECTION_REAL},
    "L3": {**make_member_b_row(), "mark": "L3"},
}


def _run_chain(tmp, matcher, member_rows, output_dir_name="drawings"):
    """One genuine workflow run: rev 0 -> rev 3, all three candidates resolved."""
    intake, _, built = _workflow_built()
    workflow = workflow_module.start_project_workflow(
        intake.collection,
        intake=intake,
        member_rows=member_rows,
        member_placements=HUMAN_SUPPLIED_MEMBER_PLACEMENTS,
        section_matcher=matcher,
    )
    states = [workflow]
    for package_id in CANDIDATE_IDS:
        states.append(workflow_module.resolve_project_connection(
            states[-1],
            package_id=package_id,
            resolutions=_resolutions_for(built, package_id),
            output_dir=Path(tmp) / output_dir_name,
        ))
    return states[-1]


# =============================================================================
# 1. THE VOCABULARY — the CAD chain's copied spellings cannot drift.
# =============================================================================
class TestTheChainVocabulary:

    def test_the_copied_spellings_are_the_matchers_own(self, section_matcher_module):
        assert REFERENCE_SOURCE_LIVE_SUPABASE == section_matcher_module.SOURCE_LIVE_SUPABASE
        assert REFERENCE_SOURCE_KINDS == section_matcher_module.SOURCE_KINDS
        assert REFERENCE_IDENTITY_UNVERSIONED == section_matcher_module.IDENTITY_UNVERSIONED
        assert REFERENCE_IDENTITY_STATUSES == section_matcher_module.IDENTITY_STATUSES

    def test_the_cad_chain_does_not_import_the_matcher_module(self):
        """The vocabulary is COPIED, never imported: that module reads os.environ at
        import time, and every CAD module must import without a configured
        environment (the real_member_adapter.py precedent)."""
        for name, source in CHAIN_SOURCES.items():
            assert "engineering_data.section_matcher" not in source, name

    def test_there_is_exactly_one_identity_status_and_it_is_not_a_version(self):
        assert REFERENCE_IDENTITY_STATUSES == (REFERENCE_IDENTITY_UNVERSIONED,)
        for name, source in CHAIN_SOURCES.items():
            literals = {node.value for node in ast.walk(ast.parse(source))
                        if isinstance(node, ast.Constant) and isinstance(node.value, str)}
            version_like = {text for text in literals
                            if "VERSIONED" in text and "UNVERSIONED" not in text}
            assert not version_like, (
                f"{name} names a version-looking value ({sorted(version_like)}); a declared "
                "catalogue version does not exist for the live table and may not be implied "
                "by a digest"
            )
        # The chain may still READ the matcher's declared catalogue_version (it does,
        # at 7AA) — what it may never do is derive one, and the projections it writes
        # are checked for that by name below.

    def test_no_cad_module_calls_the_digest_a_catalogue_version(self):
        for name, source in CHAIN_SOURCES.items():
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    keys = {key.value for key in node.keys if isinstance(key, ast.Constant)}
                    assert "catalogue_version" not in keys, (
                        f"{name} projects a field named catalogue_version; the reference digest "
                        "is not a catalogue version and may never be presented as one"
                    )


# =============================================================================
# 2. THE RECORD — a closed vocabulary, frozen, and never a version.
# =============================================================================
class TestTheRecord:

    def test_an_unknown_source_kind_cannot_be_constructed(self):
        with pytest.raises(ValueError):
            ReferenceDataIdentity(source_kind="BACKUP_CSV", identity_status=REFERENCE_IDENTITY_UNVERSIONED)
        with pytest.raises(ValueError):
            ReferenceDataIdentity(source_kind=None, identity_status=REFERENCE_IDENTITY_UNVERSIONED)

    def test_an_unknown_identity_status_cannot_be_constructed(self):
        for claimed in ("VERSIONED", "DRAFT", "2026.09", "local-section-catalogue@7f9a08b3167ff8d4", None):
            with pytest.raises(ValueError):
                ReferenceDataIdentity(source_kind=REFERENCE_SOURCE_LIVE_SUPABASE, identity_status=claimed)

    def test_a_malformed_digest_cannot_be_recorded(self):
        for bad in ("", "0" * 63, "0" * 65, "A" * 64, "z" * 64, 1234):
            with pytest.raises(ValueError):
                ReferenceDataIdentity(
                    source_kind=REFERENCE_SOURCE_LIVE_SUPABASE,
                    identity_status=REFERENCE_IDENTITY_UNVERSIONED,
                    reference_data_digest=bad,
                )

    def test_the_record_is_frozen_and_offers_nothing_callable(self):
        identity = ReferenceDataIdentity(
            source_kind=REFERENCE_SOURCE_LIVE_SUPABASE,
            identity_status=REFERENCE_IDENTITY_UNVERSIONED,
            reference_data_digest="a" * 64,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.identity_status = "VERSIONED"
        for name in dir(identity):
            if name.startswith("__"):
                continue
            assert not callable(getattr(identity, name)), (
                f"ReferenceDataIdentity.{name} is callable; the record is a value to read, "
                "never something that can be called to change a decision"
            )

    def test_absence_and_unversioned_are_never_the_same_value(self):
        """The distinction the whole milestone rests on."""
        recorded = ReferenceDataIdentity(
            source_kind=REFERENCE_SOURCE_LIVE_SUPABASE,
            identity_status=REFERENCE_IDENTITY_UNVERSIONED,
            reference_data_digest="a" * 64,
        )
        assert reference_data_projection(None) is None
        assert reference_data_projection(recorded) == {
            "source_kind": "LIVE_SUPABASE",
            "identity_status": "UNVERSIONED",
            "reference_data_digest": "a" * 64,
        }
        assert reference_data_projection(None) != reference_data_projection(recorded)
        # A recorded identity with no digest is STILL a recorded identity.
        assert reference_data_projection(ReferenceDataIdentity(
            source_kind=REFERENCE_SOURCE_LIVE_SUPABASE,
            identity_status=REFERENCE_IDENTITY_UNVERSIONED,
            reference_data_digest=None,
        )) is not None

    def test_a_matcher_that_declares_no_identity_records_none(self):
        assert capture_reference_identity(None) is None
        assert capture_reference_identity(object()) is None
        assert capture_reference_identity(HUMAN_SUPPLIED_SECTION_MATCHER) is None

    def test_a_declared_identity_this_chain_cannot_record_is_refused_loudly(self):
        class _Hostile:
            source_kind = REFERENCE_SOURCE_LIVE_SUPABASE
            identity_status = "VERSIONED"
            reference_data_digest = "0" * 64

        with pytest.raises(ValueError):
            capture_reference_identity(SimpleNamespace(reference_identity=_Hostile()))
        # ...and a declared object missing the fields entirely, likewise.
        with pytest.raises(ValueError):
            capture_reference_identity(SimpleNamespace(reference_identity=object()))


# =============================================================================
# 3. THE GENUINE CHAIN — the identity reaches the packaged deliverable.
# =============================================================================
@pytest.fixture(scope="module")
def identity_chain(tmp_path_factory, identity_matcher, identity_rows):
    """The real Arkles job, rev 3, over the REAL SectionMatcher with captured rows."""
    _require_capture()
    tmp = Path(tmp_path_factory.mktemp("identity_chain"))
    workflow = _run_chain(tmp, identity_matcher, MEMBER_ROWS_REAL_SECTIONS)
    acceptance = accept_production_job(workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    return SimpleNamespace(
        tmp=tmp, workflow=workflow, acceptance=acceptance, package=package,
        matcher=identity_matcher, rows=identity_rows,
    )


class TestTheGenuineChainCarriesTheIdentity:

    def test_7aa_records_the_matchers_own_identity_verbatim(self, identity_chain):
        recorded = _record(identity_chain.workflow, "RP-0001").pipeline.reference_identity
        declared = identity_chain.matcher.reference_identity   # STEP A's genuine object
        assert recorded is not None
        assert (recorded.source_kind, recorded.identity_status, recorded.reference_data_digest) == \
            (declared.source_kind, declared.identity_status, declared.reference_data_digest)
        assert recorded.source_kind == REFERENCE_SOURCE_LIVE_SUPABASE
        assert recorded.identity_status == REFERENCE_IDENTITY_UNVERSIONED

    def test_the_recorded_digest_is_the_digest_of_the_rows_the_matcher_loaded(
            self, identity_chain):
        """Recomputed INDEPENDENTLY here, from the rows supplied, by the rule
        restated in tests/test_real_world_catalogue_identity_contract.py — never
        by asking the chain under test."""
        expected = _independent_digest(identity_chain.rows)
        assert _record(identity_chain.workflow, "RP-0001").pipeline \
            .reference_identity.reference_data_digest == expected
        assert identity_chain.matcher.reference_identity.reference_data_digest == expected

    def test_7af_carries_it_unchanged(self, identity_chain):
        record = _record(identity_chain.workflow, "RP-0001")
        assert record.dispatch_result.reference_identity == record.pipeline.reference_identity

    def test_7ag_carries_it_unchanged(self, identity_chain):
        record = _record(identity_chain.workflow, "RP-0001")
        assert record.verification_result.reference_identity == \
            record.dispatch_result.reference_identity

    def test_7aq_records_it_and_accepts_the_job(self, identity_chain):
        record = _record(identity_chain.workflow, "RP-0001")
        assert identity_chain.acceptance.status == "ACCEPTED"
        for connection in identity_chain.acceptance.connections:
            assert connection.accepted is True
            assert connection.reference_identity == record.verification_result.reference_identity

    def test_7ar_puts_it_on_the_drawing_item_not_the_project(self, identity_chain):
        package = identity_chain.package
        assert package.status == PACKAGE_STATUS_READY
        expected = reference_data_projection(
            _record(identity_chain.workflow, "RP-0001").verification_result.reference_identity)
        assert expected is not None
        for item in package.items:
            assert reference_data_projection(item.reference_data) == expected

        manifest = _manifest_of(package)
        entries = _drawings(manifest)
        assert len(entries) == len(package.items) == 3
        for entry in entries.values():
            assert entry["reference_data"] == expected, (
                "every drawing entry must carry its own reference-data provenance"
            )
        for key in ("project", "summary", "package_schema", "scope_statement", "issues"):
            block = manifest[key]
            text = json.dumps(block)
            assert "reference_data" not in text and "reference_data_digest" not in text, (
                f"the manifest's {key} block carries reference-data provenance; it belongs to "
                "the individual drawing item, never to the project as a whole"
            )

    def test_the_deliverable_states_which_reference_data_it_was_generated_from(
            self, identity_chain):
        """The milestone's own sentence, read straight off the packaged manifest."""
        manifest = _manifest_of(identity_chain.package)
        provenance = [entry["reference_data"] for entry in manifest["drawings"]]
        assert all(entry == {
            "source_kind": "LIVE_SUPABASE",
            "identity_status": "UNVERSIONED",
            "reference_data_digest": _independent_digest(identity_chain.rows),
        } for entry in provenance)

    def test_7as_accepts_the_deliverable_with_its_provenance_verified(self, identity_chain):
        result = evaluate_fabricator_acceptance(
            identity_chain.package, acceptance_answers=_all_pass_answers(identity_chain.package),
        )
        assert result.status == ACCEPTANCE_STATUS_ACCEPTED
        assert result.refusal_reasons == ()

    def test_the_digest_is_never_presented_as_a_catalogue_version(self, identity_chain):
        digest = _independent_digest(identity_chain.rows)
        manifest = _manifest_of(identity_chain.package)
        assert digest in json.dumps(manifest["drawings"])
        assert digest not in json.dumps({key: manifest[key] for key in manifest if key != "drawings"})
        # Nothing anywhere in the packaged deliverable calls it a version.
        text = Path(identity_chain.package.manifest_path).read_text()
        assert "catalogue_version" not in text
        assert "CATALOGUE_VERSION" not in text
        # The controller: a declared version is still declared as absent upstream.
        assert _record(identity_chain.workflow, "RP-0001").pipeline.catalogue_version is None

    def test_the_same_acceptance_builds_a_byte_identical_manifest_twice(self, identity_chain):
        """No timestamp, no random id, no environment value: the provenance
        portion and the complete manifest are byte-identical."""
        first = build_fabrication_package(identity_chain.acceptance, output_dir=identity_chain.tmp / "again")
        assert first.status == PACKAGE_STATUS_READY
        assert Path(first.manifest_path).read_bytes() == \
            Path(identity_chain.package.manifest_path).read_bytes()


# =============================================================================
# 4. ABSENCE — recorded as absent, never filled in.
# =============================================================================
@pytest.fixture(scope="module")
def absence_chain(tmp_path_factory):
    """The same genuine job through a matcher that declares NO reference identity."""
    _require_capture()
    from tests.test_project_workflow import _full_sequence

    tmp = Path(tmp_path_factory.mktemp("absence_chain"))
    workflow = _full_sequence(tmp)[-1]
    acceptance = accept_production_job(workflow)
    package = build_fabrication_package(acceptance, output_dir=tmp / "pkg")
    assert HUMAN_SUPPLIED_SECTION_MATCHER is not None
    return SimpleNamespace(tmp=tmp, workflow=workflow, acceptance=acceptance, package=package)


class TestAbsenceIsRecordedAsAbsence:

    def test_every_stage_records_no_identity_at_all(self, absence_chain):
        record = _record(absence_chain.workflow, "RP-0001")
        assert record.pipeline.reference_identity is None
        assert record.dispatch_result.reference_identity is None
        assert record.verification_result.reference_identity is None
        for connection in absence_chain.acceptance.connections:
            assert connection.reference_identity is None

    def test_the_manifest_states_the_absence_explicitly(self, absence_chain):
        assert absence_chain.package.status == PACKAGE_STATUS_READY
        manifest = _manifest_of(absence_chain.package)
        entries = _drawings(manifest)
        assert entries
        for entry in entries.values():
            assert "reference_data" in entry, "absence must be stated, never omitted"
            assert entry["reference_data"] is None

    def test_absence_is_never_turned_into_an_invented_identity(self, absence_chain):
        manifest = _manifest_of(absence_chain.package)
        text = json.dumps(manifest["drawings"])
        assert "UNVERSIONED" not in text
        assert "LIVE_SUPABASE" not in text
        assert "source_kind" not in text

    def test_7as_still_accepts_when_no_provenance_was_recorded(self, absence_chain):
        """A declared absence is a complete statement, not a defect."""
        result = evaluate_fabricator_acceptance(
            absence_chain.package, acceptance_answers=_all_pass_answers(absence_chain.package),
        )
        assert result.status == ACCEPTANCE_STATUS_ACCEPTED


# =============================================================================
# 5. THE REFUSED FALLBACK — no claim that the refused row supplied anything.
# =============================================================================
@pytest.fixture(scope="module")
def fallback_chain(tmp_path_factory, section_matcher_module):
    """The real job with a matcher whose table answers the member's section ONLY
    with a different section's row (the ".0"..".9" suffix fallback)."""
    _require_capture()
    tmp = Path(tmp_path_factory.mktemp("fallback_chain"))
    rows = [copy.deepcopy(LIVE_310UB40_4), copy.deepcopy(LIVE_250PFC)]
    matcher = _real_matcher_over(section_matcher_module, rows)
    workflow = _run_chain(
        tmp, matcher,
        {"L2": {**make_member_a_row(), "mark": "L2"},     # section "310UB40"
         "L3": {**make_member_b_row(), "mark": "L3"}},
    )
    return SimpleNamespace(tmp=tmp, workflow=workflow, matcher=matcher)


class TestTheRefusedFallbackMakesNoProvenanceClaim:

    def test_the_refused_connection_never_reaches_an_artifact(self, fallback_chain):
        record = _record(fallback_chain.workflow, "RP-0001")
        assert record.dispatch_result.output_status == "BLOCKED_REVIEW"
        assert record.dispatch_result.generated_files == ()
        assert record.dispatch_result.generation_error is None
        assert record.verification_result is not None
        assert record.verification_result.artifact_path is None

    def test_no_deliverable_claims_the_refused_row_supplied_anything(self, fallback_chain):
        acceptance = accept_production_job(fallback_chain.workflow)
        connection = next(c for c in acceptance.connections if c.package_id == "RP-0001")
        assert connection.accepted is False
        assert connection.verified_artifact is None

        workflow = fallback_chain.workflow
        assert workflow.generated_files == ()
        manifest = json.dumps(build_fabrication_package(
            acceptance, output_dir=fallback_chain.tmp / "pkg_never_written",
        ).manifest)
        assert REJECTED_ROW_NAME not in manifest
        assert MEMBER_SECTION_REAL not in manifest

    def test_the_identity_still_describes_the_source_consulted_only(self, fallback_chain):
        """QUESTION A is still answered honestly: which dataset was read. It says
        nothing about which row supplied a member — and no member was supplied
        with one."""
        record = _record(fallback_chain.workflow, "RP-0001")
        identity = record.pipeline.reference_identity
        if identity is not None:
            assert identity.source_kind == REFERENCE_SOURCE_LIVE_SUPABASE
            assert identity.identity_status == REFERENCE_IDENTITY_UNVERSIONED
        assert record.pipeline.validation_passed is False
        assert REJECTED_ROW_NAME in record.pipeline.validation_failure.message


# =============================================================================
# 6. PER CONNECTION, NEVER PROJECT-WIDE.
# =============================================================================
class TestProvenanceIsPerConnection:

    def test_two_connections_with_different_identities_keep_their_own(self, identity_chain):
        other = ReferenceDataIdentity(
            source_kind=REFERENCE_SOURCE_LIVE_SUPABASE,
            identity_status=REFERENCE_IDENTITY_UNVERSIONED,
            reference_data_digest="b" * 64,
        )
        connections = list(identity_chain.acceptance.connections)
        connections[0] = dataclasses.replace(connections[0], reference_identity=other)
        package = build_fabrication_package(
            dataclasses.replace(identity_chain.acceptance, connections=tuple(connections)),
            output_dir=identity_chain.tmp / "per_connection",
        )
        assert package.status == PACKAGE_STATUS_READY
        manifest = _manifest_of(package)
        entries = manifest["drawings"]
        assert entries[0]["reference_data"]["reference_data_digest"] == "b" * 64
        assert entries[1]["reference_data"]["reference_data_digest"] == \
            _independent_digest(identity_chain.rows)
        assert entries[0]["reference_data"] != entries[1]["reference_data"]

    def test_the_manifest_has_no_project_level_provenance_key(self, identity_chain):
        manifest = _manifest_of(identity_chain.package)
        assert "reference_data" not in manifest
        assert "reference_data" not in manifest["project"]
        assert "reference_data" not in manifest["summary"]


# =============================================================================
# 7. TAMPERED OR FORGED PROVENANCE IS REFUSED BY 7AS.
# =============================================================================
class TestTheDeliverableRefusesTamperedProvenance:

    @pytest.fixture()
    def tamperable(self, identity_chain, tmp_path):
        """A fresh copy of the genuine deliverable, so a mutation here can never
        reach the module-scoped fixture the other tests share."""
        target = tmp_path / "deliverable"
        source = Path(identity_chain.package.manifest_path).parent
        target.mkdir(parents=True)
        (target / "drawings").mkdir()
        for path in source.rglob("*"):
            if path.is_file():
                (target / path.relative_to(source)).write_bytes(path.read_bytes())
        package = dataclasses.replace(
            identity_chain.package, manifest_path=str(target / "project-manifest.json"),
        )
        answers = _all_pass_answers(package)
        before = _tree_hashes(target)
        return SimpleNamespace(
            package=package, answers=answers, target=target, before=before,
            original_manifest=(target / "project-manifest.json").read_bytes(),
        )

    def _evaluate(self, tamperable, package=None):
        return evaluate_fabricator_acceptance(
            package if package is not None else tamperable.package,
            acceptance_answers=tamperable.answers,
        )

    def _assert_refused_and_intact(self, tamperable, package=None):
        """Refused, and nothing on disk touched BY THE EVALUATION — the snapshot
        is taken after the tamper, so a forged manifest is not mistaken for 7AS
        writing to the deliverable."""
        before = _tree_hashes(tamperable.target)
        reason = _refusal(self._evaluate(tamperable, package))
        assert _tree_hashes(tamperable.target) == before, (
            "7AS wrote into the deliverable it was evaluating"
        )
        return reason

    @pytest.mark.parametrize("field,value", [
        ("reference_data_digest", "c" * 64),      # M2 — a different, well-formed SHA-256
        ("source_kind", "BACKUP_CSV"),            # M4 — a different source kind
        ("identity_status", "VERSIONED"),         # M3 — a different, accepted-looking status
    ])
    def test_a_manifest_edited_on_disk_is_refused(self, tamperable, field, value):
        manifest = json.loads(tamperable.original_manifest)
        manifest["drawings"][0]["reference_data"][field] = value
        (tamperable.target / "project-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._assert_refused_and_intact(tamperable)

    @pytest.mark.parametrize("field,value", [
        ("reference_data_digest", "c" * 64),
        ("reference_data_digest", None),
        ("source_kind", "BACKUP_CSV"),
        ("identity_status", "VERSIONED"),
    ])
    def test_a_forged_projection_that_matches_the_disk_is_refused_by_the_provenance_check(
            self, tamperable, field, value):
        """The disk bytes agree with the recorded projection, so the generic
        integrity check passes — the provenance check is what has to fire."""
        manifest = json.loads(tamperable.original_manifest)
        manifest["drawings"][0]["reference_data"][field] = value
        forged = _forged(tamperable.package, manifest)
        reason = self._assert_refused_and_intact(tamperable, forged)
        assert "reference-data provenance" in reason

    def test_provenance_moved_to_the_project_block_is_refused(self, tamperable):
        manifest = json.loads(tamperable.original_manifest)
        moved = manifest["drawings"][0].pop("reference_data")
        manifest["project"]["reference_data"] = moved
        forged = _forged(tamperable.package, manifest)
        reason = self._assert_refused_and_intact(tamperable, forged)
        assert "reference-data provenance" in reason

    def test_provenance_dropped_from_one_drawing_is_refused(self, tamperable):
        """M5 — the omission case."""
        manifest = json.loads(tamperable.original_manifest)
        del manifest["drawings"][-1]["reference_data"]
        forged = _forged(tamperable.package, manifest)
        reason = self._assert_refused_and_intact(tamperable, forged)
        assert "reference-data provenance" in reason
        assert manifest["drawings"][-1]["drawing_number"] in reason

    @pytest.mark.parametrize("malformed", [
        "LIVE_SUPABASE",                                   # not a record at all
        {"source_kind": "LIVE_SUPABASE"},                  # missing fields
        {"source_kind": "LIVE_SUPABASE", "identity_status": "UNVERSIONED",
         "reference_data_digest": "not-a-digest"},
        {"source_kind": "LIVE_SUPABASE", "identity_status": "UNVERSIONED",
         "reference_data_digest": "D" * 64},               # uppercase is not the canonical form
        {"source_kind": "LIVE_SUPABASE", "identity_status": "UNVERSIONED",
         "reference_data_digest": None, "catalogue_version": "2026.09"},
    ])
    def test_malformed_provenance_fails_closed(self, tamperable, malformed):
        manifest = json.loads(tamperable.original_manifest)
        manifest["drawings"][0]["reference_data"] = malformed
        forged = _forged(tamperable.package, manifest)
        reason = self._assert_refused_and_intact(tamperable, forged)
        assert "reference-data provenance" in reason

    def test_the_untampered_copy_still_accepts(self, tamperable):
        """The control: the same fixture, untouched, is ACCEPTED."""
        result = self._evaluate(tamperable)
        assert result.status == ACCEPTANCE_STATUS_ACCEPTED
        assert _tree_hashes(tamperable.target) == tamperable.before

    def test_the_genuine_deliverable_survived_every_tamper_untouched(self, identity_chain):
        """Every tamper above ran on a throwaway copy; the genuine package the
        rest of this file shares is byte-identical to what it always was."""
        manifest_bytes = Path(identity_chain.package.manifest_path).read_bytes()
        assert _manifest_of(identity_chain.package)["drawings"][0]["reference_data"] == {
            "source_kind": "LIVE_SUPABASE",
            "identity_status": "UNVERSIONED",
            "reference_data_digest": _independent_digest(identity_chain.rows),
        }
        assert manifest_bytes == Path(identity_chain.package.manifest_path).read_bytes()


# =============================================================================
# 8. MUTATION TESTING (M1) — the focused check really is what catches it.
# =============================================================================
def _focused_chain_problems(pipeline_result, dispatch_result, verification_result):
    """The focused provenance assertions, as a function, so the SAME checks can
    be run against a mutated build and must FAIL there."""
    problems = []
    if verification_result.reference_identity != dispatch_result.reference_identity:
        problems.append("7AG dropped or changed the identity it was given by 7AF")
    if dispatch_result.reference_identity != pipeline_result.reference_identity:
        problems.append("7AF dropped or changed the identity it was given by 7AA")
    if verification_result.reference_identity is None:
        problems.append("no reference identity survived to the verification manifest")
    return problems


class TestMutationsInThrowawayCopies:

    @pytest.fixture(autouse=True)
    def mutations_are_restored(self):
        """EVERY mutation in this class happens in a throwaway copy. This guard
        proves it: all six chain modules are byte-identical before and after
        each mutation test."""
        chain_before = {name: (path.read_bytes(), hashlib.sha256(path.read_bytes()).hexdigest())
                        for name, path in CHAIN_PATHS.items()}
        yield
        for name, path in CHAIN_PATHS.items():
            before_bytes, before_sha = chain_before[name]
            after_bytes = path.read_bytes()
            assert after_bytes == before_bytes, f"{name} was modified by a mutation test"
            assert hashlib.sha256(after_bytes).hexdigest() == before_sha

    def test_m1_removing_the_provenance_from_7ag_is_caught(self, identity_chain, tmp_path):
        """M1 — in a THROWAWAY COPY of the module, never the real file."""
        original_path = CHAIN_PATHS["7AG"]
        before = original_path.read_bytes()
        sha_before = hashlib.sha256(before).hexdigest()

        source = before.decode()
        anchor = "        reference_identity=dispatch_result.reference_identity,\n"
        assert source.count(anchor) >= 1, "the mutation anchor moved"
        mutated = source.replace(anchor, "")
        assert mutated != source, "the mutation did not apply — the anchor text moved"
        (tmp_path / "_mutant_7ag.py").write_text(mutated, encoding="utf-8")

        spec = importlib.util.spec_from_file_location("_mutant_7ag", tmp_path / "_mutant_7ag.py")
        mutant = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mutant)

        record = _record(identity_chain.workflow, "RP-0001")
        mutated_result = mutant.verify_drawing_artifact(
            record.dispatch_result, assembly=record.pipeline.reviewed_assembly,
        )
        assert mutated_result.reference_identity is None
        problems = _focused_chain_problems(
            record.pipeline, record.dispatch_result, mutated_result)
        assert problems, "the focused check failed to notice 7AG dropping the provenance"

        # The real module is untouched, and the UNMUTATED chain still passes.
        assert hashlib.sha256(original_path.read_bytes()).hexdigest() == sha_before
        assert _focused_chain_problems(
            record.pipeline, record.dispatch_result, record.verification_result) == []

    def test_the_chain_modules_never_import_a_reference_source_themselves(self):
        """No stage can recompute a digest, because no stage can REACH reference
        data: the identity is a value handed down the chain. Checked on the
        module's imports, not on its prose."""
        for name, source in CHAIN_SOURCES.items():
            imported = set()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
                    imported.update(f"{node.module}.{alias.name}" for alias in node.names)
            offenders = sorted(
                module for module in imported
                if any(word in module.lower()
                       for word in ("supabase", "section_matcher", "config", "requests", "anthropic"))
            )
            assert not offenders, (
                f"{name} imports {offenders}; a stage that can read reference data could "
                "recompute a digest, and provenance must only ever be carried"
            )

    def test_only_the_owning_module_captures_an_identity(self):
        """7AA is the one seam that reads a matcher; every other stage only
        carries the value it was given."""
        assert "capture_reference_identity" in CHAIN_SOURCES["7AA"]
        for name, source in CHAIN_SOURCES.items():
            if name == "7AA":
                continue
            assert "capture_reference_identity" not in source, (
                f"{name} captures a reference identity instead of carrying the one it was "
                "given; provenance is reconstructed at exactly one seam"
            )

    def test_only_7aa_touches_a_section_matcher_at_all(self):
        """7AA is the single seam that reads a matcher object; every later stage
        is handed the recorded value, never the thing that produced it. Checked
        on the modules' IDENTIFIERS (arguments, names, attributes), not prose."""
        users = set()
        for name, source in CHAIN_SOURCES.items():
            for node in ast.walk(ast.parse(source)):
                identifiers = {
                    node.id for node in ast.walk(node) if isinstance(node, ast.Name)
                } if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else set()
                args = {arg.arg for arg in node.args.args} if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)) else set()
                if "section_matcher" in identifiers | args:
                    users.add(name)
        assert users == {"7AA"}, (
            f"{sorted(users)} touch a section matcher; provenance must be captured at "
            "exactly one seam and carried as a value everywhere else"
        )


# =============================================================================
# 9. THE ENTRY POINTS THE CHAIN USES ARE UNCHANGED IN SHAPE.
# =============================================================================
class TestTheExistingContractIsIntact:

    def test_the_digest_never_appears_as_a_catalogue_version_field(self, identity_chain):
        record = _record(identity_chain.workflow, "RP-0001")
        assert record.pipeline.catalogue_version is None
        assert record.dispatch_result.catalogue_version is None
        assert record.pipeline.reference_identity.reference_data_digest != \
            record.pipeline.catalogue_version

    def test_verification_semantics_are_unchanged(self, identity_chain):
        record = _record(identity_chain.workflow, "RP-0001")
        verification = verify_drawing_artifact(
            record.dispatch_result, assembly=record.pipeline.reviewed_assembly,
        )
        assert verification.verification_status == record.verification_result.verification_status
        assert verification.sha256 == record.verification_result.sha256
        assert verification.page_count == record.verification_result.page_count
        assert [c.code for c in verification.checks] == \
            [c.code for c in record.verification_result.checks]
        assert verification.reference_identity == record.dispatch_result.reference_identity

    def test_the_drawing_generator_is_unaware_of_provenance(self):
        source = inspect.getsource(generate_fabrication_drawing_from_reviewed_assembly)
        assert "reference_identity" not in source
        assert "reference_data" not in source

    def test_a_matcher_without_the_step_a_contract_is_unaffected(self):
        class _PlainMatcher:
            def match(self, raw_name):
                return None

        assert capture_reference_identity(_PlainMatcher()) is None
