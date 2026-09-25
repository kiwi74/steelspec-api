"""
DXF parser — extracts structural steel members AND connections
(bolts, plates, welds) from a DXF file and writes the results
directly into Supabase, scoped to the project that triggered the
extraction.

THE DXF AUTHORITY RULE (see resolve_drawn_section below): the DRAWING states
the member's section, and the reference catalogue may CONFIRM that identity but
never CHANGE it. The catalogue does not always answer for the token the drawing
states: when its table carries no row for a bare token, SectionMatcher's
".0"..".9" suffix fallback answers with a DIFFERENT section ("310UB40" is
answered with the 310UB40.4 row). That is a name resolution, not this member's
identity, so this parser refuses such a row as identity AND as properties
alike: the member keeps the section the drawing states, carries no catalogue
family and NO calculated weight (never a substituted section's weight), and is
held for review under the same refusal note the PDF production path's section
boundary already writes. A member is never renamed to a section the drawing did
not specify, and no substituted tonnage is ever added to a project total.

WHICH COLUMN CARRIES THAT IDENTITY (Milestone J7, Option C): section_name is
FK-BOUND to steel_sections(name), so it may hold a section the catalogue carries
and nothing else — and the catalogue answered for this member only on an EXACT
resolution. Every other member is persisted with section_name NULL and its drawn
identity in section_name_raw, which is not FK-bound: NULL there already meant
"the catalogue did not answer for this section", so no reader has to learn a new
state. An unresolved token is now persisted this way rather than discarded — a
member the drawing states is evidence, and dropping it removed it from the
schedule, the report and the reviewer's queue at once. app/validation/rules.py
applies the same rule on the PDF path, so a member's authority fields mean the
same thing whichever reader the drawing came from.

WHAT THIS READER DOES NOT READ (Milestone J11): this module can see evidence the
drawing states and fail to turn it into a row, and until J11 several of those
failures produced NOTHING — no row, no review item, no warning, no count. The
module had no issue channel of any kind. It now records every such discard into
app/validation/dxf_drop_accounting, persists the accounting on the project
beside the PDF path's own `unmatched_sections`/`warnings`, and reports it in the
summary it returns, so that a DXF-derived report can never imply that all
drawing evidence was extracted when something was not. The accounting NEVER
changes what is extracted: the same members, lengths, sections and weights are
produced as before, and nothing is invented to stand in for what was discarded.
See the docstring of app/validation/dxf_drop_accounting for the record model and
the reason vocabulary.
"""
import math
import re
from dataclasses import dataclass

import ezdxf
from ezdxf.entities import Line, LWPolyline, Polyline, Text, MText

from app.engineering_data.section_matcher import (
    RESOLUTION_EXACT,
    RESOLUTION_NONE,
    RESOLUTION_SUFFIX_FALLBACK,
    SectionMatcher,
    reference_data_projection,
)
from app.supabase_client import supabase
from app.validation.dxf_drop_accounting import (
    KIND_CONNECTION_ASSOCIATION,
    KIND_CONNECTION_CALLOUT,
    KIND_CONNECTION_EVIDENCE,
    KIND_MEMBER_CALLOUT,
    KIND_SOURCE_GEOMETRY,
    REASON_NO_CHILD_EVIDENCE_PARSED,
    REASON_NO_MEMBER_WITHIN_THRESHOLD,
    REASON_NO_PAIRED_LINE,
    REASON_UNRECOGNISED_TEXT_LAYER,
    REASON_UNSUPPORTED_CALLOUT_SHAPE,
    REASON_UNSUPPORTED_SOURCE_GEOMETRY,
    DXFDropAccounting,
)
from app.validation.project_status import derive_project_status, review_status_of
from app.validation.rules import SUBSTITUTION_NOTE, UNRESOLVED_SECTION_NOTE

