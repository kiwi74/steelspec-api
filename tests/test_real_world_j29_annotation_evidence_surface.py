"""
J29 — the read-only PDF annotation evidence surface, proved over a real drawing set.

WHAT THIS FILE IS

The proof that an authenticated project owner can INSPECT the PDF annotation evidence
J28 recorded for their own project, and that nobody else can: one read-only route,

    GET /production/review/{project_id}/annotations[?page=N]

over the rows J28's storage layer declares, through the repository read that owns this
table's transport, rendered by one page function that emits no form, no input and no
control.

WHAT IT PROVES, AND WHAT IT DOES NOT

It proves that J29 DISPLAYS WHAT J28 EXTRACTED. It does not prove — and this surface does
not claim — which physical member, if any, an annotation refers to. An occurrence is a mark
drawn on a page: the tests below assert that every recorded field reaches the page under
J28's own name and with J28's own value, and they assert nothing about member identity,
because nothing in this layer establishes one.

WHAT IS REAL, AND WHAT IS DOUBLED

    REAL      the route, the J19 identity and authorization boundary, the repository's own
              read (its `.table()`, its column list, its project filter), J28's storage
              authority over which reading stands for a page, J29's composition, and the
              renderer. The credentials are genuine ES256 tokens verified against a real
              in-process JWKS document (J19's own harness). The evidence is a GENUINE
              reading of the real Arkles Strand lodged drawing set.
    DOUBLED   the database. `_ReadOnlyStore` answers the annotation table's read and raises
              on every write method the production client exposes, so "rendering writes
              nothing" is proved over a client that could have written.

The reading is taken ONCE for the module: it is a real five-page extraction of a real
lodged set. Where the document is absent the section SKIPS — it never substitutes a
fixture and never claims a pass it did not earn.

THE STORE IS APPLIED, AND THAT IS NOW OBSERVED RATHER THAN ASSUMED

The J28 migration was applied to the deployed database by J37C-2, so the table exists.
The surface still renders a store that is not there as its own named state and creates
nothing — that branch is a branch of the reader, and the tests below still prove it over a
client that answers the client's own refusal — but the live read is no longer expected to
land in it. That read now reaches the deployed table, and it skips only where this machine
has no live configuration at all, never in place of a read it could have made.
"""
from __future__ import annotations

import ast
import copy
import html
import inspect
import json
import re
import types
from pathlib import Path

import pytest

from app.drawing_reading import pdf_annotation_extractor as extractor
from app.engineering_data import pdf_annotation_evidence as storage

from tests import production_review_auth as auth
from tests import test_real_world_j4_production_extraction_report_truth as j4
from tests import test_real_world_j19_production_review_binding as j19
from tests import test_real_world_j20_connection_review_persistence_gap as j20

production = j4.production

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "production_annotation_evidence.py"
RENDER_PATH = REPO / "app" / "review_ui" / "render.py"
REPOSITORY_PATH = REPO / "app" / "engineering_data" / "repository.py"

ANNOTATIONS_ROUTE = "/production/review/{project_id}/annotations"

REAL_PDF = Path("/Users/chad/Downloads/24633 33 Arkles Strand - Plans 17.08.26 (lodged).pdf")

needs_real_pdf = pytest.mark.skipif(
    not REAL_PDF.exists(),
    reason=f"the real Arkles PDF is not at {REAL_PDF}. This is NOT a claim that the drawings "
           "contain no annotations — it is the absence of the document.",
)

#: The five real pages this file reads. They are the pages the J28 reading actually holds
#: this evidence on, established by reading the document rather than assumed: repeated
#: marks, schedule rows and leaders present AND absent on 6, 8, 11 and 21; the two recorded
#: overprints on 11; the occurrences the grammar could not read on 35.
PROOF_PAGES = (6, 8, 11, 21, 35)

PROJECT = "j29-arkles-project"
DRAWING = "j29-arkles-drawing"
#: The extraction invocation the proof reading was taken by. Required by the storage layer
#: since J33: an occurrence is recorded with the attempt that found it, never without one.
RUN = "j29-arkles-run"
#: A second invocation of the same page under the same rules — the ordinary retry. Its
#: occurrences are identical to RUN's, which is exactly what the extractor's determinism
#: means and exactly what J33 made storable.
LATER_RUN = "j29-arkles-run-2"
OWNER = "j29-arkles-owner"
OTHER_OWNER = "j29-some-other-reviewer"
OTHER_PROJECT = "j29-some-other-project"

#: A project that EXISTS in the deployed database, for the one live read below. It is the
#: Arkles project the J37C-2 writer smoke test recorded its occurrence against, and it is
#: named by its real id because the live read has to name something the database holds —
#: the previous probe's invented name was not a uuid at all, so the read never reached the
#: table and the test skipped whatever the table's state was. Only this project's EXISTENCE
#: is relied on: the assertions below are the reader's contract, and never what any
#: particular project happens to hold.
LIVE_PROJECT = "aa1ebc53-90fd-4ed9-8d62-b362079f7319"

#: What the database's own default would assign. `occurrence_rows` deliberately does not set
#: it, so a double standing in for the table has to, exactly as the table does.
EXTRACTED_AT = "2026-09-27T00:00:00+00:00"

#: How this renderer prints a field the record does not state. Not a blank cell: the page
#: says the record is silent rather than leaving a gap that reads as a missing value.
NOT_STATED = '<span class="note">(not stated)</span>'

#: How the page prints a recorded boolean: the JSON form it is recorded in, not "True".
TRUE = json.dumps(True)
FALSE = json.dumps(False)

MEMBER_VOCABULARY = ("member_id", "placement_id", "is_steel_member", "same_member",
                     "steel_members", "member_placements", "placement")


