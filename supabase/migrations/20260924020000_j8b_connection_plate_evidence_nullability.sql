-- Milestone J8B — connection_plates evidence nullability.
--
-- WHY: `connection_plates.grade` is `text NOT NULL DEFAULT '300'` and
-- `connection_plates.thickness` is `numeric(6) NOT NULL` with no default, while
-- BOTH honest production writers deliberately produce NULL for an unstated
-- value:
--
--   * app/pipeline.py sends `"grade": _extracted_plate_grade(p)` and
--     `"thickness": _extracted_plate_thickness(p)`. The first is documented to
--     find nothing in every real capture (a plate carries no material of its
--     own); the second returns None for thickness that is absent, blank, zero,
--     negative, non-numeric or non-finite.
--   * app/drawing_reading/dxf_parser.py produced the same shape once its
--     fabricated `"grade": "300"` literal is removed (J8B Phase 3), because the
--     DXF plate regex extracts plate TYPE and THICKNESS only — there is no
--     plate-grade evidence in the parser at all.
--
-- An explicit JSON null bypasses a column DEFAULT (PostgREST only honours the
-- default when the key is omitted, via `Prefer: missing=default`, which this
-- client does not send). So on a NOT NULL column the honest payload fails with
-- SQLSTATE 23502, and on a defaulted column the fabricated '300' is written
-- into rows whose drawing never stated a grade. The two column properties are
-- therefore not neutral conveniences — they are the reason a fabricated value
-- and a real one are indistinguishable in this table.
--
-- WHAT: exactly four ALTER COLUMN clauses on ONE table. No data movement, no
-- column added, dropped or renamed, no type change.
--
-- PROPERTIES (all required):
--   * nullable            - NULL becomes the persisted representation of
--                           "the drawing stated no grade / no thickness",
--                           which is a DIFFERENT state from a stated value.
--   * no default          - explicitly dropped on both columns. `thickness`
--                           has none today; the DROP DEFAULT is stated anyway
--                           because this migration establishes the intended
--                           invariant for the table, not merely its current
--                           accident. A default would silently re-invent the
--                           fabrication this milestone removes.
--   * additive-only       - no DROP of a column, no UPDATE, no backfill, no
--                           index, constraint, trigger, enum, generated column
--                           or foreign key. Nothing is written to any row.
--   * non-destructive     - the three existing historical thickness values
--                           (12.0, 16.0, 20.0) are drawing-callout evidenced and
--                           are NOT touched; the historical grade values are
--                           corrected by a separate, pinned-ID guarded step, not
--                           by this file.
--   * backward-compatible - existing inserts naming both columns are
--                           unaffected; the columns simply accept NULL now.
--
-- WHAT DEPENDS ON EITHER COLUMN: nothing. `grade` and `thickness` participate in
-- no index (only the pkey and the connection_id index exist), no CHECK, no
-- trigger, no view or materialised view and no function; no other table
-- references them; and no production code READS connection_plates.grade at all
-- (app/report/pdf_generator.py selects the row with `*` and renders plate_type,
-- thickness, width and depth only).
--
-- EXECUTION STATE. Two states, both recorded.
--
-- AUTHORED STATE: as authored this file was NOT APPLIED. It is the smallest
-- migration the change requires, and applying it is a credentialed operator
-- decision — the file was not written as an executed artefact.
--
-- APPLIED STATE: it WAS applied on 2026-09-24, through the Supabase Management
-- API SQL endpoint (`POST /v1/projects/{ref}/database/query`) — the credentialed
-- mechanism this project uses for live statements. No migration runner, CLI or CI
-- job executed it, no migration tracking table records it, and this header claims
-- no mechanism beyond that. The authored state above is retained because it is
-- still part of the record; the change is live.
--
-- Immediately after the DDL, the DDL's effect was not yet visible on the
-- REST surface: PostgREST still listed `grade` and `thickness` in the table's
-- `required` set and `grade` with default `'300'::text`. That was a stale schema
-- CACHE in front of a database that had already changed — not a failed
-- statement. The cache was refreshed with the canonical
-- `notify pgrst, 'reload schema'`, after which the surface matched the database.
--
-- VERIFIED READ-ONLY AFTERWARDS, on both surfaces:
--   * information_schema.columns: both columns is_nullable = 'YES',
--     column_default IS NULL; steel_members.grade unchanged (nullable, no
--     default); connection_plates.thickness still numeric.
--   * the PostgREST OpenAPI document: `required` for connection_plates is
--     ["id", "connection_id", "plate_type"] — `grade` and `thickness` absent —
--     and neither property carries a `default`.
--   * unchanged by the DDL: steel_sections 222 rows with the pinned catalogue
--     digest, steel_members 128 rows, connection_plates 3 rows, and both
--     tables' non-grade content byte-identical to the pre-image captured before
--     the change. The historical grade VALUES were corrected separately, under
--     the pinned-ID guards in
--     app/engineering_data/historical_grade_correction.py.
--
-- RE-RUNNING THIS FILE IS SAFE AND IDEMPOTENT IN EFFECT (each clause drops a
-- property that is already absent) but unnecessary: the intended state is
-- already live. This file is not re-applied by any test or task.

begin;

alter table connection_plates
    alter column grade drop not null;

alter table connection_plates
    alter column grade drop default;

alter table connection_plates
    alter column thickness drop not null;

alter table connection_plates
    alter column thickness drop default;

commit;
