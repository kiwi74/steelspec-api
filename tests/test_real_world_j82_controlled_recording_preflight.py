"""
J82 — CONTROLLED REAL-EVIDENCE RECORDING PREFLIGHT: THE PROOFS, AND THE VERDICT IT REACHES.

WHAT THIS MILESTONE ASKS, AND WHAT THE ANSWER IS

The question is whether the live production review project
(`2389c115-664f-4fe4-8b76-ca07aac3719d`) already holds enough genuine persisted evidence to
create a legitimate NEW review revision in which (a) at least one persisted
`ReviewSnapshotItem` carries a real J72 candidate origin, (b) at least one persisted
`ExceptionResolutionTask` carries a real J79 field binding into `ENGINEERING_FIELDS`, (c)
that origin was produced by the existing production machinery rather than injected, and (d)
J81 can then read it back through the real production GET path.

The answer this file proves is **NO — the live evidence is not sufficient, and the missing
piece is not a row**. Read the whole file as one argument:

  A  the identity of the project is settled by a live read, not by the brief (§1);
  B  the recorded revision 0 is enumerated item by item and task by task (§2): three items,
     none of them addressing a candidate, and twenty-eight tasks, none of them bound;
  C  the ORIGIN half is nevertheless derivable and unambiguous — the drawing has exactly ONE
     capture run, page 7 holds exactly two candidates and page 29 exactly one, so the
     existing production machinery states a single origin per item with nothing to choose
     (§3, §4). This half is READY;
  D  the FIELD half is likewise ready in the machinery: the builder in the tree TODAY binds
     `ENGINEERING_FIELDS` on eleven of the same twenty-eight tasks, and the persisted revision
     0 binds none of them because it was written before J79. This half is READY TOO;
  E  revision safety is exact: the chain is at 0, the successor is exactly 1, and J77's own
     rule is driven over its four cases (§6);
  F  and yet NO REVISION CAN BE RECORDED, because a revision is written by a resolve, a
     resolve requires at least one human answer, and not one of the twenty-eight persisted
     tasks has an answer or can be given one from anything this project has persisted (§7);
  G  nothing was written (§8-E, §8-F): the citation table is empty, the claim table is empty,
     the fabrication pointer is NULL and its bucket is empty — before and after this file ran;
  H  and the numbers above are load-bearing rather than decorative: three MUTATION CONTROLS
     drive exec'd copies of the real modules and go red if the pinned facts ever stop
     discriminating.

So the verdict is `J82_BLOCKED`, and the blocker is stated precisely: a genuine human answer
to at least one exception task of the live project does not exist in any persisted row, and
this milestone may not invent one (§7) nor mutate live without a demonstrated justification
(§10). Everything that CAN be established read-only was established, including the two halves
that a future recording would consume.

WHAT IS REAL HERE, AND WHAT IS NOT

  * the live band is read through the system's OWN decode path — `snapshot_item_from_row` and
    the store's own readers — and the collection is the real `workflow.collection` that the
    real reconstruction produced, never a look-alike;
  * the mutation controls exec the REAL module sources with one line changed, so a control
    that stops discriminating is a red test rather than a silent pass;
  * the freshness numbers this file needs (one run, two candidates, one candidate) are read
    from the storage layer's own `authoritative_captures`, which is what selects the reading
    that stands for each page;
  * NOT here: no engine write, no review revision, no citation row, no claim, no storage
    object, no fabrication call, no route request of any kind. This file only ever SELECTs,
    and every assertion about "nothing was written" is a read of a table that must stay empty.

THE ONE PLACE THE BRIEF AND THE CODEBASE DISAGREE, stated here rather than hidden. The
brief's §1 names `2389c115-664f-4fe8-4b76-ca07aac3719d` as the correct project and calls the
other id transposed. It is the other way round: `...-4fe4-8b76-...` is the real project and
`...-4fe8-4b76-...` is the transposition, which the deployment does not hold at all. Area A
settles it with a live read. This file therefore imports the real id from J80's module and
never writes the string out, which is also why it cannot drift back.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.cad_engine import connection_review_snapshot as crs
from app.cad_engine import exception_resolution as ex
from app.cad_engine.candidate_origin_address import (
    CANDIDATE_ARRAY_KEY,
    CandidateOrigin,
    ORIGIN_KEYS,
    origin_of,
    origins_for_page,
)
from app.cad_engine.review_contract import ENGINEERING_FIELDS
from app.engineering_data.page_extraction_capture import authoritative_captures
from app.production_connection_review import (
    recorded_review_state,
    section_matcher_for_review,
)
from app.production_review import project_workflow_reconstruction as recon
from app.production_review_resolution import (
    INPUT_REFUSED_NO_RESOLUTIONS,
    ResolutionInputRefused,
    parse_resolution_request,
)

from tests import test_real_world_j20_connection_review_persistence_gap as j20
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j80_cited_candidate_resolution as j80

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"
SNAPSHOT_SOURCE_PATH = APP / "cad_engine" / "connection_review_snapshot.py"
ORIGIN_SOURCE_PATH = APP / "cad_engine" / "candidate_origin_address.py"
SNAPSHOT_SOURCE = SNAPSHOT_SOURCE_PATH.read_text(encoding="utf-8")
ORIGIN_SOURCE = ORIGIN_SOURCE_PATH.read_text(encoding="utf-8")

#: The real deployment, imported rather than retyped — see the module docstring.
PROJECT = j80.PROJECT
BRIEF_PROJECT = j80.BRIEF_PROJECT

#: The one run that read this project's document, taken from J80's module, and pinned below
#: against the live ledger rather than trusted.
RUN_ID = j80.RUN_OLD

#: The live citation table's name and the fabrication pointer's column, read out of the
#: modules that own them.
CITATION_TABLE = crs.CITATION_TABLE
POINTER_COLUMN = "fab_drawings_pdf_path"
FABRICATION_BUCKET = "fabrication-drawings"
CLAIM_TABLE = "project_review_claims"

#: The recorded revision-0 facts, as objects. These are the LIVE deployment's values and are
#: re-read on every run; the literals here are pins, not sources.
PACKAGES = ("RP-0001", "RP-0002", "RP-0003")
TASKS_PER_ITEM = {"RP-0001": 9, "RP-0002": 9, "RP-0003": 10}
TOTAL_TASKS = 28
ITEMS = 3

#: Each item's recorded `source_page` and `detail_reference`, exactly as the persisted payload
#: states them — including RP-0003's detail string, which is a string and not a number.
RECORDED_PAGE = {"RP-0001": 7, "RP-0002": 7, "RP-0003": 29}
RECORDED_DETAIL = {"RP-0001": None, "RP-0002": None, "RP-0003": "24"}

#: The page each item's own array holds, and how many candidates that array contains. Page 7
#: carries two, page 29 carries one — which is why the origin below is determined rather than
#: chosen (§4).
PAGE_CANDIDATE_COUNT = {7: 2, 29: 1}

#: The derivable J72 origin of each item: the one run, at the item's own zero-based position
#: in its page's own `raw_connections` array.
DERIVED_ORIGIN = {
    "RP-0001": (RUN_ID, 0),
    "RP-0002": (RUN_ID, 1),
    "RP-0003": (RUN_ID, 0),
}

#: The submission order the flattened list uses — which DIVERGES from the array position for
#: RP-0003 (2 against 0) and is therefore exactly the number J72 forbids substituting (§3).
SUBMISSION_INDEX = {"RP-0001": 0, "RP-0002": 1, "RP-0003": 2}

#: The evidence keys every persisted item carries, and the two it must NOT carry.
RECORDED_EVIDENCE_KEYS = (
    "detail_reference", "drawing_number", "grid_reference", "source_drawing_id", "source_page",
)

#: The seven engineering fields, read from the module that owns them rather than restated.
EXPECTED_ENGINEERING_FIELDS = (
    "connected_member_marks", "position", "plate", "holes", "location", "attachments",
    "material",
)

production = j4.production


# ===========================================================================
# The live band, read once.
# ===========================================================================
@pytest.fixture(scope="module")
def live_client(production):
    if not production.live_config:
        pytest.skip("no live Supabase configuration available; the live proofs are skipped "
                    "rather than run against a synthetic database")
    client = production.repository.supabase
    try:
        client.table(CITATION_TABLE).select("project_id").limit(1).execute()
    except Exception as exc:  # credentials/network unavailable in this process
        pytest.skip(f"the live deployment could not be read: {type(exc).__name__}")
    return client


def _rows(client, table, columns="*", **filters):
    try:
        query = client.table(table).select(columns)
        for column, value in filters.items():
            query = query.eq(column, value)
        return query.execute().data or []
    except Exception as exc:
        pytest.skip(f"live {table} could not be read: {type(exc).__name__}: {exc}")


def _reason(outcome):
    """R3's refusal code, or the sentinel that says the outcome was a reading."""
    return getattr(outcome, "reason_code", "RESOLVED")