# ===========================================================================
# The doubles.
# ===========================================================================
class _ProjectQuery:
    """`projects.select(...).eq("id", …).single().execute()` — the HTTP layer's own read."""

    def __init__(self, rows):
        self.rows = rows
        self.project_id = None

    def select(self, *columns):
        return self

    def eq(self, column, value):
        assert column == "id", column
        self.project_id = value
        return self

    def single(self):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self.rows.get(self.project_id))


class _ProjectsClient:
    """The HTTP layer's client: it answers the project read and refuses every other table.

    The route authorizes from the row it loads and reads nothing else, so a request that
    reached for another table on this client is an assertion failure rather than a silently
    answered query.
    """

    def __init__(self, rows):
        self.rows = dict(rows)
        self.tables_read: list[str] = []

    def table(self, name):
        self.tables_read.append(name)
        assert name == "projects", f"the HTTP layer read {name!r}"
        return _ProjectQuery(self.rows)


class _ReadQuery:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.filters: list[tuple] = []

    def select(self, *columns):
        self.store.calls.append(("select", self.name, tuple(columns)))
        return self

    def eq(self, column, value):
        self.store.calls.append(("eq", self.name, column))
        self.filters.append((column, value))
        return self

    def execute(self):
        matched = [
            row for row in self.store.tables.get(self.name, [])
            if all(row.get(column) == value for column, value in self.filters)
        ]
        return types.SimpleNamespace(data=copy.deepcopy(matched))


class _ReadOnlyStore:
    """The annotation table as a GET may use it: reads only.

    Every write method the production client exposes raises with its own name, so a request
    that tried to record, resolve or advance anything fails here saying which call it made.
    The read sequence is recorded, which is how "one select, on one table, with the declared
    columns, filtered by the authorized project" is checked.
    """

    WRITES = ("insert", "update", "upsert", "delete", "rpc")

    def __init__(self, tables=None):
        self.tables = dict(tables or {})
        self.calls: list[tuple] = []

    def table(self, name):
        self.calls.append(("table", name))
        return _ReadQuery(self, name)

    def __getattr__(self, name):
        if name in _ReadOnlyStore.WRITES:
            raise AssertionError(f"a GET called the write method {name!r}")
        raise AttributeError(name)


class _LiveReadsOnly:
    """The REAL production repository, as the live read below may use it: reads only.

    `_ReadOnlyStore` gives this guarantee to the doubled requests. This gives it to the one
    request that goes to the deployed database: the read it forwards is the repository's
    own, unsubstituted, and every write verb the production client exposes raises here
    naming itself, so a live read that tried to record anything would fail saying which
    call it made. The calls are kept, which is how "one read, of one project, through the
    read function and nothing else" is asserted rather than assumed.
    """

    WRITES = _ReadOnlyStore.WRITES

    def __init__(self, repository):
        self.repository = repository
        self.calls: list[str] = []
        self.reads: list[str] = []

    def pdf_annotation_occurrences_for_project(self, project_id):
        self.calls.append("pdf_annotation_occurrences_for_project")
        self.reads.append(project_id)
        return self.repository.pdf_annotation_occurrences_for_project(project_id)

    def __getattr__(self, name):
        if name in _LiveReadsOnly.WRITES:
            raise AssertionError(f"the live read called the write method {name!r}")
        raise AttributeError(name)


def _project_row(project_id, owner):
    return {"id": project_id, "user_id": owner, "status": "review"}


def _tripwires(production, monkeypatch):
    """Every writer a request must not reach, made to fail loudly if it does."""
    from app.engineering_data import connection_review_repository as review_store
    import app.cad_engine.project_workflow as project_workflow

    def refuse(name):
        def tripwire(*args, **kwargs):
            raise AssertionError(f"a GET reached the writer {name}")
        return tripwire

    monkeypatch.setattr(
        production.repository, "insert_pdf_annotation_occurrences",
        refuse("insert_pdf_annotation_occurrences"),
    )
    monkeypatch.setattr(review_store, "persist_review_snapshot", refuse("persist_review_snapshot"))
    monkeypatch.setattr(review_store, "record_project_review", refuse("record_project_review"))
    monkeypatch.setattr(
        project_workflow, "resolve_project_connection", refuse("resolve_project_connection"),
    )
    monkeypatch.setattr(
        project_workflow, "refresh_project_workflow", refuse("refresh_project_workflow"),
    )


def _surface(production, monkeypatch, *, tables, projects, owner):
    """The real application, the real route, and one authenticated client.

    The HTTP layer's own project read is answered by a client that refuses every table but
    `projects`; the annotation table is answered by the recording read-only store, patched
    into the repository's own client global so the REAL read function runs. The composition
    and the renderer are the production ones, unsubstituted.
    """
    client = _ProjectsClient(projects)
    monkeypatch.setattr(production.main, "supabase", client)
    store = _ReadOnlyStore(tables)
    monkeypatch.setattr(production.repository, "supabase", store)
    _tripwires(production, monkeypatch)

    tokens = auth.install(monkeypatch, production)
    from fastapi.testclient import TestClient

    def url(project_id=PROJECT, page=None):
        base = f"/production/review/{project_id}/annotations"
        return base if page is None else f"{base}?page={page}"

    return types.SimpleNamespace(
        client=client,
        store=store,
        tokens=tokens,
        owner=owner,
        http=TestClient(production.main.app, headers=tokens.headers(owner)),
        bare=TestClient(production.main.app),
        other=TestClient(production.main.app, headers=tokens.headers(OTHER_OWNER)),
        url=url,
    )


