"""
Reserved interface for the future CAD engine (Slice B — not built
yet). This module defines the CONTRACT the CAD engine will consume:
validated, structured engineering data — never the original PDF.

Per the architecture principle this project has held since its first
design review: AI interprets engineering documents; a deterministic
geometry engine (planned: CadQuery, on OpenCascade/OCCT) generates
actual drawings. The CAD engine must never have to re-read a drawing
or re-interpret ambiguous data — by the time it receives a
ValidatedSteelMember, every field has already passed validation.

Nothing in this file does anything yet. It exists so the interface is
agreed and stable before implementation starts.
"""
from dataclasses import dataclass


@dataclass
class ValidatedSteelMember:
    mark: str
    section: str
    length_mm: float
    material: str
    orientation: dict | None
    connection_refs: list[str]
    source_refs: list[dict]
    validation_status: str


@dataclass
class ValidatedConnection:
    connection_id: str
    connected_members: list[str]
    plates: list[dict]
    bolts: list[dict]
    welds: list[dict]
    dimensions: dict | None
    source_refs: list[dict]
    validation_status: str


def generate_geometry(members: list[ValidatedSteelMember], connections: list[ValidatedConnection]):
    """Not implemented. Reserved for Slice B — see project roadmap."""
    raise NotImplementedError(
        "The CAD engine has not been built yet. This is Slice B of the SteelSpec "
        "roadmap: a CadQuery/OpenCascade-based parametric geometry engine, to be "
        "built only after the validation layer's output has been proven reliable "
        "against real drawings."
    )