def _code_only(source: str) -> str:
    """The source with EVERY docstring removed — module, class and function.

    A pin that must hold over executable code rather than over prose: a docstring quoting a
    value to explain it is not a hand-typed constant, and collapsing the two would either
    forbid the explanation or weaken the check to a set-membership test that a substring
    inside a larger string passes for free.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


@pytest.fixture(scope="module")
def live(live_client):
    """Everything this file reads off the live deployment, read exactly once.

    `snapshot` is the recorded revision 0 through the system's own decode path; `workflow`
    and `collection` are the genuinely reconstructed live band — the same objects the
    snapshot builder would consume if a revision were ever written over them.
    """
    from app.engineering_data import repository as project_store

    state = recorded_review_state(PROJECT)
    reconstructed = recon.reconstruct_project_workflow(
        PROJECT, section_matcher=section_matcher_for_review()
    )
    assert reconstructed is not None, "the live project did not reconstruct"
    return {
        "store": project_store,
        "item_rows": _rows(live_client, crs.ITEM_TABLE, "*", project_id=PROJECT),
        "snapshot": state.snapshot,
        "reconstructed": reconstructed,
        "workflow": reconstructed.workflow,
        "collection": reconstructed.workflow.collection,
        "captures": project_store.page_extraction_captures_for_drawing(
            reconstructed.drawing_id
        ),
    }


def _items(live):
    return {item.review_package_id: item for item in live["snapshot"].items}


def _candidates(live):
    return {c.review_package_id: c for c in live["collection"].candidates}


# ===========================================================================
# AREA A — the identity of the live project (§1).
# ===========================================================================
class TestAreaATheProjectIdentity:
    def test_a1_the_real_project_exists_and_the_transposition_does_not(self, live_client):
        """The brief names the transposed id. A read settles which one the deployment holds,
        so the fact is checked rather than asserted in a comment."""
        assert len(_rows(live_client, "projects", "id", id=PROJECT)) == 1
        assert _rows(live_client, "projects", "id", id=BRIEF_PROJECT) == []

    def test_a2_the_id_is_the_imported_one_and_no_code_in_this_file_retypes_it(self):
        """The id is `j80.PROJECT` — the same object, not an equal string — and EXECUTABLE
        code in this file never spells it out. A hand-typed id is how the transposition
        entered this workstream in the first place, so the absence is checked over the
        source with every docstring removed: prose may quote the id to explain it, code may
        not carry it."""
        assert PROJECT is j80.PROJECT
        code = _code_only(Path(__file__).read_text(encoding="utf-8"))
        for hand_typed in (PROJECT, BRIEF_PROJECT):
            assert hand_typed not in code, hand_typed

    def test_a3_the_two_ids_are_the_same_characters_in_a_different_order(self):
        assert BRIEF_PROJECT != PROJECT
        assert sorted(BRIEF_PROJECT) == sorted(PROJECT)
        assert BRIEF_PROJECT.split("-")[:2] == PROJECT.split("-")[:2]


# ===========================================================================
# AREA B — the revision-0 preflight table (§2).
# ===========================================================================
class TestAreaBTheRecordedRevisionZero:
    def test_b1_the_chain_is_at_revision_zero_and_holds_three_items(self, live):
        snapshot = live["snapshot"]
        assert snapshot.review_revision == 0
        assert snapshot.project_status == "REVIEW"
        assert tuple(item.review_package_id for item in snapshot.items) == PACKAGES
        assert len(live["item_rows"]) == ITEMS

    def test_b2_every_item_is_unprocessed_and_holds_no_origin(self, live):
        for item in live["snapshot"].items:
            assert item.decision == "REVIEW"
            assert item.last_processed_revision is None, item.review_package_id
            assert "candidate_index" not in item.evidence, item.review_package_id
            assert "analysis_run_id" not in item.evidence, item.review_package_id
            assert origin_of(item.evidence) is None

    def test_b3_every_item_names_the_same_drawing_and_its_own_recorded_page(self, live):
        """The §2 per-item table, as one assertion, with the drawing DERIVED from the rows
        rather than retyped."""
        table = []
        for item in live["snapshot"].items:
            table.append((
                item.review_package_id,
                item.evidence["source_drawing_id"],
                item.evidence["source_page"],
                item.evidence["detail_reference"],
                item.evidence["grid_reference"],
                sorted(item.evidence),
            ))
        assert [row[0] for row in table] == list(PACKAGES)
        assert [row[2] for row in table] == [RECORDED_PAGE[p] for p in PACKAGES]
        assert [row[3] for row in table] == [RECORDED_DETAIL[p] for p in PACKAGES]
        assert {row[4] for row in table} == {None}
        assert {row[1] for row in table} == {live["reconstructed"].drawing_id}
        assert {tuple(row[5]) for row in table} == {RECORDED_EVIDENCE_KEYS}

    def test_b4_every_persisted_task_states_no_field_binding(self, live):
        """The §2 per-task table, read from the PERSISTED payload — never inferred from the
        task type. Every one of the twenty-eight is `LEGACY_TASK_NO_FIELD_BINDING`."""
        counts, bound, stated = {}, [], []
        for item in live["snapshot"].items:
            counts[item.review_package_id] = len(item.tasks)
            for payload in item.tasks:
                task = crs._task_from_row(payload)
                assert task.field_name is None, (task.task_id, task.field_name)
                if task.field_name is not None:
                    stated.append(task.field_name)
                if task.field_name in ENGINEERING_FIELDS:
                    bound.append(task.task_id)
        assert counts == TASKS_PER_ITEM, counts
        assert sum(counts.values()) == TOTAL_TASKS
        assert stated == []
        assert bound == []

    def test_b5_no_persisted_task_has_been_answered(self, live):
        """§7's precondition, read from the record: every task is OPEN with a null
        resolution. This is the fact the verdict turns on, so it is asserted rather than
        assumed."""
        for item in live["snapshot"].items:
            for payload in item.tasks:
                assert payload["status"] == "OPEN", payload["task_id"]
                assert payload["resolution"] is None, payload["task_id"]

    def test_b6_every_task_asks_for_content_that_is_not_in_the_record(self, live):
        """The other half of §7: a task is answerable only from engineering content, and each
        one's own statement says which. The four gate acknowledgments are named here because
        they are the tempting shortcut — and each one's own `evidence_requirement` says it
        grants nothing by itself."""
        #: Each gate acknowledgment's own words for the same fact — that it supplies no
        #: engineering value. Pinned per type because the four say it differently, and
        #: collapsing them into one phrase would be this file inventing a wording.
        acknowledgement = {
            ex.TASK_COMPLETE_REVIEW: "does not make missing engineering information appear",
            ex.TASK_REVIEW_MALFORMED_FIELDS: "grants nothing by itself",
            ex.TASK_REVIEW_SPECIFICATION: "grants nothing",
            ex.TASK_REVIEW_VALIDATION: "grants nothing",
        }
        seen = set()
        for item in live["snapshot"].items:
            for payload in item.tasks:
                task = crs._task_from_row(payload)
                seen.add(task.task_type)
                assert task.question.strip(), task.task_id
                assert task.evidence_requirement.strip(), task.task_id
                if task.task_type in acknowledgement:
                    assert acknowledgement[task.task_type] in task.evidence_requirement, \
                        task.task_id
        assert set(acknowledgement) <= seen


# ===========================================================================
# AREA C — the origin half is derivable, and unambiguous (§3, §4).
# ===========================================================================
class TestAreaCTheOriginHalf:
    def test_c1_the_document_was_read_by_exactly_one_run(self, live):
        """§3's substitution hazard, closed by fact rather than by care: with a single run
        there is no newer attempt to put in the place of an older one, so the item's silence
        about its run cannot be filled with the wrong one. A second run would make this red."""
        chosen = authoritative_captures(live["captures"])
        runs = tuple(dict.fromkeys(str(row["analysis_run_id"]) for row in chosen))
        assert runs == (RUN_ID,), runs
        assert live["reconstructed"].capture_run_ids == (RUN_ID,)

    def test_c2_every_cited_page_was_read_exactly_once(self, live):
        """The §2 "recorded attempts" column, and the fact that makes it short: there is one
        attempt per page, so the attempt a page's reading came from is not a choice between
        several. A second attempt on page 7 would put the origin's run back in question."""
        attempts = {}
        for row in live["captures"]:
            attempts.setdefault(row["page_number"], []).append(str(row["analysis_run_id"]))
        cited = [RECORDED_PAGE[package] for package in PACKAGES]
        assert {page: len(attempts[page]) for page in cited} == {7: 1, 29: 1}
        assert {run for page in cited for run in attempts[page]} == {RUN_ID}
        assert len(live["captures"]) == 30

    def test_c3_the_cited_pages_state_their_own_arrays_and_the_counts_are_pinned(self, live):
        """§4: the array a `candidate_index` indexes into, at its own length. Two candidates
        on page 7 and one on page 29 — which is what makes each item's position determined
        rather than selected."""
        chosen = {row["page_number"]: row for row in authoritative_captures(live["captures"])}
        counts = {
            page: len(chosen[page]["payload"][CANDIDATE_ARRAY_KEY])
            for page in PAGE_CANDIDATE_COUNT
        }
        assert counts == PAGE_CANDIDATE_COUNT, counts

    def test_c4_the_existing_machinery_derives_one_origin_per_item(self, live):
        """The whole §3 chain, driven at its SOURCE: the page's own array, through the real
        `origins_for_page`, at the run the ledger states."""
        chosen = {row["page_number"]: row for row in authoritative_captures(live["captures"])}
        derived = {}
        for page, count in PAGE_CANDIDATE_COUNT.items():
            origins = origins_for_page(
                chosen[page]["payload"][CANDIDATE_ARRAY_KEY],
                analysis_run_id=chosen[page]["analysis_run_id"],
            )
            assert len(origins) == count
            assert all(o is not None for o in origins)
            for index, origin in enumerate(origins):
                assert origin == CandidateOrigin(RUN_ID, index)
        for package, (run, index) in DERIVED_ORIGIN.items():
            page = RECORDED_PAGE[package]
            derived[package] = origins_for_page(
                chosen[page]["payload"][CANDIDATE_ARRAY_KEY],
                analysis_run_id=chosen[page]["analysis_run_id"],
            )[index]
        assert {p: (o.analysis_run_id, o.candidate_index) for p, o in derived.items()} \
            == DERIVED_ORIGIN

    def test_c5_the_collection_already_carries_that_origin_on_every_candidate(self, live):
        """And the reconstruction hands it through untouched — the collection the builder
        itself consumes is the one this assertion reads."""
        carried = {
            package: (c.origin.analysis_run_id, c.origin.candidate_index)
            for package, c in _candidates(live).items()
        }
        assert carried == DERIVED_ORIGIN, carried

    def test_c6_the_array_position_is_not_the_submission_order(self, live):
        """§3's forbidden substitutions, refuted by the live numbers: RP-0003 is third in the
        flattened list and FIRST in its page's array, so a submission-order origin would name
        a different candidate. The two numbers are pinned side by side."""
        candidates = _candidates(live)
        assert {p: c.submission_index for p, c in candidates.items()} == SUBMISSION_INDEX
        divergent = [
            p for p, c in candidates.items()
            if c.submission_index != c.origin.candidate_index
        ]
        assert divergent == ["RP-0003"], divergent

    def test_c7_the_recorded_evidence_is_not_the_derived_origin(self, live):
        """The preflight's central asymmetry, stated as one fact: the machinery can address
        every item, and the recorded revision 0 addresses none. Nothing was repaired and
        nothing will be."""
        assert all(origin_of(item.evidence) is None for item in live["snapshot"].items)
        assert all(c.origin is not None for c in live["collection"].candidates)