# ===========================================================================
# The real document, read once.
# ===========================================================================
@pytest.fixture(scope="module")
def reading():
    """One genuine reading of the five pages this file proves over."""
    if not REAL_PDF.exists():  # pragma: no cover - guarded by needs_real_pdf
        pytest.skip("the real Arkles PDF is not present on this machine")
    return extractor.extract_annotations(REAL_PDF, pages=PROOF_PAGES)


@pytest.fixture(scope="module")
def rows(reading):
    """The rows J28 would record for that reading, with the table's own default added.

    `extracted_at` is the database's default and is not set by `occurrence_rows`, so a
    double standing in for the table assigns it — one instant for the whole reading, which
    is what one insert of one reading produces.
    """
    built = storage.occurrence_rows(reading, drawing_id=DRAWING, project_id=PROJECT,
                                    analysis_run_id=RUN)
    assert built, "the real reading produced no rows to prove over"
    return [{**row, "extracted_at": EXTRACTED_AT} for row in built]


@pytest.fixture()
def arkles(production, monkeypatch, rows):
    return _surface(
        production, monkeypatch,
        tables={storage.ANNOTATION_TABLE: rows},
        projects={
            PROJECT: _project_row(PROJECT, OWNER),
            OTHER_PROJECT: _project_row(OTHER_PROJECT, OTHER_OWNER),
        },
        owner=OWNER,
    )


@pytest.fixture()
def page_text(arkles):
    response = arkles.http.get(arkles.url())
    assert response.status_code == 200, response.text
    return response.text


# ===========================================================================
# Reading the rendered page back — the page is parsed, never taken on trust.
# ===========================================================================
def _table_rows(text):
    """Every occurrence row the page rendered, as the page states it.

    Every table on the page is read, not just the first: a project with several drawings
    renders one table per drawing and page, and a test that read only the first would pass
    while the rest were wrong.
    """
    rendered = []
    for body in re.findall(r"<tbody>(.*?)</tbody>", text, re.S):
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
            cells = re.findall(r"<td>(.*?)</td>", row, re.S)
            assert len(cells) == 10, f"an occurrence row printed {len(cells)} cells"
            pre = re.findall(r"<pre>(.*?)</pre>", cells[9], re.S)
            assert len(pre) == 2, "an occurrence row did not print both recorded documents"
            rendered.append({
                "cells": [html.unescape(cell) for cell in cells[:9]],
                "evidence": json.loads(html.unescape(pre[0])),
                "rule_set": json.loads(html.unescape(pre[1])),
            })
    return rendered


def _standing(rows):
    """The rows that stand for each page, in the order the record's own rule puts them.

    Computed here INDEPENDENTLY of the storage layer's own sort — page, then x, then y —
    so the order this file expects is not read back out of the code it is testing.
    """
    return sorted(rows, key=lambda row: (row["page_number"], row["annotation_x"],
                                         row["annotation_y"]))


def _as_shown(value):
    """One recorded value as the page must print it.

    The page's rule, restated here so the expectation is not read out of the code: a string
    is shown verbatim, anything else as its own JSON — which is how a boolean recorded in a
    boolean column reads as `true`, the form JSON gives it — and a value the record does not
    state is shown as absent rather than as an empty cell.
    """
    if value is None:
        return NOT_STATED
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _expected_cells(row):
    """The nine cells the page must print for one row, in the table's own column order."""
    return [
        _as_shown(value) for value in (
            row["mark_candidate"], row["mark_readable"], row["annotation_x"],
            row["annotation_y"], row["operator_count"], row["duplicate_operator_count"],
            row["schedule_row_candidate"], row["tag_box_present"], row["leader_present"],
        )
    ]


def _as_recorded(value):
    """A value as it round-trips through the JSON column it is stored in."""
    return json.loads(json.dumps(value))


# ===========================================================================
# A. Authorization — J19's boundary, on this route.
# ===========================================================================
class TestAuthorization:
    def test_the_route_requires_a_credential_and_reads_nothing_without_one(self, arkles):
        response = arkles.bare.get(arkles.url())
        assert response.status_code == 401
        assert response.json()["detail"]["refusal"] == "IDENTITY_NO_TOKEN"
        assert arkles.store.calls == [], arkles.store.calls
        assert arkles.client.tables_read == [], arkles.client.tables_read

    def test_a_token_this_project_did_not_issue_is_refused_before_any_read(self, arkles):
        headers = {"Authorization": f"Bearer {arkles.tokens.foreign_token(OWNER)}"}
        response = arkles.http.get(arkles.url(), headers=headers)
        assert response.status_code == 401
        assert arkles.store.calls == [], arkles.store.calls

    def test_a_malformed_token_is_refused(self, arkles):
        headers = {"Authorization": "Bearer not-a-token-at-all"}
        assert arkles.http.get(arkles.url(), headers=headers).status_code == 401
        assert arkles.store.calls == [], arkles.store.calls

    def test_another_reviewer_cannot_read_this_projects_evidence(self, arkles):
        response = arkles.other.get(arkles.url())
        assert response.status_code == 403
        assert arkles.store.calls == [], arkles.store.calls

    def test_an_unknown_project_is_not_found(self, arkles):
        assert arkles.http.get(arkles.url("j29-no-such-project")).status_code == 404
        assert arkles.store.calls == [], arkles.store.calls

    def test_the_owner_of_another_project_cannot_read_this_one(self, arkles):
        """Authorization is the project's own owner, not merely any signed-in reviewer."""
        headers = arkles.tokens.headers(OTHER_OWNER)
        assert arkles.http.get(arkles.url(PROJECT), headers=headers).status_code == 403
        assert arkles.http.get(arkles.url(OTHER_PROJECT), headers=headers).status_code == 200

    def test_no_request_field_can_assert_an_owner(self, arkles):
        """The owner is the token's subject. Nothing a client sends can change that."""
        for query in ("?owner=" + OWNER, "?user_id=" + OWNER, "?project_id=" + OTHER_PROJECT):
            assert arkles.other.get(arkles.url() + query).status_code == 403, query
        assert arkles.store.calls == [], arkles.store.calls

    def test_the_read_is_filtered_by_the_project_that_was_authorized(self, arkles, page_text):
        reads = [call for call in arkles.store.calls if call[0] == "table"]
        assert reads == [("table", storage.ANNOTATION_TABLE)], arkles.store.calls
        assert ("select", storage.ANNOTATION_TABLE,
                (",".join(storage.ANNOTATION_COLUMNS),)) in arkles.store.calls
        assert ("eq", storage.ANNOTATION_TABLE, "project_id") in arkles.store.calls

    def test_the_project_read_is_the_only_other_table_touched(self, arkles, page_text):
        assert arkles.client.tables_read == ["projects"], arkles.client.tables_read


