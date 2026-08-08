"""
SteelSpec PDF report generator.

Pulls a project's real extracted data straight from Supabase
(steel_members, connections + their bolt/weld/plate details) and
produces a professional steel schedule + connection report PDF,
matching the style established during earlier prototyping.

Returns raw PDF bytes — the caller is responsible for uploading
them to Storage.
"""
from io import BytesIO
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

from app.supabase_client import supabase

# === COLOURS (matching the SteelSpec brand) ===
RUST = HexColor("#c4633a")
RUST_LIGHT = HexColor("#d4722a")
DARK_NAVY = HexColor("#1a1a1a")
LIGHT_BLUE = HexColor("#e8f0f8")  # kept for section shading continuity
LIGHT_GREY = HexColor("#f5f6f8")
MID_GREY = HexColor("#666666")
TABLE_ALT_ROW = HexColor("#f5f8fb")

PAGE_WIDTH, _ = A4


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="CoverTitle", fontName="Helvetica-Bold", fontSize=26,
        textColor=white, spaceAfter=6 * mm,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", fontName="Helvetica-Bold", fontSize=15,
        textColor=DARK_NAVY, spaceBefore=6 * mm, spaceAfter=3 * mm,
    ))
    styles.add(ParagraphStyle(
        name="BodyText2", fontName="Helvetica", fontSize=9,
        textColor=MID_GREY, spaceAfter=2 * mm, leading=13,
    ))
    return styles


