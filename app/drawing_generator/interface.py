"""
Reserved interface for the future fabrication drawing generator
(Slice C — not built yet).

IMPORTANT DISTINCTION: this is NOT app/report/pdf_generator.py. That
module produces a data report — tables, schedules, a cover page. This
module will eventually produce actual technical drawings: dimensioned
orthographic views, member elevations, material lists laid out the
way FABs.pdf and ASMs.pdf are (see the reference drawings supplied
for this project). It consumes 3D geometry from cad_engine, not raw
engineering data.

Nothing in this file does anything yet.
"""


def generate_fabrication_drawings(geometry):
    """Not implemented. Reserved for Slice C, after cad_engine exists."""
    raise NotImplementedError(
        "The fabrication drawing generator has not been built yet. This depends "
        "on cad_engine (Slice B) existing first — a technical drawing is a 2D "
        "projection of a 3D solid, not something generated independently of one."
    )