# ===========================================================================
# B. The page IS the record — every field, every row, field for field.
# ===========================================================================
class TestThePageIsTheRecord:
    def test_the_page_shows_every_standing_occurrence_and_no_other(self, rows, page_text):
        assert len(_table_rows(page_text)) == len(rows)

    def test_two_attempts_of_one_page_show_only_the_reading_that_stands(
            self, production, monkeypatch, rows):
        """J33 made a page's re-reads storable. J29 still shows exactly ONE of them.

        The second attempt below is the ordinary retry: identical occurrences, same rule
        set, a later instant. Both are in the store. The page must render one reading's
        worth of rows, and they must be the LATER attempt's — which is what its own
        boundary sentence promises ("earlier readings stay recorded and are not shown
        here"). The attempt is provenance and is not displayed at all.
        """
        later_at = "2026-09-27T05:00:00+00:00"
        later = [{**row, "analysis_run_id": LATER_RUN, "extracted_at": later_at}
                 for row in rows]
        assert len(later) == len(rows)

        surface = _surface(
            production, monkeypatch,
            tables={storage.ANNOTATION_TABLE: [*rows, *later]},
            projects={PROJECT: _project_row(PROJECT, OWNER)},
            owner=OWNER,
        )
        response = surface.http.get(surface.url())
        assert response.status_code == 200, response.text
        text = response.text

        # One reading, not two: the later attempt's rows stand and the earlier are hidden.
        assert len(_table_rows(text)) == len(rows)
        assert later_at in text
        assert EXTRACTED_AT not in text
        # No historical-attempt UI, and no attempt id on the page: the run is provenance,
        # and this surface does not present attempts.
        assert RUN not in text
        assert LATER_RUN not in text
        # And it is still a read: the whole sequence is the read sequence, and no write
        # method exists to call — `_ReadOnlyStore` raises if one is reached at all.
        assert all(call[0] in ("table", "select", "eq") for call in surface.store.calls), \
            surface.store.calls
        assert ("select", storage.ANNOTATION_TABLE,
                (",".join(storage.ANNOTATION_COLUMNS),)) in surface.store.calls

    def test_every_printed_field_is_the_records_own_value(self, rows, page_text):
        """Nine cells per row, compared to the row, in the table's own column order."""
        rendered = _table_rows(page_text)
        expected = _standing(rows)
        assert len(rendered) == len(expected)
        for printed, row in zip(rendered, expected):
            assert printed["cells"] == _expected_cells(row), row

    def test_the_reading_printed_is_the_extractors_own_payload(self, reading, page_text):
        """The record shown is the extractor's OWN payload, key for key.

        The expectation is `occurrence_payload` called again in this test — not the row the
        storage layer built — so the page cannot agree with itself by construction.
        """
        payloads = {
            (occurrence.page_number, occurrence.annotation_x, occurrence.annotation_y):
                _as_recorded(extractor.occurrence_payload(occurrence))
            for occurrence in reading.occurrences
        }
        printed = _table_rows(page_text)
        assert len(printed) == len(payloads)
        for row in printed:
            key = (row["evidence"]["page_number"], row["evidence"]["annotation_x"],
                   row["evidence"]["annotation_y"])
            assert key in payloads, key
            assert row["evidence"] == payloads[key], key

    def test_the_reading_carries_the_whole_j28_contract(self, page_text):
        """Every key J28 defines is present, and the page shows all of them."""
        printed = _table_rows(page_text)
        assert printed
        keys = set(printed[0]["evidence"])
        assert len(keys) == 21, sorted(keys)
        for name in ("raw_text_run", "font", "font_size", "text_op_index", "text_matrix",
                     "ctm", "tag_box_geometry", "leader_endpoint", "leader_chain_nodes",
                     "leader_truncated", "nearby_text_runs", "schedule_row_candidate"):
            assert name in keys, name
        assert all(set(row["evidence"]) == keys for row in printed)

    def test_the_rule_set_is_shown_as_the_record_carries_it(self, rows, page_text):
        expected = {json.dumps(row["rule_set"], ensure_ascii=False) for row in rows}
        assert len(expected) == 1, "one reading was recorded under more than one rule set"
        printed = _table_rows(page_text)
        assert printed
        for row in printed:
            assert json.dumps(row["rule_set"], ensure_ascii=False) in expected

    def test_the_provenance_is_the_records_own(self, rows, page_text):
        digest = rows[0]["source_pdf_sha256"]
        assert len(digest) == 64
        for token in (EXTRACTED_AT, extractor.EXTRACTOR_VERSION, digest):
            assert token in page_text, token
        for row in _table_rows(page_text):
            assert row["evidence"]["page_number"] in PROOF_PAGES

    def test_the_pages_are_grouped_by_drawing_and_page(self, rows, page_text):
        printed = re.findall(r"<h2>Drawing (.*?) · page (\d+)</h2>", page_text)
        expected = sorted({(DRAWING, row["page_number"]) for row in rows})
        assert [(drawing, int(page)) for drawing, page in printed] == expected, printed


