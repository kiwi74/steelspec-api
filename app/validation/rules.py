"""
Validation module — the standalone engine that decides whether
AI-extracted engineering data makes sense, separate from both how
confident the AI was (that's a different question, see module note
below) and separate from persisting it.

Built directly against real problems found in the Arkles Strand test:
timber contamination in the steel schedule, the same mark reported
with conflicting sections across pages, a mark string with the
section size accidentally folded into it, and members with no length
silently reading as if their weight were a calculated zero.

AI CONFIDENCE vs. VALIDATION — kept deliberately separate:
  - Confidence answers "how sure was the AI it read this correctly?"
  - Validation answers "does this make sense against the rest of the
    drawing set?" A member can be 97% confident AND still be flagged
    here, if e.g. a different page reports a conflicting section for
    the same mark. Neither one overrides the other.
"""
import re
from dataclasses import dataclass, field

# === Non-steel material detection ===
# Deliberately conservative: only fires on an actual timber keyword,
# never just on an unusual-looking dimension. A bare "220" or "300FC"
# that fails section matching is NOT timber — it's an unmatched steel
# callout that needs a human to look at it, which is a different,
# less severe flag (see classify_member below).
TIMBER_KEYWORDS = [
    "SG6", "SG8", "SG10", "H1.2", "H3.1", "H3.2", "H4", "H5", "H6",
    "LVL", "HYSPAN", "HY90", "HYONE", "SPOTTED GUM", "TREATED PINE",
    "KWILA", "MERBAU", "PLYWOOD",
]
TIMBER_PATTERN = re.compile("|".join(re.escape(k) for k in TIMBER_KEYWORDS), re.IGNORECASE)

# === Non-steel material detection: AS/NZS softwood stress grades (F-grades) ===
# A second, deliberately narrower signal, for the same reason the keyword list
# above is deliberately narrow. A graded softwood section is called out with a
# stress grade — "3/240x45 F27", "2/200x45 F27" — and none of the keywords above
# appears in it, so those callouts were being read as unmatched STEEL. But the
# bare token "F8"/"F11"/"F17" is also a perfectly ordinary MEMBER MARK: this
# project's own corpus carries steel members marked F1, F2, F3, F6, F11 and F12.
# Matching an F-number on its own would turn six real steel members into timber.
#
# What separates the two is the section size: an F-grade only means a stress
# grade when it is attached to a dimension pair, and that pair is the anchor
# here. The anchor is deliberately tight — the grade must follow the size with
# nothing but whitespace between them — because an open middle would let a
# steel callout through ("200x200x6 SHS F17" must NOT be timber; the loose form
# of this pattern reads it as one).
#
# The enumerated grades are the standard softwood family. Listing them rather
# than accepting any \d+ is what keeps a section-shaped token with some other
# suffix out, and it is why F6 and F12 — the marks that exist in the corpus and
# are NOT grades in this family — cannot collide with a real one.
STRESS_GRADES = ["F4", "F5", "F7", "F8", "F11", "F14", "F17", "F22", "F27", "F34"]
STRESS_GRADE_PATTERN = re.compile(
    r"(?<![\w.])\d{2,3}\s*[xX]\s*\d{2,3}\s*"
    r"(?:" + "|".join(re.escape(g) for g in STRESS_GRADES) + r")(?![\w.])",
    re.IGNORECASE,
)

# A looser hint pattern than the strict section matcher's regex —
# used only to catch a section size accidentally folded into the mark
# field itself (e.g. "P1 89x5 SHS" instead of mark="P1"). Being a bit
# generous here is safe: the outcome is "flag for review", never
# "silently delete data".
MARK_ANOMALY_HINT = re.compile(
    r"\d{2,3}\s*[xX]\s*\d{1,3}(?:\s*[xX]\s*[\d.]+)?\s*(?:SHS|RHS|PFC|UB|UC|CHS|EA|UA|FL)"
    r"|\d{2,3}(?:PFC|UB|UC)\d*",
    re.IGNORECASE,
)