# ===========================================================================
# AREA D — the field half is ready in the machinery and absent from the record (§5).
# ===========================================================================
class TestAreaDTheFieldHalf:
    def test_d1_the_seven_engineering_fields_are_what_this_milestone_says(self):
        assert ENGINEERING_FIELDS == EXPECTED_ENGINEERING_FIELDS
        assert ex.ENGINEERING_FIELDS == ENGINEERING_FIELDS
        assert "connection_id" not in ENGINEERING_FIELDS

    def test_d2_the_builder_in_the_tree_binds_every_engineering_field_it_owns(self, live):
        """The SAME workflow, the same collection, the same twenty-eight tasks — built by the
        builder that is in the tree right now, in memory, and never persisted. Eleven tasks
        state a field, and the set covers five of the seven fields."""
        fresh = crs.build_review_snapshot(
            live["workflow"], project_id=PROJECT,
            evidence_rows={"steel_members": [], "connections": []},
            previous_revision=None,
        )
        bound = [
            (t.get("task_type"), t.get("field_name"))
            for item in fresh.items for t in item.tasks
            if t.get("field_name") in ENGINEERING_FIELDS
        ]
        assert len(bound) == 11, bound
        assert {field for _, field in bound} == {
            "plate", "holes", "location", "position", "attachments",
        }
        assert {task for task, _ in bound} == {
            "PROVIDE_PLATE", "PROVIDE_HOLE_DIAMETER", "PROVIDE_LOCATION",
            "SELECT_POSITION", "SELECT_ATTACHMENT",
        }

    def test_d3_the_same_builder_states_the_origin_on_every_item(self, live):
        """The origin half proved the same way, on the same object: the builder hands each
        candidate's own origin into the item's evidence, and the two keys are PRESENT rather
        than written as null."""
        fresh = crs.build_review_snapshot(
            live["workflow"], project_id=PROJECT,
            evidence_rows={"steel_members": [], "connections": []},
            previous_revision=None,
        )
        for item in fresh.items:
            origin = origin_of(item.evidence)
            assert origin is not None, item.review_package_id
            assert all(key in item.evidence for key in ORIGIN_KEYS)
            assert (origin.analysis_run_id, origin.candidate_index) \
                == DERIVED_ORIGIN[item.review_package_id]

    def test_d4_the_persisted_revision_predates_the_binding_and_is_not_repaired(self, live):
        """Why D2 and D3 differ from the record, stated as the fact it is: the signed
        difference between what the builder states today and what revision 0 states. Neither
        side is wrong; the record is simply older, and §5 forbids backfilling it."""
        persisted = [
            crs._task_from_row(t) for item in live["snapshot"].items for t in item.tasks
        ]
        assert [t.field_name for t in persisted] == [None] * TOTAL_TASKS
        #: And the key is not merely null on the record: it is ABSENT, exactly as J72 leaves
        #: its two origin keys. A revision written before the field existed keeps no trace of
        #: it, which is what "legacy" means here and why §5 forbids backfilling.
        assert all("field_name" not in t for item in live["snapshot"].items for t in item.tasks)
        assert all(origin_of(item.evidence) is None for item in live["snapshot"].items)