# ===========================================================================
# C. The real Arkles evidence — what the drawings actually say.
# ===========================================================================
@needs_real_pdf
class TestTheRealEvidenceIsDisplayed:
    def test_the_repeated_mark_is_displayed_at_every_place_it_was_drawn(
        self, rows, page_text,
    ):
        """The brief's repeated-mark item: one mark, many places, each its own occurrence."""
        repeated = [row for row in rows if row["mark_candidate"] == "P4"]
        assert len(repeated) >= 10, f"only {len(repeated)} P4 occurrences on {PROOF_PAGES}"
        assert len({row["page_number"] for row in repeated}) >= 3
        assert len(repeated) == len({(row["page_number"], row["annotation_x"],
                                      row["annotation_y"]) for row in repeated})
        printed = [row for row in _table_rows(page_text) if row["cells"][0] == "P4"]
        assert len(printed) == len(repeated)

    def test_the_schedule_evidence_is_displayed_in_all_three_of_its_states(
        self, rows, page_text,
    ):
        """The schedule-column reading, which is tri-state: read true, read false, or not
        read at all — and the third is a reading of its own, never folded into the second."""
        scheduled = [row for row in rows if row["schedule_row_candidate"] is True]
        unscheduled = [row for row in rows if row["schedule_row_candidate"] is False]
        unstated = [row for row in rows if row["schedule_row_candidate"] is None]
        assert scheduled and unscheduled and unstated, (
            len(scheduled), len(unscheduled), len(unstated),
        )
        printed = _table_rows(page_text)
        assert sum(1 for row in printed if row["cells"][6] == TRUE) == len(scheduled)
        assert sum(1 for row in printed if row["cells"][6] == FALSE) == len(unscheduled)
        assert sum(1 for row in printed if row["cells"][6] == NOT_STATED) == len(unstated)

    def test_the_overprints_are_shown_as_one_occurrence_each_with_their_copy_count(
        self, rows, page_text,
    ):
        """A mark drawn twice is ONE occurrence with the copy counted — never two rows."""
        overprints = [row for row in rows if row["duplicate_operator_count"] > 0]
        assert len(overprints) >= 2
        assert {"BF2", "S202"} <= {row["mark_candidate"] for row in overprints}
        assert all(row["operator_count"] >= 1 for row in overprints)
        printed = [row for row in _table_rows(page_text) if row["cells"][5] != "0"]
        assert len(printed) == len(overprints)
        assert all(row["evidence"]["duplicate_operator_count"] > 0 for row in printed)

    def test_an_occurrence_with_a_leader_and_one_without_are_both_displayed(
        self, rows, page_text,
    ):
        with_leader = [row for row in rows if row["leader_present"] is True]
        without = [row for row in rows if row["leader_present"] is False]
        assert with_leader and without, (len(with_leader), len(without))
        printed = _table_rows(page_text)
        assert sum(1 for row in printed if row["cells"][8] == TRUE) == len(with_leader)
        assert sum(1 for row in printed if row["cells"][8] == FALSE) == len(without)
        endpoint = [row for row in printed if row["cells"][8] == TRUE
                    and row["evidence"]["leader_endpoint"] is not None]
        assert endpoint, "a leader was recorded with no endpoint to display"

    def test_the_unreadable_occurrences_are_displayed_as_unreadable(self, rows, page_text):
        """Page 35 holds the runs the grammar could not read. They stay on the page."""
        unreadable = [row for row in rows if row["mark_readable"] is False]
        assert unreadable, "no unreadable occurrence was recorded on these pages"
        assert all(row["page_number"] == 35 for row in unreadable)
        assert all(row["mark_candidate"] is None for row in unreadable)
        assert all(row["evidence"]["raw_text_run"] == "CCAH3.2treated" for row in unreadable)
        printed = [row for row in _table_rows(page_text) if row["cells"][1] == FALSE]
        assert len(printed) == len(unreadable)
        assert all(row["cells"][0] == NOT_STATED for row in printed)

    def test_a_mark_split_across_operators_is_displayed_with_its_operator_count(
        self, rows, page_text,
    ):
        split = [row for row in rows if row["operator_count"] > 1 and row["mark_candidate"]]
        assert split, "no run on these pages was assembled from more than one operator"
        assert any(row["mark_candidate"] == "P4" and row["operator_count"] == 2
                   for row in split)
        every_split = [row for row in rows if row["operator_count"] > 1]
        printed = [row for row in _table_rows(page_text) if row["cells"][4] != "1"]
        assert len(printed) == len(every_split)

    def test_the_tag_box_evidence_is_displayed_for_both_outcomes(self, rows, page_text):
        present = [row for row in rows if row["tag_box_present"]]
        absent = [row for row in rows if not row["tag_box_present"]]
        assert present and absent, (len(present), len(absent))
        printed = _table_rows(page_text)
        assert sum(1 for row in printed if row["cells"][7] == TRUE) == len(present)
        assert sum(1 for row in printed if row["cells"][7] == FALSE) == len(absent)
        with_geometry = [row for row in printed if row["cells"][7] == TRUE
                         and row["evidence"]["tag_box_geometry"] is not None]
        assert with_geometry, "a tag box was recorded with no geometry to display"


