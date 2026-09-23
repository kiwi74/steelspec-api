"""
Fabrication drawing generator (Slice C).

IMPORTANT DISTINCTION: this is NOT app/report/pdf_generator.py. That
module produces a data report — tables, schedules, a cover page. This
module produces an actual technical drawing: a dimensioned elevation
view, a cross-section view, and a title block, output as a real DXF
file. It consumes 3D geometry from cad_engine (a GeneratedMemberGeometry),
never raw engineering data and never the original PDF.

MILESTONE 1: one sheet for one unconnected straight member — proving
the geometry -> DXF pipeline end to end.

MILESTONE 2: if the member's geometry carries a connection (only
END_PLATE exists yet — see app/cad_engine/connections.py), the DXF
sheet also gets an end-plate view (plate outline + hole circles, read
from the actual solid) and shows the plate edge-on in the elevation.
Still no welds, no bolt solids, no other connection types.

MILESTONE 3: a second, independent output format — a PDF fabrication
drawing (app/drawing_generator/pdf_builder.py), including an
END_PLATE connection view where present. This is NOT a replacement
for the DXF path, and NOT app/report/pdf_generator.py's data report —
see pdf_builder.py's module docstring for the distinction. No API
endpoint calls either drawing path yet.

MILESTONE 3.1 (current addition): generate_fabrication_drawing_pdf
gained three optional keyword arguments — `material`, `revision`,
`date` — purely presentational title-block fields (see
pdf_builder.DrawingMetadata). None of them are geometry:
GeneratedMemberGeometry has no material field, so `material` defaults
to None and renders as "NOT SPECIFIED" unless the caller supplies one
explicitly — never invented from anywhere.

MILESTONE 7T (current addition): a third top-level builder,
generate_connection_fabrication_drawing_pdf(), for a reviewed
two-member connection (app.cad_engine.reviewed_connection_assembly.
ReviewedTwoMemberConnectionAssembly) rather than a single
GeneratedMemberGeometry — see pdf_builder.build_connection_pdf()'s own
MILESTONE 7T note for how it renders both members and the actual
generated connection geometry (never Member A alone, which was 7S's
documented, temporary limitation). Reuses _validate_geometry_for_drawing()
unchanged, applied once per member — the identical fail-loudly
precondition check the single-member path already enforces, never
duplicated into a second rule.

MILESTONE 7AV: a fourth top-level builder,
generate_multi_member_connection_fabrication_drawing_pdf(), for a
reviewed 3+-member connection (app.cad_engine.multi_member_connection.
ReviewedMultiMemberConnectionAssembly) — the two-member entry above
is left COMPLETELY unchanged. It applies the same existing
_validate_geometry_for_drawing() precondition check to EVERY member's
own geometry, then renders every member (one elevation rectangle and
one title-block row each — see pdf_builder's MILESTONE 7AV note).
The >26-member label-vocabulary boundary is enforced inside
pdf_builder.build_multi_member_connection_pdf(), where the labels are
actually drawn.

INTERFACE CHANGES FROM THE ORIGINAL STUB, AND WHY:
- The placeholder signature was `generate_fabrication_drawings(geometry)`
  with no type hint, matching cad_engine's single GeneratedMemberGeometry
  output — that part is unchanged. `output_path`, `quantity`, and `status`
  were added because a title block needs quantity/status, and neither
  GeneratedMemberGeometry nor ValidatedSteelMember carries them (they're
  drawing metadata, not CAD geometry or extracted engineering data).
- `generate_fabrication_drawing_pdf` is a new, separate function with the
  same shape, for the new PDF output path. The precondition checks were
  factored into `_validate_geometry_for_drawing()` so both formats enforce
  the identical "fail loudly on missing/invalid geometry" rule from one
  place, rather than duplicating it.
"""
import datetime as _datetime
from pathlib import Path

from app.drawing_generator import dxf_builder, pdf_builder
from app.drawing_generator.errors import DrawingValidationError

__all__ = [
    "DrawingValidationError", "generate_fabrication_drawings", "generate_fabrication_drawing_pdf",
    "generate_connection_fabrication_drawing_pdf",
    "generate_multi_member_connection_fabrication_drawing_pdf",
]


def _validate_geometry_for_drawing(geometry, quantity: int) -> None:
    """
    Fails clearly (DrawingValidationError) on missing/invalid
    geometry, rather than producing an empty or misleading drawing —
    matching this project's validation philosophy throughout
    (see app/validation/rules.py, app/cad_engine/interface.py). Shared
    by both the DXF and PDF drawing paths.
    """
    if geometry is None:
        raise DrawingValidationError("No geometry supplied — cannot generate a drawing from nothing.")
    if getattr(geometry, "solid", None) is None:
        raise DrawingValidationError(
            "Supplied geometry has no CAD solid (geometry.solid is None). A fabrication "
            "drawing is a 2D projection of a real solid — it cannot be produced without one."
        )
    if not getattr(geometry, "mark", None):
        raise DrawingValidationError("Supplied geometry has no member mark.")
    if not getattr(geometry, "section_name", None):
        raise DrawingValidationError("Supplied geometry has no section name.")
    length_mm = getattr(geometry, "length_mm", None)
    if length_mm is None or length_mm <= 0:
        raise DrawingValidationError(
            f"Supplied geometry has an invalid length_mm ({length_mm!r}). A drawing "
            "requires an explicit, positive length — it is never inferred or defaulted."
        )
    if quantity <= 0:
        raise DrawingValidationError(f"Quantity must be positive, got {quantity!r}.")