STEEL_LAYER_PATTERNS = [r"^S[-_]?BEAM", r"^S[-_]?STEEL", r"^S[-_]?COL", r"^S[-_]?BRAC", r"^STR", r"^STEEL"]
STEEL_TEXT_LAYER_PATTERNS = [r"^S[-_]?TEXT", r"^S[-_]?ANNO", r"^S[-_]?NOTE"]
CONNECTION_LAYER_PATTERNS = [r"^S[-_]?CONN", r"^S[-_]?DET", r"^CONN", r"^DETAIL"]
IGNORE_LAYER_PATTERNS = [r"^A[-_]", r"^DEFPOINTS", r"^0$", r"DIMS?$", r"GRID"]


@dataclass(frozen=True)
class DrawnSectionResolution:
    """
    What ONE drawn section token means — decided once, here.

    `token_raw` is the token exactly as the drawing's own text gave it, kept so
    nothing the drawing stated is lost (the persisted member row carries it
    verbatim too, in `section_name_raw`).

    `identity` is the AUTHORITATIVE section identity for this member: the
    catalogue's own spelling of the same section on an exact resolution, or the
    DRAWN token whenever the catalogue did not confirm it. It is never the name
    of a different section.

    Being this member's identity is not the same as being persistable in
    section_name, which is FK-bound to the catalogue (see the module docstring):
    a caller writes `identity` into that column only on an EXACT resolution, and
    preserves it in section_name_raw otherwise. The distinction is the caller's
    to make — this function reports what the section IS, not where it may live.

    `enrichment_row` is the catalogue row whose standard properties may be used
    — present ONLY for an exact resolution, and None whenever the catalogue did
    not answer for the drawn section itself.

    `refused_candidate` is the different section the catalogue offered instead,
    recorded as the provenance of a refusal, or None.

    `resolution` is the matcher's own route — EXACT, SUFFIX_FALLBACK or NONE.
    This module defines no such vocabulary: the three constants are imported
    from app/engineering_data/section_matcher, so the two cannot drift apart.
    """
    token_raw: str
    identity: str
    enrichment_row: dict | None
    refused_candidate: str | None
    resolution: str


def resolve_drawn_section(matcher, token_raw: str) -> DrawnSectionResolution:
    """
    THE DXF AUTHORITY RULE, as one function.

    The drawing states the section; the catalogue may CONFIRM that identity but
    never CHANGE it. So this boundary asks the matcher HOW the token resolved
    (SectionMatcher.resolve), not merely which row came back: match() reports
    only the row, and a suffix-fallback row is indistinguishable from an exact
    one, which is exactly how a different catalogue section could otherwise
    become this member's identity without anyone noticing.

      EXACT            the requested token IS a catalogue key. The catalogue's
                       own spelling of that same section is used, exactly as
                       this parser has always used it, and that row's standard
                       properties may enrich the member.
      SUFFIX_FALLBACK  only a ".0"..".9" extension of the token resolved, i.e.
                       the catalogue offered a DIFFERENT section. The drawn
                       token stays the identity; the offered row is refused as
                       identity and as properties alike; `refused_candidate`
                       records what was refused so a human can act on it.
      NONE             nothing resolved. The drawn token is preserved and the
                       existing unmatched handling is retained by the caller.

    A matcher that cannot report a resolution cannot show whether its row is
    the drawn section or a substitution, so none of its rows may identify or
    enrich a member here. That case is refused loudly rather than quietly
    enriching a member from an unverified row — this boundary never degrades
    into trusting an unqualified lookup.
    """
    resolve = getattr(matcher, "resolve", None)
    if resolve is None:
        raise AttributeError(
            "the DXF section authority boundary needs a matcher that reports HOW each "
            "token resolved (SectionMatcher.resolve). A matcher that can only match() "
            "cannot show whether its row is the drawn section or a substitution, so no "
            "row of its may be used to identify or enrich a member."
        )

    outcome = resolve(token_raw)

    if outcome.resolution == RESOLUTION_EXACT:
        return DrawnSectionResolution(
            token_raw=token_raw,
            identity=outcome.catalogue_row["name"],
            enrichment_row=outcome.catalogue_row,
            refused_candidate=None,
            resolution=RESOLUTION_EXACT,
        )

    # SUFFIX_FALLBACK and NONE alike: the catalogue did not answer for the drawn
    # token, so it has no row to offer this member. The identity is the section
    # the DRAWING states — the matcher's canonical form of it, which carries no
    # section meaning and never falls back (see SectionMatch.drawing_token).
    refused_row = outcome.catalogue_row
    return DrawnSectionResolution(
        token_raw=token_raw,
        identity=outcome.drawing_token,
        enrichment_row=None,
        refused_candidate=refused_row["name"] if refused_row else None,
        resolution=outcome.resolution,
    )


