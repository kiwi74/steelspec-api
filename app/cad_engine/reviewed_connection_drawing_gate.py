"""
Milestone 7S — the explicit production boundary between REVIEWED
CONNECTION GEOMETRY and FABRICATION DRAWING GENERATION: a reviewed
two-member connection assembly may not reach the drawing generator
unless it has first passed the existing 7R validation gate.

    reviewed connection record          reviewed member records
            |                                    |
    real_connection_adapter (7D)        real_member_adapter (7A)
            |                                    |
    ValidatedConnection                  ValidatedSteelMember(s)
            |                                    |
            +---- 7I placement / 7J location ----+
                            |
            build_reviewed_two_member_connection_assembly() (7O)
                            |
              ReviewedTwoMemberConnectionAssembly
                            |
            validate_reviewed_connection_assembly() (7R)
                            |            (raises on failure -- see below)
                            v
        generate_connection_fabrication_drawing_pdf() (7T)
                            |
                 fabrication drawing PDF artifact

STEP 1 FINDING, 7S (superseded by 7T — kept for provenance): 7S found
that `generate_fabrication_drawing_pdf()` renders only a single
GeneratedMemberGeometry, and that the 7K-7R two-member splice
architecture deliberately never unions the connection plate into
either member's own `.solid` (see two_member_connection.py's own
docstring) — so 7S could only render Member A's own elevation/cross
section, with no connection-detail view at all. That was an explicitly
documented, temporary limitation, not a design decision to keep.

MILESTONE 7T CLOSES THAT GAP: `GeneratedConnectionAssembly.plate`/
`.holes` (7K/7L) are already in the exact shape the existing
single-member connection-detail renderer (GeneratedConnection,
Milestone 6D) expects — see
app.drawing_generator.pdf_builder.build_connection_pdf()'s own module
note for the adapter that reuses that renderer unchanged. This module
now calls generate_connection_fabrication_drawing_pdf() (7T), which
renders Member A, Member B, and the actual generated connection plate/
hole geometry — never Member A alone.

STEP 4/5 — enforcement and failure behaviour (unchanged since 7S):
this module calls ONLY validate_reviewed_connection_assembly() (7R) —
never 7P or 7Q directly, since 7R already composes them (see 7R's own
docstring). If validation raises GeometryValidationError, it
propagates completely unchanged; generate_connection_fabrication_drawing_pdf()
is never called, no output file is ever written, and nothing is
repaired, inferred, or silently bypassed — including no fallback to
the old Member-A-only drawing.

STEP 3/11 — boundaries: a successful call proves only that the
reviewed connection assembly passed the currently-required geometric
consistency checks (7R's own exact meaning) and that the existing
drawing generator was reached with real, project-space member and
connection geometry. It does not add, invent, or imply an approval
status (APPROVED/ENGINEERED/COMPLIANT/FABRICATION READY) anywhere in
the output — status/material/revision remain exactly the existing,
plain keyword arguments generate_connection_fabrication_drawing_pdf()
already accepts and defaults (e.g. status="TEST"), never overridden or
dressed up by this module. No production API endpoint currently calls
this function (confirmed directly: app/main.py's only routes are
/health, /extract, and /generate-report, none of which touch
generate_connection_fabrication_drawing_pdf or anything from 7K-7R) —
this module establishes the internal boundary only; wiring an API
endpoint to it is explicitly future work.
"""
from pathlib import Path

from app.cad_engine.reviewed_connection_assembly import ReviewedTwoMemberConnectionAssembly
from app.cad_engine.reviewed_connection_validation_gate import validate_reviewed_connection_assembly
from app.drawing_generator.interface import generate_connection_fabrication_drawing_pdf

__all__ = ["generate_fabrication_drawing_from_reviewed_assembly"]


def generate_fabrication_drawing_from_reviewed_assembly(
    assembly: ReviewedTwoMemberConnectionAssembly,
    output_path: str | Path,
    **pdf_kwargs,
) -> Path:
    """
    Validates `assembly` through the existing 7R gate
    (validate_reviewed_connection_assembly() — which itself composes
    7P and 7Q; neither is called separately here), and only on success
    renders the complete reviewed connection — both members' own
    project-space geometry and the actual generated connection plate/
    hole geometry — via generate_connection_fabrication_drawing_pdf() (7T).

    Raises GeometryValidationError, propagated unchanged from 7R (and
    transitively from 7P/7Q/7K/7L), if the assembly is not
    geometrically consistent — the drawing generator is never called
    in that case, no output file is written, and there is no fallback
    to a partial or Member-A-only drawing.

    `**pdf_kwargs` forwards unchanged to
    generate_connection_fabrication_drawing_pdf() (status, material,
    revision, date, project_id, source_drawing_id) — this function
    adds no new drawing content and no approval/compliance labelling
    of its own.
    """
    validate_reviewed_connection_assembly(assembly)  # 7R — raises before any drawing work happens

    return generate_connection_fabrication_drawing_pdf(assembly, output_path, **pdf_kwargs)
