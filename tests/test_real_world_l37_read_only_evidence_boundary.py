"""
L37 — A TRIPWIRE ON THE REVIEW READ PATH.

WHAT THIS FILE IS

The executable half of L36's mutation control. L36 proved by SOURCE INSPECTION that the
review read path contains no write call in reach — that `build_workflow_review`,
`recorded_review_state`, the wire formatter and the page renderer are all readers, and that
the L29/L32 field is taken off a snapshot already returned rather than fetched.

An inspection is not a tripwire. This file makes the property FAILABLE: it stands up the
three review writers as spies, exercises the real wire path, and asserts the spies were
never reached. A future change that introduces a call from the read/render path to a writer
fails HERE, rather than in production.

WHAT IT DOES NOT DO

It does not re-test the writers' implementations, and it says nothing about whether
recording a revision is correct — only that READING a review never does it. The two
evidence identities are the synthetic RUN_A/RUN_B, never the real Arkles ids, because a
tripwire about a shape should not depend on a project.

DOUBLED, NOT LIVE

`WorkflowReview` is built directly and passed to the real `workflow_wire`. No client, no
query, no network and no database is touched.
"""
from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "https://l37-boundary.test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "l37-test-placeholder-not-a-credential")

import pytest  # noqa: E402

from app.production_connection_review import WorkflowReview  # noqa: E402
from app.production_review_wire import workflow_wire  # noqa: E402

PROJECT = "00000000-0000-4000-8000-000000000037"
RUN_A = "RUN_A"   # what the RECORDED revision was built from
RUN_B = "RUN_B"   # what the CURRENT reconstruction read


def _review(**overrides) -> WorkflowReview:
    """One composition whose two bands name different runs, at revision 0."""
    base = dict(
        project_id=PROJECT,
        identity=(("Project", PROJECT), ("Current reconstruction", "from the selected document")),
        coverage=(),
        capture_runs=(RUN_B,),                      # the current reconstruction's evidence
        reconstructed=None,
        view=None,
        refusal_code="",
        refusal_detail="",
        persisted_code="REVIEW_STATE_RECORDED",
        persisted_revisions=(0,),
        persisted_view=None,
        recorded_field_readings=(),
        limitations=(),
        recorded_evidence_run_ids=(RUN_A,),          # the recorded band's evidence
    )
    base.update(overrides)
    return WorkflowReview(**base)


@pytest.fixture()
def writers(monkeypatch):
    """The three review writers, stood up as spies wherever they are defined.

    Patched at their DEFINING modules so that any import path a future change might take
    reaches the spy rather than the real function.
    """
    calls: list[str] = []

    def spy(name):
        def _spy(*args, **kwargs):
            calls.append(name)
            raise AssertionError(
                f"{name} was reached from the review READ path. Reading a review never "
                "records a revision, opens one, or resolves anything."
            )
        return _spy

    import app.production_connection_review as review_module
    import app.production_review_opening as opening_module
    import app.production_review_resolution as resolution_module
    import app.engineering_data.connection_review_repository as store_module
    import app.cad_engine.project_workflow as workflow_module

    for module, name in (
        (store_module, "record_project_review"),
        (opening_module, "open_project_review"),
        (resolution_module, "resolve_production_connection"),
        (workflow_module, "resolve_project_connection"),
    ):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, spy(name), raising=False)

    # The review module re-exports some of these; patch them there too if present.
    for name in ("record_project_review", "open_project_review"):
        if hasattr(review_module, name):
            monkeypatch.setattr(review_module, name, spy(name), raising=False)

    return calls


class TestReadingAReviewReachesNoWriter:
    def test_formatting_the_wire_calls_no_writer(self, writers):
        wire = workflow_wire(_review())
        assert writers == [], f"the read path reached {writers}"
        assert wire["revision"] == 0

    def test_the_two_evidence_identities_survive_formatting(self, writers):
        wire = workflow_wire(_review())

        assert wire["recorded_evidence_run_ids"] == [RUN_A]
        assert wire["capture_runs"] == [RUN_B]

        # ...and they are not the same evidence, nor collapsed into one another.
        assert set(wire["recorded_evidence_run_ids"]).isdisjoint(wire["capture_runs"])
        assert RUN_B not in wire["recorded_evidence_run_ids"]
        assert RUN_A not in wire["capture_runs"]

    def test_reading_leaves_the_revision_where_it_was(self, writers):
        wire = workflow_wire(_review())
        assert wire["revision"] == 0
        assert wire["recorded_revisions"] == [0]
        assert writers == []

    def test_an_unrecorded_project_also_reaches_no_writer(self, writers):
        wire = workflow_wire(
            _review(
                recorded_evidence_run_ids=(),
                persisted_revisions=(),
                persisted_code="NO_PERSISTED_CONNECTION_REVIEW_STATE",
            )
        )
        assert wire["recorded_evidence_run_ids"] == []
        assert wire["revision_recorded"] is False
        assert writers == []

    def test_the_spies_would_catch_a_writer_if_one_were_reached(self, writers):
        """The control for the control: the tripwire is armed, not merely silent.

        Without this, a spy patched onto the wrong name would let every test above pass
        while proving nothing.
        """
        import app.engineering_data.connection_review_repository as store_module

        with pytest.raises(AssertionError, match="record_project_review"):
            store_module.record_project_review(None, None)
        assert writers == ["record_project_review"]