def classify_layer(name: str) -> str:
    name = name.upper().strip()
    for p in IGNORE_LAYER_PATTERNS:
        if re.match(p, name): return "ignore"
    for p in CONNECTION_LAYER_PATTERNS:
        if re.match(p, name): return "connection"
    for p in STEEL_TEXT_LAYER_PATTERNS:
        if re.match(p, name): return "steel_text"
    for p in STEEL_LAYER_PATTERNS:
        if re.match(p, name): return "steel"
    return "unknown"


def distance(p1, p2):
    return math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)


def _entity_text(entity):
    """
    The text a TEXT/MTEXT entity states, stripped, or None for any other entity.

    One definition, used by every text read in this module (member callouts, the
    unread-layer accounting, and connection callouts), so the three cannot come
    to disagree about what a given entity says.
    """
    if isinstance(entity, Text):
        return entity.dxf.text.strip()
    if isinstance(entity, MText):
        return entity.plain_text().strip()
    return None


def _callout_context(text: str, limit: int = 80) -> str:
    """
    A callout as one short line, for a drop record's context.

    Whitespace is collapsed so a multi-line MText stays a single record line,
    and the result is capped: the context identifies what was discarded, it is
    not a copy of the drawing's text.
    """
    collapsed = " ".join((text or "").split())
    return collapsed[:limit]


def parse_connection_text(text: str) -> dict:
    result = {"bolt_groups": [], "plates": [], "welds": [], "connection_type": "unspecified"}

    bolt_pattern = r"(\d+)\s*[x×]\s*M(\d+)\s*(?:HD\s*)?(?:Gr\.?\s*)(\d+\.?\d*)"
    for qty, size, grade in re.findall(bolt_pattern, text, re.IGNORECASE):
        result["bolt_groups"].append({"bolt_size": f"M{size}", "bolt_grade": grade, "quantity": int(qty)})
        if result["connection_type"] == "unspecified":
            result["connection_type"] = "bolted"

    # PLATE EVIDENCE (Milestone J8B). A plate callout states a TYPE and a
    # THICKNESS and nothing else: the pattern below captures exactly those two,
    # and the DXF text model this parser reads carries no plate-material field
    # at all. So a plate grade is ABSENT for every plate extracted here, and
    # absence is persisted as None rather than filled in. The value this line
    # used to write ("300") was a module literal: it was written for every plate
    # in every DXF, including the plates whose drawing stated no grade, and a
    # persisted value is a claim about the drawing that nothing downstream can
    # tell from a stated one. Nothing is inferred in its place — not from the
    # connection's grade, the bolt grade, the member grade, the project, the
    # plate's type and not from its thickness. The same rule already governs the
    # PDF path in app/pipeline.py; it is written out here rather than imported
    # because that module is the vision pipeline, and a DXF reader must not
    # depend on the AI analysis stage to know what it did not read.
    plate_pattern = r"(end\s*plate|base\s*plate|gusset|stiffener|cleat)\s*(\d+)\s*mm"
    for plate_type, thickness in re.findall(plate_pattern, text, re.IGNORECASE):
        # A plate thickness is a POSITIVE dimension stated in mm, and the
        # callout states it. "0 mm" is not a thickness any drawing stated, so it
        # is withheld as None instead of persisted as a measured zero — the same
        # rule app/pipeline.py's extraction helper applies to the PDF path. The
        # plate row is still written, carrying the type the drawing did state.
        stated_thickness = float(thickness)
        result["plates"].append({
            "plate_type": plate_type.lower().replace(" ", "_"),
            "thickness": stated_thickness if stated_thickness > 0 else None,
            "grade": None,
        })

    weld_pattern = r"(\d+)\s*mm\s*(fillet|butt)\s*weld"
    for size, weld_type in re.findall(weld_pattern, text, re.IGNORECASE):
        wt = "fillet" if "fillet" in weld_type.lower() else "butt"
        result["welds"].append({"weld_type": wt, "size": float(size)})
        if result["connection_type"] == "bolted":
            result["connection_type"] = "bolted_and_welded"
        elif result["connection_type"] == "unspecified":
            result["connection_type"] = "welded"

    return result