# === The section authority boundary ===
# The matcher's own resolution vocabulary is defined in
# app/engineering_data/section_matcher.py (RESOLUTION_EXACT /
# RESOLUTION_SUFFIX_FALLBACK / RESOLUTION_NONE, added with the SectionMatch
# contract). It is compared here as a plain string and deliberately NOT
# imported: that module reaches app.supabase_client -> app.config, which reads
# os.environ at import time, so importing it would make this pure validation
# module unusable without a configured environment — including in the test
# suites that use matcher doubles. tests/test_real_world_production_section_
# authority.py asserts the two spellings are the same value, so they cannot
# drift apart silently.
SUFFIX_FALLBACK = "SUFFIX_FALLBACK"

# === The persisted section-resolution vocabulary (Milestone J5) ===
# The SAME three spellings, for the same reason, so a persisted
# steel_members.section_resolution column carries one vocabulary whichever
# path wrote it. These are the values that survive into the database; they
# record the decision the authority boundary above already made and never
# make one themselves.
RESOLUTION_EXACT = "EXACT"
RESOLUTION_NONE = "NONE"

# What a refused substitution is told to the engineer. One definition, used
# both on the validated row and on the row app/pipeline.py persists, so the
# refusal cannot be lost to consolidation or paraphrased somewhere else.
SUBSTITUTION_NOTE = (
    "Section '{token}' is not carried by the reference catalogue. The catalogue "
    "offered '{candidate}' as a suffix-fallback candidate, which describes a "
    "different section — it has NOT been adopted as this member's identity, and "
    "none of its properties have been applied. Engineer confirmation required."
)

# What an unresolved section is told to the engineer. Kept separate from
# SUBSTITUTION_NOTE on purpose: a suffix fallback means the catalogue RECOGNISED
# the token's shape and offered a different section, whereas this note means it
# offered nothing at all. Conflating them would tell an engineer a candidate
# exists when none does. Used by app/drawing_reading/dxf_parser.py, whose rows
# have no ValidationIssue channel — the PDF path surfaces the same state through
# the UNRECOGNISED_SECTION_FORMAT issue instead.
UNRESOLVED_SECTION_NOTE = (
    "Section '{token}' is not carried by the reference catalogue and no "
    "suffix-fallback candidate answers for it either. No catalogue identity, "
    "family or weight has been applied to this member. Engineer review required."
)


@dataclass
class ValidationIssue:
    """Mirrors the structure requested in the build spec, adapted to this codebase's conventions."""
    status: str          # WARNING | REVIEW_REQUIRED | ERROR
    severity: str        # LOW | MEDIUM | HIGH
    rule: str            # e.g. CONFLICTING_MEMBER_DEFINITION
    entity_type: str     # steel_member | connection | project
    message: str
    sources: list[dict] = field(default_factory=list)   # [{"page": int}, ...]


def is_timber_or_non_steel(raw_text: str) -> bool:
    text = raw_text or ""
    return bool(TIMBER_PATTERN.search(text) or STRESS_GRADE_PATTERN.search(text))


def fold_mark_format_anomalies(raw_members: list[dict]) -> list[dict]:
    """
    Fixes marks like "P1 89x5 SHS" -> mark "P1" (the section is
    already correctly captured in section_name_raw separately; the
    bug is only ever in the mark field). Returns new dicts; does not
    mutate the input.
    """
    fixed = []
    for m in raw_members:
        mark = (m.get("mark") or "").strip()
        match = MARK_ANOMALY_HINT.search(mark)
        if match and match.start() > 0:
            candidate = mark[:match.start()].strip()
            if 0 < len(candidate) <= 10:
                m = {**m, "mark": candidate, "_mark_was_corrected": True, "_original_mark": mark}
        fixed.append(m)
    return fixed


def classify_member(raw_section: str, matched: dict | None, mark: str = "") -> str:
    """
    Returns 'steel_confirmed', 'non_steel_material', or 'unmatched_steel'.
    Checks both the section text AND the mark text for timber keywords —
    real extractions sometimes put a material description in the mark
    field rather than the section field (e.g. mark "200x90 spotted gum
    beam" with section_name_raw just "200x90"), depending on how the
    drawing itself labelled the callout.
    """
    if matched:
        return "steel_confirmed"
    if is_timber_or_non_steel(raw_section or "") or is_timber_or_non_steel(mark or ""):
        return "non_steel_material"
    return "unmatched_steel"