def generate_fabrication_drawings(
    geometry,
    output_path: str | Path,
    *,
    quantity: int = 1,
    status: str = "TEST",
) -> Path:
    """
    Builds a single-member fabrication drawing (elevation + cross
    section + title block) from a GeneratedMemberGeometry and saves
    it as a real DXF file at output_path.
    """
    _validate_geometry_for_drawing(geometry, quantity)

    doc = dxf_builder.build_drawing(geometry, quantity, status)

    output_path = Path(output_path)
    doc.saveas(output_path)
    return output_path


def generate_fabrication_drawing_pdf(
    geometry,
    output_path: str | Path,
    *,
    quantity: int = 1,
    status: str | None = None,
    material: str | None = None,
    revision: str = "A",
    date: _datetime.date | None = None,
) -> Path:
    """
    Builds a single-member fabrication drawing PDF (bordered A3
    landscape sheet: elevation, cross section, end-plate view when
    present, and a structured title block) from a
    GeneratedMemberGeometry, and saves it at output_path.

    `material`, `revision`, `date` are presentation-only title-block
    fields — see pdf_builder.DrawingMetadata. `date` defaults to
    today; pass an explicit date for deterministic output (e.g. in
    tests). `status` is optional and never defaulted (MILESTONE 7AW):
    None draws no STATUS cell at all, so the drawing never implies an
    approval state that was never recorded.
    """
    _validate_geometry_for_drawing(geometry, quantity)

    pdf_bytes = pdf_builder.build_pdf(
        geometry, quantity, status, material=material, revision=revision, date=date
    )

    output_path = Path(output_path)
    output_path.write_bytes(pdf_bytes)
    return output_path


def generate_connection_fabrication_drawing_pdf(
    assembly,
    output_path: str | Path,
    *,
    status: str | None = None,
    material: str | None = None,
    revision: str = "A",
    date: _datetime.date | None = None,
    project_id: str | None = None,
    source_drawing_id: str | None = None,
) -> Path:
    """
    Milestone 7T — builds a two-member connection fabrication drawing
    PDF (project-space connection elevation, connection detail view,
    and a two-member title block — see pdf_builder.build_connection_pdf())
    from a ReviewedTwoMemberConnectionAssembly, and saves it at
    output_path.

    Applies the existing _validate_geometry_for_drawing() precondition
    check to BOTH assembly.member_a.geometry and
    assembly.member_b.geometry — unchanged, never duplicated into a
    second rule.

    `status`, `material`, `revision`, `date` are the same
    presentation-only title-block fields generate_fabrication_drawing_pdf()
    already accepts — see pdf_builder.DrawingMetadata; `status` is
    optional and never defaulted (MILESTONE 7AW — None draws no STATUS
    cell). `project_id` and `source_drawing_id` (Milestone 7AR) are
    optional job-identity title-block fields: when either is None no
    project row is drawn and nothing is invented. There is no
    `quantity` parameter here: a connection drawing names two specific,
    individually marked members, not a repeated quantity of one part.
    """
    _validate_geometry_for_drawing(assembly.member_a.geometry, 1)
    _validate_geometry_for_drawing(assembly.member_b.geometry, 1)

    pdf_bytes = pdf_builder.build_connection_pdf(
        assembly, status, material=material, revision=revision, date=date,
        project_id=project_id, source_drawing_id=source_drawing_id,
    )

    output_path = Path(output_path)
    output_path.write_bytes(pdf_bytes)
    return output_path


def generate_multi_member_connection_fabrication_drawing_pdf(
    assembly,
    output_path: str | Path,
    *,
    status: str | None = None,
    material: str | None = None,
    revision: str = "A",
    date: _datetime.date | None = None,
    project_id: str | None = None,
    source_drawing_id: str | None = None,
) -> Path:
    """
    Milestone 7AV — builds a multi-member (3+) connection fabrication
    drawing PDF (project-space connection elevation, connection detail
    view, and a per-member title block — see
    pdf_builder.build_multi_member_connection_pdf()) from a
    ReviewedMultiMemberConnectionAssembly, and saves it at
    output_path.

    Applies the existing _validate_geometry_for_drawing() precondition
    check to EVERY assembly member's own geometry — unchanged, never
    duplicated into a second rule; a missing or invalid member
    geometry fails the whole drawing, and no partial or subset
    drawing is ever produced.

    `status`, `material`, `revision`, `date` are the same
    presentation-only title-block fields generate_fabrication_drawing_pdf()
    already accepts — see pdf_builder.DrawingMetadata; `status` is
    optional and never defaulted (MILESTONE 7AW — None draws no STATUS
    cell). `project_id` and `source_drawing_id` (Milestone 7AR) are
    optional job-identity title-block fields: when either is None no
    project row is drawn and nothing is invented. There is no
    `quantity` parameter here, for the same reason as the two-member
    entry: a connection drawing names specific, individually marked
    members, not a repeated quantity of one part.
    """
    for member in assembly.members:
        _validate_geometry_for_drawing(member.geometry, 1)

    pdf_bytes = pdf_builder.build_multi_member_connection_pdf(
        assembly, status, material=material, revision=revision, date=date,
        project_id=project_id, source_drawing_id=source_drawing_id,
    )

    output_path = Path(output_path)
    output_path.write_bytes(pdf_bytes)
    return output_path
