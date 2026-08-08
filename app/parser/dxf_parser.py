"""
DXF parser — extracts structural steel members and connections
from a DXF file and writes the results directly into Supabase,
scoped to the project that triggered the extraction.

This is the same extraction logic we prototyped earlier, adapted
to write into the real schema instead of an in-memory model.
"""
import math
import re
import ezdxf
from ezdxf.entities import Line, LWPolyline, Text, MText

from app.parser.section_matcher import SectionMatcher
from app.supabase_client import supabase

STEEL_LAYER_PATTERNS = [r"^S[-_]?BEAM", r"^S[-_]?STEEL", r"^S[-_]?COL", r"^S[-_]?BRAC", r"^STR", r"^STEEL"]
STEEL_TEXT_LAYER_PATTERNS = [r"^S[-_]?TEXT", r"^S[-_]?ANNO", r"^S[-_]?NOTE"]
CONNECTION_LAYER_PATTERNS = [r"^S[-_]?CONN", r"^S[-_]?DET", r"^CONN", r"^DETAIL"]
IGNORE_LAYER_PATTERNS = [r"^A[-_]", r"^DEFPOINTS", r"^0$", r"DIMS?$", r"GRID"]


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


def parse_dxf_and_save(filepath: str, project_id: str) -> dict:
    """
    Parse a DXF file and write extracted members/connections into
    Supabase against the given project_id. Returns a summary dict.
    """
    matcher = SectionMatcher()
    section_regex = matcher.get_section_regex()

    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()

    layer_classes = {layer.dxf.name: classify_layer(layer.dxf.name) for layer in doc.layers}

    # Extract geometry from steel layers
    geometries = []
    for e in msp:
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

    # Extract text labels from steel text / steel layers
    texts = []
    for e in msp:
        cls = layer_classes.get(e.dxf.layer, "unknown")
        if cls not in ("steel", "steel_text"):
            continue
        if isinstance(e, Text):
            texts.append({"text": e.dxf.text.strip(), "pos": (e.dxf.insert.x, e.dxf.insert.y)})
        elif isinstance(e, MText):
            texts.append({"text": e.plain_text().strip(), "pos": (e.dxf.insert.x, e.dxf.insert.y)})

    # Match labels to sections, associate with nearest geometry
    members_to_insert = []
    used = set()
    counter = 0

    for t in texts:
        matches = section_regex.findall(t["text"])
        if not matches:
            continue
        section = matcher.match(matches[0])
        if not section:
            continue

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
            weight = section.get("weight_per_metre") or 0
            total_weight = round((g["length"] / 1000) * weight, 2)

            members_to_insert.append({
                "project_id": project_id,
                "mark": f"M{counter}",
                "section_name": section["name"],
                "section_name_raw": matches[0],
                "section_family": section["family"],
                "length_mm": round(g["length"], 0),
                "grade": "300PLUS",
                "quantity": 1,
                "weight_per_metre": weight,
                "total_weight_kg": total_weight,
                "confidence": "medium",
                "source_layer": g["layer"],
            })

    # Write members to Supabase
    if members_to_insert:
        supabase.table("steel_members").insert(members_to_insert).execute()

    total_weight_kg = sum(m["total_weight_kg"] for m in members_to_insert)
    unique_sections = len(set(m["section_name"] for m in members_to_insert))

    # Update the project summary row
    supabase.table("projects").update({
        "status": "review",  # DXF extractions always need human review
        "total_members": len(members_to_insert),
        "total_unique_sections": unique_sections,
        "total_weight_kg": total_weight_kg,
        "total_weight_tonnes": round(total_weight_kg / 1000, 3),
    }).eq("id", project_id).execute()

    return {
        "members_extracted": len(members_to_insert),
        "unique_sections": unique_sections,
        "total_weight_kg": total_weight_kg,
    }