def consolidate_members(annotated_members: list[dict]) -> tuple[list[dict], list[ValidationIssue]]:
    """
    Groups by mark. Where every page agrees on the same matched
    section, consolidates into one confirmed row (and treats
    multi-page agreement as a positive signal, not just deduplication).
    Where pages disagree, keeps every distinct definition but flags
    all of them REVIEW_REQUIRED — the system never silently picks a
    winner between conflicting engineer drawings.

    Agreement only confirms a section the catalogue actually carries: a
    refused section substitution (see the authority boundary in
    validate_extraction) is never promoted to review_status "extracted",
    no matter how many pages repeat it.

    Pages that agree on the section are still not agreement when they disagree
    on HOW it resolved (Milestone J5). A mark confirmed on one page and left
    unresolved or refused on another is a mark the drawing set does not settle,
    so every one of those rows is kept and flagged rather than reduced to the
    confirmed one — otherwise the unresolved evidence would disappear with the
    row that carried it, the token would vanish from unmatched_sections, and the
    survivor would claim agreement it never had.
    """
    groups: dict[str, list[dict]] = {}
    for m in annotated_members:
        key = (m.get("mark") or "").strip().upper()
        groups.setdefault(key, []).append(m)

    final: list[dict] = []
    issues: list[ValidationIssue] = []

    for mark_key, rows in groups.items():
        if not mark_key:
            final.extend(rows)  # nothing sensible to group on; pass through unchanged
            continue

        matched_names = {r["section_name"] for r in rows if r.get("section_name")}
        # How each page's section resolved. Exactly one state means the pages
        # agree; None is the legacy/unknown state and is treated as its own
        # single state, so rows written before this was recorded consolidate
        # exactly as they always did.
        resolutions = {r.get("section_resolution") for r in rows}
        resolution_conflict = len(resolutions) > 1

        if len(matched_names) <= 1 and not resolution_conflict:
            # 0 or 1 distinct confirmed section, agreed by every page -> one row.
            primary = next((r for r in rows if r.get("section_name")), rows[0])
            source_pages = sorted({r["source_page"] for r in rows if r.get("source_page") is not None})
            primary = dict(primary)
            primary["source_pages_confirmed"] = source_pages
            # Multi-page agreement is a positive signal — but only for a section
            # the catalogue actually confirmed. A refused substitution, an
            # unresolved token, or a row from before the resolution was recorded
            # is NOT confirmation, however many pages repeat it; only an EXACT
            # resolution (or the legacy unrecorded one) is promoted here.
            if (len(rows) > 1 and len(matched_names) == 1
                    and primary.get("section_resolution") in (RESOLUTION_EXACT, None)):
                primary["review_status"] = "extracted"
                primary["validation_note"] = f"Confirmed on {len(rows)} page(s): {source_pages}"
            final.append(primary)
        elif resolution_conflict:
            # The pages do not agree on what this member IS, even where they
            # agree on the section name. Every row is kept, including the
            # unresolved and refused ones, so no evidence is discarded and no
            # winner is invented between drawings.
            states = sorted(str(v) for v in resolutions)
            message = (
                f"Mark '{mark_key}' resolves differently across the drawing set: {states}. "
                "A section that was unresolved or refused on any page is never dropped in "
                "favour of one that was confirmed, and the pages are not treated as agreeing."
            )
            if len(matched_names) > 1:
                message += f" Conflicting section definitions are also present: {sorted(matched_names)}."
            for r in rows:
                r2 = dict(r)
                r2["review_status"] = "review_required"
                r2["validation_note"] = message
                final.append(r2)
            issues.append(ValidationIssue(
                status="REVIEW_REQUIRED", severity="HIGH", rule="CONFLICTING_SECTION_RESOLUTION",
                entity_type="steel_member",
                message=message,
                sources=[{"page": r.get("source_page")} for r in rows],
            ))
        else:
            # Genuine conflict — surface every distinctly-matched version, not the unmatched noise around them.
            conflicting_rows = [r for r in rows if r.get("section_name")]
            for r in conflicting_rows:
                r2 = dict(r)
                r2["review_status"] = "review_required"
                r2["validation_note"] = f"Conflicting definitions for mark '{mark_key}': {sorted(matched_names)}"
                final.append(r2)
            issues.append(ValidationIssue(
                status="REVIEW_REQUIRED", severity="HIGH", rule="CONFLICTING_MEMBER_DEFINITION",
                entity_type="steel_member",
                message=f"Mark '{mark_key}' has conflicting section definitions across the drawing set: {sorted(matched_names)}",
                sources=[{"page": r.get("source_page")} for r in rows],
            ))

    return final, issues