# ===========================================================================
# D. Read-only — proved over a client that could have written.
# ===========================================================================
class TestReadOnly:
    def test_the_page_has_no_form_and_no_input_and_no_script(self, page_text):
        lowered = page_text.lower()
        for forbidden in ("<form", "<input", "<textarea", "<button", "<script"):
            assert forbidden not in lowered, forbidden

    def test_no_write_method_was_reachable_from_the_transport(self, arkles, page_text):
        for name in _ReadOnlyStore.WRITES:
            with pytest.raises(AssertionError, match=name):
                getattr(arkles.store, name)

    def test_the_transport_issued_reads_only(self, arkles, page_text):
        assert [call[0] for call in arkles.store.calls] == ["table", "select", "eq"]

    def test_the_repository_read_names_no_writer(self):
        """The read this surface uses is one statement and no write verb."""
        tree = ast.parse(REPOSITORY_PATH.read_text())
        found = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "pdf_annotation_occurrences_for_project"
        ]
        assert len(found) == 1, "the read transport must exist exactly once"
        source = ast.unparse(found[0])
        assert ".select(" in source and ".eq(" in source
        for forbidden in ("insert", "update", "upsert", "delete", "rpc"):
            assert forbidden not in source, forbidden

    def test_nothing_the_requests_touched_was_mutated(self, arkles, rows, page_text):
        """The doubled table is byte-identical after two more requests."""
        before = json.dumps(arkles.store.tables, sort_keys=True, default=str)
        assert arkles.http.get(arkles.url()).status_code == 200
        assert arkles.http.get(arkles.url(PROJECT, page=11)).status_code == 200
        assert json.dumps(arkles.store.tables, sort_keys=True, default=str) == before

    def test_the_writers_the_request_must_never_reach_are_armed(
        self, production, arkles, page_text,
    ):
        """The read-only claim above is made over a harness whose tripwires actually fire."""
        with pytest.raises(AssertionError, match="insert_pdf_annotation_occurrences"):
            production.repository.insert_pdf_annotation_occurrences([])


# ===========================================================================
# E. No member identity — nothing here names one, holds one or shows one.
# ===========================================================================
class TestNoMemberIdentity:
    def test_the_composition_never_names_a_member(self):
        code = j19._code_only(MODULE_PATH)
        for forbidden in MEMBER_VOCABULARY:
            assert forbidden not in code, forbidden

    def test_the_rendered_section_never_names_a_member(self, tmp_path):
        """Only the section this milestone added, not the layers it borrows from."""
        source = RENDER_PATH.read_text()
        start = source.index("The annotation-evidence surface (J29)")
        end = source.index("# The page-exception surface (J18)")
        section = tmp_path / "j29_section.py"
        section.write_text(source[start:end])
        code = j19._code_only(section)
        for forbidden in MEMBER_VOCABULARY:
            assert forbidden not in code, forbidden

    def test_the_page_carries_no_member_field(self, page_text):
        lowered = page_text.lower()
        for forbidden in MEMBER_VOCABULARY:
            assert forbidden not in lowered, forbidden

    def test_the_page_states_what_an_occurrence_is_not(self, page_text):
        """The distinction is DISPLAYED, not merely absent: the page says it."""
        assert "is not a member" in page_text
        assert "annotation/leader evidence" in page_text
        assert "not an attachment point" in page_text

    def test_the_review_carries_no_field_that_could_hold_an_identity(self):
        """The composition's own records, enumerated: no field names a relationship."""
        import dataclasses

        import app.production_annotation_evidence as surface

        for record in (surface.AnnotationEvidenceOccurrence, surface.AnnotationEvidenceGroup,
                       surface.AnnotationEvidenceReview):
            names = {field.name.lower() for field in dataclasses.fields(record)}
            for forbidden in MEMBER_VOCABULARY:
                assert not [name for name in names if forbidden in name], \
                    (record.__name__, forbidden)

    def test_the_route_declares_no_member_language(self, production):
        paths = [path for _, path in j20._declared_routes(production.main.app)]
        assert ANNOTATIONS_ROUTE in paths
        for forbidden in ("member", "placement", "identity"):
            assert not [path for path in paths if forbidden in path], forbidden

    def test_nothing_here_scores_ranks_or_recommends(self):
        """No "best match", no confidence, no ordering of its own invention.

        The page names every drawing it was given and shows every standing occurrence of it,
        in the authority's own order. There is no selection anywhere in the module. The one
        thing it does order — the list of page NUMBERS the selector links to — is a set of
        numbers, not a ranking of occurrences, and it is asserted here rather than left
        implied.
        """
        code = j19._code_only(MODULE_PATH)
        for forbidden in ("score", "confidence", "best", "rank", "recommend", ".sort("):
            assert forbidden not in code, forbidden
        orderings = re.findall(r"sorted\(([^)]*)\)", code)
        assert orderings == ["{group.page_number for group in every_group}"], orderings


# ===========================================================================
# F. The store that is not there is a STATE, not a crash.
# ===========================================================================
class _AbsentTableError(Exception):
    """The shape PostgREST answers with when the table is not in its schema cache."""

    def __init__(self):
        super().__init__(
            "{'message': \"Could not find the table 'public.pdf_annotation_occurrences' in "
            "the schema cache\", 'code': 'PGRST205'}"
        )
        self.code = "PGRST205"


class _EmptyStore:
    def pdf_annotation_occurrences_for_project(self, project_id):
        return []


class _RefusingStore:
    def __init__(self, error):
        self.error = error

    def pdf_annotation_occurrences_for_project(self, project_id):
        raise self.error


