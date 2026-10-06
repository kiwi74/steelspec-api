"""
L32 — THE RECORDED BAND AND THE CURRENT RECONSTRUCTION ARE DIFFERENT BANDS.

WHAT THIS FILE IS

The regression L28/L29/L31 kept deferring, and the whole point of the contract change they
made. A review page shows two bands that can describe DIFFERENT EXTRACTION LINEAGES:

    RECORDED REVIEW            the persisted revision and the evidence IT was built from
    CURRENT RECONSTRUCTION     a fresh reading of the selected document, right now

After a lineage is selected these name different analysis runs. The trap is that the
reconstruction's own revision is ALWAYS 0 — it is a fresh reconstruction, not a recorded
revision — so both bands can report the number 0 while describing entirely different
evidence. If the wire exposed only one evidence identity, a reviewer would have no way to
tell which lineage the page was showing, and "revision 0" would read as a claim about the
projection.

WHAT THIS FILE PROVES

That the two evidence identities are independently visible, and that both internal revision
numbers being 0 does NOT collapse them: `recorded_evidence_run_ids` names what the record
holds, `capture_runs` names what the projection read, and `revision` remains the recorded
head.

The fixture values are deliberately the real ones from the controlled Arkles project — RUN_A
is what revision 0 was recorded from, RUN_B is the selected lineage — because a regression
about distinguishing two specific things should name those things. They live in the TEST
only; no production module knows either id.

DOUBLED, NOT LIVE

`WorkflowReview` is built directly and passed to the real `workflow_wire`, so the wire form
is the production one and nothing is queried, written or posted.
"""
from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://l32-wire.test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "l32-test-placeholder-not-a-credential")

from app.production_connection_review import WorkflowReview  # noqa: E402
from app.production_review_wire import workflow_wire  # noqa: E402

PROJECT = "2389c115-664f-4fe4-8b76-ca07aac3719d"

#: What the RECORDED revision 0 was built from (the original lineage).
RUN_A = "2830d3bc-6918-4550-b860-f9047a342899"
#: What the CURRENT reconstruction read (the selected L14 lineage).
RUN_B = "e697dfee-d22c-4ab4-a0b9-a193d2dd70af"


def _review(**overrides) -> WorkflowReview:
    """One composition whose two bands name different runs — the state after a selection."""
    base = dict(
        project_id=PROJECT,
        identity=(("Project", PROJECT), ("Current reconstruction", "from the selected document")),
        coverage=(),
        # the CURRENT reconstruction's evidence
        capture_runs=(RUN_B,),
        reconstructed=None,
        view=None,
        refusal_code="",
        refusal_detail="",
        persisted_code="REVIEW_STATE_RECORDED",
        persisted_revisions=(0,),
        persisted_view=None,
        recorded_field_readings=(),
        limitations=(),
        # the RECORDED band's evidence
        recorded_evidence_run_ids=(RUN_A,),
    )
    base.update(overrides)
    return WorkflowReview(**base)


class TestTheTwoBandsAreSeparatelyVisible:
    def test_the_wire_exposes_both_identities(self):
        wire = workflow_wire(_review())
        assert wire["recorded_evidence_run_ids"] == [RUN_A]
        assert wire["capture_runs"] == [RUN_B]

    def test_the_two_identities_are_not_the_same_evidence(self):
        """The whole point: these name different lineages, and the wire says so."""
        wire = workflow_wire(_review())
        assert wire["recorded_evidence_run_ids"] != wire["capture_runs"]

    def test_both_internal_revisions_being_zero_does_not_collapse_them(self):
        """The trap this contract exists to close.

        The reconstruction's own revision is always 0, and the recorded head here is 0 too,
        so the two bands report the same NUMBER while describing different EVIDENCE. The
        identities must remain distinguishable regardless.
        """
        wire = workflow_wire(_review())
        assert wire["revision"] == 0
        assert wire["recorded_revisions"] == [0]
        assert wire["recorded_evidence_run_ids"] == [RUN_A]
        assert wire["capture_runs"] == [RUN_B]
        assert set(wire["recorded_evidence_run_ids"]).isdisjoint(wire["capture_runs"])

    def test_the_recorded_identity_is_not_the_reconstructions(self):
        """`revision` describes the RECORD, so it must not be read as describing RUN_B."""
        wire = workflow_wire(_review())
        assert wire["revision"] == 0
        assert RUN_B not in wire["recorded_evidence_run_ids"]
        assert RUN_A not in wire["capture_runs"]

    def test_an_unrecorded_project_states_no_recorded_evidence(self):
        """`()` is an unrecorded project, not a missing field — and the projection still shows."""
        wire = workflow_wire(
            _review(recorded_evidence_run_ids=(), persisted_revisions=(), persisted_code="NO_PERSISTED_CONNECTION_REVIEW_STATE")
        )
        assert wire["recorded_evidence_run_ids"] == []
        assert wire["capture_runs"] == [RUN_B]
        assert wire["revision_recorded"] is False

    def test_the_reconstruction_identity_no_longer_states_a_bare_revision_number(self):
        """The row names the band; it does not print a 0 that reads as the persisted revision."""
        wire = workflow_wire(_review())
        rows = dict((k, v) for k, v in wire["identity"])
        assert rows.get("Current reconstruction") == "from the selected document"
        assert "Reconstruction revision" not in rows