def find_nearby_member_ids(pos, member_records, max_dist=3000, max_count=2):
    scored = []
    for rec in member_records:
        d = distance(pos, rec["midpoint"])
        if d <= max_dist:
            scored.append((d, rec["id"]))
    scored.sort(key=lambda x: x[0])
    return [member_id for _, member_id in scored[:max_count]]


def parse_dxf_and_save(filepath: str, project_id: str, matcher=None) -> dict:
    """
    Extract one DXF into steel_members/connections for `project_id`.

    `matcher` defaults to the live SectionMatcher and exists so the section
    authority boundary can be exercised against a matcher whose catalogue rows
    are supplied, rather than reaching Supabase — the same injection seam
    app.validation.rules.validate_extraction() and
    app.cad_engine.real_member_adapter already use. Production callers pass
    nothing and get the live matcher, exactly as before.
    """
    if matcher is None:
        matcher = SectionMatcher()
    section_regex = matcher.get_section_regex()

    # The reference-data identity of the catalogue THIS matcher loaded
    # (Milestone J6) — read once, while the genuine matcher that decides every
    # drawn section below is still in hand, and carried verbatim onto every
    # member row it produces. Never recomputed downstream: the live table is
    # unversioned and its content can change after this run, at which point a
    # fresh digest would no longer describe the rows these resolutions used.
    # None when the matcher records no identity — absence persisted as absence.
    # Provenance only; nothing may read it to decide a section.
    reference_identity = reference_data_projection(getattr(matcher, "reference_identity", None))

    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()
    layer_classes = {layer.dxf.name: classify_layer(layer.dxf.name) for layer in doc.layers}

    # Every discard this run makes is recorded here, and only here (Milestone
    # J11). It is written into the project record and the returned summary below
    # — a discard that reaches neither has been silently absorbed, which is the
    # state this milestone exists to end.
    accounting = DXFDropAccounting()

    geometries = []
    for e in msp:
        # UNSUPPORTED SOURCE GEOMETRY (Milestone J11). This reader's whole
        # geometry vocabulary is LINE (see the geometry check below and the
        # connection loop's text-only read): a member or plate drawn as a
        # polyline looks to this reader exactly like a drawing with no members,
        # which is the failure this accounting exists to make visible rather
        # than silent. Recorded whatever layer it sits on — including the
        # default layer "0", which is where app/drawing_generator/dxf_builder.py
        # puts every polyline it writes, so reading SteelSpec's own output back
        # now reports why it extracted nothing instead of quietly returning
        # zero. This is a statement about shape, NOT a claim that the entity is
        # a member: nothing here is turned into a member row.
        if isinstance(e, (LWPolyline, Polyline)):
            accounting.record(
                KIND_SOURCE_GEOMETRY, REASON_UNSUPPORTED_SOURCE_GEOMETRY,
                context=f"{type(e).__name__} on layer '{e.dxf.layer}'",
            )
            continue

        cls = layer_classes.get(e.dxf.layer, "unknown")
        if cls not in ("steel", "unknown"):
            continue
        if isinstance(e, Line):
            start = (e.dxf.start.x, e.dxf.start.y)
            end = (e.dxf.end.x, e.dxf.end.y)
            length = distance(start, end)
            if length < 100:
                continue
            geometries.append({"start": start, "end": end, "length": length, "layer": e.dxf.layer})

    texts = []
    for e in msp:
        cls = layer_classes.get(e.dxf.layer, "unknown")
        if cls not in ("steel", "steel_text"):
            # Not read as member text. A text on such a layer is recorded as
            # discarded member evidence ONLY when it states a section this
            # reader's own vocabulary recognises: dimensions, notes, detail
            # tags and title-block text state no section and are simply not
            # member callouts, and reporting them would call ordinary
            # annotation "not extracted". Connection layers are excluded
            # because their callouts are read by the connection loop below,
            # not dropped here.
            if cls != "connection":
                unread = _entity_text(e)
                if unread and section_regex.findall(unread):
                    accounting.record(
                        KIND_MEMBER_CALLOUT, REASON_UNRECOGNISED_TEXT_LAYER,
                        context=f"{unread} on layer '{e.dxf.layer}'",
                    )
            continue
        text = _entity_text(e)
        if text:
            texts.append({"text": text, "pos": (e.dxf.insert.x, e.dxf.insert.y)})

    members_to_insert = []
    member_midpoints = []
    used = set()
    counter = 0

    for t in texts:
        matches = section_regex.findall(t["text"])
        if not matches:
            continue

        # The one catalogue interaction on the member path, and the only thing
        # that may say what this drawn token means — see resolve_drawn_section.
        drawn = resolve_drawn_section(matcher, matches[0])

        # An unresolved drawn section was discarded right here by a `continue`, so
        # a member the drawing states vanished from the schedule entirely. It is
        # persisted instead (Milestone J7): a gap in the reference catalogue is
        # not grounds for deleting the drawing's own evidence. It carries no
        # catalogue identity, family or weight and is held for review — the same
        # treatment every other section the catalogue did not answer for already
        # received on both paths.

        best_idx, best_dist = None, float("inf")
        for i, g in enumerate(geometries):
            if i in used:
                continue
            mid = ((g["start"][0] + g["end"][0]) / 2, (g["start"][1] + g["end"][1]) / 2)
            d = distance(t["pos"], mid)
            if d < best_dist:
                best_dist, best_idx = d, i

        if best_idx is not None and best_dist <= 2000:
            used.add(best_idx)
            g = geometries[best_idx]
            counter += 1

            # WHICH identity may enter the FK-bound column (Milestone J7,
            # Option C). section_name references steel_sections(name), so it
            # may carry the catalogue's own name for the drawn section and
            # nothing else — which means an EXACT resolution is the only case
            # that may populate it. For a refused suffix fallback the drawn
            # token is not a catalogue key and the candidate is a DIFFERENT
            # section, so writing either there would substitute one identity
            # for another or violate the FK outright; for NONE the catalogue
            # offered nothing at all. Both persist NULL. The member is not
            # anonymised by this: its drawn identity is in section_name_raw
            # below, which is not FK-bound.
            catalogue_identity = (
                drawn.identity if drawn.resolution == RESOLUTION_EXACT else None
            )

            row = {
                "project_id": project_id,
                "mark": f"M{counter}",
                "section_name": catalogue_identity,
                "section_name_raw": drawn.token_raw,
                "length_mm": round(g["length"], 0),
                # The drawing's OWN grade evidence, or None. This path extracts
                # none — it reads section tokens, not material callouts — so
                # absence is persisted as absence, explicitly and by key. It is
                # never defaulted to "300PLUS" (the hardcode this replaces), never
                # inherited from another member, and never inferred from the
                # section, the family or the catalogue: a grade the drawing did
                # not state is not this system's to supply, and a persisted
                # "300PLUS" that nobody read is indistinguishable from one the
                # engineer did.
                "grade": None,
                "quantity": 1,
                "confidence": "medium",
                "source_layer": g["layer"],
                # HOW the drawn section resolved (Milestone J5), from the one
                # resolve_drawn_section call above — never a second resolution.
                # All three states reach this row now: NONE included, since J7
                # stopped discarding it. The refused candidate is provenance
                # only, and is None for EXACT and for NONE.
                "section_resolution": drawn.resolution,
                "section_substituted_candidate": drawn.refused_candidate,
                # WHICH reference dataset this row's section was resolved
                # against (Milestone J6), read verbatim from the same matcher
                # that answered for `drawn` above. Provenance ONLY — it changes
                # no identity, no family and no weight, and is never recomputed
                # from a later catalogue read. NULL means no identity was
                # recorded for this row.
                "reference_data_identity": reference_identity,
            }

            if drawn.enrichment_row is None:
                # The catalogue did not answer for this section: a refused
                # substitution (SUFFIX_FALLBACK) or nothing at all (NONE). In
                # both cases there is no row to enrich from, so this member
                # carries no catalogue family and NO calculated weight — never
                # the refused substitute's weight, and never a fabricated zero
                # that would read as "this member weighs nothing". It is held for
                # review, and the note says which of the two refusals this is.
                if drawn.resolution == RESOLUTION_SUFFIX_FALLBACK:
                    note = SUBSTITUTION_NOTE.format(
                        token=drawn.token_raw, candidate=drawn.refused_candidate,
                    )
                else:
                    note = UNRESOLVED_SECTION_NOTE.format(token=drawn.token_raw)
                row.update({
                    "section_family": None,
                    "weight_per_metre": None,
                    "total_weight_kg": None,
                    "review_status": "review_required",
                    "notes": note,
                })
            else:
                # EXACT: the catalogue answered for the drawn section itself,
                # so its standard properties are used exactly as before.
                weight = drawn.enrichment_row.get("weight_per_metre")
                row.update({
                    "section_family": drawn.enrichment_row["family"],
                    # A catalogue row with no evidenced weight keeps NULL. It is
                    # NOT a zero-weight member: a persisted 0 is indistinguishable
                    # from a section that genuinely weighs nothing, and reads
                    # downstream as a real measurement. Same for its total — no
                    # total is calculated from a weight nobody has.
                    "weight_per_metre": weight,
                    "total_weight_kg": (
                        round((g["length"] / 1000) * weight, 2) if weight is not None else None
                    ),
                })

            members_to_insert.append(row)
            mid = ((g["start"][0] + g["end"][0]) / 2, (g["start"][1] + g["end"][1]) / 2)
            member_midpoints.append(mid)
        else:
            # THE PRIMARY SILENT DROP (Milestone J11). The callout WAS recognised
            # as a section by this reader's own vocabulary, but no unused LINE
            # lay within the pairing distance, so the member the drawing states
            # is not written. Until this else existed, this branch fell through
            # in silence — the row, the schedule entry and the reviewer's item
            # all simply did not exist, and nothing said so. The discard is now
            # recorded. Nothing is fabricated in its place: no member row, no
            # length from the text, no weight, and no zero — the member stays
            # unextracted and the report says so.
            accounting.record(
                KIND_MEMBER_CALLOUT, REASON_NO_PAIRED_LINE, context=drawn.token_raw,
            )

    member_records = []
    if members_to_insert:
        result = supabase.table("steel_members").insert(members_to_insert).execute()
        inserted = result.data or []
        for rec, midpoint in zip(inserted, member_midpoints):
            member_records.append({"id": rec["id"], "midpoint": midpoint})

    # A refused or unresolved member's tonnage is NOT_CALCULATED rather than
    # zero, so the project total is a lower bound, never a substituted figure —
    # the same roll-up app/pipeline.py performs for the PDF path.
    total_weight_kg = sum(m["total_weight_kg"] or 0 for m in members_to_insert)
    # Only a catalogue identity is a distinct section (Milestone J7). An
    # unresolved or refused member now persists with section_name NULL, and NULL
    # is the ABSENCE of a section identity — counting it here would report one
    # more unique section than the drawing set contains, and would report the
    # same phantom section for every project that has one. app/pipeline.py counts
    # it the same way.
    unique_sections = len(set(m["section_name"] for m in members_to_insert if m["section_name"]))

    connections_extracted = 0
    # The review state each connection row carries, collected as it is written
    # (Milestone J13). This reader states no connection review state at all, so
    # every entry here is None — which is exactly what the status derivation
    # must be told, rather than being handed a reassuring default.
    connection_review_statuses = []
    for e in msp:
        cls = layer_classes.get(e.dxf.layer, "unknown")
        if cls != "connection":
            continue

        text = _entity_text(e)
        if text is None:
            # Not a text entity. This reader reads connection callouts as TEXT
            # only, so drawn connection geometry (plate outlines, hole circles,
            # leader lines) is not read here — by design, and unchanged. It is
            # NOT reported as discarded evidence: a circle is a hole symbol and a
            # leader is a pointer, neither of which this reader has ever read or
            # is expected to read. Polylines, the shape a plate is drawn in, are
            # already accounted for by the source-geometry pass above.
            continue
        pos = (e.dxf.insert.x, e.dxf.insert.y)

        if not text:
            continue

        has_bolts = bool(re.search(r"M\d+", text))
        has_plate = bool(re.search(r"plate", text, re.IGNORECASE))
        has_weld = bool(re.search(r"weld", text, re.IGNORECASE))
        if not (has_bolts or has_plate or has_weld):
            # A connection callout that states none of the shapes this gate
            # accepts. The gate is deliberate and is NOT changed here — but the
            # omission is no longer invisible: the callout is recorded, so a
            # reader of the report can tell that connection evidence the drawing
            # states was not extracted. Recorded as UNSUPPORTED_BY_DESIGN rather
            # than as a failure, which is what it is.
            accounting.record(
                KIND_CONNECTION_CALLOUT, REASON_UNSUPPORTED_CALLOUT_SHAPE,
                context=_callout_context(text),
            )
            continue

        parsed = parse_connection_text(text)
        if not (parsed["bolt_groups"] or parsed["welds"] or parsed["plates"]):
            # Passed the gate but no child evidence could be read out of it: the
            # callout states bolts, a plate or a weld, and this reader's own
            # patterns did not recognise the statement. The connection row is
            # still written below, exactly as before — but its detail is absent,
            # and that absence is now recorded rather than only visible to
            # someone who notices the row is empty.
            accounting.record(
                KIND_CONNECTION_EVIDENCE, REASON_NO_CHILD_EVIDENCE_PARSED,
                context=_callout_context(text),
            )
        grid_match = re.search(r"\b([A-Z]\d{1,2})\b", text)

        conn_row = {
            "project_id": project_id,
            "connection_type": parsed["connection_type"],
            "location": {"x": pos[0], "y": pos[1]},
            "grid_reference": grid_match.group(1) if grid_match else None,
            "description": text.replace("\n", " — ")[:500],
            "confidence": "medium",
            "notes": "Extracted from DXF connection callout",
        }
        conn_result = supabase.table("connections").insert(conn_row).execute()
        connection_id = conn_result.data[0]["id"]
        connections_extracted += 1
        connection_review_statuses.append(review_status_of(conn_row))

        if parsed["bolt_groups"]:
            supabase.table("bolt_groups").insert([{**bg, "connection_id": connection_id} for bg in parsed["bolt_groups"]]).execute()
        if parsed["welds"]:
            supabase.table("weld_details").insert([{**w, "connection_id": connection_id} for w in parsed["welds"]]).execute()
        if parsed["plates"]:
            supabase.table("connection_plates").insert([{**p, "connection_id": connection_id} for p in parsed["plates"]]).execute()

        nearby_ids = find_nearby_member_ids(pos, member_records)
        if nearby_ids:
            supabase.table("connection_members").insert([{"connection_id": connection_id, "member_id": mid} for mid in nearby_ids]).execute()
        else:
            # The connection WAS extracted; it just joins nothing. Recorded so a
            # reader can tell an unlinked connection from a linked one — the row
            # itself reads the same either way, and no link is invented to fill
            # the gap.
            accounting.record(
                KIND_CONNECTION_ASSOCIATION, REASON_NO_MEMBER_WITHIN_THRESHOLD,
                context=f"connection at ({pos[0]:.0f}, {pos[1]:.0f})",
            )

    # The accounting, in the form the project record and the report both carry:
    # empty when nothing was discarded, so a clean extraction persists no
    # disclosure at all (see DXFDropAccounting.headline).
    drop_warnings = accounting.warnings()

    # Which drawn tokens the catalogue did not answer for — the same rule and
    # the same definition app/pipeline.py already persists for the PDF path
    # (Milestone J7): a REFUSED substitution is excluded, because the catalogue
    # DID answer that token, with a different section, and the engineer has been
    # told which. This path wrote neither this nor `warnings` before, so a
    # DXF-derived report had no UNMATCHED SECTIONS section and no data-quality
    # notes at all.
    unmatched_sections = sorted({
        m["section_name_raw"] for m in members_to_insert
        if not m["section_name"] and m["section_name_raw"]
        and m.get("section_resolution") != RESOLUTION_SUFFIX_FALLBACK
    })

    # THE PROJECT'S TERMINAL STATE, DERIVED (Milestone J13). This used to be the
    # constant "review", which made every successful run read as though a human
    # were still required — including runs whose every member and connection was
    # explicitly resolved. The status is now what the evidence this run persisted
    # actually supports, and nothing else. Note what that means for THIS path:
    # the reader states no member review state on an exact member (see the row
    # built above, and the contract its boundary suite pins), so a DXF project
    # with any extracted member does not prove completeness and stays at
    # "review". That is the fail-closed outcome, not an oversight — absence of a
    # statement is not a statement of completeness. Making this path qualify for
    # "done" would mean stating its review state explicitly, which would change
    # the extraction output, and is a separate milestone.
    status = derive_project_status(
        member_review_statuses=[review_status_of(m) for m in members_to_insert],
        connection_review_statuses=connection_review_statuses,
        unmatched_sections=unmatched_sections,
        warnings=drop_warnings,
    ).status

    supabase.table("projects").update({
        "status": status,
        "total_members": len(members_to_insert),
        "total_unique_sections": unique_sections,
        "total_connections": connections_extracted,
        "total_weight_kg": total_weight_kg,
        "total_weight_tonnes": round(total_weight_kg / 1000, 3),
        "unmatched_sections": unmatched_sections,
        "warnings": drop_warnings,
    }).eq("id", project_id).execute()

    return {
        "members_extracted": len(members_to_insert),
        "unique_sections": unique_sections,
        "connections_extracted": connections_extracted,
        "total_weight_kg": total_weight_kg,
        # WHAT WAS NOT READ (Milestone J11). The summary previously carried only
        # what WAS extracted, so a caller could not tell a complete extraction
        # from one that discarded half the drawing. `dropped_evidence_count` and
        # `dropped_by_reason` are the counts by the stable reason codes;
        # `dropped_evidence` is every record, in the DXF's own entity order.
        # None of it changes what was extracted — it reports what was not.
        "dropped_evidence_count": len(accounting),
        "dropped_by_reason": accounting.by_reason(),
        "dropped_evidence": accounting.as_dicts(),
    }