def check_missing_lengths(final_members: list[dict]) -> ValidationIssue | None:
    """
    A member with no explicit length must never silently become a
    calculated weight of zero — that's indistinguishable from "this
    genuinely weighs nothing". If most members in a project are
    missing a length, that's a project-level data-quality signal
    worth surfacing loudly, not a per-row footnote.
    """
    if not final_members:
        return None
    missing = sum(1 for m in final_members if m.get("length_mm") is None)
    proportion = missing / len(final_members)
    if proportion >= 0.5:
        return ValidationIssue(
            status="WARNING", severity="MEDIUM", rule="MISSING_MEMBER_LENGTH",
            entity_type="project",
            message=(
                f"{missing} of {len(final_members)} members have no explicit length dimension. "
                "Tonnage for these members is NOT_CALCULATED, not zero — treat any total weight "
                "figure as a lower bound, not a complete count."
            ),
        )
    return None


def match_member_section(matcher, raw_section: str):
    """
    ONE matching call per member, and its full outcome:
    (resolution, drawing_token, catalogue_row).

    The production SectionMatcher answers with its own resolution report —
    drawing_token is the requested token in the matcher's canonical form, and
    catalogue_row is the row it selected. A matcher that cannot report a
    resolution (the pre-existing test doubles, which only implement match())
    returns (None, None, row); the caller then applies the legacy identity rule
    unchanged. Nothing in production reaches that branch.
    """
    resolve = getattr(matcher, "resolve", None)
    if resolve is None:
        return None, None, matcher.match(raw_section)
    outcome = resolve(raw_section)
    return outcome.resolution, outcome.drawing_token, outcome.catalogue_row


