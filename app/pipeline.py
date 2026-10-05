"""
Top-level orchestrator — the only module that coordinates across
ai_analysis, validation, and engineering_data. main.py stays thin
(HTTP concerns only); this owns the multi-stage flow:

    PDF -> ai_analysis (Claude vision, per page)
        -> validation (classify, consolidate, flag)
        -> engineering_data (persist)

Kept as a single top-level module rather than nested under any one
of the three stages, since it depends on all of them and belongs to
none.
"""
import logging
from dataclasses import dataclass

from app.ai_analysis.pdf_vision_analyzer import analyze_pdf_pages, page_count_of
from app.drawing_reading.pdf_annotation_extractor import (
    AnnotationExtractionRefused,
    extract_annotations,
    source_bytes,
    source_document_sha256,
)
from app.engineering_data import repository as repo
from app.engineering_data.page_extraction_capture import capture_rows
from app.production_extraction_source import SourceDocument, resolve_extraction_source
from app.engineering_data.pdf_annotation_evidence import (
    AnnotationEvidenceRefused,
    occurrence_rows,
)
from app.engineering_data.section_matcher import SectionMatcher, reference_data_projection
from app.validation.page_coverage import (
    COVERAGE_PREFIX,
    PageCoverage,
    coverage_from_warnings,
    coverage_of,
)
from app.validation.page_windows import (
    CONTINUATION_DOCUMENT_TOTAL_MISMATCH,
    CONTINUATION_DRAWING_SET_UNRESOLVED,
    CONTINUATION_FAILURES_UNRECORDED,
    CONTINUATION_MEMBER_MARK_COLLISION,
    CONTINUATION_NO_COVERAGE_RECORD,
    CONTINUATION_RECORD_CONFLICT,
    CONTINUATION_SOURCE_MISMATCH,
    ContinuationRefused,
    PageWindow,
    accumulate_coverage,
    check_page_window,
)
from app.validation.parse_failures import (
    PARSE_FAILURES_PREFIX,
    RETRY_EVIDENCE_ALREADY_PERSISTED,
    RETRY_MEMBER_MARK_COLLISION,
    RETRY_READ_MISMATCH,
    ParseFailures,
    RetryRefused,
    check_retry_request,
    failures_of,
    parse_failures_from_warnings,
)
from app.validation import connection_type_accounting
# Wave 3J Phase B — the persisted failure classification. `safe_failure_message` is the
# only value these boundaries may put into an `error_message` column.
from app.validation.failure_classification import safe_failure_message
from app.validation.project_status import derive_project_status, review_status_of
from app.validation.rules import (
    SUBSTITUTION_NOTE,
    SUFFIX_FALLBACK,
    validate_extraction,
)
from app.config import PDF_VISION_MODEL, MAX_PDF_PAGES

# Wave 3H Phase A — the module logger for this process's operator diagnostics.
#
# Deliberately unconfigured, for the reason stated at the same line in `app/main.py`:
# the standard library's last-resort handling routes WARNING and above to stderr, and
# uvicorn's default configuration leaves the root logger alone. No handler is attached
# here and none is needed.
logger = logging.getLogger(__name__)


# ======================================================================================
# EXTRACTED ENGINEERING EVIDENCE — preserved, never manufactured.
# ======================================================================================
# The production precedence this module enforces, in order:
#
#     drawing evidence  >  absence of evidence  >  catalogue defaults
#                       >  historical assumptions  >  hardcoded defaults
#
# The catalogue may supply standard properties ONLY through the authority rules
# in app/validation/rules.py — an EXACT resolution, never a refused suffix-fallback
# row. It may never supply a grade or a dimension the drawing did not state, and
# neither may a convention, a fixture, a filename or a default.
#
# The rules below exist because the persisted row is a claim about the drawing.
# A fabricated value there is indistinguishable from a stated one, and nothing
# downstream can tell them apart.

# The member-level keys a material designation can arrive under. The extraction
# schema (app/ai_analysis/pdf_vision_analyzer.py) asks for a material designation
# on a connection and states that members joined by it may carry it; it is never
# derived from anything else. A member row is therefore given a grade only from
# its OWN extracted material evidence — the connection's material is not applied
# to the members it joins, because the extraction reports it for the connection.
MEMBER_GRADE_KEYS = ("material", "grade")


def _extracted_grade(member: dict) -> str | None:
    """
    The member's grade as the drawing's own extraction reported it, or None.

    Explicit presence only. An absent key, a None, a blank string and a
    non-string are all ABSENCE, and absence is what gets persisted. Nothing
    here infers a grade from the section family, the section name, the
    project, a material default, the catalogue row, the filename, the NZ
    convention or any other indirect assumption — the drawing's extraction is
    the only source, and when it reported nothing the answer is None.

    The value is preserved VERBATIM: "AS/NZS 3678-300" is not normalised into
    anything, including "300PLUS".
    """
    for key in MEMBER_GRADE_KEYS:
        if key not in member:
            continue
        value = member[key]
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        if value is not None:
            # A non-string grade is not a designation the drawing stated, and
            # is never coerced into one.
            continue
    return None


# The plate-level keys a material designation can arrive under. The extraction
# contract asks a plate for its type and its dimensions ONLY — its plate example
# is {"type", "thickness_mm", "width_mm", "depth_mm"}, and every plate in every
# real capture carries exactly those four keys — so in production today this
# finds nothing and a plate grade is None. It is read the same explicit-presence
# way as a member's so that a genuine plate-grade reading, should the extraction
# ever state one, is preserved verbatim instead of being discarded.
PLATE_GRADE_KEYS = ("grade", "material")


def _extracted_plate_grade(plate: dict) -> str | None:
    """
    The plate's grade as the drawing's own extraction reported it, or None.

    The extraction contract states material at the CONNECTION level — the system
    prompt scopes it to the connection "or for the members it joins when clearly
    associated with them" — and gives a plate no material field at all. A
    connection's material is therefore NOT plate-grade evidence, and is not
    applied here: the extraction reported it for the connection, and spreading it
    across that connection's plates would manufacture a plate grade the drawing
    never stated. Nor is a grade derived from the plate's type, its thickness,
    its width or depth, the member grades, the section, the catalogue row, the
    project, the filename or any convention.

    Explicit presence only. An absent key, a None, a blank string and a
    non-string are all ABSENCE, and absence is what gets persisted. A stated
    value is preserved VERBATIM: "AS/NZS 3678-300" is not normalised into "300".
    """
    for key in PLATE_GRADE_KEYS:
        if key not in plate:
            continue
        value = plate[key]
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        if value is not None:
            # A non-string grade is not a designation the drawing stated, and
            # is never coerced into one.
            continue
    return None