# ===========================================================================
# AREA E — revision safety (§6).
# ===========================================================================
class TestAreaERevisionSafety:
    def test_e1_the_successor_of_revision_zero_is_exactly_one(self, live_client):
        from app.engineering_data import connection_review_repository as store

        assert store.latest_recorded_revision(PROJECT) == 0
        headers = _rows(live_client, crs.SNAPSHOT_TABLE, "review_revision", project_id=PROJECT)
        assert sorted(row["review_revision"] for row in headers) == [0]

    def test_e2_the_builders_revision_arithmetic_admits_only_the_next_revision(self):
        """`expected = 0 if previous_revision is None else previous_revision + 1`, read out of
        the source by AST so the rule is pinned where it lives rather than restated."""
        source = SNAPSHOT_SOURCE
        assert ("expected = 0 if previous_revision is None else previous_revision + 1"
                in source), "the revision arithmetic moved"

    def test_e3_a_prior_origin_is_carried_and_a_divergence_is_refused(self):
        """J77's own function, driven over its first two cases."""
        prior = CandidateOrigin(RUN_ID, 1)
        assert crs._carried_origin(prior, prior, touched=False, where="w") == prior
        assert crs._carried_origin(prior, prior, touched=True, where="w") == prior
        with pytest.raises(crs.SnapshotRefused) as refusal:
            crs._carried_origin(
                prior, CandidateOrigin(j80.RUN_NEW, 1), touched=False, where="w"
            )
        assert refusal.value.code == crs.SNAPSHOT_REFUSED_PRIOR_REVISION_DIVERGED

    def test_e4_an_untouched_connection_with_no_prior_origin_records_nothing(self):
        """THE LIVE CASE, driven over the real rule. Every connection of this project is
        untouched (`last_processed_revision` is None) and no prior origin was recorded, so the
        successor revision would leave both keys ABSENT — which is exactly why the record
        cannot acquire an origin without a resolve."""
        assert crs._carried_origin(
            None, CandidateOrigin(RUN_ID, 0), touched=False, where="w"
        ) is None

    def test_e5_a_touched_connection_acquires_the_derived_origin(self):
        """The other side of the same case, so the rule above is not merely refusing
        everything: a connection genuinely processed at this revision acquires exactly what
        the collection derived."""
        derived = CandidateOrigin(RUN_ID, 0)
        assert crs._carried_origin(None, derived, touched=True, where="w") == derived

    def test_e6_no_connection_has_ever_been_processed_on_this_project(self, live):
        assert [c.last_processed_revision for c in live["workflow"].connections] \
            == [None, None, None]

    def test_e7_the_package_id_set_is_equal_on_both_sides_of_the_revision(self, live):
        """The appeared/disappeared rule, satisfied before it is reached: the workflow's
        connections and the recorded revision's items name the same three packages."""
        assert [c.package_id for c in live["workflow"].connections] == list(PACKAGES)
        assert [i.review_package_id for i in live["snapshot"].items] == list(PACKAGES)