def validate_extraction(raw_members: list[dict], matcher) -> dict:
    """
    Main entry point. Takes the AI's raw per-page member list (already
    flattened across all pages) and a SectionMatcher instance, and
    returns:
      - members: final rows ready for steel_members, each carrying a
        classification, review_status, and any validation_note
      - excluded: non-steel rows kept for transparency, never persisted
        to steel_members
      - issues: project- and mark-level ValidationIssues

    THE SECTION AUTHORITY BOUNDARY lives here, at the one place a section is
    ever matched: a member's identity is the section its drawing states. The
    catalogue may enrich a member it genuinely answered for; where it offered
    a different section through its suffix fallback, that row is refused as
    identity and as properties alike, and the member goes to review.
    """
    corrected = fold_mark_format_anomalies(raw_members)

    classified = []
    excluded = []
    for m in corrected:
        raw_section = m.get("section")
        if not raw_section:
            continue
        # The matcher's canonical spelling of the token is deliberately NOT
        # unpacked into a name (Milestone J7): it is no longer consulted anywhere
        # below. See the production identity rule for why the drawing's own
        # extracted section, not the matcher's canonical form of it, is what gets
        # persisted alongside the row.
        resolution, _canonical_token, matched = match_member_section(matcher, raw_section)
        substituted = resolution == SUFFIX_FALLBACK

        # === The authority rule ===
        # The catalogue row may ENRICH a member it actually answered for. It may
        # never CONFIRM — still less RENAME — a member whose section the drawing
        # stated and the catalogue did not carry. So a suffix-fallback row is
        # withheld from every enrichment path (it stays None here, and
        # app/pipeline.py reads family/weight from exactly this field), and the
        # member is classified on the drawing's own evidence alone.
        enriching_row = None if substituted else matched
        category = classify_member(raw_section, enriching_row, mark=m.get("mark") or "")

        # === The production identity rule — Option C (Milestone J7) ===
        # `section_name` is FK-BOUND: it references steel_sections(name). It may
        # therefore carry a catalogue identity only, and the catalogue answered
        # for this member only where it resolved the drawn token EXACTLY. An exact
        # resolution names the same section the catalogue does — the catalogue
        # merely spells it its own way, e.g. "25x25x3EA" against the matcher's
        # canonical "25X25X3EA" — so it is kept byte-for-byte, exactly as before.
        #
        # A suffix fallback named a DIFFERENT section: the catalogue's name for it
        # is refused outright, and the DRAWN token is not a catalogue key either,
        # so NEITHER may enter the FK column. NONE has no catalogue answer at all.
        # Both persist NULL there — which is FK-safe, and which already meant
        # "unmatched" on this path before J7, so no reader has to learn a new
        # state to read it correctly. The member's identity is not lost: the
        # drawing's own section stays verbatim in section_name_raw, which is not
        # FK-bound, and the refused candidate stays in
        # section_substituted_candidate as evidence of what was refused.
        #
        # This deliberately evolves the J5 fallback contract, which wrote the
        # drawn token into section_name — a value the live catalogue does not
        # carry, so the row could not be persisted against the real FK at all.
        section_name = None if substituted else (matched["name"] if matched else None)

        row = {**m, "matched": enriching_row, "section_name": section_name, "category": category}

        # === Recording the decision above, not making a new one (Milestone J5) ===
        # HOW this section resolved is written onto every row, not only the
        # refused substitutions, so a persisted member can be read back as what
        # it actually is instead of being re-inferred from section_name. These
        # two writes are the recorded outcome of the authority boundary and the
        # identity rule immediately above; they feed nothing back into them, and
        # `substituted`/`enriching_row`/`section_name`/`category` are all
        # computed before this point and are unaffected by it. `resolution` is
        # None only for a matcher that cannot report one, which stays None.
        row["section_resolution"] = resolution
        # The refused candidate is provenance ONLY — never identity, never
        # properties. None for EXACT and for NONE.
        row["section_substituted_candidate"] = matched.get("name") if substituted else None

        if substituted:
            row["validation_note"] = SUBSTITUTION_NOTE.format(
                token=m.get("section"), candidate=matched.get("name"),
            )
        if category == "non_steel_material":
            excluded.append(row)
        else:
            classified.append(row)

    final_members, conflict_issues = consolidate_members(classified)

    issues = list(conflict_issues)
    for m in final_members:
        if m["category"] == "unmatched_steel":
            if m.get("section_resolution") == SUFFIX_FALLBACK:
                # The catalogue DID recognise this token's shape — it offered a
                # different section's identity for it. That is a refusal, not a
                # format failure, and it is reported as its own rule so the
                # engineer sees which of the two they are looking at.
                issues.append(ValidationIssue(
                    status="REVIEW_REQUIRED", severity="HIGH",
                    rule="SECTION_SUBSTITUTION_REFUSED",
                    entity_type="steel_member",
                    message=(
                        f"Mark '{m.get('mark')}' reports '{m.get('section')}'. The reference catalogue "
                        f"does not carry that section; its nearest suffix-fallback candidate "
                        f"'{m.get('section_substituted_candidate')}' describes a different section and has "
                        "been refused. The member keeps the drawn section and is held for engineer "
                        "confirmation."
                    ),
                    sources=[{"page": m.get("source_page")}],
                ))
            else:
                issues.append(ValidationIssue(
                    status="REVIEW_REQUIRED", severity="MEDIUM", rule="UNRECOGNISED_SECTION_FORMAT",
                    entity_type="steel_member",
                    message=f"Mark '{m.get('mark')}' reports '{m.get('section')}', which doesn't match a recognised steel section format.",
                    sources=[{"page": m.get("source_page")}],
                ))

    length_issue = check_missing_lengths(final_members)
    if length_issue:
        issues.append(length_issue)

    if excluded:
        marks = sorted({e.get("mark") for e in excluded if e.get("mark")})
        issues.append(ValidationIssue(
            status="WARNING", severity="LOW", rule="MATERIAL_CONTAMINATION_WARNING",
            entity_type="project",
            message=f"{len(excluded)} record(s) appear to describe timber or other non-steel material and were excluded from the steel schedule: {', '.join(marks)}",
        ))

    return {"members": final_members, "excluded": excluded, "issues": issues}