def _extracted_plate_thickness(plate: dict) -> float | None:
    """
    The plate's thickness in mm as the extraction reported it, or None.

    Explicit presence/None semantics — never truthiness, which cannot tell
    "absent" from "zero" and so silently collapsed one into the other.

        positive number                -> preserved exactly
        absent / None / blank          -> None (unknown)
        zero, negative, non-numeric    -> None (invalid evidence, not a value)

    A structural plate thickness is a POSITIVE dimension. Zero is not a plate
    thickness, so it is refused as a value rather than stored as one: a
    persisted 0 is indistinguishable from a genuinely measured 0 mm plate, and
    is read downstream as a real dimension. Invalid evidence fails closed —
    the plate row is still kept for the fields the drawing did state (type,
    width, depth); only the thickness it did not validly state is withheld.
    """
    if "thickness_mm" not in plate:
        return None
    value = plate["thickness_mm"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return value if value > 0 else None


def parse_pdf_and_save(filepath: str, project_id: str, user_id: str, storage_path: str) -> dict:
    """
    Parses a PDF drawing set and writes validated results into
    Supabase against the given project_id. Same name and signature as
    the function this replaces, so the API layer's call site is
    unaffected by this restructure.
    """
    import os
    file_name = os.path.basename(storage_path)

    # Milestone J61 — the DOCUMENT this run reads, recorded before the lineage that reads it.
    #
    # The order is the whole of the design. A `drawings` row is created afresh by every
    # whole-document run, so it can never be the identity of a document; the document row is
    # therefore created FIRST, from the file's own bytes, and the drawing is created
    # pointing at it. Re-reading the same file resolves to the same document row, so a
    # repeated run is a repeat rather than a second document.
    #
    # The bytes are read once, here, from the file this run was handed — the same file
    # `_run_pipeline` will render. No second download is performed and no second hashing
    # rule is introduced: `source_document_sha256` is the J28 rule, over the J28 bytes.
    #
    # A source that cannot be read is NOT given an identity. A hash is never fabricated and
    # a document is never created from an unproven one, so the run continues with no
    # document — which is exactly the state every extraction was in before this milestone —
    # and fails downstream at the same point it always did.
    document_id = None
    try:
        payload = source_bytes(filepath)
    except AnnotationExtractionRefused:
        payload = None
    if payload is not None:
        document = repo.create_project_document(
            project_id,
            storage_path=storage_path,
            file_name=file_name,
            source_format="PDF",
            byte_size=len(payload),
            page_count=None,  # the document's own count is established by the reading, below
            content_sha256=source_document_sha256(payload),
        )
        document_id = document["id"]

    drawing_set = repo.create_drawing_set(project_id, file_name)
    drawing_set_id = drawing_set["id"]

    drawing = repo.create_drawing(drawing_set_id, file_name, storage_path,
                                  document_id=document_id)
    drawing_id = drawing["id"]

    analysis_run = repo.create_analysis_run(drawing_set_id, PDF_VISION_MODEL)
    analysis_run_id = analysis_run["id"]

    try:
        result = _run_pipeline(
            filepath, project_id, user_id, drawing_set_id, drawing_id,
            analysis_run_id=analysis_run_id,
        )
    except Exception as e:
        # Wave 3H Phase A — the operator diagnostic destination for this boundary.
        #
        # This handler marks the run and the set failed and then re-raises, so the same
        # exception also reaches `run_extraction`'s handler and is logged there too. Two
        # records for one failure is intended: they are two facts about two different
        # records — this one says the analysis run and the drawing set were marked failed,
        # that one says the project was — and both carry the same traceback.
        #
        # The message interpolates nothing, for the reason stated at the same boundary in
        # `app/main.py`.
        #
        # Wave 3J Phase B — the two persisted values are now the SteelSpec-owned
        # classification rather than `str(e)[:500]`. Both rows get the SAME value, and
        # they get it from one call, so the two columns cannot disagree about a failure
        # they both describe. The status, the write order, the re-raise and the fact that
        # exactly these two columns move are all unchanged.
        logger.exception("SteelSpec PDF drawing-set analysis failed")
        classification = safe_failure_message(e)
        repo.update_analysis_run(analysis_run_id, status="failed", error_message=classification)
        repo.update_drawing_set(drawing_set_id, status="failed", error_message=classification)
        raise

    # Milestone J15: `total_pages` states the size of the drawing set that was
    # uploaded, and `pages_processed` states how much of it this run read. They
    # were the same value before this milestone — `total_pages=result[
    # "pages_processed"]` — which is precisely how a 35-page set analysed to a
    # 30-page cap was persisted as a 30-page set, with nothing recording that
    # five pages were never looked at. When the document's own count could not
    # be established, the column keeps the value it was created with rather than
    # a 0 that would read like a count.
    page_totals = (
        {} if result["total_pages"] is None else {"total_pages": result["total_pages"]}
    )

    repo.update_analysis_run(
        analysis_run_id, status="completed",
        pages_processed=result["pages_processed"], completed_at="now()",
        **page_totals,
    )
    # Milestone J61 — the document's own page count, the same fact the drawing row is given
    # at the same seam (`update_drawing_meta` inside `_run_pipeline`). It is written HERE,
    # from the run's own reported total, rather than by threading a second identity through
    # the pipeline: one fact, one value, recorded once on each row that carries it. A run
    # that established no count passes `None`, which is the absence it is.
    if document_id is not None:
        repo.update_document_page_count(document_id, result["total_pages"])
    repo.update_drawing_set(
        drawing_set_id, status="analyzed",
        pages_analysed=result["analysed_pages"],
        members_found=result["members_extracted"], review_required_count=result["review_required_count"],
        **page_totals,
    )

    return result


def _persisted_notes(m: dict) -> str | None:
    """
    The notes column for one member row.

    A refused section substitution always states itself, even if consolidation
    rewrote the row's validation_note (it does, for a mark agreed across
    several pages): the catalogue candidate must never be reachable from a
    persisted row without the refusal that accompanies it.
    """
    if m.get("section_resolution") == SUFFIX_FALLBACK:
        return SUBSTITUTION_NOTE.format(
            token=m.get("section"), candidate=m.get("section_substituted_candidate"),
        )
    return m.get("validation_note") or (
        f"Grid: {m['grid_reference']}" if m.get("grid_reference") else None
    )


def _flatten_pages(pages, drawing_id: str) -> tuple[list[dict], list[dict]]:
    """Every page's raw members and raw connections, as two flat lists.

    Members carry their source page AND drawing ID (the provenance a persisted
    member row keeps); connections carry only the page number they were read
    from, which is the shape the connection row's `source_page` is written from.
    Shared by both entry points (Milestone J16) so a window's evidence is
    flattened by the same rules as a whole document's.
    """
    raw_members = []
    for p in pages:
        for m in p.raw_members:
            raw_members.append({**m, "source_page": p.page_number, "source_drawing_id": drawing_id})

    connections_raw = []
    for p in pages:
        for c in p.raw_connections:
            connections_raw.append({**c, "page_num": p.page_number})

    return raw_members, connections_raw


def _record_page_captures(pages, *, analysis_run_id: str, project_id: str,
                          drawing_set_id: str, drawing_id: str) -> int:
    """The raw AI readings of one run, recorded BEFORE any engineering row of that run.

    One call and one statement (see `repo.insert_page_extraction_captures`), so a run's
    readings are all recorded or none are — a half-recorded window would be
    indistinguishable from a run that genuinely read fewer pages.

    It is written first of the run's writes because the two have no shared transaction,
    and of the two orders only one fails safe. A capture that fails here aborts the run
    before a single member or connection row exists, so engineering evidence can never
    be persisted without the reading it came from. The other order leaves exactly that —
    engineering rows whose reading was never recorded — and on the retry path that state
    is permanent, because a page that already has evidence is refused a retry forever
    (`RETRY_EVIDENCE_ALREADY_PERSISTED`). The cost of this order is that a failure after
    the capture leaves readings for a window whose coverage record was never advanced:
    the window stays unread and re-readable, which is a state the record already
    describes, and the superseded attempt stays in the history rather than being lost.

    `model` is the constant this run's `analysis_runs` row was created with, read at
    the same call site — never today's configuration re-derived later, which would
    describe an old reading with a model that did not produce it.
    """
    return repo.insert_page_extraction_captures(
        capture_rows(
            pages,
            analysis_run_id=analysis_run_id,
            drawing_id=drawing_id,
            drawing_set_id=drawing_set_id,
            project_id=project_id,
            model=PDF_VISION_MODEL,
        )
    )


# ======================================================================================
# MILESTONE J36 — THE PDF'S OWN ANNOTATION READING, BESIDE THE AI'S
# ======================================================================================
# J28 reads the annotation occurrences a PDF contains by parsing the file's own content
# stream: text-showing operators, their matrices and the stroked vector geometry. It
# shares no input with the AI extraction — the same bytes are read a second way, and
# deterministically — and its result is filed against the SAME extraction invocation the
# page captures above already name. Nothing any run does next reads it back.
#
# POSITION, and this is the whole of the milestone's design. It is written where the
# identity, the source file and the pages a run actually read are all in hand, and BEFORE
# that run's own closing boundary:
#
#     _record_page_captures(...)
#     _record_annotation_evidence(...)      <- here, in all four paths
#     insert_members(...) / _persist_connections(...) / ...
#     update_project_summary(...)           <- the boundary
#
# After `update_project_summary` a page is not read again: `retry_pdf_page` refuses a page
# the record no longer names as failed, and `plan_continuation` derives the next window
# from the coverage line that write carries. An annotation reading taken after it could
# therefore never be taken at all. Taken before it, a failure leaves the region
# re-readable and the reading is simply taken again — the same fails-safe property
# `_record_page_captures` above is placed for.
#
# FAILURE IS NON-FATAL, and deliberately invisible to the persistence layer: this is a
# second, independent reading of the drawing, so its absence changes no member, no
# connection, no review item, no capture, no parse_failed, no coverage record, no project
# status and no retryability. It is NOT written into `projects.warnings` — that column is
# the J16/J17 boundary record, read back by `coverage_from_warnings` and
# `parse_failures_from_warnings` — and not into either `error_message`, which state
# engineering facts about a run that this milestone must not overwrite.

_ANNOTATION_STORE_ABSENT_CODE = "PGRST205"


def _annotation_store_is_absent(error: BaseException) -> bool:
    """Whether the store's own refusal says the occurrence table is not present at all.

    The same condition the J29 read surface treats as an absent store, restated here
    rather than imported from it: the client's own code when it carries one, and its own
    wording when it does not.

    Deliberately narrow, and everything it does not match is re-raised, because each of
    those failures means something this milestone must not hide. A foreign-key refusal
    (23503) means the run the occurrence is attributed to does not exist; a duplicate key
    (23505) means one attempt wrote one occurrence twice, which is a defect in the caller;
    the append-only trigger (P0001) means something tried to rewrite a recorded reading; a
    permission refusal (42501) means the writer's grant is wrong; and a network fault means
    the database is unreachable. An unreachable database is not an absent table, and is
    never reported as one.
    """
    if getattr(error, "code", None) == _ANNOTATION_STORE_ABSENT_CODE:
        return True
    described = str(error)
    return _ANNOTATION_STORE_ABSENT_CODE in described or "schema cache" in described.lower()


def _record_annotation_evidence(filepath, pages, *, project_id: str, drawing_id: str,
                                analysis_run_id: str) -> int:
    """The PDF's own annotation reading of the pages one run read. Returns how many rows.

    The return value is reported for tests and ignored by every caller, deliberately: a
    J28 reading changes nothing the run does next, and a caller that branched on it would
    be giving this layer a say it must never have.

    `pages` is the run's OWN reading — the whole document for `_run_pipeline`, the
    invocation's window for `continue_pdf_extraction`, the single retried page for
    `retry_pdf_page` — so exactly the pages this invocation read are asked for, and no row
    is ever recorded for a page it did not read. `extractor_version` is the extractor's own
    constant and `analysis_run_id` is the run already in scope; neither is defaulted and
    neither is re-derived.

    The extractor opens `filepath` itself and hashes the bytes it reads, so the recorded
    `source_pdf_sha256` is over exactly the document this run read. The file is neither
    re-downloaded nor re-derived here, and no second hashing rule is introduced.

    A refusal from either J28 layer, or a store that is not present at all, is swallowed:
    the run continues exactly as it would have without this call. Everything else —
    including any unexpected Python exception — propagates, because a J28 step that ate an
    unknown failure would be a second, invisible failure mode beside the one being
    recorded.
    """
    try:
        evidence = extract_annotations(filepath, pages=[p.page_number for p in pages])
        rows = occurrence_rows(
            evidence,
            drawing_id=drawing_id,
            project_id=project_id,
            analysis_run_id=analysis_run_id,
        )
        return repo.insert_pdf_annotation_occurrences(rows)
    except (AnnotationExtractionRefused, AnnotationEvidenceRefused):
        return 0
    except Exception as error:
        if _annotation_store_is_absent(error):
            return 0
        raise


def _member_rows(validated_members, *, project_id: str, drawing_id: str, reference_identity) -> list[dict]:
    """The `steel_members` rows a validated member set is persisted as.

    The ONE place that row shape is built. Both entry points use it, so a
    member read in a window (J16) is persisted by exactly the rules — and with
    exactly the provenance — a member read in a whole-document run is. Every
    field here is either copied from the validated member or absent; nothing is
    defaulted into a value the drawing did not state, except the mark's own
    `"?"` placeholder for a member the extraction gave no mark.
    """
    rows = []
    for m in validated_members:
        confidence_score = m.get("confidence")
        if confidence_score is None:
            confidence_tier = "medium"
        elif confidence_score >= 95:
            confidence_tier = "high"
        elif confidence_score >= 80:
            confidence_tier = "medium"
        else:
            confidence_tier = "low"

        # A refused section substitution arrives here as unmatched_steel —
        # the catalogue did not confirm the drawn section — so it takes the
        # existing review route rather than a new one, and no enrichment below
        # can apply to it (its `matched` was withheld by validate_extraction).
        review = m.get("review_status") or ("review_required" if (confidence_tier == "low" or m["category"] == "unmatched_steel") else "extracted")

        matched = m.get("matched")
        weight_per_metre = matched.get("weight_per_metre") if matched else None
        length_mm = m.get("length_mm")
        total_weight_kg = (
            round((length_mm / 1000) * weight_per_metre * (m.get("quantity") or 1), 2)
            if (matched and weight_per_metre and length_mm) else None
        )

        rows.append({
            "project_id": project_id,
            "mark": m.get("mark") or "?",
            "section_name": m.get("section_name"),
            "section_name_raw": m.get("section"),
            "section_family": matched["family"] if matched else None,
            # HOW this section resolved (Milestone J5): EXACT, SUFFIX_FALLBACK or
            # NONE, recorded by validate_extraction at the one place a section is
            # ever matched, plus the catalogue name a suffix fallback refused.
            # Carried through verbatim — no second resolution happens here, and
            # nothing downstream has to infer the resolution from section_name.
            # NULL for any row validated before this was recorded.
            "section_resolution": m.get("section_resolution"),
            "section_substituted_candidate": m.get("section_substituted_candidate"),
            # WHICH reference dataset this row's section was resolved against
            # (Milestone J6): source_kind / identity_status / the digest of the
            # rows this run's matcher actually loaded, carried verbatim from
            # the projection above. Provenance ONLY — it is never read to
            # decide a section, a family, a weight or a review state, and it is
            # never recomputed from a later catalogue read. NULL means no
            # identity was recorded for this row (every row written before J6).
            "reference_data_identity": reference_identity,
            "length_mm": length_mm,
            # The drawing's own material evidence, or None. Never a default:
            # a grade the drawing did not state is not this system's to supply.
            "grade": _extracted_grade(m),
            "quantity": m.get("quantity") or 1,
            "weight_per_metre": weight_per_metre,
            "total_weight_kg": total_weight_kg,
            "confidence": confidence_tier,
            "confidence_score": confidence_score,
            "source_page": m.get("source_page"),
            "source_drawing_id": m.get("source_drawing_id"),
            "extraction_method": "vision_claude",
            "review_status": review,
            "detail_reference": m.get("detail_reference"),
            "notes": _persisted_notes(m),
        })
    return rows


def _persist_connections(connections_raw, *, project_id: str, drawing_id: str,
                         member_rows) -> tuple[int, int, list]:
    """Persists every extracted connection and its child rows.

    Returns `(connections_extracted, connections_review_required,
    connection_review_statuses)` — the last being the review state each row was
    written with, collected as it is persisted (Milestone J13) so the project's
    terminal status is derived from the rows themselves rather than re-read.

    `member_rows` are the persisted member rows a connection's marks resolve
    against: the rows this run inserted and, for a continuation (Milestone J16),
    the rows earlier windows persisted as well — so a connection read on a later
    page still links the member it names. A mark that resolves to nothing is
    simply not linked: an unlinked connection is evidence of a connection whose
    members this run could not identify, and is not a reason to invent one.
    """
    # The one place a connection's mark strings become member IDs. A continuation
    # has already refused any window that repeats a persisted mark, so this map
    # has one entry per mark across the whole project.
    mark_to_id = {row["mark"]: row["id"] for row in member_rows if row.get("mark")}

    valid_connection_types = connection_type_accounting.RECOGNISED_CONNECTION_TYPES
    connections_extracted = 0
    connections_review_required = 0
    connection_review_statuses = []

    for c in connections_raw:
        conn_type = connection_type_accounting.normalise_source_token(c.get("connection_type"))
        if conn_type not in valid_connection_types:
            bolts, welds = c.get("bolts") or [], c.get("welds") or []
            conn_type = "bolted_and_welded" if (bolts and welds) else ("bolted" if bolts else "welded" if welds else "unspecified")

        confidence_score = c.get("confidence")
        if confidence_score is None:
            confidence_tier = "medium"
        elif confidence_score >= 95:
            confidence_tier = "high"
        elif confidence_score >= 80:
            confidence_tier = "medium"
        else:
            confidence_tier = "low"
        review = "review_required" if confidence_tier == "low" else "extracted"

        bolts, plates, welds = c.get("bolts") or [], c.get("plates") or [], c.get("welds") or []
        description_bits = (
            [f'{b.get("quantity", "?")}x {b.get("size", "?")} Gr{b.get("grade", "?")}' for b in bolts]
            + [f'{p.get("type", "plate")} {p.get("thickness_mm", "?")}mm' for p in plates]
            + [f'{w.get("size_mm", "?")}mm {w.get("type", "")} weld' for w in welds]
        )

        conn_row = {
            "project_id": project_id,
            "connection_type": conn_type,
            "grid_reference": c.get("grid_reference"),
            "detail_reference": c.get("detail_reference"),
            "description": " — ".join(description_bits)[:500] if description_bits else None,
            "confidence": confidence_tier,
            "confidence_score": confidence_score,
            "source_page": c.get("page_num"),
            "source_drawing_id": drawing_id,
            "extraction_method": "vision_claude",
            "review_status": review,
            "notes": "Extracted from PDF drawing via vision analysis",
        }
        # A connection type the drawing STATED that is outside the enum was
        # replaced by the fallback above. The drawn token's own home is the
        # page capture; this records ON THE ROW that a replacement happened, so
        # a later reader cannot mistake it for a drawing that stated nothing
        # (Milestone J37C-8V, following the J11 accounting precedent). Written
        # only when there is something to account for, so a row from a
        # recognised type persists exactly the columns it always did.
        accounting = connection_type_accounting.warning_for_unrecognised(
            c.get("connection_type"), conn_type,
        )
        if accounting:
            warnings = list(conn_row.get("warnings") or [])
            if accounting not in warnings:
                warnings.append(accounting)
            conn_row["warnings"] = warnings

        conn_row = repo.insert_connection(conn_row)
        connection_id = conn_row["id"]
        connections_extracted += 1
        connection_review_statuses.append(review_status_of(conn_row))
        if review == "review_required":
            connections_review_required += 1

        repo.insert_bolt_groups(connection_id, [
            {"bolt_size": b.get("size") or "?", "bolt_grade": b.get("grade") or "?", "quantity": b.get("quantity") or 1}
            for b in bolts
        ])
        repo.insert_connection_plates(connection_id, [
            {"plate_type": p.get("type") or "plate",
             # Explicit presence/None semantics: a missing thickness stays
             # unknown and an invalid one is refused, rather than either being
             # collapsed into a persisted 0.
             "thickness": _extracted_plate_thickness(p),
             # The plate's OWN stated grade, or None. Never the connection's
             # material, the member grades, the dimensions or a default: a plate
             # grade the drawing did not state is not this system's to supply.
             "width": p.get("width_mm"), "depth": p.get("depth_mm"),
             "grade": _extracted_plate_grade(p)}
            for p in plates
        ])
        repo.insert_weld_details(connection_id, [
            {"weld_type": w.get("type") or "fillet", "size": w.get("size_mm")} for w in welds
        ])

        linked_ids = [mark_to_id[mark] for mark in (c.get("connects_members") or []) if mark in mark_to_id]
        repo.link_connection_members(connection_id, linked_ids)

        repo.insert_review_items([{"project_id": project_id, "item_type": "connection", "item_id": connection_id, "status": review}])

    return connections_extracted, connections_review_required, connection_review_statuses


def _run_pipeline(filepath: str, project_id: str, user_id: str, drawing_set_id: str, drawing_id: str,
                  *, analysis_run_id: str) -> dict:
    matcher = SectionMatcher()

    # The reference-data identity of the catalogue THIS matcher loaded
    # (Milestone J6) — read once, here, while the genuine matcher that decides
    # every section in this run is still in hand, and carried verbatim onto
    # every member row it produces. It is a run-level fact (one matcher, one
    # read of the reference table), never reconstructed or recomputed
    # downstream: the live table is unversioned and its content can change
    # after this run, at which point a fresh digest would no longer describe
    # the rows these resolutions actually used. None when the matcher records
    # no identity — absence is persisted as absence.
    reference_identity = reference_data_projection(getattr(matcher, "reference_identity", None))

    # === Stage 1: AI analysis — pure reading, page by page ===
    pages = analyze_pdf_pages(filepath, user_id, project_id, drawing_id, MAX_PDF_PAGES)
    if not pages:
        raise RuntimeError("Could not render any pages from this PDF.")

    # Milestone J15 — WHAT THIS RUN ACTUALLY READ, of what it was given.
    #
    # `len(pages)` is the number of pages that came back readable; it is not the
    # size of the drawing set. `analyze_pdf_pages` is capped by MAX_PDF_PAGES
    # and silently renders fewer pages than a long document has, so asking the
    # count of gathered pages "was the whole set read?" answers its own
    # question. The document's own page count is read separately, from the file
    # itself, before any cap applies — and when it cannot be established the
    # coverage is recorded as unknown rather than assumed to match.
    #
    # A page whose response could not be read (`parse_failed`) is counted as a
    # reading failure, NOT as a page with no steel: this seam is where the two
    # stop being the same thing, because only one of them is a statement about
    # the drawing.
    #
    # Milestone J17 — the SAME reading of the same pages states both halves of
    # the record: the count that goes into the coverage line and the page numbers
    # that go into the failures line. One source, two lines, so a run cannot
    # persist a count that disagrees with the pages it names.
    failures = failures_of(p.page_number for p in pages if p.parse_failed)
    coverage = coverage_of(
        total_pages=page_count_of(filepath),
        page_numbers=[p.page_number for p in pages],
        parse_failed_page_numbers=failures.page_numbers,
    )

    drawing_meta = next(
        ({"drawing_number": p.drawing_number, "drawing_title": p.drawing_title, "revision": p.revision}
         for p in pages if p.page_number == 1),
        {"drawing_number": None, "drawing_title": None, "revision": None},
    )

    raw_members, connections_raw = _flatten_pages(pages, drawing_id)

    # === Stage 2: Validation — classify, consolidate, flag ===
    validated = validate_extraction(raw_members, matcher)

    # === Stage 3: Engineering data — persist the validated results ===
    # Milestone J23 — the reading itself is recorded first, before any member or
    # connection row of this run exists. The raw AI result lives in `pages` and
    # nowhere else; this is the last moment it is in hand.
    _record_page_captures(
        pages,
        analysis_run_id=analysis_run_id,
        project_id=project_id,
        drawing_set_id=drawing_set_id,
        drawing_id=drawing_id,
    )

    # Milestone J36 — the same pages, read a second way: the PDF's own annotation
    # occurrences (J28). Every page this whole-document run read was read, so the scope is
    # all of `pages`. Nothing below depends on the result.
    _record_annotation_evidence(
        filepath,
        pages,
        project_id=project_id,
        drawing_id=drawing_id,
        analysis_run_id=analysis_run_id,
    )

    # One row shape, one function: Milestone J16's continuation persists a
    # WINDOW's members through this same builder, so a member read on page 31 is
    # written by the identical rules as one read on page 3 — the two entry
    # points cannot drift apart in what a persisted member row means.
    members_to_insert = _member_rows(
        validated["members"],
        project_id=project_id,
        drawing_id=drawing_id,
        reference_identity=reference_identity,
    )

    inserted_members = repo.insert_members(members_to_insert)

    if inserted_members:
        repo.insert_review_items([
            {"project_id": project_id, "item_type": "steel_member", "item_id": row["id"], "status": row["review_status"]}
            for row in inserted_members
        ])

    # === Connections: resolve mark strings to real member IDs, now that members are inserted ===
    # One persistence path for both entry points (Milestone J16): this call
    # passes the members this run inserted, and a continuation passes the
    # project's persisted members alongside its own, so a connection read on a
    # later window still links the member it names.
    connections_extracted, connections_review_required, connection_review_statuses = _persist_connections(
        connections_raw,
        project_id=project_id,
        drawing_id=drawing_id,
        member_rows=inserted_members,
    )

    # === Roll up and persist project-level summary, including validation warnings ===
    # Milestone J15: the drawing's page count is the DOCUMENT's own count, not
    # the number of pages this run processed. Passing the processed count here
    # is what made a truncated reading look like a complete one. When the count
    # could not be established, the column keeps the absence it was created with
    # — an unproven number is not written as if it were measured.
    repo.update_drawing_meta(drawing_id, coverage.total_pages, drawing_meta["drawing_number"], drawing_meta["drawing_title"], drawing_meta["revision"])

    total_weight_kg = sum(m["total_weight_kg"] or 0 for m in members_to_insert)
    unique_sections = len(set(m["section_name"] for m in members_to_insert if m["section_name"]))
    members_review_required = sum(1 for m in members_to_insert if m["review_status"] == "review_required")
    review_required_count = members_review_required + connections_review_required

    warning_messages = [issue.message for issue in validated["issues"]]
    # The coverage record rides the same column as J11's accounting, for the same
    # reason: `projects.warnings` is where a run states what it did NOT read.
    # Written for every run, complete or not — a complete coverage has to be
    # stated to be checkable, otherwise its absence would have to mean both
    # "nothing to report" and "no record kept". It is read back through
    # app/validation/page_coverage.py, never by matching this prose.
    #
    # Milestone J17 — WHICH pages failed rides the same column too, one line
    # above the count, written in the same call: a reader that finds them
    # disagreeing has found a record that contradicts itself rather than a page
    # it can act on. The coverage line stays LAST, because it is the line the
    # boundary is derived from.
    warning_messages.append(failures.as_line())
    warning_messages.append(coverage.as_line())
    # A section is UNMATCHED when the reference catalogue answered for nothing the
    # drawing stated. Since Milestone J7 every persisted row also records HOW it
    # resolved, so the two states that leave section_name empty are no longer
    # indistinguishable: a REFUSED substitution (SUFFIX_FALLBACK) is excluded
    # here, because the catalogue DID answer that token — with a different
    # section — and the engineer has been told which. It keeps its drawn identity
    # and a named candidate, so reporting it as unidentified would overstate what
    # this project does not know. NONE alone is unmatched. Rows written before J7
    # carry no recorded resolution and keep the previous rule unchanged.
    unmatched_sections = sorted({
        m["section_name_raw"] for m in members_to_insert
        if not m["section_name"] and m["section_name_raw"]
        and m.get("section_resolution") != SUFFIX_FALLBACK
    })

    # THE PROJECT'S TERMINAL STATE, DERIVED (Milestone J13). This was the
    # constant "review", which parked every project — including one where every
    # member and connection came back explicitly "extracted" — in a state that
    # cannot be told apart from one awaiting a human. The status is now what the
    # evidence this run persisted supports, and nothing else. This path states a
    # review state on every member and every connection it writes (above), so a
    # clean run has the evidence to prove completeness and can reach "done",
    # while any held record, unmatched section or J11 discard keeps it at
    # "review". Milestone J15 adds the precondition underneath all of it: a run
    # that did not read the whole drawing set — pages never analysed, pages
    # whose response could not be read, or a page count it could not establish —
    # has not seen everything it is reporting on, and cannot be done either.
    status = derive_project_status(
        member_review_statuses=[review_status_of(m) for m in members_to_insert],
        connection_review_statuses=connection_review_statuses,
        unmatched_sections=unmatched_sections,
        warnings=warning_messages,
        coverage=coverage,
    ).status

    repo.update_project_summary(
        project_id,
        status=status,
        total_members=len(members_to_insert),
        total_unique_sections=unique_sections,
        total_connections=connections_extracted,
        total_weight_kg=total_weight_kg,
        total_weight_tonnes=round(total_weight_kg / 1000, 3),
        unmatched_sections=unmatched_sections,
        warnings=warning_messages,
        engineer_reference=drawing_meta["drawing_number"],
        structural_engineer=drawing_meta["drawing_title"],
    )
    # E2E-001N — that write described ONE document. On a project holding more than one
    # document it is not the project's summary, so it is reconciled to the document set
    # before this function returns.
    reconcile_project_summary(project_id)

    return {
        "pages_processed": len(pages),
        "total_pages": coverage.total_pages,
        "analysed_pages": coverage.analysed_pages,
        "parse_failed_pages": coverage.parse_failed_pages,
        "not_analysed_pages": coverage.not_analysed_pages,
        "members_extracted": len(members_to_insert),
        "connections_extracted": connections_extracted,
        "unique_sections": unique_sections,
        "review_required_count": review_required_count,
        "total_weight_kg": total_weight_kg,
        "excluded_non_steel": len(validated["excluded"]),
        "warnings": warning_messages,
    }


# ======================================================================================
# MILESTONE J16 — CONTINUING A PDF DRAWING SET BEYOND ONE EXTRACTION WINDOW
# ======================================================================================
# A drawing set larger than MAX_PDF_PAGES used to have one honest outcome: the
# first window is read, and (since J15) the pages that were never reached are
# recorded as NOT_ANALYSED. There was no production way to read them.
#
# The continuation below reads the NEXT window and nothing else. It is not a
# re-run: it creates no drawing set, no drawing, no member for a page that was
# already read, and it refuses outright rather than risk reading a page twice.
# What it must never do is decide anything a human should: it does not merge,
# deduplicate or re-identify persisted evidence, and where the production data
# model cannot answer a question safely (member identity across windows, see
# _member_rows / the collision refusal below) it stops and says so.
#
# THE PERSISTED STATE, AND WHICH PART OF IT IS AUTHORITATIVE
#
#   projects.warnings          the COVERAGE RECORD — authoritative for what has
#                              been read. It is the single fact the boundary is
#                              derived from, and the only thing that advances
#                              the boundary (it is written LAST).
#   drawing_sets               counters for the whole document, kept in step with
#                              the coverage record. Disagreement is refused
#                              (CONTINUATION_RECORD_CONFLICT), never merged.
#   analysis_runs              ONE RUN PER WINDOW: the history of which windows
#                              were read. It is not a competing coverage record —
#                              nothing derives the boundary from it.
#   drawings.page_count        the document's own page count, written by the run
#                              that read page 1 (whose metadata a continuation
#                              must not overwrite: it never saw page 1).
#   drawing_pages / steel_members / connections
#                              the evidence itself, one row per page/reading,
#                              each carrying the TRUE page it came from.
#
# The order of writes is evidence first, coverage last, so a crash can leave a
# window's rows written without the boundary advancing — which the next attempt
# detects (the mark collision, or the record conflict) and refuses. That is the
# fail-closed direction: the worst outcome is a project held at "review" with a
# named reason, never a project that reports itself complete on unread pages.


@dataclass(frozen=True)
class ContinuationPlan:
    """Everything a continuation decided from PERSISTED state, before any file was read.

    Returned by `plan_continuation` so that the decision to continue (or the
    refusal) is available to a caller that must not download or analyse
    anything until it knows there is a window to read.
    """

    project: dict
    drawing_set: dict
    drawing: dict
    previous_coverage: PageCoverage
    previous_failures: ParseFailures
    window: PageWindow
    persisted_members: tuple[dict, ...]
    persisted_connections: tuple[dict, ...]


def _accumulated_warnings(
    previous_warnings, window_issue_messages, coverage: PageCoverage, failures: ParseFailures,
) -> list[str]:
    """The project's warnings, with this window's added, carrying ONE record of each kind.

    The coverage record has to describe the whole document, so the previous
    window's line is removed rather than duplicated — a project with two
    coverage lines would state two different readings of one drawing set, and
    `coverage_from_warnings` reads the first it finds. Milestone J17's failures
    line is the same kind of record and is replaced the same way, for the same
    reason: it also describes the whole document, and a project with two of them
    could be read as naming two different sets of failed pages. Everything else
    is kept in the order it was first stated; a message this window repeats is
    not stated twice, because the set of problems is the same either way.

    The coverage line is written LAST of the two, and both are written in one
    call, so the count and the page numbers it counts cannot be persisted apart.
    """
    accumulated = [
        w for w in (previous_warnings or [])
        if not (
            isinstance(w, str)
            and (w.startswith(COVERAGE_PREFIX) or w.startswith(PARSE_FAILURES_PREFIX))
        )
    ]
    for message in window_issue_messages:
        if message not in accumulated:
            accumulated.append(message)
    accumulated.append(failures.as_line())
    accumulated.append(coverage.as_line())
    return accumulated


def _source_lineage(
    project_id: str,
    project: dict,
    storage_path: str,
    *,
    document_id: str | None,
    refuse,
):
    """The one drawing set and one drawing a continuation or retry adds to.

    Milestone J63. Before it, this choice was decided by the same four checks in both
    callers: exactly one drawing set, exactly one drawing, and a source path agreeing with
    the project's own column and with the drawing's. Those checks are guards, and they do
    not all protect the same thing. Classified by what they protect:

        A. same-document continuation   the coverage record, the drawing set's counters,
                                       the drawing's page count and — in the executor,
                                       where the file is in hand — the file's own page
                                       count. None of them is this function.
        B. retry of the same document   the same, plus `RETRY_EVIDENCE_ALREADY_PERSISTED`
                                       and the page the failures record names as failed.
                                       Not this function either.
        C. accidental second document   the COUNT checks below. Two lineages for one
                                       project mean two documents' worth of record, and
                                       adding a window to the wrong one is the error this
                                       guard exists to prevent.
        D. cross-project source mismatch the PATH agreement below: the bytes about to be
                                       read must be the bytes the addressed source and the
                                       lineage both name.

    C and D are what this function IS, and J63 changes exactly two things about them. Both
    changes are confined to a request that ADDRESSED a document; an omitted `document_id`
    takes the pre-J63 branch below, whose checks are the pre-J63 ones written as they were
    written, so no project that could be continued before this milestone is refused or
    re-routed by it.

        C  is narrowed, not relaxed: when a document is addressed, the count checks apply
           to the lineages that read THAT document, and a document read by two lineages is
           still refused rather than chosen between. The code it refuses with is J16's own
           `CONTINUATION_DRAWING_SET_UNRESOLVED`, whose published meaning is already this
           ("which document a continuation would be adding to is ambiguous").
        D  is re-sourced, not dropped: the agreement is against the ADDRESSED DOCUMENT's
           own `storage_path`, which is the whole point of the milestone — a document that
           is not the project's newest upload is now a legitimate source, so
           `projects.uploaded_file_path` can no longer be the thing the path is checked
           against. The drawing's half of the check is unchanged, and it is what keeps a
           document whose lineage read a DIFFERENT file from being continued.

    `refuse` is the caller's own exception — `ContinuationRefused` or `RetryRefused` — so
    that each caller keeps its own vocabulary while there is still exactly one statement
    in this codebase of which guard protects what. The repository is this module's own
    `repo`, passed in rather than imported again by the resolver, so that the store a test
    replaces for the reads below is the same store the source was resolved from.
    """
    try:
        source = resolve_extraction_source(
            project_id, document_id=document_id, project=project, repository=repo,
        )
    except ContinuationRefused as refused:
        # J16's refusal, in the caller's own exception type. The code and the detail are
        # carried through unchanged: they name the persisted fact that was wrong, and the
        # caller's vocabulary has no second name for it.
        raise refuse(refused.code, refused.detail) from None

    if storage_path != source.storage_path:
        raise refuse(
            CONTINUATION_SOURCE_MISMATCH,
            f"this run is pointed at {storage_path!r}, but the source document it was "
            f"resolved from states {source.storage_path!r}",
        )

    drawing_sets = repo.drawing_sets_for_project(project_id)

    if not source.addressed:
        # ==========================================================================
        # THE PRE-J63 PATH. Every check below is the check this function replaced,
        # written as it was written: a project whose source is the project-level column
        # is decided by exactly the rules it was decided by before J63.
        # ==========================================================================
        if len(drawing_sets) != 1:
            # Two drawing sets for one project means the extraction has been run
            # against it twice: POST /extract has no idempotency guard, and each run
            # creates its own drawing set, drawing and analysis run and re-inserts
            # every member and connection row. Choosing one to add to here would be
            # choosing which of two documents a continuation belongs to, so it is
            # refused.
            raise refuse(
                CONTINUATION_DRAWING_SET_UNRESOLVED,
                f"this project has {len(drawing_sets)} drawing set(s); this request must add to "
                f"exactly one, so which document these pages belong to is ambiguous",
            )
        drawing_set = drawing_sets[0]

        drawings = repo.drawings_for_drawing_set(drawing_set["id"])
        if len(drawings) != 1:
            raise refuse(
                CONTINUATION_DRAWING_SET_UNRESOLVED,
                f"drawing set {drawing_set.get('id')!r} has {len(drawings)} drawing(s); this "
                f"request must add to exactly one",
            )
        drawing = drawings[0]

        # The pages about to be read must be pages of the SAME document. There is no
        # content hash anywhere in this codebase to prove sameness with, so identity
        # is the agreement of every source reference production keeps: the path the
        # project was uploaded at, the path its drawing was read from, and (below,
        # in the executor, where the file is in hand) the document's own page count.
        # Two different documents that agree on all three are indistinguishable
        # here — see the limitations in the J16 report.
        #
        # The first half is re-stated rather than dropped: for this branch
        # `source.storage_path` IS `projects.uploaded_file_path`, so the comparison
        # above already made it, and J63 leaves a guard it did not need to change.
        if storage_path != project.get("uploaded_file_path") or storage_path != drawing.get("storage_path"):
            raise refuse(
                CONTINUATION_SOURCE_MISMATCH,
                f"this continuation is pointed at {storage_path!r}, but this project's extraction read "
                f"{project.get('uploaded_file_path')!r} (drawing row: {drawing.get('storage_path')!r})",
            )
        return drawing_set, drawing

    # ==================================================================================
    # THE ADDRESSED PATH. The lineages of the document the caller named — found by
    # reading the project's own drawing sets and drawings, and taking the ones whose
    # `document_id` is the addressed document. No ordering, no first, no newest: the
    # addressed id is the only thing selected on, and it selects by equality.
    # ==================================================================================
    lineages = [
        (drawing_set, drawing)
        for drawing_set in drawing_sets
        for drawing in repo.drawings_for_drawing_set(drawing_set["id"])
        if drawing.get("document_id") == source.document_id
    ]
    if len(lineages) != 1:
        raise refuse(
            CONTINUATION_DRAWING_SET_UNRESOLVED,
            f"source document {source.document_id!r} is read by {len(lineages)} drawing "
            f"lineage(s); this request must add to exactly one, so which record these pages "
            f"belong to is not resolvable from this document alone",
        )
    drawing_set, drawing = lineages[0]

    # Guard D, against the lineage that read the addressed document. A document whose
    # lineage read a different file is refused: the bytes about to be read would be added
    # to a record made from other bytes, which is the one thing a page window must not do.
    if storage_path != drawing.get("storage_path"):
        raise refuse(
            CONTINUATION_SOURCE_MISMATCH,
            f"this run is pointed at {storage_path!r}, but the lineage of source document "
            f"{source.document_id!r} read {drawing.get('storage_path')!r}",
        )
    return drawing_set, drawing


def plan_first_window(project_id: str, source: SourceDocument) -> SourceDocument:
    """Decides which document the FIRST window of a new reading reads.

    Milestone J63A. `/extract` is the one extraction route that starts something new: it
    creates the drawing set, the drawing and the analysis run that the pages it reads
    belong to. Every other route adds to a reading that already exists and decides which
    one by `_source_lineage`'s guards. This is that question asked of a reading that does
    not exist yet — WHICH of the project's documents is this reading of? — and it is
    answered here, before anything is downloaded, queued or written.

    The source is passed in rather than resolved again: an addressed request has already
    chosen, and the only case this function exists for is the one that chose nothing.
    It has exactly three answers.

        ADDRESSED           returned unchanged. The caller named the document, and a
                            choice that was made is not ambiguous. Nothing is read.
        OMITTED, 0 or 1     the pre-J63 request, unchanged. A project holding no document
        document            yet is the upload flow J61's rule deliberately allows — the
                            file being extracted is not a `project_documents` row until a
                            run reads it — and one document is not a choice.
        OMITTED, 2 or more  refused, with `CONTINUATION_DRAWING_SET_UNRESOLVED`: J16's own
        documents           code, already published, whose published meaning is already
                            exactly this state ("which document these pages belong to is
                            ambiguous"). The continuation and retry routes already answer
                            this state with that code at 409, so a caller reads ONE
                            vocabulary for one state, and J63A added no code at all.

    `projects.uploaded_file_path` is not consulted on the refused branch. On a project
    that holds more than one document the project-level column is not a source, it is a
    CHOICE among sources, and this function refuses to make it: no document is preferred
    by name, by size, by page count, by role, by `created_at`, by being first, by being
    newest, or by agreeing with the project's column. The documents are COUNTED, and a
    count is not a choice.

    The count is of DOCUMENTS and not of drawing lineages. A project whose one document
    was read twice holds two lineages and one source, and a new reading of it reads that
    source; the lineage ambiguity is guard C of `_source_lineage`, which belongs to the
    operations that ADD to a lineage and is unchanged by this milestone.

    Exactly one read — the project's own documents, through this module's own store, the
    same store every other persisted fact here is read from — and no write of any kind.
    """
    if source.addressed:
        return source

    documents = repo.project_documents_for_project(project_id)
    if len(documents) > 1:
        raise ContinuationRefused(
            CONTINUATION_DRAWING_SET_UNRESOLVED,
            f"this project holds {len(documents)} source documents and this request names "
            f"none of them; which document a new reading of this project would read is "
            f"ambiguous, and this route does not choose one",
        )
    return source


def plan_continuation(
    project_id: str,
    storage_path: str,
    *,
    document_id: str | None = None,
    requested_first_page: int | None = None,
    requested_last_page: int | None = None,
    requested_total_pages: int | None = None,
) -> ContinuationPlan:
    """Decides whether this project can be continued, and from which page.

    Reads persisted state only — no download, no rendering, no AI. Raises
    `ContinuationRefused` with a named reason when it cannot: an ambiguous
    drawing set, a different source file, no coverage record, a record that
    disagrees with itself or with its counters, or a window that is not the
    next one.

    `document_id` (Milestone J63) is OPTIONAL and names which of the project's
    source documents these pages belong to. Omitted, the source is the
    project-level column and every check below is the check this function has
    always applied. Named, the lineage that read that document is the one this
    plan is about, and the source agreement is against THAT document's own
    path — see `_source_lineage` for which guard protects what.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise ContinuationRefused(
            CONTINUATION_DRAWING_SET_UNRESOLVED,
            f"there is no project {project_id!r} to continue",
        )

    drawing_set, drawing = _source_lineage(
        project_id, project, storage_path,
        document_id=document_id, refuse=ContinuationRefused,
    )

    previous = coverage_from_warnings(project.get("warnings"))
    if previous is None:
        raise ContinuationRefused(
            CONTINUATION_NO_COVERAGE_RECORD,
            "this project carries no PDF coverage record, so which of its pages have been read is "
            "unknown; continuing it could read and persist pages that already have evidence",
        )

    # Milestone J17 — the other half of that record. A window adds its own
    # failures to the ones already recorded, which is only truthful if the
    # earlier ones are NAMED: a record that states a count without the pages
    # cannot be extended, and restating it with this window's pages alone would
    # contradict the count it rides beside. A record that states NO failures
    # needs no names, and a record written by this code always states both.
    previous_failures = parse_failures_from_warnings(project.get("warnings"))
    if previous_failures is None and previous.parse_failed_pages:
        raise ContinuationRefused(
            CONTINUATION_FAILURES_UNRECORDED,
            f"the coverage record states {previous.parse_failed_pages} parse-failed page(s), but "
            f"this project carries no readable record of WHICH pages they are, so this window "
            f"cannot state them without contradicting the count it would be written beside",
        )
    if previous_failures is None:
        previous_failures = ParseFailures()
    if previous_failures.count != previous.parse_failed_pages:
        raise ContinuationRefused(
            CONTINUATION_RECORD_CONFLICT,
            f"the coverage record states {previous.parse_failed_pages} parse-failed page(s), but "
            f"the failures record names {previous_failures.count}; the two halves of one reading "
            f"disagree, so neither can be added to",
        )

    decision = check_page_window(
        previous,
        window_size=MAX_PDF_PAGES,
        requested_first_page=requested_first_page,
        requested_last_page=requested_last_page,
        requested_total_pages=requested_total_pages,
    )
    if not decision.accepted:
        raise ContinuationRefused(decision.refusal_code, decision.detail)

    # Two records of the same fact must agree before either is added to: the
    # drawing set's counters and the coverage record are written from the same
    # measurement at every commit, so a disagreement means a commit was
    # interrupted (or a row was edited), and only those two writes disagree.
    if (drawing_set.get("pages_analysed") != previous.analysed_pages
            or drawing_set.get("total_pages") != previous.total_pages):
        raise ContinuationRefused(
            CONTINUATION_RECORD_CONFLICT,
            f"the drawing set records pages_analysed={drawing_set.get('pages_analysed')!r} "
            f"total_pages={drawing_set.get('total_pages')!r}, but the coverage record states "
            f"analysed={previous.analysed_pages} total={previous.total_pages}; one of them is "
            f"stale, so the boundary between read and unread pages is not trustworthy",
        )

    if drawing.get("page_count") is not None and drawing["page_count"] != previous.total_pages:
        raise ContinuationRefused(
            CONTINUATION_DOCUMENT_TOTAL_MISMATCH,
            f"the drawing records {drawing['page_count']} page(s), but this drawing set has been "
            f"read as {previous.total_pages} page(s); the source may have changed",
        )

    return ContinuationPlan(
        project=project,
        drawing_set=drawing_set,
        drawing=drawing,
        previous_coverage=previous,
        previous_failures=previous_failures,
        window=decision.window,
        persisted_members=tuple(repo.member_rows_for_project(project_id)),
        persisted_connections=tuple(repo.connection_rows_for_project(project_id)),
    )


def continue_pdf_extraction(
    filepath: str, project_id: str, user_id: str, storage_path: str,
    *,
    document_id: str | None = None,
    requested_first_page: int | None = None,
    requested_last_page: int | None = None,
    requested_total_pages: int | None = None,
) -> dict:
    """Reads the NEXT window of a PDF whose earlier windows are already persisted.

    The document's size is the one its extraction already established: a file
    that states a different number of pages is a different document as far as
    this contract is concerned, and is refused rather than partly read.

    Only this window is read and only this window's evidence is written. Every
    page of it is accounted for before anything is persisted, and the coverage
    record is advanced last, so the returned summary and the persisted state
    agree about what has been read — including when the window contains a page
    whose response could not be parsed, which is recorded as read-and-failed and
    keeps the project from reaching "done".
    """
    plan = plan_continuation(
        project_id,
        storage_path,
        document_id=document_id,
        requested_first_page=requested_first_page,
        requested_last_page=requested_last_page,
        requested_total_pages=requested_total_pages,
    )
    window = plan.window
    previous = plan.previous_coverage
    drawing_set_id = plan.drawing_set["id"]
    drawing_id = plan.drawing["id"]

    document_pages = page_count_of(filepath)
    if document_pages is None or document_pages != previous.total_pages:
        raise ContinuationRefused(
            CONTINUATION_DOCUMENT_TOTAL_MISMATCH,
            f"the file at {storage_path!r} states {document_pages!r} page(s), but this drawing set "
            f"has been read as {previous.total_pages} page(s); the source may have changed",
        )

    matcher = SectionMatcher()
    # The reference-data identity of the catalogue THIS matcher loaded, read once
    # while the matcher that decides every section of this window is in hand —
    # the same J6 provenance rule the whole-document path follows.
    reference_identity = reference_data_projection(getattr(matcher, "reference_identity", None))

    # === Stage 1: AI analysis — this window's pages, and no others ===
    pages = analyze_pdf_pages(
        filepath, user_id, project_id, drawing_id, window.size, first_page=window.first_page,
    )
    if not pages:
        raise RuntimeError("Could not render any pages from this PDF.")

    # Milestone J15's arithmetic, applied to one window: the document's own page
    # count with this window's pages added to what was already read. A window
    # that did not come back in full raises here (see `accumulate_coverage`) —
    # nothing is written, and the boundary does not move past a page nobody read.
    #
    # Milestone J17 states the same window's failures twice, from one reading:
    # the count the coverage line carries and the page numbers the failures line
    # names, with the earlier windows' failures carried forward so a page that
    # failed on page 17 is still recorded after page 60 has been read.
    window_failures = failures_of(p.page_number for p in pages if p.parse_failed)
    accumulated = accumulate_coverage(
        previous,
        window=window,
        analysed_page_numbers=[p.page_number for p in pages],
        parse_failed_page_numbers=window_failures.page_numbers,
    )
    accumulated_failures = plan.previous_failures.including(window_failures.page_numbers)

    raw_members, connections_raw = _flatten_pages(pages, drawing_id)

    # === Stage 2: Validation — the same rules the whole-document path applies ===
    validated = validate_extraction(raw_members, matcher)
    window_rows = _member_rows(
        validated["members"],
        project_id=project_id,
        drawing_id=drawing_id,
        reference_identity=reference_identity,
    )

    # === Cross-window identity, decided BEFORE anything is written ===
    # A member mark is the identity this system already consolidates members on
    # within one run. Across windows there is no such rule: two persisted rows
    # with one mark may be the same member detailed on two sheets, or two
    # different members. Merging them would be inventing semantics the data does
    # not carry, and inserting the second would silently duplicate the first, so
    # the window is refused and the mark is named for a human to look at.
    persisted_marks = {row.get("mark") for row in plan.persisted_members if row.get("mark")}
    colliding_marks = sorted({row["mark"] for row in window_rows} & persisted_marks)
    if colliding_marks:
        raise ContinuationRefused(
            CONTINUATION_MEMBER_MARK_COLLISION,
            f"{len(colliding_marks)} member mark(s) on pages {window.first_page}-{window.last_page} "
            f"are already persisted for this project ({', '.join(colliding_marks[:10])}"
            f"{', …' if len(colliding_marks) > 10 else ''}); member identity across windows has no "
            f"merge rule, so this window is refused rather than persisted as a second copy",
        )

    # === Stage 3: Engineering data — this window's evidence, then the commit ===
    analysis_run = repo.create_analysis_run(drawing_set_id, PDF_VISION_MODEL)
    analysis_run_id = analysis_run["id"]

    # Milestone J23 — this window's raw AI readings, recorded before its evidence.
    # `pages` is the window's own reading and this is the last moment it is in hand.
    _record_page_captures(
        pages,
        analysis_run_id=analysis_run_id,
        project_id=project_id,
        drawing_set_id=drawing_set_id,
        drawing_id=drawing_id,
    )

    # Milestone J36 — this WINDOW's pages, read a second way (J28). `pages` is the
    # window's own reading and no other page is asked for, so a page this invocation did
    # not read records nothing. Written before the commit below, because after it the
    # window is answered and is never re-read.
    _record_annotation_evidence(
        filepath,
        pages,
        project_id=project_id,
        drawing_id=drawing_id,
        analysis_run_id=analysis_run_id,
    )

    inserted_members = repo.insert_members(window_rows)
    if inserted_members:
        repo.insert_review_items([
            {"project_id": project_id, "item_type": "steel_member", "item_id": row["id"], "status": row["review_status"]}
            for row in inserted_members
        ])

    # Marks from earlier windows resolve too: the collision check above has
    # already established that a mark is unique across the whole project, so
    # this mapping is unambiguous without any merge decision. Both the persisted
    # rows (read back, not remembered) and this window's rows are evidence a
    # connection may name.
    connections_extracted, connections_review_required, connection_review_statuses = _persist_connections(
        connections_raw,
        project_id=project_id,
        drawing_id=drawing_id,
        member_rows=[*plan.persisted_members, *inserted_members],
    )

    # === The accumulated state: what is persisted, plus this window ===
    # Every total is recomputed over the PERSISTED rows rather than added to the
    # project's previous numbers, so a window can never double-count a row: the
    # rows are the evidence, and the summary is a function of them.
    total_weight_kg = (
        sum(row.get("total_weight_kg") or 0 for row in plan.persisted_members)
        + sum(row["total_weight_kg"] or 0 for row in window_rows)
    )
    unique_sections = {row.get("section_name") for row in plan.persisted_members if row.get("section_name")}
    unique_sections |= {row["section_name"] for row in window_rows if row["section_name"]}

    # The same rule the whole-document path applies, over this window's rows: a
    # section is unmatched only when the catalogue answered for nothing the
    # drawing stated, and a refused substitution is not unmatched.
    window_unmatched = {
        row["section_name_raw"] for row in window_rows
        if not row["section_name"] and row["section_name_raw"]
        and row.get("section_resolution") != SUFFIX_FALLBACK
    }
    unmatched_sections = sorted(set(plan.project.get("unmatched_sections") or []) | window_unmatched)

    # Milestone J13: the terminal state is derived from ALL the evidence, not
    # from this window's — the persisted rows are read back and re-stated with
    # this window's, so a project whose first window left a member in review
    # cannot reach "done" by continuing.
    member_review_statuses = (
        [review_status_of(row) for row in plan.persisted_members]
        + [review_status_of(row) for row in window_rows]
    )
    connection_statuses = (
        [review_status_of(row) for row in plan.persisted_connections] + connection_review_statuses
    )
    review_required_count = (
        sum(1 for status in member_review_statuses if status == "review_required")
        + sum(1 for status in connection_statuses if status == "review_required")
    )
    warnings = _accumulated_warnings(
        plan.project.get("warnings"), [issue.message for issue in validated["issues"]],
        accumulated, accumulated_failures,
    )
    status = derive_project_status(
        member_review_statuses=member_review_statuses,
        connection_review_statuses=connection_statuses,
        unmatched_sections=unmatched_sections,
        warnings=warnings,
        coverage=accumulated,
    ).status

    repo.update_drawing_set(
        drawing_set_id, status="analyzed",
        pages_analysed=accumulated.analysed_pages,
        members_found=len(member_review_statuses),
        review_required_count=review_required_count,
        total_pages=document_pages,
    )
    repo.update_analysis_run(
        analysis_run_id, status="completed",
        pages_processed=window.size, total_pages=document_pages, completed_at="now()",
    )
    # The coverage record is the commit: it is what the next boundary is derived
    # from, so it is written last, after every piece of evidence it accounts for.
    repo.update_project_summary(
        project_id,
        status=status,
        total_members=len(member_review_statuses),
        total_unique_sections=len(unique_sections),
        total_connections=len(plan.persisted_connections) + connections_extracted,
        total_weight_kg=total_weight_kg,
        total_weight_tonnes=round(total_weight_kg / 1000, 3),
        unmatched_sections=unmatched_sections,
        warnings=warnings,
    )
    # E2E-001N — reconcile the project row to its whole document set (see below).
    reconcile_project_summary(project_id)

    return {
        "window": {
            "first_page": window.first_page,
            "last_page": window.last_page,
            "size": window.size,
        },
        "pages_processed": len(pages),
        "total_pages": accumulated.total_pages,
        "analysed_pages": accumulated.analysed_pages,
        "parse_failed_pages": accumulated.parse_failed_pages,
        "not_analysed_pages": accumulated.not_analysed_pages,
        "is_complete": accumulated.is_complete,
        # The next boundary, derived: windows are contiguous by construction, so
        # the page after the last one read is the page after `analysed_pages`.
        "next_window": (
            None if accumulated.is_complete
            else {"first_page": accumulated.analysed_pages + 1,
                  "last_page": min(accumulated.analysed_pages + MAX_PDF_PAGES, accumulated.total_pages)}
        ),
        "members_extracted": len(window_rows),
        "connections_extracted": connections_extracted,
        "review_required_count": review_required_count,
        "total_weight_kg": total_weight_kg,
        "excluded_non_steel": len(validated["excluded"]),
        "project_status": status,
        "warnings": warnings,
    }


# ======================================================================================
# Milestone J17 — READING ONE PAGE AGAIN
# ======================================================================================
# J15 recorded HOW MANY pages of a drawing set were read without a readable
# response; J17 records WHICH ones (`app/validation/parse_failures.py`) so that a
# single one of them can be read again. This section is the only place that
# re-reads a page of a document whose evidence is already persisted, so what it
# may touch is deliberately narrow:
#
#   IT READS ONE PAGE. `first_page=page`, one page wide. The document is the one
#   the project's extraction already read (same storage path, same drawing, same
#   page count), and the page is one the committed record NAMES as failed — so
#   the page it reads is the page it was asked for and no other.
#
#   IT WRITES ONLY THAT PAGE'S EVIDENCE. Every row it inserts carries the true
#   `source_page` and `source_drawing_id` (the same `_flatten_pages` /
#   `_member_rows` / `_persist_connections` the whole-document and window paths
#   use), and it inserts nothing for a page whose evidence is already persisted —
#   that is refused by exact identity, not by resemblance (see below).
#
#   IT MOVES NO BOUNDARY. A parse failure is a reading failure OF a page that was
#   read (J15), so the page is already inside the coverage record's `analysed`
#   count; retrying it changes only how many of those pages have no readable
#   response. `analysed` and `not_analysed` are therefore UNCHANGED, which is why
#   this cannot make a continuation skip a page: J16's next window is derived
#   from `analysed_pages`, and a retry never moves it.
#
#   IT CLEARS A FAILURE ONLY BY REWRITING THE RECORD. The failures line and the
#   coverage line are written together, in one call, last — so the page stops
#   being named as failed exactly when it has evidence that was read from it, and
#   the count and the page numbers they state cannot be persisted apart. A failed
#   retry writes no evidence and does not touch the record: the page stays named,
#   the coverage stays truthful, the project stays out of "done", and the page can
#   be retried again.
#
#   IT DERIVES NO STATUS. J13 remains the only authority: the project's status is
#   derived from the persisted rows plus this page's, exactly as the window path
#   derives it.
#
# WHAT IT CANNOT DO, SAID PLAINLY. There is no transaction across these writes
# (see the J16 report; the same order is used here) — evidence is written first
# and the record that names the page as failed is cleared last, so a crash in
# between leaves evidence persisted for a page the record still calls failed. That
# is DETECTED and refused, not repaired: the next retry of that page finds
# evidence rows already attributed to it (RETRY_EVIDENCE_ALREADY_PERSISTED) and
# stops rather than inserting a second copy. Two retries arriving at once can pass
# that check simultaneously and both insert; nothing here can prevent that, and
# this milestone does not claim otherwise.


@dataclass(frozen=True)
class RetryPlan:
    """Everything a retry decided from PERSISTED state, before any file was read.

    Returned by `plan_retry` so that the decision to read a page again — or the
    refusal — is available to a caller that must not download, render or analyse
    anything until it knows the page can be read. `page_number` is the page the
    contract accepted, which is the requested page whenever a plan exists.
    """

    project: dict
    drawing_set: dict
    drawing: dict
    coverage: PageCoverage
    failures: ParseFailures
    page_number: int
    persisted_members: tuple[dict, ...]
    persisted_connections: tuple[dict, ...]


def plan_retry(
    project_id: str, storage_path: str, *, page_number, document_id: str | None = None,
) -> RetryPlan:
    """Decides whether one page of this project may be read again.

    Reads persisted state only — no download, no rendering, no AI, no page image
    stored — so a request that must not be served is refused without spending a
    vision call. Raises `RetryRefused` with a named reason: an ambiguous drawing
    set, a different source file, no coverage record, a record this milestone
    cannot read, a record that disagrees with itself or with its counters, a page
    number that is not one 1-based page, a page past the document, a page the
    record does not name as failed, or a page that already has evidence.

    `document_id` (Milestone J63) is OPTIONAL and names which of the project's
    source documents the page belongs to, on exactly the terms
    `plan_continuation` accepts it: omitted is the project-level source and the
    checks this function has always applied, and named resolves the lineage
    that read that document. A retry is guard B of `_source_lineage` — the
    record it re-reads is the record of ONE document.
    """
    project = repo.get_project(project_id)
    if project is None:
        raise RetryRefused(
            CONTINUATION_DRAWING_SET_UNRESOLVED,
            f"there is no project {project_id!r} whose page could be read again",
        )

    drawing_set, drawing = _source_lineage(
        project_id, project, storage_path,
        document_id=document_id, refuse=RetryRefused,
    )

    coverage = coverage_from_warnings(project.get("warnings"))
    if coverage is None:
        raise RetryRefused(
            CONTINUATION_NO_COVERAGE_RECORD,
            "this project carries no PDF coverage record, so which pages have been read — and "
            "therefore whether this page failed — is unknown",
        )

    # Read on the same terms as the window path reads it: a failures line this
    # milestone cannot read is no record at all, and a project whose coverage
    # record states failures without one is a record it cannot act on.
    failures = parse_failures_from_warnings(project.get("warnings"))

    # The J17 contract decides the page itself, against both halves of the
    # record: a malformed page number, an unusable coverage record, a project
    # whose failures record names no pages, two halves that disagree, a page past
    # the document, and a page that is not named as failed.
    decision = check_retry_request(
        page_number=page_number, coverage=coverage, failures=failures,
    )
    if not decision.accepted:
        raise RetryRefused(decision.refusal_code, decision.detail)
    page_number = decision.page_number

    # Two records of the same fact must agree before either is rewritten: a
    # retry re-states the drawing set's counters from the coverage record, so a
    # set whose counters are already stale would be repaired silently by it.
    # Refused instead — the same rule, and the same refusal, as the window path.
    if (drawing_set.get("pages_analysed") != coverage.analysed_pages
            or drawing_set.get("total_pages") != coverage.total_pages):
        raise RetryRefused(
            CONTINUATION_RECORD_CONFLICT,
            f"the drawing set records pages_analysed={drawing_set.get('pages_analysed')!r} "
            f"total_pages={drawing_set.get('total_pages')!r}, but the coverage record states "
            f"analysed={coverage.analysed_pages} total={coverage.total_pages}; one of them is "
            f"stale, so which page this is cannot be trusted",
        )

    if drawing.get("page_count") is not None and drawing["page_count"] != coverage.total_pages:
        raise RetryRefused(
            CONTINUATION_DOCUMENT_TOTAL_MISMATCH,
            f"the drawing records {drawing['page_count']} page(s), but this drawing set has been "
            f"read as {coverage.total_pages} page(s); the source may have changed",
        )

    # A page the record names as failed is a page that produced NO evidence: a
    # parse failure persists no member and no connection. Evidence already
    # attributed to this exact page of this exact drawing therefore means an
    # earlier attempt wrote it and did not live to clear the record, and reading
    # the page again would duplicate it. Exact identity (drawing + page), no
    # fuzzy matching: rows that merely look similar are not counted here.
    already_persisted = repo.evidence_rows_for_page(drawing["id"], page_number)
    if already_persisted:
        tables = ", ".join(sorted({row["table"] for row in already_persisted}))
        raise RetryRefused(
            RETRY_EVIDENCE_ALREADY_PERSISTED,
            f"page {page_number} is recorded as a page whose response could not be read, but "
            f"{len(already_persisted)} evidence row(s) ({tables}) are already persisted against "
            f"it; the record and the evidence disagree, and re-reading the page would write a "
            f"second copy of evidence that already exists",
        )

    # `check_retry_request` accepted the page, so this is a readable record that
    # names it. `None` cannot reach here — a project without one is refused above.
    return RetryPlan(
        project=project,
        drawing_set=drawing_set,
        drawing=drawing,
        coverage=coverage,
        failures=failures,
        page_number=page_number,
        persisted_members=tuple(repo.member_rows_for_project(project_id)),
        persisted_connections=tuple(repo.connection_rows_for_project(project_id)),
    )


def retry_pdf_page(
    filepath: str, project_id: str, user_id: str, storage_path: str, *,
    page_number, document_id: str | None = None,
) -> dict:
    """Reads ONE page of a drawing set again, the one the record names as failed.

    The page image is NOT stored again: this module renders every page before it
    asks the model about it, so a page that was analysed — a parse failure
    included — already has the image a reviewer would look at, and storing it
    twice would record one page of one drawing twice.

    Returns the outcome of the read. `outcome` is `"PARSED"` when the page's
    response was readable — its evidence is persisted and the record no longer
    names the page as failed — or `"PARSE_FAILED"` when it was not, in which case
    nothing about the drawing set was written and the page is still named as
    failed and still retryable. Refuses (raises `RetryRefused`) without reading
    anything when the page may not be read again, and raises whatever the read
    itself raises when the page could not be rendered or the model could not be
    reached; neither writes anything.
    """
    plan = plan_retry(
        project_id, storage_path, page_number=page_number, document_id=document_id,
    )
    page = plan.page_number
    drawing_set_id = plan.drawing_set["id"]
    drawing_id = plan.drawing["id"]

    document_pages = page_count_of(filepath)
    if document_pages is None or document_pages != plan.coverage.total_pages:
        raise RetryRefused(
            CONTINUATION_DOCUMENT_TOTAL_MISMATCH,
            f"the file at {storage_path!r} states {document_pages!r} page(s), but this drawing set "
            f"has been read as {plan.coverage.total_pages} page(s); the source may have changed",
        )

    matcher = SectionMatcher()
    reference_identity = reference_data_projection(getattr(matcher, "reference_identity", None))

    # === Stage 1: the read — this page, and no other page of the document ===
    pages = analyze_pdf_pages(
        filepath, user_id, project_id, drawing_id, 1, first_page=page, store_page_image=False,
    )
    if len(pages) != 1 or pages[0].page_number != page:
        raise RetryRefused(
            RETRY_READ_MISMATCH,
            f"the read of page {page} of this drawing set returned "
            f"{[p.page_number for p in pages]} instead; evidence may only be persisted under the "
            f"page it was read from, so nothing is written",
        )
    extraction = pages[0]

    # === The page still cannot be read ===
    # The record already names this page, so re-stating it would claim an attempt
    # changed something, and inventing anything for the page is exactly what must
    # not happen. The attempt itself is recorded as one run of one page, and the
    # drawing set is left exactly as it was: still failed, still retryable, still
    # keeping the project out of "done".
    if extraction.parse_failed:
        run = repo.create_analysis_run(drawing_set_id, PDF_VISION_MODEL)

        # Milestone J23 — the attempt is recorded even though it produced nothing.
        # This page's reading failed, and a failed reading is a reading: recording it
        # is what stops the next attempt from being the only thing the record has ever
        # known about this page. It writes no engineering row, so it creates no review
        # state — a capture is not evidence and is deliberately not counted as any.
        _record_page_captures(
            pages,
            analysis_run_id=run["id"],
            project_id=project_id,
            drawing_set_id=drawing_set_id,
            drawing_id=drawing_id,
        )

        # Milestone J36 — this page read a second way (J28), under this attempt's own run.
        # This branch returns before any engineering persistence, so without its own call
        # here a parse-failed page would never have its annotation reading recorded — and
        # a parse-failed page is exactly the one whose escape hatch is a human. The
        # reading is of the FILE, which was read successfully either way: the AI's
        # response failed to parse, the bytes did not.
        _record_annotation_evidence(
            filepath,
            pages,
            project_id=project_id,
            drawing_id=drawing_id,
            analysis_run_id=run["id"],
        )

        repo.update_analysis_run(
            run["id"], status="completed",
            pages_processed=1, total_pages=document_pages, completed_at="now()",
            error_message=(
                f"page {page} was read again and its response could not be parsed as engineering "
                f"evidence; the drawing set's record is unchanged"
            ),
        )
        return {
            "page_number": page,
            "outcome": "PARSE_FAILED",
            "resolved": False,
            "retryable": True,
            "pages_processed": 1,
            "total_pages": plan.coverage.total_pages,
            "analysed_pages": plan.coverage.analysed_pages,
            "parse_failed_pages": plan.coverage.parse_failed_pages,
            "not_analysed_pages": plan.coverage.not_analysed_pages,
            "is_complete": plan.coverage.is_complete,
            "parse_failure_pages": list(plan.failures.page_numbers),
            "members_extracted": 0,
            "connections_extracted": 0,
            "review_required_count": None,
            # No write reached the project row, so this is the status already
            # persisted for it — not a new derivation, and not a claim.
            "project_status": plan.project.get("status"),
        }

    # === Stage 2: Validation — the same rules every other path applies ===
    raw_members, connections_raw = _flatten_pages(pages, drawing_id)
    validated = validate_extraction(raw_members, matcher)
    page_rows = _member_rows(
        validated["members"],
        project_id=project_id,
        drawing_id=drawing_id,
        reference_identity=reference_identity,
    )

    # === Cross-page identity, decided BEFORE anything is written ===
    # The same rule the window path applies: a mark already persisted for this
    # project may be the same member detailed on two sheets or a different member
    # entirely, and nothing in the data says which. Refused rather than merged or
    # inserted as a second copy — and refused before any write, so this leaves the
    # drawing set, the record and the run history untouched.
    persisted_marks = {row.get("mark") for row in plan.persisted_members if row.get("mark")}
    colliding_marks = sorted({row["mark"] for row in page_rows} & persisted_marks)
    if colliding_marks:
        raise RetryRefused(
            RETRY_MEMBER_MARK_COLLISION,
            f"{len(colliding_marks)} member mark(s) read from page {page} are already persisted "
            f"for this project ({', '.join(colliding_marks[:10])}"
            f"{', …' if len(colliding_marks) > 10 else ''}); member identity across pages has no "
            f"merge rule, so this page is refused rather than persisted as a second copy",
        )

    # === Stage 3: Engineering data — this page's evidence, then the record ===
    analysis_run = repo.create_analysis_run(drawing_set_id, PDF_VISION_MODEL)
    analysis_run_id = analysis_run["id"]

    # Milestone J23 — the retry's own reading, recorded before its evidence. A retry
    # is a new run, so this is a new capture row for a page that may already have one:
    # the earlier attempt is left exactly as it was, which is what makes the history of
    # a page's readings survive a successful recovery.
    _record_page_captures(
        pages,
        analysis_run_id=analysis_run_id,
        project_id=project_id,
        drawing_set_id=drawing_set_id,
        drawing_id=drawing_id,
    )

    # Milestone J36 — the retried page, read a second way (J28), under this attempt's run.
    # One page is the whole scope: the retry read one page and no other page of this
    # document was touched by it. Written before the record stops naming the page as
    # failed, because that write is what makes the page permanently unreadable again.
    _record_annotation_evidence(
        filepath,
        pages,
        project_id=project_id,
        drawing_id=drawing_id,
        analysis_run_id=analysis_run_id,
    )

    inserted_members = repo.insert_members(page_rows)
    if inserted_members:
        repo.insert_review_items([
            {"project_id": project_id, "item_type": "steel_member", "item_id": row["id"],
             "status": row["review_status"]}
            for row in inserted_members
        ])

    connections_extracted, _, connection_review_statuses = _persist_connections(
        connections_raw,
        project_id=project_id,
        drawing_id=drawing_id,
        member_rows=[*plan.persisted_members, *inserted_members],
    )

    # === The reconciled record: this page is no longer a failure ===
    # The page was already INSIDE `analysed` (a parse failure is a reading
    # failure of a page that was read), so re-reading it does not change how many
    # pages of the document have been read — only how many of them had no
    # readable response. `not_analysed` counts the pages never read, which this
    # changes not at all, so `total = analysed + not_analysed` still holds and
    # J16's next window is exactly where it was. `without` raises if this page was
    # not named as failed, which cannot happen here: the plan refused every page
    # the record does not name.
    reconciled_failures = plan.failures.without(page)
    reconciled_coverage = PageCoverage(
        total_pages=plan.coverage.total_pages,
        analysed_pages=plan.coverage.analysed_pages,
        parse_failed_pages=plan.coverage.parse_failed_pages - 1,
        not_analysed_pages=plan.coverage.not_analysed_pages,
    )

    # Every total is recomputed over the PERSISTED rows plus this page's, so a
    # project summary is a function of its evidence and can never double-count.
    total_weight_kg = (
        sum(row.get("total_weight_kg") or 0 for row in plan.persisted_members)
        + sum(row["total_weight_kg"] or 0 for row in page_rows)
    )
    unique_sections = {
        row.get("section_name") for row in plan.persisted_members if row.get("section_name")
    }
    unique_sections |= {row["section_name"] for row in page_rows if row["section_name"]}

    page_unmatched = {
        row["section_name_raw"] for row in page_rows
        if not row["section_name"] and row["section_name_raw"]
        and row.get("section_resolution") != SUFFIX_FALLBACK
    }
    unmatched_sections = sorted(set(plan.project.get("unmatched_sections") or []) | page_unmatched)

    # Milestone J13: the terminal state is derived from ALL the evidence — the
    # persisted rows are read back and re-stated with this page's — so a project
    # cannot reach "done" by having one page read again.
    member_review_statuses = (
        [review_status_of(row) for row in plan.persisted_members]
        + [review_status_of(row) for row in page_rows]
    )
    connection_statuses = (
        [review_status_of(row) for row in plan.persisted_connections] + connection_review_statuses
    )
    review_required_count = (
        sum(1 for status in member_review_statuses if status == "review_required")
        + sum(1 for status in connection_statuses if status == "review_required")
    )
    warnings = _accumulated_warnings(
        plan.project.get("warnings"), [issue.message for issue in validated["issues"]],
        reconciled_coverage, reconciled_failures,
    )
    status = derive_project_status(
        member_review_statuses=member_review_statuses,
        connection_review_statuses=connection_statuses,
        unmatched_sections=unmatched_sections,
        warnings=warnings,
        coverage=reconciled_coverage,
    ).status

    repo.update_drawing_set(
        drawing_set_id, status="analyzed",
        pages_analysed=reconciled_coverage.analysed_pages,
        members_found=len(member_review_statuses),
        review_required_count=review_required_count,
        total_pages=document_pages,
    )
    repo.update_analysis_run(
        analysis_run_id, status="completed",
        pages_processed=1, total_pages=document_pages, completed_at="now()",
    )
    # Clearing the failure IS this write: the record that names page `page` as
    # failed stops naming it here, in the same call that restates the count it is
    # checked against, and it is written last — after every piece of evidence the
    # page produced.
    repo.update_project_summary(
        project_id,
        status=status,
        total_members=len(member_review_statuses),
        total_unique_sections=len(unique_sections),
        total_connections=len(plan.persisted_connections) + connections_extracted,
        total_weight_kg=total_weight_kg,
        total_weight_tonnes=round(total_weight_kg / 1000, 3),
        unmatched_sections=unmatched_sections,
        warnings=warnings,
    )
    # E2E-001N — reconcile the project row to its whole document set (see below).
    reconcile_project_summary(project_id)

    return {
        "page_number": page,
        "outcome": "PARSED",
        "resolved": True,
        # The page is no longer named as failed, so a retry of it is refused as a
        # page that is already answered — not because this outcome is terminal.
        "retryable": False,
        "pages_processed": 1,
        "total_pages": reconciled_coverage.total_pages,
        "analysed_pages": reconciled_coverage.analysed_pages,
        "parse_failed_pages": reconciled_coverage.parse_failed_pages,
        "not_analysed_pages": reconciled_coverage.not_analysed_pages,
        "is_complete": reconciled_coverage.is_complete,
        "parse_failure_pages": list(reconciled_failures.page_numbers),
        "members_extracted": len(page_rows),
        "connections_extracted": connections_extracted,
        "review_required_count": review_required_count,
        "total_weight_kg": total_weight_kg,
        "excluded_non_steel": len(validated["excluded"]),
        "project_status": status,
        "warnings": warnings,
    }


# ======================================================================================
# E2E-001N — THE PROJECT SUMMARY OF A MULTI-DOCUMENT PROJECT.
#
# Before this milestone every write to a project row described ONE document.
# `parse_pdf_and_save` derives a status from the evidence it just persisted and the coverage
# it just read, then writes it with absolute totals — `total_members=len(members_to_insert)`
# and so on. That is correct while a project holds one document, and it stops being correct
# the moment it holds two: the second run would replace the project's totals with its own,
# and whichever document finished LAST would define the whole project.
#
# WHAT THIS FUNCTION DOES. It is called after each run's own write, and it makes the project
# row describe the document SET. It reads nothing new: every fact it uses is already
# persisted — per-document run state from `document_extraction_states` (project_documents →
# drawings → drawing_sets → analysis_runs), and the project's own members and connections.
#
# A SINGLE-DOCUMENT PROJECT IS UNCHANGED. Fewer than two documents means the run's own
# summary already WAS the project's, so this returns without writing. That is deliberate:
# the existing behaviour is the first branch rather than something preserved by accident.
#
# WHAT IT CANNOT DO YET, STATED RATHER THAN GLOSSED. Page coverage is established per
# document by the run that read it, and no cross-document coverage record is persisted. This
# function therefore passes `coverage=None`, which `derive_project_status` treats exactly as
# an incomplete coverage — so a multi-document project can reach `review` but not `done`
# until cross-document coverage exists. That is the conservative direction on purpose: it can
# under-claim completion and cannot over-claim it, which is the only acceptable error here.
# ======================================================================================
def reconcile_project_summary(project_id: str) -> None:
    """Make the project row describe its whole document set, not the run that wrote last.

    A store that cannot answer "which documents does this project have, and what read them"
    is a store this function has no basis to reconcile, so it returns and leaves the run's
    own summary standing — which is the pre-E2E-001N behaviour rather than a wrong one. The
    production repository answers it; a partial double that predates this milestone does not.
    """
    reader = getattr(repo, "document_extraction_states", None)
    if reader is None:
        return
    states = reader(project_id)
    if len(states) < 2:
        # The pre-E2E-001N behaviour, and the first branch on purpose.
        return

    members = repo.member_rows_for_project(project_id)
    connections = repo.connection_rows_for_project(project_id)

    run_states = [state.get("run_status") for state in states]
    if any(state is None or state == "running" for state in run_states):
        status = "processing"
    elif any(state == "failed" for state in run_states):
        status = "failed"
    else:
        status = derive_project_status(
            member_review_statuses=[row.get("review_status") for row in members],
            connection_review_statuses=[row.get("review_status") for row in connections],
            unmatched_sections=(),
            warnings=(),
            coverage=None,
        ).status

    total_weight_kg = sum(float(row.get("total_weight_kg") or 0) for row in members)
    sections = {row.get("section_name") for row in members if row.get("section_name")}

    repo.update_project_summary(
        project_id,
        status=status,
        total_members=len(members),
        total_unique_sections=len(sections),
        total_connections=len(connections),
        total_weight_kg=total_weight_kg,
        total_weight_tonnes=round(total_weight_kg / 1000, 3),
        # No single document's number describes a set of documents, so neither reference is
        # taken from whichever run finished last. `None` states that no one document is the
        # project's; keeping the last run's value would have stated the opposite.
        engineer_reference=None,
        structural_engineer=None,
    )