# ===========================================================================
# AREA F — the human-input boundary, and the verdict it forces (§7, §8).
# ===========================================================================
class TestAreaFTheHumanInputBoundary:
    def test_f1_a_resolve_without_a_human_answer_is_refused(self):
        """Why no revision can be recorded: the only writer of a successor revision is a
        resolve, and a resolve with no answers is refused by the route's own parser. This is
        the mechanism behind the verdict, not a policy statement about it."""
        with pytest.raises(ResolutionInputRefused) as refusal:
            parse_resolution_request({"expected_revision": 0, "resolutions": []})
        assert refusal.value.code == INPUT_REFUSED_NO_RESOLUTIONS
        assert "records no human answer" in refusal.value.statement

    def test_f2_the_project_has_never_been_claimed_for_resolution(self, live_client):
        """A claim is what a resolve takes first. None was ever taken, which is the second,
        independent witness that no live mutation has happened."""
        assert _rows(live_client, CLAIM_TABLE, "project_id", project_id=PROJECT) == []

    def test_f3_the_verdicts_preconditions_are_all_true_at_once(self, live, live_client):
        """The whole §8 argument in one place, so its parts cannot be read separately: the
        record holds three items with no origin, twenty-eight tasks with no binding and no
        answers, and the chain is still at revision 0 — while the machinery beside it can
        state both halves. B and C are ready; A is not, and cannot be made so here."""
        snapshot = live["snapshot"]
        assert len(snapshot.items) == ITEMS
        assert all(origin_of(item.evidence) is None for item in snapshot.items)
        assert all(
            crs._task_from_row(t).field_name is None for item in snapshot.items for t in item.tasks
        )
        assert all(
            t["resolution"] is None for item in snapshot.items for t in item.tasks
        )
        from app.engineering_data import connection_review_repository as store

        assert store.latest_recorded_revision(PROJECT) == 0

    def test_f4_the_boundary_is_stated_by_the_tasks_themselves(self, live):
        """§7's classification, read off the record rather than assigned by this file: every
        task's own `answer_type` names an engineering value or a gate acknowledgment, and no
        answer type names a value the record already holds."""
        answer_types = {
            t["answer_type"] for item in live["snapshot"].items for t in item.tasks
        }
        assert answer_types == {
            ex.ANSWER_APPROVE_REVIEW,
            ex.ANSWER_MEMBER_POSITION_ATTACHMENTS,
            ex.ANSWER_POSITION_VALUE,
            ex.ANSWER_ATTACHMENTS_VALUE,
            ex.ANSWER_PLATE_VALUE,
            ex.ANSWER_HOLES_VALUE,
            ex.ANSWER_LOCATION_VALUE,
            ex.ANSWER_CONFIRMED_FIELDS,
            ex.ANSWER_ACKNOWLEDGMENT,
            ex.ANSWER_CONNECTION_IDENTITY,
        }, sorted(answer_types)


