"""
HISTORICAL GRADE CORRECTION — one frozen correction, executed once, under guard.

WHAT THIS IS
============
Milestone J8B removed a grade that two production paths wrote into EVERY row they
persisted, whether or not the drawing stated one:

    app/pipeline.py                    "grade": "300PLUS"   every steel_members row
    app/drawing_reading/dxf_parser.py  "grade": "300"      every connection_plates row

The code no longer writes either literal, and the honest writers now produce NULL
for a grade no drawing stated. The rows already written still carry the literal,
and a persisted grade is a claim about a drawing: those 128 member rows and 3
plate rows assert a material no drawing stated, and nothing downstream can tell
them apart from a grade that was really read.

The read-only audit that preceded this module (J8A) established, from the live
data and the whole recorded history of this repository, that ALL 128 member
values and ALL 3 plate values are application literals: no revision of this code
ever persisted an extracted member grade, no revision extracted a plate grade at
all, and no other column of any of those rows preserves recoverable evidence of
one. This module corrects them to NULL — which is a DIFFERENT persisted value from
a stated grade, and that difference is the entire point.

WHAT THIS MODULE REFUSES TO BE
==============================
  * NOT a general data-edit API. No caller supplies a table, a column, a value or
    a predicate: the table, the column, the value written and the exact row set
    are frozen constants below, and `apply_correction` refuses any plan that does
    not match them exactly.
  * NOT a "clean up grades" tool. `grade = '300PLUS'` is a GUARD here, never a
    SELECTOR: the row predicate is the pinned id set first, and the value only
    sharpens it. A row selected by its value alone is precisely the mistake this
    module exists to correct.
  * NOT a repair for anything else: it writes ONE column of the pinned rows, and
    proves afterwards that no other field of any row moved.

THE GUARDS — all must pass BEFORE a single row is written
========================================================
  1. the pinned id set is the one this module was written for: its own sha256
     equals the pinned digest, checked when the set is read and again in the plan
  2. no target id outside the pinned set — the row predicate IS the pinned set,
     and the pre-image proves the fabricated value is carried by no other row
  3. no missing target id — every pinned id exists in the table
  4. every target carries exactly the fabricated value right now
  5. the pre-image count of rows carrying it is exactly the accepted number
     (128 members, 3 plates), so a row the audit never saw cannot be swept up
  6. the pre-image holds NO row already NULL in that column, which is what makes
     the post-image NULL count a proof that EXACTLY these rows changed
  7. the table's row count is unchanged by the correction

THE PROOF AFTERWARDS
====================
A deterministic fingerprint over every NON-grade field of every row, taken before
and after: identical means nothing but the grade moved. Then the counts — every
target NULL, none left carrying the fabricated value, no other grade value
changed, the row count unchanged. The update is issued in bounded batches of
pinned ids, each carrying the same id-AND-current-value predicate, so a partial
failure can only leave rows uncorrected (reported, never silent) and can never
change a row the guards did not name.

THE CLIENT IS SUPPLIED, NEVER CREATED
=====================================
Nothing here imports the application's Supabase client, so this module can be
planned, applied and verified against any client — the live one, or an
in-process double. Planning reads; only `apply_correction` writes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


# ======================================================================================
# THE FROZEN FACTS. These are captured evidence, not configuration: the two id sets
# are the exact rows the J8A audit read, and the two digests are their identity.
# ======================================================================================
MEMBER_TABLE = "steel_members"
MEMBER_FABRICATED_GRADE = "300PLUS"
MEMBER_TARGET_COUNT = 128
MEMBER_ID_SET_SHA256 = "edd4bba4071863080f6e70858e7008f76745fc53cb415007b14130dc3439b492"

PLATE_TABLE = "connection_plates"
PLATE_FABRICATED_GRADE = "300"
PLATE_TARGET_COUNT = 3
PLATE_ID_SET_SHA256 = "5447f145dfc2b484b8bdcebe880137fdc15772d2e011d46926d063660ca596d1"

# How many pinned ids one statement may name. The predicate is the same pinned
# set and the same current-value guard at every batch size; the bound exists so a
# correction of 128 rows does not build a multi-kilobyte request URL.
BATCH_SIZE = 32

# sha256 over the sorted ids joined by "\n" — the identity of an id set.
_ID_SET_SEPARATOR = "\n"

# The one column this module may write, and the one value it writes.
CORRECTED_COLUMN = "grade"
CORRECTED_VALUE = None


class CorrectionRefused(RuntimeError):
    """A guard failed. Nothing was written, or the write was refused before it began."""


def _sha256_of_id_set(ids: Iterable[str]) -> str:
    return hashlib.sha256(_ID_SET_SEPARATOR.join(sorted(ids)).encode()).hexdigest()


@dataclass(frozen=True)
class _Target:
    label: str
    table: str
    ids: tuple[str, ...]
    fabricated_grade: str
    expected_count: int
    id_set_sha256: str

    def pinned_ids(self) -> frozenset[str]:
        if _sha256_of_id_set(self.ids) != self.id_set_sha256:
            raise CorrectionRefused(
                f"the pinned {self.label} id set does not hash to its pinned digest; "
                "refusing to correct rows this module was not written for"
            )
        if len(set(self.ids)) != self.expected_count or len(self.ids) != self.expected_count:
            raise CorrectionRefused(f"the pinned {self.label} id set is not {self.expected_count} distinct ids")
        return frozenset(self.ids)


def pinned_member_ids() -> frozenset[str]:
    """The 128 historical steel_members rows, verified against their pinned digest."""
    return MEMBER_TARGET.pinned_ids()


def pinned_plate_ids() -> frozenset[str]:
    """The 3 historical connection_plates rows, verified against their pinned digest."""
    return PLATE_TARGET.pinned_ids()

# ====================================================================
# THE PINNED ROW SETS — 128 historical steel_members rows and 3 historical
# connection_plates rows, exactly as the J8A read-only capture returned them.
# Their sha256 identities are above; pinned_*_ids() verify them on every use.
# ====================================================================
MEMBER_TARGET = _Target(
    label="member",
    table=MEMBER_TABLE,
    ids=(
        "05954b7b-2a29-437d-b06c-5117b53f84b0",
        "05aa39d3-2e50-46c3-9887-941e505c2634",
        "06066bab-e510-45ae-bece-e72f3eb9dd85",
        "06c4f44d-3045-4334-93c0-3a495ec25f1c",
        "08a1a003-39f0-42fc-8db9-67858465ac99",
        "09715ad3-6ae9-49c0-8eef-c7ac3b878b97",
        "0a34e736-596d-4252-b864-1131bf9e7326",
        "0a915d2e-530b-436d-a65a-ee29916ea553",
        "11bd06a9-c27f-4b5c-9ffe-badff7926143",
        "145c0356-4c39-4d4b-aee1-6d2a849305b3",
        "14d284e3-df46-4bae-bd7a-968de0b62404",
        "174c7cac-807d-46ed-86cd-3294cc7a15c2",
        "18e00e48-d780-4530-9bd3-67e5b3674f55",
        "1ccb8f93-ac2e-4763-9854-bde1cd766c41",
        "1d657cfe-06b0-4a08-a84f-e160fc1caa95",
        "211a7f56-51db-4117-8bb1-b56a3cc8608b",
        "23db77f7-e905-42c8-a6ce-bb50c3959e4e",
        "26079069-84b6-4a1c-9011-0cad8a650406",
        "26eff0ac-fccf-4edb-ac6c-e2ecdde23d0c",
        "293297c0-3603-40cd-b389-9366d099dbdb",
        "295eba59-543e-44c0-b40e-58303d406e6b",
        "2c9084d7-cb4f-48d0-ac52-dff336df38e2",
        "2cdf4d4a-1963-450f-abc9-af9f33fd5719",
        "2ebf76a7-6cff-4228-ae49-9fc3980278f2",
        "3213e332-9fff-4f04-9e63-51c7f57d55af",
        "33653708-5022-40c3-a980-676c1448f832",
        "33b62310-9503-4077-b8c1-5fbbdc924745",
        "38b58836-dfdf-4043-9fb0-a43ab6bc278a",
        "3be211f5-a729-466d-9419-8cb520edca24",
        "41e8698d-3386-42d6-b9a6-3af32e168451",
        "42367650-2107-41d0-abae-b4c9fe84c43a",
        "432bed43-7e40-4bda-948f-b0264b3ecb00",
        "487c7cd8-c842-4a7a-bc9d-127d9ad40f1e",
        "4a26e85b-f28c-4b16-b860-c364d4ef4b1c",
        "4a54416e-6edf-49b8-b136-ea377530470e",
        "4ae54a09-ebb5-458b-ad9d-9f35c2960d7e",
        "4b5d3a6e-774a-4c0c-823e-270ccbd51694",
        "4f8fd13d-1e5f-4472-835b-c0ba9fb68601",
        "53836b32-e741-487e-a5fe-db93386e17e5",
        "561e71f0-c818-43ea-8932-fd1e17489029",
        "56350d91-18a7-4e62-a442-722f4844b67a",
        "57f84616-1dbf-454b-895a-f5fbe54a9c52",
        "5a072084-715f-4d9a-8e5c-cb99666e50fe",
        "5b4a3942-2bd6-42e7-8d27-555e2bb1fe67",
        "5b7e68e6-3831-4549-84aa-228b60917505",
        "61debda4-0187-42fd-985b-b66e67670b28",
        "6735645c-cc05-4026-a4c6-b4c9ed0b725e",
        "6de10e46-1efb-4fb5-8c1e-7c71ec844e44",
        "6eb8cdd4-5340-4ae6-b377-f85155be17ea",
        "71f1c6d3-5125-43b6-93d2-e63b3985cc13",
        "720d9e59-6793-48fc-bd9a-dc82a9303f08",
        "731a6f96-c54e-4bad-a60d-88dbb7876653",
        "7421c3eb-476e-4788-b0b8-2b15e72e3d2b",
        "746dfd28-9e5e-4844-8fb8-b31f04d8c05e",
        "75b76144-c997-4767-8c5c-d1ef7b76eb97",
        "76d714f5-621a-44d0-a3ff-b2b86eb94bdd",
        "79e82036-4837-47cf-bfb2-21762401ed0e",
        "7b2744e8-4792-45d9-99d3-3658cafe32bf",
        "7cc4abb7-937d-45da-88e6-d696407bb176",
        "7d544726-a693-4f61-b84a-9c03bf6dbb4c",
        "7d83cad7-6678-4b31-94ba-8b53a0efa6c8",
        "80e3c8ad-dd06-4c60-9823-484ec2bb2aa2",
        "837aad2a-ae78-43b4-93cf-53f23475d25e",
        "89808870-7456-4a30-8655-99b9c45c8f2b",
        "8a81e367-29fd-487c-887c-afa7c56613f6",
        "8cb71d22-097b-463a-8a96-b857abd0d038",
        "8e9b2039-7696-4342-9d97-dfce36cb92e6",
        "9002c15a-72b0-47a9-bafc-a7eeba63aa69",
        "92563ad0-62a9-4bbc-9dbe-5a31b6ee6d32",
        "92caeaf5-37d6-4fd5-998c-358446e94f14",
        "93432d8e-7a9d-4b21-9ed6-eb292e234744",
        "9366bff4-7cd4-4744-844d-c8a5586cea29",
        "955ce67c-a4a6-4d8b-8d5b-8018e5e2ede9",
        "9a722bc2-cc53-41d1-91e7-f8d521804468",
        "9c9fc3dd-a744-49ac-8f7c-b7b95de3801b",
        "9d4e57ec-8925-43a6-abd8-08b7cf1ebbef",
        "9ede5c5f-f657-422b-b17e-51fca2b246e1",
        "9f149109-826c-4c76-92a8-140cfb0fdb0c",
        "a0cfbbbd-1074-4de4-a880-aede03680398",
        "a136f6f5-16e4-47f2-ae63-8f2b6837d829",
        "a198c839-4470-4ff5-a90c-3bf09d5d4bb9",
        "a3f4b19b-747b-4762-9303-05a6c55ae06d",
        "a618092f-ba31-4e19-a703-82bddda5d855",
        "a6e202fd-8e62-485a-9c93-b3a58357908f",
        "a73e6c4b-843e-416f-8433-f3a5cd3eef61",
        "ace87af6-05d3-478f-8e28-5db65fdae10a",
        "ae36838b-e27c-4945-89d9-da48d94b8831",
        "aea3e651-1e8f-4a37-8410-a6acd809cb3f",
        "afbd6b59-6188-443e-8574-b6db21200835",
        "b058fe4e-a7ce-401d-ba95-bc15eb633fd6",
        "b0d581ec-3f91-4a5a-b649-9e418c7e7ec3",
        "b32b11b3-5d63-43bb-9168-ca6277126bc4",
        "b503ba6e-bedd-468a-a44b-5e4099a1e4ef",
        "b984ab22-e598-4949-9d7d-8cb65966802b",
        "bb32891c-75a5-4a9f-ab03-e40dd87a6c7e",
        "bddcda96-9a5d-4b45-a48a-54b509497f80",
        "c2814f28-1e69-4531-93d8-7e19795c54aa",
        "c33fc68c-844e-427d-abcc-14fcc51681ce",
        "c7463b9c-84f9-497d-9bde-708847c6b0c7",
        "cc65f021-4ce3-48f9-989a-fbd4735e3eb2",
        "d04bfaca-8adf-4d0d-ad0d-e7aca486594e",
        "d6d616e2-9f08-48bf-9cb5-3659f777ada2",
        "d821d806-6692-4a30-a400-9ef059d10338",
        "d9cceb20-7a66-498a-ad21-5ac193cb24d2",
        "da030276-5069-4f8c-a723-6584d4aa6216",
        "db6bb0d0-ca73-4a58-a40e-be6893bd1a92",
        "ddbcc9c2-4f75-45be-8ff4-65a93ae52bf6",
        "de2d966e-9ab9-48df-9050-9c299586fe4f",
        "df252982-5cc8-41b2-9db8-e6e25e262e8f",
        "e05cc834-6d37-490f-8ac5-499ef91f8ac7",
        "e1fd9112-a6c0-4743-b169-dd8e1078f5ab",
        "e22c9ac7-1b2c-4e31-bd00-fd181ed02a58",
        "e2c40a69-9ccf-49b7-96b2-5d85b6f12659",
        "e34386ad-d896-4499-bfb8-a14e4cdd5db9",
        "e5cdb11a-c518-4ee8-8d22-8d81ad016dad",
        "e6128592-c28d-4f42-9e24-aa36695242da",
        "e7195b8c-61c6-4f0b-b406-521e5fa42df8",
        "e7a8a8c5-c9dd-4053-89cd-948ba70461f6",
        "e818214c-f606-49b7-a49b-56c111265367",
        "e8c4eaff-5b40-41ed-aef3-3a7430635b46",
        "e90c6811-7886-4477-85f5-5fe3a568ea68",
        "eb385f0f-e2c2-460e-82e3-6b7011bc4d8e",
        "ece46f5c-d4c4-48c0-8de9-b19555b73158",
        "f1422bd5-5c92-4bcc-8cf0-68e64a493438",
        "fc1242a6-b38b-4603-b405-e02649b7c2b3",
        "fd447dbd-dd1d-4646-a889-545ac34cfe34",
        "fd9338c2-7441-4705-ae96-48d0963c0e9e",
        "fd982a5d-9b0f-45b9-b654-ca5ac751a25b",
    ),
    fabricated_grade=MEMBER_FABRICATED_GRADE,
    expected_count=MEMBER_TARGET_COUNT,
    id_set_sha256=MEMBER_ID_SET_SHA256,
)

PLATE_TARGET = _Target(
    label="plate",
    table=PLATE_TABLE,
    ids=(
        "cbefa379-89e8-4b2d-b3e1-fc0d674b910e",
        "e2db830b-082c-41ea-b8b2-bf381794e976",
        "fae6aae3-333a-476b-a76b-bf9c00964773",
    ),
    fabricated_grade=PLATE_FABRICATED_GRADE,
    expected_count=PLATE_TARGET_COUNT,
    id_set_sha256=PLATE_ID_SET_SHA256,
)

_TARGET_BY_TABLE = {MEMBER_TABLE: MEMBER_TARGET, PLATE_TABLE: PLATE_TARGET}


@dataclass(frozen=True)
class CorrectionPlan:
    """What the guards found, and therefore what the correction will do."""
    label: str
    table: str
    ids: tuple[str, ...]
    fabricated_grade: str
    expected_count: int
    id_set_sha256: str
    rows_before: int
    rows_carrying_the_fabricated_grade: int
    rows_already_null: int
    other_grade_values: tuple[tuple[str, int], ...]
    non_grade_fingerprint: str

    @property
    def rows_to_correct(self) -> int:
        return len(self.ids)


@dataclass(frozen=True)
class CorrectionResult:
    """What the correction did, verified from a fresh read."""
    label: str
    table: str
    rows_requested: int
    batches: int
    rows_before: int
    rows_after: int
    rows_now_null: int
    rows_still_carrying_the_fabricated_grade: int
    other_grade_values_unchanged: bool
    non_grade_fingerprint_before: str
    non_grade_fingerprint_after: str

    @property
    def verified(self) -> bool:
        return (
            self.rows_now_null == self.rows_requested
            and self.rows_still_carrying_the_fabricated_grade == 0
            and self.rows_after == self.rows_before
            and self.other_grade_values_unchanged
            and self.non_grade_fingerprint_before == self.non_grade_fingerprint_after
        )


def _read_rows(client: Any, table: str) -> list[dict]:
    return list(client.table(table).select("*").execute().data or [])


def _non_grade_fingerprint(rows: Sequence[dict]) -> str:
    """
    A deterministic fingerprint of every NON-grade field of every row.

    Sorted by id, keys sorted, `grade` removed — so the fingerprint cannot change
    because of row order, key order or the one column this module is allowed to
    write. Two equal fingerprints mean nothing outside `grade` moved.
    """
    projection = [
        {key: value for key, value in sorted(row.items()) if key != CORRECTED_COLUMN}
        for row in sorted(rows, key=lambda r: str(r.get("id")))
    ]
    return hashlib.sha256(json.dumps(projection, sort_keys=True, default=str).encode()).hexdigest()


def _plan(client: Any, target: _Target) -> CorrectionPlan:
    pinned = target.pinned_ids()
    rows = _read_rows(client, target.table)
    present = {str(row["id"]) for row in rows}

    missing = sorted(pinned - present)
    if missing:
        raise CorrectionRefused(
            f"{len(missing)} pinned {target.label} id(s) are absent from {target.table}: refusing"
        )

    carrying = {str(row["id"]) for row in rows if row.get(CORRECTED_COLUMN) == target.fabricated_grade}
    unexpected = sorted(carrying - pinned)
    if unexpected:
        raise CorrectionRefused(
            f"{len(unexpected)} row(s) outside the pinned {target.label} set carry "
            f"{target.fabricated_grade!r}: the accepted audit no longer describes this table"
        )

    already_null = [str(row["id"]) for row in rows if row.get(CORRECTED_COLUMN) is None]
    if already_null:
        raise CorrectionRefused(
            f"{len(already_null)} {target.label} row(s) are already NULL: the post-image count "
            "could no longer prove that exactly the pinned rows changed"
        )

    if len(carrying) != target.expected_count:
        raise CorrectionRefused(
            f"{len(carrying)} row(s) carry {target.fabricated_grade!r}; the accepted pre-image is "
            f"{target.expected_count}"
        )

    others = sorted(
        (str(row.get(CORRECTED_COLUMN)), 1)
        for row in rows
        if row.get(CORRECTED_COLUMN) not in (None, target.fabricated_grade)
    )
    other_counts: dict[str, int] = {}
    for value, _ in others:
        other_counts[value] = other_counts.get(value, 0) + 1

    return CorrectionPlan(
        label=target.label,
        table=target.table,
        ids=tuple(sorted(pinned)),
        fabricated_grade=target.fabricated_grade,
        expected_count=target.expected_count,
        id_set_sha256=target.id_set_sha256,
        rows_before=len(rows),
        rows_carrying_the_fabricated_grade=len(carrying),
        rows_already_null=len(already_null),
        other_grade_values=tuple(sorted(other_counts.items())),
        non_grade_fingerprint=_non_grade_fingerprint(rows),
    )


def plan_member_correction(client: Any) -> CorrectionPlan:
    """Read-only: the guards, evaluated against the table as it is NOW."""
    return _plan(client, MEMBER_TARGET)


def plan_plate_correction(client: Any) -> CorrectionPlan:
    """Read-only: the guards, evaluated against the table as it is NOW."""
    return _plan(client, PLATE_TARGET)


def _batches(ids: Sequence[str]) -> list[list[str]]:
    return [list(ids[i:i + BATCH_SIZE]) for i in range(0, len(ids), BATCH_SIZE)]


def apply_correction(client: Any, plan: CorrectionPlan) -> CorrectionResult:
    """
    Execute the correction the plan describes, then verify it from a fresh read.

    The plan is re-derived at execution time, so the guards are evaluated against
    what is true NOW rather than against what was true when the plan was made. A
    pre-image that moved in between is a refusal, not a merge.
    """
    target = _TARGET_BY_TABLE.get(plan.table)
    if target is None or plan.ids != tuple(sorted(target.pinned_ids())) or (
        plan.fabricated_grade != target.fabricated_grade
    ):
        raise CorrectionRefused(
            "this plan is not one of the two frozen corrections this module is scoped to"
        )

    fresh = _plan(client, target)
    if fresh.non_grade_fingerprint != plan.non_grade_fingerprint:
        raise CorrectionRefused(
            "the table's non-grade content changed between planning and execution: refusing"
        )

    batches = _batches(fresh.ids)
    for batch in batches:
        (
            client.table(target.table)
            .update({CORRECTED_COLUMN: CORRECTED_VALUE})
            .in_("id", batch)
            .eq(CORRECTED_COLUMN, target.fabricated_grade)
            .execute()
        )

    after = _read_rows(client, target.table)
    after_counts: dict[str, int] = {}
    for row in after:
        value = row.get(CORRECTED_COLUMN)
        if value not in (None, target.fabricated_grade):
            key = str(value)
            after_counts[key] = after_counts.get(key, 0) + 1

    result = CorrectionResult(
        label=target.label,
        table=target.table,
        rows_requested=len(fresh.ids),
        batches=len(batches),
        rows_before=fresh.rows_before,
        rows_now_null=sum(1 for row in after if row.get(CORRECTED_COLUMN) is None),
        rows_still_carrying_the_fabricated_grade=sum(
            1 for row in after if row.get(CORRECTED_COLUMN) == target.fabricated_grade
        ),
        rows_after=len(after),
        other_grade_values_unchanged=tuple(sorted(after_counts.items())) == fresh.other_grade_values,
        non_grade_fingerprint_before=fresh.non_grade_fingerprint,
        non_grade_fingerprint_after=_non_grade_fingerprint(after),
    )
    if not result.verified:
        raise CorrectionRefused(f"the {target.label} correction did not verify: {result}")
    return result


__all__ = [
    "CorrectionPlan",
    "CorrectionRefused",
    "CorrectionResult",
    "MEMBER_FABRICATED_GRADE",
    "MEMBER_ID_SET_SHA256",
    "MEMBER_TABLE",
    "MEMBER_TARGET_COUNT",
    "PLATE_FABRICATED_GRADE",
    "PLATE_ID_SET_SHA256",
    "PLATE_TABLE",
    "PLATE_TARGET_COUNT",
    "apply_correction",
    "pinned_member_ids",
    "pinned_plate_ids",
    "plan_member_correction",
    "plan_plate_correction",
]
