"""
Milestone 7I — the smallest useful assembly-level container: a set of
independently-placed members sharing one project coordinate system,
with no automatic union and no connection geometry between them.

    Project CAD A  --\\
                       +--> GeneratedAssembly --> 2 independently identifiable solids
    Project CAD B  --/

Deliberately minimal (Step 17: "do not create unnecessary
abstractions"): GeneratedAssembly is a plain list wrapper, not a scene
graph, not a spatial index, and it performs no geometry operations of
its own. Each PlacedMember keeps its already-placed
GeneratedMemberGeometry (see app/cad_engine/placement.py) next to the
MemberPlacement that produced it, so a caller can always answer "where
did this member's geometry come from" without recomputing it.

GeneratedAssembly never calls `.union()` (or any other boolean
operation) across members — each PlacedMember's `.geometry.solid`
remains its own independent CadQuery object. Two members sharing a
project coordinate system is not a claim that they are structurally
connected, clash-free, or fabrication-ready; see
tests/test_real_multi_member_placement.py's module docstring for the
full list of claims this milestone explicitly does not make.
"""
from dataclasses import dataclass, field

from app.cad_engine.interface import GeneratedMemberGeometry
from app.cad_engine.placement import MemberPlacement

__all__ = ["PlacedMember", "GeneratedAssembly"]


@dataclass(frozen=True)
class PlacedMember:
    mark: str
    geometry: GeneratedMemberGeometry  # already in project space (post place_member_geometry())
    placement: MemberPlacement


@dataclass
class GeneratedAssembly:
    members: list[PlacedMember] = field(default_factory=list)
