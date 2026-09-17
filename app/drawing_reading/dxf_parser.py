"""
DXF parser — extracts structural steel members AND connections
(bolts, plates, welds) from a DXF file and writes the results
directly into Supabase, scoped to the project that triggered the
extraction.
"""
import math
import re
import ezdxf
from ezdxf.entities import Line, LWPolyline, Text, MText

from app.engineering_data.section_matcher import SectionMatcher
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


def parse_connection_text(text: str) -> dict:
    result = {"bolt_groups": [], "plates": [], "welds": [], "connection_type": "unspecified"}

    bolt_pattern = r"(\d+)\s*[x×]\s*M(\d+)\s*(?:HD\s*)?(?:Gr\.?\s*)(\d+\.?\d*)"
    for qty, size, grade in re.findall(bolt_pattern, text, re.IGNORECASE):
        result["bolt_groups"].append({"bolt_size": f"M{size}", "bolt_grade": grade, "quantity": int(qty)})
        if result["connection_type"] == "unspecified":
            result["connection_type"] = "bolted"

    plate_pattern = r"(end\s*plate|base\s*plate|gusset|stiffener|cleat)\s*(\d+)\s*mm"
    for plate_type, thickness in re.findall(plate_pattern, text, re.IGNORECASE):
        result["plates"].append({"plate_type": plate_type.lower().replace(" ", "_"), "thickness": float(thickness), "grade": "300"})

    weld_pattern = r"(\d+)\s*mm\s*(fillet|butt)\s*weld"
    for size, weld_type in re.findall(weld_pattern, text, re.IGNORECASE):
        wt = "fillet" if "fillet" in weld_type.lower() else "butt"
        result["welds"].append({"weld_type": wt, "size": float(size)})
        if result["connection_type"] == "bolted":
            result["connection_type"] = "bolted_and_welded"
        elif result["connection_type"] == "unspecified":
            result["connection_type"] = "welded"

    return result


def find_nearby_member_ids(pos, member_records, max_dist=3000, max_count=2):
    scored = []
    for rec in member_records:
        d = distance(pos, rec["midpoint"])
        if d <= max_dist:
            scored.append((d, rec["id"]))
    scored.sort(key=lambda x: x[0])
    return [member_id for _, member_id in scored[:max_count]]


def parse_dxf_and_save(filepath: str, project_id: str) -> dict:
    matcher = SectionMatcher()
    section_regex = matcher.get_section_regex()

    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()
    layer_classes = {layer.dxf.name: classify_layer(layer.dxf.name) for layer in doc.layers}

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

    texts = []
    for e in msp:
        cls = layer_classes.get(e.dxf.layer, "unknown")
        if cls not in ("steel", "steel_text"):
            continue
        if isinstance(e, Text):
            texts.append({"text": e.dxf.text.strip(), "pos": (e.dxf.insert.x, e.dxf.insert.y)})
        elif isinstance(e, MText):
            texts.append({"text": e.plain_text().strip(), "pos": (e.dxf.insert.x, e.dxf.insert.y)})

    members_to_insert = []
    member_midpoints = []
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
            mid = ((g["start"][0] + g["end"][0]) / 2, (g["start"][1] + g["end"][1]) / 2)
            member_midpoints.append(mid)

    member_records = []
    if members_to_insert:
        result = supabase.table("steel_members").insert(members_to_insert).execute()
        inserted = result.data or []
        for rec, midpoint in zip(inserted, member_midpoints):
            member_records.append({"id": rec["id"], "midpoint": midpoint})

    total_weight_kg = sum(m["total_weight_kg"] for m in members_to_insert)
    unique_sections = len(set(m["section_name"] for m in members_to_insert))

    connections_extracted = 0
    for e in msp:
        cls = layer_classes.get(e.dxf.layer, "unknown")
        if cls != "connection":
            continue

        text, pos = None, None
        if isinstance(e, Text):
            text = e.dxf.text.strip()
            pos = (e.dxf.insert.x, e.dxf.insert.y)
        elif isinstance(e, MText):
            text = e.plain_text().strip()
            pos = (e.dxf.insert.x, e.dxf.insert.y)
        else:
            continue

        if not text:
            continue

        has_bolts = bool(re.search(r"M\d+", text))
        has_plate = bool(re.search(r"plate", text, re.IGNORECASE))
        has_weld = bool(re.search(r"weld", text, re.IGNORECASE))
        if not (has_bolts or has_plate or has_weld):
            continue

        parsed = parse_connection_text(text)
        grid_match = re.search(r"\b([A-Z]\d{1,2})\b", text)

        conn_row = {
            "project_id": project_id,
            "connection_type": parsed["connection_type"],
            "location": {"x": pos[0], "y": pos[1]},
            "grid_reference": grid_match.group(1) if grid_match else None,
            "description": text.replace("\n", " — ")[:500],
            "confidence": "medium",
            "notes": "Extracted from DXF connection callout",
        }
        conn_result = supabase.table("connections").insert(conn_row).execute()
        connection_id = conn_result.data[0]["id"]
        connections_extracted += 1

        if parsed["bolt_groups"]:
            supabase.table("bolt_groups").insert([{**bg, "connection_id": connection_id} for bg in parsed["bolt_groups"]]).execute()
        if parsed["welds"]:
            supabase.table("weld_details").insert([{**w, "connection_id": connection_id} for w in parsed["welds"]]).execute()
        if parsed["plates"]:
            supabase.table("connection_plates").insert([{**p, "connection_id": connection_id} for p in parsed["plates"]]).execute()

        nearby_ids = find_nearby_member_ids(pos, member_records)
        if nearby_ids:
            supabase.table("connection_members").insert([{"connection_id": connection_id, "member_id": mid} for mid in nearby_ids]).execute()

    supabase.table("projects").update({
        "status": "review",
        "total_members": len(members_to_insert),
        "total_unique_sections": unique_sections,
        "total_connections": connections_extracted,
        "total_weight_kg": total_weight_kg,
        "total_weight_tonnes": round(total_weight_kg / 1000, 3),
    }).eq("id", project_id).execute()

    return {
        "members_extracted": len(members_to_insert),
        "unique_sections": unique_sections,
        "connections_extracted": connections_extracted,
        "total_weight_kg": total_weight_kg,
    }