def _styled_table(data, col_widths, has_totals_row=False):
    table = Table(data, colWidths=col_widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), RUST),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, 0), 1, RUST),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, HexColor("#e0e0e0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    if has_totals_row:
        cmds += [
            ("LINEABOVE", (0, -1), (-1, -1), 1, DARK_NAVY),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ]
    table.setStyle(TableStyle(cmds))
    return table


def generate_report_pdf(project_id: str) -> bytes:
    """
    Build the full steel schedule + connection report PDF for a
    project, pulling live data from Supabase. Returns PDF bytes.
    """
    styles = _styles()

    project = supabase.table("projects").select("*").eq("id", project_id).single().execute().data
    members = (
        supabase.table("steel_members")
        .select("*")
        .eq("project_id", project_id)
        .order("mark")
        .execute()
        .data
    )
    connections = (
        supabase.table("connections")
        .select("*, bolt_groups(*), weld_details(*), connection_plates(*)")
        .eq("project_id", project_id)
        .execute()
        .data
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
    )
    story = []

    # === COVER PAGE ===
    cover_rows = [[""], [""]]
    cover_rows.append([Paragraph("STEELSPEC", ParagraphStyle(
        "Logo", fontName="Helvetica-Bold", fontSize=13, textColor=RUST_LIGHT, spaceAfter=12 * mm,
    ))])
    cover_rows.append([Paragraph(project.get("name") or "Steel Takeoff Report", styles["CoverTitle"])])
    cover_rows.append([Paragraph(
        "Structural Steel Schedule &amp; Connection Report",
        ParagraphStyle("Sub", fontName="Helvetica", fontSize=12, textColor=HexColor("#b0c4de")),
    )])
    cover_rows.append([""])

    details = [
        ("Address", project.get("address")),
        ("Client", project.get("client")),
        ("Structural Engineer", project.get("structural_engineer")),
        ("Engineer Reference", project.get("engineer_reference")),
        ("Source File", project.get("source_file")),
        ("Date Generated", datetime.now().strftime("%d %B %Y")),
    ]
    for label, value in details:
        if value:
            cover_rows.append([Paragraph(
                f'<font color="#c4a084"><b>{label}:</b></font>&nbsp;&nbsp;'
                f'<font color="#ffffff">{value}</font>',
                ParagraphStyle("Detail", fontName="Helvetica", fontSize=10, textColor=white,
                               spaceAfter=2 * mm, leading=14),
            )])

    cover_table = Table(cover_rows, colWidths=[PAGE_WIDTH - 30 * mm])
    cover_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK_NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 20 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 20 * mm),
        ("TOPPADDING", (0, 0), (-1, 0), 30 * mm),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 20 * mm),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(cover_table)
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "<b>IMPORTANT:</b> This report is an automated extraction of structural steel data "
        "from the uploaded design file. It is intended for estimating and fabrication "
        "planning purposes only. All quantities and connection details should be verified "
        "against the original structural drawings before fabrication. This report does not "
        "constitute engineering design or advice.",
        ParagraphStyle("Disclaimer", fontName="Helvetica", fontSize=7.5, textColor=MID_GREY,
                       leading=10, backColor=LIGHT_GREY, borderPadding=(3 * mm, 3 * mm, 3 * mm, 3 * mm)),
    ))
    story.append(PageBreak())

    # === MEMBER SCHEDULE ===
    story.append(Paragraph("Steel Member Schedule", styles["SectionHeading"]))
    story.append(Paragraph(
        "All structural steel members extracted from the uploaded design file, with section "
        "properties and calculated weights.",
        styles["BodyText2"],
    ))

    header = ["Mark", "Section", "Grade", "Length\n(mm)", "Qty", "kg/m", "Total\n(kg)", "Confidence"]
    data = [header]
    total_weight = 0.0
    for m in members:
        w = float(m.get("total_weight_kg") or 0)
        total_weight += w
        data.append([
            m.get("mark") or "-",
            m.get("section_name") or m.get("section_name_raw") or "?",
            m.get("grade") or "-",
            f'{m.get("length_mm"):.0f}' if m.get("length_mm") else "-",
            str(m.get("quantity") or 1),
            f'{m.get("weight_per_metre"):.1f}' if m.get("weight_per_metre") else "-",
            f"{w:.1f}",
            (m.get("confidence") or "high").title(),
        ])
    data.append(["", "", "", "", "", "", f"{total_weight:.1f}", ""])

    col_widths = [16 * mm, 30 * mm, 18 * mm, 20 * mm, 14 * mm, 16 * mm, 18 * mm, 22 * mm]
    story.append(_styled_table(data, col_widths, has_totals_row=True))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f"<b>Total structural steel weight: {total_weight:.0f} kg "
        f"({total_weight / 1000:.2f} tonnes)</b>",
        ParagraphStyle("Tonnage", fontName="Helvetica-Bold", fontSize=11,
                       textColor=RUST, alignment=TA_RIGHT),
    ))

    # === CONNECTIONS (if any were extracted) ===
    story.append(PageBreak())
    story.append(Paragraph("Connection Summary", styles["SectionHeading"]))

    if connections:
        story.append(Paragraph(
            "Connections identified in the design file with bolt, weld, and plate details "
            "as specified.",
            styles["BodyText2"],
        ))
        conn_header = ["ID", "Grid", "Type", "Description", "Bolts", "Detail Ref"]
        conn_data = [conn_header]
        for c in connections:
            bolt_desc = "-"
            if c.get("bolt_groups"):
                bg = c["bolt_groups"][0]
                bolt_desc = f'{bg["quantity"]}x {bg["bolt_size"]} Gr{bg["bolt_grade"]}'
            conn_data.append([
                c.get("id", "")[:8],
                c.get("grid_reference") or "-",
                (c.get("connection_type") or "-").replace("_", " "),
                c.get("description") or "-",
                bolt_desc,
                c.get("detail_reference") or "-",
            ])
        story.append(_styled_table(conn_data, [16 * mm, 14 * mm, 20 * mm, 55 * mm, 26 * mm, 18 * mm]))
    else:
        story.append(Paragraph(
            "No connection details were identified in this file. Connection extraction "
            "(bolts, plates, welds) covers models where the engineer has specified this "
            "information explicitly — it may not be present in every source file, and "
            "broader connection extraction support is still being extended.",
            ParagraphStyle("Note", fontName="Helvetica", fontSize=9.5, textColor=MID_GREY,
                           leading=14, backColor=LIGHT_GREY, borderPadding=(4 * mm, 4 * mm, 4 * mm, 4 * mm)),
        ))

    # === WARNINGS ===
    unmatched = project.get("unmatched_sections") or []
    if unmatched:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("Unmatched Sections", styles["SectionHeading"]))
        story.append(Paragraph(
            "The following section callouts could not be matched to the steel section "
            "database and require manual review:",
            styles["BodyText2"],
        ))
        for s in unmatched:
            story.append(Paragraph(f"&bull; {s}", styles["BodyText2"]))

    def _footer(canvas_obj, doc_obj):
        canvas_obj.saveState()
        w, h = A4
        canvas_obj.setStrokeColor(RUST)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(15 * mm, h - 15 * mm, w - 15 * mm, h - 15 * mm)
        canvas_obj.setFont("Helvetica-Bold", 7)
        canvas_obj.setFillColor(RUST)
        canvas_obj.drawString(15 * mm, h - 13 * mm, "STEELSPEC")
        canvas_obj.setFont("Helvetica", 7)
        canvas_obj.setFillColor(MID_GREY)
        canvas_obj.drawRightString(w - 15 * mm, h - 13 * mm, project.get("name") or "")
        canvas_obj.setStrokeColor(HexColor("#dddddd"))
        canvas_obj.line(15 * mm, 15 * mm, w - 15 * mm, 15 * mm)
        canvas_obj.setFont("Helvetica", 6.5)
        canvas_obj.drawString(15 * mm, 10 * mm, f"Generated {datetime.now().strftime('%d %b %Y at %H:%M')}")
        canvas_obj.drawRightString(w - 15 * mm, 10 * mm, f"Page {doc_obj.page}")
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()