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
    return bool(TIMBER_PATTERN.search(raw_text or ""))


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

        if len(matched_names) <= 1:
            # 0 or 1 distinct confirmed section -> consolidate to one row.
            primary = next((r for r in rows if r.get("section_name")), rows[0])
            source_pages = sorted({r["source_page"] for r in rows if r.get("source_page") is not None})
            primary = dict(primary)
            primary["source_pages_confirmed"] = source_pages
            if len(rows) > 1 and len(matched_names) == 1:
                primary["review_status"] = "extracted"
                primary["validation_note"] = f"Confirmed on {len(rows)} page(s): {source_pages}"
            final.append(primary)
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
    """
    corrected = fold_mark_format_anomalies(raw_members)

    classified = []
    excluded = []
    for m in corrected:
        raw_section = m.get("section")
        if not raw_section:
            continue
        matched = matcher.match(raw_section)
        category = classify_member(raw_section, matched, mark=m.get("mark") or "")

        row = {**m, "matched": matched, "section_name": matched["name"] if matched else None, "category": category}
        if category == "non_steel_material":
            excluded.append(row)
        else:
            classified.append(row)

    final_members, conflict_issues = consolidate_members(classified)

    issues = list(conflict_issues)
    for m in final_members:
        if m["category"] == "unmatched_steel":
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