class TestTheAbsentStore:
    def test_an_absent_table_renders_as_its_own_named_state(self, production, monkeypatch, rows):
        import app.production_annotation_evidence as surface

        request = _surface(
            production, monkeypatch, tables={},
            projects={PROJECT: _project_row(PROJECT, OWNER)}, owner=OWNER,
        )

        def absent(name):
            raise _AbsentTableError()

        monkeypatch.setattr(request.store, "table", absent)
        response = request.http.get(request.url())
        assert response.status_code == 200, response.text
        assert surface.ANNOTATION_STORE_ABSENT in response.text
        assert "not present in this deployment" in response.text
        assert "created nothing" in response.text
        assert "<table" not in response.text

    def test_absence_is_a_value_and_never_a_write(self):
        import app.production_annotation_evidence as surface

        review = surface.build_annotation_evidence_review(
            PROJECT, repository=_RefusingStore(_AbsentTableError()),
        )
        assert review.state_code == surface.ANNOTATION_STORE_ABSENT
        assert review.state_code in surface.ANNOTATION_SURFACE_STATES
        assert review.groups == () and review.pages == ()
        assert review.total_occurrences == 0 and review.shown_occurrences == 0
        assert "created nothing" in review.state_detail

    def test_a_failure_that_is_not_absence_is_not_dressed_up_as_one(self):
        """An unreachable database is a different fact and must not render as an absent
        table."""
        import app.production_annotation_evidence as surface

        with pytest.raises(ConnectionError):
            surface.build_annotation_evidence_review(
                PROJECT, repository=_RefusingStore(ConnectionError("unreachable")),
            )

    def test_a_project_with_nothing_recorded_says_so_without_claiming_absence(self):
        import app.production_annotation_evidence as surface

        review = surface.build_annotation_evidence_review(PROJECT, repository=_EmptyStore())
        assert review.state_code == surface.ANNOTATION_EVIDENCE_NONE
        assert surface.ANNOTATION_STORE_ABSENT not in review.state_detail

    def test_a_page_with_nothing_recorded_is_its_own_state(self, rows):
        import app.production_annotation_evidence as surface

        class _Store:
            def pdf_annotation_occurrences_for_project(self, project_id):
                return copy.deepcopy(rows)

        review = surface.build_annotation_evidence_review(
            PROJECT, page=99, repository=_Store(),
        )
        assert review.state_code == surface.ANNOTATION_EVIDENCE_NONE_FOR_PAGE
        assert review.shown_occurrences == 0
        assert review.total_occurrences == len(rows)
        # The page list is the project's WHOLE recorded set — the filter narrowed what is
        # shown, never what was read.
        assert review.pages == tuple(sorted({row["page_number"] for row in rows}))

    def test_the_live_store_is_read_and_holds_the_contract_it_reports(self, production):
        """The deployed database, read through the surface's own read path.

        The J28 migration is APPLIED (J37C-2), so the store is there and this read reaches
        it. What is asserted is the READER'S CONTRACT and nothing else: the store is
        present rather than reported absent, the read completes once through the
        repository's own read function, what was read and what is shown agree, and no write
        verb was reachable while it ran. Which rows a project holds is not this file's
        business — an empty project satisfies every assertion below.

        It skips only where this machine has no live configuration at all. It deliberately
        does NOT skip when the read fails: the store exists now, so a failed read is a real
        failure, and skipping on it is exactly how the previous probe stayed silent while
        the table's state changed underneath it.
        """
        if not production.live_config:
            pytest.skip("no live Supabase configuration available; the live test is "
                        "skipped rather than run against a synthetic database")
        import app.production_annotation_evidence as surface

        store = _LiveReadsOnly(production.repository)
        review = surface.build_annotation_evidence_review(LIVE_PROJECT, repository=store)

        # 1. The store is present. Its absence is a branch of the reader, and a table that
        #    exists is not what that branch answers: before J37C-2 this same read returned
        #    the client's PGRST205 and the surface rendered ANNOTATION_STORE_ABSENT.
        assert review.state_code in surface.ANNOTATION_SURFACE_STATES
        assert review.state_code != surface.ANNOTATION_STORE_ABSENT
        assert surface.ANNOTATION_STORE_ABSENT not in review.state_detail

        # 2. The read reached the deployed table — once, for this project, through the
        #    repository's read function and nothing else of the repository.
        assert store.reads == [LIVE_PROJECT]
        assert store.calls == ["pdf_annotation_occurrences_for_project"]

        # 3. Structurally valid: unfiltered, so nothing is hidden, and the page list is the
        #    shown groups' own pages — the module's own rule, not a restatement of it.
        assert review.page_filter is None
        assert review.shown_occurrences == review.total_occurrences
        assert review.pages == tuple(sorted({group.page_number for group in review.groups}))
        for group in review.groups:
            assert group.drawing_id
            assert group.page_number >= 1
            assert group.occurrences

        # 4. No mutation. Every write verb the production client exposes raises here rather
        #    than reaching the database, and the read above called none of them.
        for name in _LiveReadsOnly.WRITES:
            with pytest.raises(AssertionError, match=name):
                getattr(store, name)

    def test_the_migration_is_present_and_no_second_one_was_added(self):
        """The file this surface reads through exists, and applying it added no second
        migration. Its APPLIED state is not asserted from here — the live read above is
        what observes the deployed database, and that is the only place it can be seen.

        The count is eight, not seven: J44 and then J61 each later added one migration of
        their own, and each is named here so that a further one cannot appear unremarked.
        J61's names a source document's identity and points a drawing at it; it adds no
        annotation occurrence and this surface still reads only the J28 table."""
        assert (REPO / "supabase" / "migrations" /
                "20260927000000_j28_pdf_annotation_occurrences.sql").exists()
        migrations = sorted(path.name for path in (REPO / "supabase" / "migrations").glob("*.sql"))
        assert migrations == [
            "20260924000000_j5_section_resolution_truth.sql",
            "20260924010000_j6_reference_data_identity.sql",
            "20260924020000_j8b_connection_plate_evidence_nullability.sql",
            "20260925000000_j22_connection_review_persistence.sql",
            "20260925010000_j23_page_extraction_captures.sql",
            "20260927000000_j28_pdf_annotation_occurrences.sql",
            "20260928000000_j44_project_review_claims.sql",
            "20260929000000_j61_project_documents.sql",
            "20260929010000_j66_field_evidence_citations.sql",
            "20261006000000_l19_selected_extraction_lineage.sql",
        ], migrations


