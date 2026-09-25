-- Milestone J5 — production section-resolution truth.
--
-- WHY: app/validation/rules.py has always computed HOW a member's section
-- resolved (EXACT / SUFFIX_FALLBACK / NONE, plus the catalogue name a suffix
-- fallback refused), and app/pipeline.py then discarded both values before
-- persisting the member. Nothing downstream could therefore tell a confirmed
-- catalogue section from a refused substitution or an unresolved token by
-- reading steel_members, so the report inferred a match instead.
--
-- WHAT: the two nullable columns that value needs. Nothing else.
--
-- PROPERTIES (all required):
--   * additive            - two ADD COLUMN statements, no other clause
--   * nullable            - no NOT NULL, so every legacy row is representable
--   * no default          - deliberately NO DEFAULT: a default would invent a
--                           resolution for rows extracted before J5, which is
--                           exactly the kind of unearned assertion J5 removes.
--                           Legacy rows keep NULL and read as "not recorded".
--   * backward-compatible - existing inserts that do not name these columns are
--                           unaffected; both are NULL for them.
--   * non-destructive     - no DROP, no ALTER COLUMN, no data movement
--
-- VOCABULARY (enforced in code, not here — see app/validation/rules.py):
--   section_resolution            'EXACT' | 'SUFFIX_FALLBACK' | 'NONE' | NULL
--   section_substituted_candidate the refused catalogue section name for a
--                                 SUFFIX_FALLBACK row; NULL for EXACT and NONE
--
-- NULL means "not recorded" and is a distinct state from all three values: the
-- production report renders it neutrally and never infers a match from it.
--
-- EXECUTION STATE — CORRECTED BY J8B PHASE 13, WHICH FOUND THIS CLAIM STALE.
--
-- As authored, this file was NOT EXECUTED, and the J5 implementation task did not
-- apply it: applying it was a separate, credentialed operator decision. That
-- decision was taken, and the two columns are now LIVE.
--
-- VERIFIED READ-ONLY ON 2026-09-24: information_schema reports
-- `steel_members.section_resolution` and
-- `steel_members.section_substituted_candidate` present, and the J5 production
-- tests read them from the live table rather than assuming them. So the sentence
-- "not applied to the live database" is no longer a true statement about the
-- CURRENT state, and is superseded by this note; the authored state above is
-- retained because it is still part of the record.
--
-- WHAT REMAINS TRUE AND IS NOT CLAIMED OTHERWISE: no migration RUNNER executed
-- this file. It was applied out of band — there is no runner history for it, no
-- migration tracking table records it, and this header claims no mechanism beyond
-- "the change is live". The DDL below is retained verbatim as the authoritative
-- statement of what the change is; re-running it is harmless if the columns
-- already exist (`add column if not exists`).

alter table steel_members
    add column if not exists section_resolution text;

alter table steel_members
    add column if not exists section_substituted_candidate text;