# ===========================================================================
# AREA G — nothing was written (§8-E, §8-F, §10).
# ===========================================================================
class TestAreaGNothingWasWritten:
    def test_g1_the_citation_table_holds_no_row(self, live_client):
        assert _rows(live_client, CITATION_TABLE, "project_id") == []

    def test_g2_the_fabrication_pointer_is_null_and_its_bucket_is_empty(self, live_client):
        rows = _rows(live_client, "projects", f"id,{POINTER_COLUMN}", id=PROJECT)
        assert rows and rows[0][POINTER_COLUMN] is None, rows
        try:
            objects = live_client.storage.from_(FABRICATION_BUCKET).list()
        except Exception as exc:
            pytest.skip(f"the fabrication bucket could not be listed: {type(exc).__name__}")
        assert objects == [], objects

    def test_g3_the_recorded_chain_is_still_at_revision_zero_after_all_of_this(self, live_client):
        """The freshness witness: whatever this file did, it did not advance the chain."""
        from app.engineering_data import connection_review_repository as store

        assert store.latest_recorded_revision(PROJECT) == 0
        assert len(_rows(live_client, crs.ITEM_TABLE, "review_package_id", project_id=PROJECT)) \
            == ITEMS

    def test_g4_the_route_set_is_unchanged_and_no_migration_names_this_milestone(
        self, production
    ):
        """A preflight adds no surface: the frozen route set is the same one J20 pins, and the
        migrations directory carries nothing for this milestone. The second half is exact —
        the database model J82 would need is the one J22 and J66 already record."""
        assert j20._declared_routes(production.main.app) == j20.FROZEN_ROUTES
        migrations = sorted(p.name for p in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert [name for name in migrations if "j82" in name.lower()] == [], migrations

    def test_g5_this_file_reads_and_never_writes(self):
        """The strongest available statement that this file performs no mutation: every
        Supabase call it makes is a `select`, and no route request is made at all. The
        assertion is over this file's own AST — no attribute named `insert`, `update`,
        `upsert` or `delete` is reached, and no HTTP client is imported."""
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        forbidden = {"insert", "update", "upsert", "delete", "rpc"}
        reached = {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden
        }
        assert reached == set(), reached
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert "httpx" not in imports
        assert "requests" not in imports


# ===========================================================================
# AREA H — the numbers are load-bearing: MUTATION CONTROLS (§9).
# ===========================================================================
def _mutant_module(path: Path, old: str, new: str, name: str):
    """An exec'd copy of a REAL module with ONE line changed.

    A control built this way goes red for the right reason: it is the product's own code
    that has been altered, not a stand-in written to fail.
    """
    source = path.read_text(encoding="utf-8")
    assert old in source, f"the mutation target is not in {path.name}"
    mutated = source.replace(old, new, 1)
    assert mutated != source
    module = type(__import__("sys"))(name)
    module.__dict__["__file__"] = str(path)
    import sys as _sys

    _sys.modules[module.__name__] = module
    try:
        exec(compile(mutated, str(path), "exec"), module.__dict__)
    finally:
        _sys.modules.pop(module.__name__, None)
    return module


class TestAreaHMutationControls:
    def test_h1_the_pinned_indices_discriminate_against_choose_the_first(self, live):
        """§4's forbidden shortcut, as a control: a copy of the real `origins_for_page` that
        answers position 0 for every candidate. On page 7's real two-candidate array it must
        disagree with the pinned origins — otherwise the pins would not be evidence."""
        chosen = {row["page_number"]: row for row in authoritative_captures(live["captures"])}
        array = chosen[7]["payload"][CANDIDATE_ARRAY_KEY]
        assert len(array) == 2

        real = origins_for_page(array, analysis_run_id=RUN_ID)
        mutant = _mutant_module(
            ORIGIN_SOURCE_PATH,
            "for index in range(len(candidates))",
            "for index in [0] * len(candidates)",
            "j82_mutant_origins_first",
        )
        wrong = mutant.origins_for_page(array, analysis_run_id=RUN_ID)
        assert [o.candidate_index for o in wrong] == [0, 0]
        assert wrong[1] != real[1], "the control does not discriminate"
        assert (real[1].analysis_run_id, real[1].candidate_index) == DERIVED_ORIGIN["RP-0002"]

    def test_h2_the_pinned_binding_discriminates_against_dropping_the_field(self, live):
        """§5's failure mode, as a control: a copy of the real snapshot writer that writes
        `field_name` as null. The eleven bound tasks must collapse to zero, so the pinned
        eleven are a fact about the binding rather than about the task list."""
        mutant = _mutant_module(
            SNAPSHOT_SOURCE_PATH,
            '"field_name": task.field_name,',
            '"field_name": None,',
            "j82_mutant_drop_field_name",
        )
        mutant_snapshot = mutant.build_review_snapshot(
            live["workflow"], project_id=PROJECT,
            evidence_rows={"steel_members": [], "connections": []},
            previous_revision=None,
        )
        mutant_bound = [
            t.get("field_name") for item in mutant_snapshot.items for t in item.tasks
            if t.get("field_name") in ENGINEERING_FIELDS
        ]
        assert mutant_bound == [], mutant_bound
        assert [len(i.tasks) for i in mutant_snapshot.items] \
            == [len(i.tasks) for i in live["snapshot"].items], \
            "the control changed the task list rather than the binding"

    def test_h3_the_pinned_absence_discriminates_against_manufacturing_an_address(self, live):
        """§6's central failure, as a control: a copy of J77's rule that records the derived
        origin for an UNTOUCHED connection. On the live case — no prior origin, never
        processed — the real rule answers None and this one answers the origin, which is what
        makes the `is None` assertions in area C and E evidence rather than tautology."""
        mutant = _mutant_module(
            SNAPSHOT_SOURCE_PATH,
            "    if prior_origin is None:\n        return derived_origin if touched else None",
            "    if prior_origin is None:\n        return derived_origin",
            "j82_mutant_manufacture_origin",
        )
        derived = CandidateOrigin(RUN_ID, 0)
        assert crs._carried_origin(None, derived, touched=False, where="w") is None
        assert mutant._carried_origin(None, derived, touched=False, where="w") == derived
        assert mutant._carried_origin(
            None, derived, touched=False, where="w"
        ) != crs._carried_origin(None, derived, touched=False, where="w")

    def test_h4_the_control_machinery_itself_refuses_a_mutation_that_does_not_apply(self):
        """A control that silently fails to mutate would pass for the wrong reason, so the
        builder is pinned against that: a target that is not in the source is an error, never
        a no-op."""
        with pytest.raises(AssertionError):
            _mutant_module(
                ORIGIN_SOURCE_PATH, "this string is not in the module", "x",
                "j82_mutant_absent",
            )