# ===========================================================================
# G. The filter, and determinism.
# ===========================================================================
class TestTheFilterAndDeterminism:
    def test_the_page_filter_narrows_what_is_shown_and_never_what_is_read(self, arkles, rows):
        response = arkles.http.get(arkles.url(PROJECT, page=11))
        assert response.status_code == 200, response.text
        assert len(_table_rows(response.text)) == sum(
            1 for row in rows if row["page_number"] == 11
        )
        # The read is the same unfiltered project read, whatever is being shown.
        assert [call[0] for call in arkles.store.calls] == ["table", "select", "eq"]
        assert ("eq", storage.ANNOTATION_TABLE, "project_id") in arkles.store.calls
        for page in sorted({row["page_number"] for row in rows}):
            assert f"?page={page}" in response.text

    def test_a_page_number_that_is_not_a_page_number_is_refused_by_the_route(self, arkles):
        for query in ("?page=0", "?page=-3", "?page=not-a-number"):
            assert arkles.http.get(arkles.url() + query).status_code == 422, query
        assert arkles.store.calls == [], arkles.store.calls

    def test_two_requests_render_the_same_bytes(self, arkles):
        assert arkles.http.get(arkles.url()).text == arkles.http.get(arkles.url()).text

    def test_the_filtered_page_is_stable_across_requests(self, arkles):
        first = arkles.http.get(arkles.url(PROJECT, page=11)).text
        second = arkles.http.get(arkles.url(PROJECT, page=11)).text
        assert first == second


# ===========================================================================
# H. Scope — what this milestone did and did not add.
# ===========================================================================
class TestScope:
    def test_the_route_is_get_only(self, production):
        methods = [
            tuple(sorted(route.methods))
            for route in production.main.app.routes
            if getattr(route, "path", None) == ANNOTATIONS_ROUTE
        ]
        assert methods == [("GET",)], methods

    def test_the_route_set_is_the_one_j20_freezes(self, production):
        assert j20._declared_routes(production.main.app) == j20.FROZEN_ROUTES
        assert ANNOTATIONS_ROUTE in [path for _, path in j20.FROZEN_ROUTES]

    def test_the_route_authorizes_before_it_reads(self, production):
        source = inspect.getsource(production.main.production_annotation_evidence)
        assert source.index("_authorized_project") < source.index(
            "build_annotation_evidence_review"
        )

    def test_the_module_holds_no_client_and_no_web_framework_at_import(self):
        tree = ast.parse(MODULE_PATH.read_text())
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert "app.supabase_client" not in imported
        assert not [name for name in imported if name.startswith("fastapi")]

    def test_the_module_reaches_no_model_a_file_or_the_network(self):
        code = j19._code_only(MODULE_PATH)
        for forbidden in ("anthropic", "openai", "requests.", "httpx", "socket",
                          "subprocess", "pypdf", "pdf2image", "TestClient"):
            assert forbidden not in code, forbidden

    def test_the_module_imports_no_second_authority(self):
        """Authorization, identity, the storage rule and the renderer are REUSED, not
        restated: no second owner rule, no second column list, no second page builder."""
        code = j19._code_only(MODULE_PATH)
        for forbidden in ("def authorize_project", "def reviewer_from_authorization",
                          "ANNOTATION_COLUMNS =", "def authoritative_occurrences",
                          "def occurrence_rows"):
            assert forbidden not in code, forbidden

    def test_the_renderer_grew_by_exactly_the_one_name_j25_declares(self):
        tree = ast.parse(RENDER_PATH.read_text())
        exported = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            ):
                exported = [element.value for element in node.value.elts]
        assert "render_annotation_evidence_page" in exported
        assert len(exported) == 10, exported


# ===========================================================================
# I. The existing surfaces are untouched by this milestone.
# ===========================================================================
class TestTheExistingSurfacesAreUnchanged:
    def test_the_internal_review_ui_still_renders(self):
        from fastapi.testclient import TestClient

        from app.review_ui.web import review_app

        assert TestClient(review_app).get("/").status_code == 200

    def test_the_other_production_review_routes_still_exist(self, production):
        paths = [path for _, path in j20._declared_routes(production.main.app)]
        assert "/production/review/{project_id}" in paths
        assert "/production/review/{project_id}/workflow" in paths
        assert "/production/review/{project_id}/pages/{page_number}/retry" in paths

    def test_no_collision_rule_and_no_extraction_contract_was_touched(self):
        """The two boundary rules J28's continuation and retry depend on, and J28's own
        extractor version, are still exactly what J28 declared."""
        pipeline_source = (REPO / "app" / "pipeline.py").read_text()
        for name in ("CONTINUATION_MEMBER_MARK_COLLISION", "RETRY_MEMBER_MARK_COLLISION"):
            assert name in pipeline_source, name
        assert extractor.EXTRACTOR_VERSION == "j28.1"
        assert storage.ANNOTATION_TABLE == "pdf_annotation_occurrences"
        # Milestone J33 added exactly one column — the attempt J23 already identifies a read
        # event by. Declared as a number so a second, undeclared column cannot ride in.
        assert len(storage.ANNOTATION_COLUMNS) == 18
        assert "analysis_run_id" in storage.ANNOTATION_COLUMNS
