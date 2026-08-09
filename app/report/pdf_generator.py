"""
SteelSpec PDF report generator — premium edition.

Pulls a project's real extracted data from Supabase and produces a
polished, professional steel schedule + connection report. Built to
look like something worth paying for: a proper cover page, a project
summary with metric cards, a clean member schedule, and individual
connection detail cards with bolt/plate/weld specifications.

Returns raw PDF bytes — the caller uploads them to Storage.
"""
from io import BytesIO
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

from app.supabase_client import supabase

# === BRAND PALETTE ===
RUST = HexColor("#c4633a")
RUST_LIGHT = HexColor("#d4722a")
RUST_DARK = HexColor("#9e4e2c")
RUST_BG = HexColor("#faf1ec")
RUST_BORDER = HexColor("#eeddd3")
DARK_NAVY = HexColor("#141414")
DARK_NAVY2 = HexColor("#1e1e1e")
INK = HexColor("#1a1a1a")
INK2 = HexColor("#444444")
GREY = HexColor("#8a857e")
GREY_LIGHT = HexColor("#b5b0a8")
BG = HexColor("#faf9f7")
BORDER = HexColor("#e9e5df")
BORDER_LIGHT = HexColor("#f1eee9")
GREEN = HexColor("#2d8a4e")
GREEN_BG = HexColor("#edf7f0")
AMBER = HexColor("#b07d18")
AMBER_BG = HexColor("#faf4e6")
BLUE = HexColor("#2d5f8a")
BLUE_BG = HexColor("#ecf2f8")
TABLE_ALT_ROW = HexColor("#faf8f6")

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 15 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

CONFIDENCE_COLORS = {
    "high": (GREEN, GREEN_BG),
    "medium": (AMBER, AMBER_BG),
    "low": (HexColor("#c44"), HexColor("#fbeaea")),
    "manual": (BLUE, BLUE_BG),
}
CONNECTION_TYPE_LABELS = {
    "bolted": "Bolted",
    "welded": "Welded",
    "bolted_and_welded": "Bolted & Welded",
    "unspecified": "Unspecified",
}


def _hex(c) -> str:
    """Convert a reportlab Color to a '#rrggbb' string usable in <font color="..."> tags."""
    return "#{:02x}{:02x}{:02x}".format(
        int(round(c.red * 255)), int(round(c.green * 255)), int(round(c.blue * 255))
    )


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="SectionLabel", fontName="Helvetica-Bold", fontSize=9.5,
        textColor=RUST, spaceAfter=2 * mm, tracking=1.2,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", fontName="Helvetica-Bold", fontSize=17,
        textColor=INK, spaceAfter=2 * mm, leading=20,
    ))
    styles.add(ParagraphStyle(
        name="SectionDesc", fontName="Helvetica", fontSize=9.5,
        textColor=GREY, spaceAfter=5 * mm, leading=14,
    ))
    styles.add(ParagraphStyle(
        name="CellText", fontName="Helvetica", fontSize=8, textColor=INK, leading=11,
    ))
    styles.add(ParagraphStyle(
        name="CellTextMono", fontName="Helvetica", fontSize=7.5, textColor=INK2, leading=10,
    ))
    styles.add(ParagraphStyle(
        name="CellHeader", fontName="Helvetica-Bold", fontSize=7.5, textColor=white, leading=10,
    ))
    styles.add(ParagraphStyle(
        name="CardTitle", fontName="Helvetica-Bold", fontSize=11, textColor=INK, spaceAfter=1 * mm,
    ))
    styles.add(ParagraphStyle(
        name="CardMeta", fontName="Helvetica", fontSize=8, textColor=GREY, leading=12,
    ))
    return styles


# === COVER PAGE (drawn directly on canvas for full control, no Table row-height quirks) ===

def _draw_cover(canvas_obj, project: dict):
    w, h = A4
    canvas_obj.saveState()

    # Full dark background block
    block_top = h - 15 * mm
    block_bottom = h - 175 * mm
    canvas_obj.setFillColor(DARK_NAVY)
    canvas_obj.rect(15 * mm, block_bottom, w - 30 * mm, block_top - block_bottom, fill=1, stroke=0)

    # Subtle engineering grid texture (faint rust lines)
    canvas_obj.saveState()
    canvas_obj.setStrokeColor(HexColor("#2a2a2a"))
    canvas_obj.setLineWidth(0.3)
    x = 15 * mm
    while x < w - 15 * mm:
        canvas_obj.line(x, block_bottom, x, block_top)
        x += 12 * mm
    canvas_obj.restoreState()

    # Logo mark (rounded square with S)
    logo_x, logo_y = 25 * mm, block_top - 22 * mm
    canvas_obj.setFillColor(RUST)
    canvas_obj.roundRect(logo_x, logo_y, 9 * mm, 9 * mm, 2 * mm, fill=1, stroke=0)
    canvas_obj.setFillColor(white)
    canvas_obj.setFont("Helvetica-Bold", 14)
    canvas_obj.drawCentredString(logo_x + 4.5 * mm, logo_y + 2.6 * mm, "S")

    canvas_obj.setFillColor(RUST)
    canvas_obj.setFont("Helvetica-Bold", 11)
    canvas_obj.drawString(logo_x + 13 * mm, logo_y + 3 * mm, "STEELSPEC")

    # Eyebrow label
    canvas_obj.setFillColor(RUST_LIGHT)
    canvas_obj.setFont("Helvetica-Bold", 8.5)
    eyebrow_y = block_top - 42 * mm
    canvas_obj.drawString(25 * mm, eyebrow_y, "— STRUCTURAL STEEL TAKEOFF")

    # Project name (wrap manually if long)
    name = project.get("name") or "Steel Takeoff Report"
    canvas_obj.setFillColor(white)
    canvas_obj.setFont("Helvetica-Bold", 26)
    max_width = w - 50 * mm
    title_y = block_top - 58 * mm
    words = name.split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if canvas_obj.stringWidth(trial, "Helvetica-Bold", 26) > max_width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    for i, line in enumerate(lines[:2]):
        canvas_obj.drawString(25 * mm, title_y - i * 11 * mm, line)

    subtitle_y = title_y - (len(lines[:2])) * 11 * mm - 6 * mm
    canvas_obj.setFillColor(HexColor("#c4a084"))
    canvas_obj.setFont("Helvetica", 11)
    canvas_obj.drawString(25 * mm, subtitle_y, "Structural Steel Schedule & Connection Report")

    # Detail lines
    details = [
        ("Client", project.get("client")),
        ("Structural Engineer", project.get("structural_engineer")),
        ("Engineer Reference", project.get("engineer_reference")),
        ("Source File", project.get("source_file")),
        ("Date Generated", datetime.now().strftime("%d %B %Y")),
    ]
    detail_y = subtitle_y - 14 * mm
    canvas_obj.setFont("Helvetica", 9.5)
    for label, value in details:
        if not value:
            continue
        canvas_obj.setFillColor(HexColor("#8a8378"))
        canvas_obj.drawString(25 * mm, detail_y, f"{label}")
        canvas_obj.setFillColor(white)
        canvas_obj.drawString(25 * mm + 42 * mm, detail_y, str(value))
        detail_y -= 6.5 * mm

    canvas_obj.restoreState()

    # Disclaimer box below the dark block
    canvas_obj.saveState()
    disc_top = block_bottom - 8 * mm
    disc_height = 26 * mm
    canvas_obj.setFillColor(BG)
    canvas_obj.rect(15 * mm, disc_top - disc_height, w - 30 * mm, disc_height, fill=1, stroke=0)
    canvas_obj.setStrokeColor(RUST)
    canvas_obj.setLineWidth(1.5)
    canvas_obj.line(15 * mm, disc_top - disc_height, 15 * mm, disc_top)

    text = (
        "IMPORTANT: This report is an automated extraction of structural steel data from the "
        "uploaded design file. It is intended for estimating and fabrication planning purposes "
        "only. All quantities and connection details should be verified against the original "
        "structural drawings before fabrication. This report does not constitute engineering "
        "design or advice."
    )
    canvas_obj.setFillColor(INK2)
    canvas_obj.setFont("Helvetica", 7.5)
    _wrap_text_canvas(canvas_obj, text, 20 * mm, disc_top - 6 * mm, w - 45 * mm, 7.5, "Helvetica", 10)
    canvas_obj.restoreState()


def _wrap_text_canvas(canvas_obj, text, x, y, max_width, font_size, font_name, line_height):
    words = text.split()
    line = ""
    lines = []
    for word in words:
        trial = f"{line} {word}".strip()
        if canvas_obj.stringWidth(trial, font_name, font_size) > max_width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    for i, l in enumerate(lines):
        canvas_obj.drawString(x, y - i * line_height, l)


# === HEADER / FOOTER FOR CONTENT PAGES ===

def _page_chrome(canvas_obj, doc_obj, project_name: str):
    canvas_obj.saveState()
    w, h = A4
    canvas_obj.setStrokeColor(RUST)
    canvas_obj.setLineWidth(0.6)
    canvas_obj.line(MARGIN, h - MARGIN, w - MARGIN, h - MARGIN)
    canvas_obj.setFont("Helvetica-Bold", 7.5)
    canvas_obj.setFillColor(RUST)
    canvas_obj.drawString(MARGIN, h - MARGIN + 3 * mm, "STEELSPEC")
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.setFillColor(GREY)
    canvas_obj.drawRightString(w - MARGIN, h - MARGIN + 3 * mm, project_name or "")

    canvas_obj.setStrokeColor(BORDER)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(MARGIN, MARGIN, w - MARGIN, MARGIN)
    canvas_obj.setFont("Helvetica", 6.5)
    canvas_obj.setFillColor(GREY_LIGHT)
    canvas_obj.drawString(MARGIN, MARGIN - 5 * mm, f"Generated {datetime.now().strftime('%d %b %Y at %H:%M')}")
    canvas_obj.drawRightString(w - MARGIN, MARGIN - 5 * mm, f"Page {doc_obj.page}")
    canvas_obj.restoreState()


# === TABLE HELPER ===

def _styled_table(data, col_widths, has_totals_row=False):
    table = Table(data, colWidths=col_widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), RUST),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, RUST_DARK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER_LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROUNDEDCORNERS", [3, 3, 0, 0]),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    if has_totals_row:
        cmds += [
            ("LINEABOVE", (0, -1), (-1, -1), 1.2, INK),
            ("BACKGROUND", (0, -1), (-1, -1), RUST_BG),
        ]
    table.setStyle(TableStyle(cmds))
    return table


def _metric_card(value: str, label: str, styles, accent=RUST):
    combined = Paragraph(
        f'<font size="22" color="{_hex(INK)}"><b>{value}</b></font>'
        f'<br/><br/>'
        f'<font size="7.5" color="{_hex(GREY)}">{label.upper()}</font>',
        ParagraphStyle("MetricCombined", fontName="Helvetica", alignment=TA_CENTER, leading=26),
    )
    inner = Table([[combined]], colWidths=[38 * mm])
    inner.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LINEABOVE", (0, 0), (-1, 0), 2.5, accent),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
    ]))
    return inner


def _confidence_pill(confidence: str, styles):
    fg, bg = CONFIDENCE_COLORS.get((confidence or "high").lower(), CONFIDENCE_COLORS["high"])
    return Paragraph(
        f'<font color="{_hex(fg)}"><b>&#9679;</b></font> '
        f'<font color="{_hex(fg)}">{(confidence or "High").title()}</font>',
        styles["CellText"],
    )


def _type_pill(conn_type: str, styles):
    label = CONNECTION_TYPE_LABELS.get(conn_type, (conn_type or "Unspecified").title())
    color = RUST if "bolt" in (conn_type or "") else BLUE
    return Paragraph(f'<font color="{_hex(color)}"><b>{label}</b></font>', styles["CellText"])


def generate_report_pdf(project_id: str) -> bytes:
    styles = _styles()

    project = supabase.table("projects").select("*").eq("id", project_id).single().execute().data
    members = (
        supabase.table("steel_members").select("*").eq("project_id", project_id).order("mark").execute().data
    )
    connections = (
        supabase.table("connections")
        .select("*, bolt_groups(*), weld_details(*), connection_plates(*), connection_members(steel_members(mark, section_name))")
        .eq("project_id", project_id)
        .execute()
        .data
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=22 * mm, bottomMargin=22 * mm,
    )
    story = []
    project_name = project.get("name") or "Untitled project"

    # === PAGE 1 handled entirely by canvas draw (cover), story starts on page 2 ===
    story.append(PageBreak())

    # === PROJECT SUMMARY ===
    total_weight_kg = sum(float(m.get("total_weight_kg") or 0) for m in members)
    unique_sections = len(set(m.get("section_name") for m in members if m.get("section_name")))

    story.append(Paragraph("SUMMARY", styles["SectionLabel"]))
    story.append(Paragraph("Project Summary", styles["SectionHeading"]))
    story.append(Paragraph(
        "Key figures extracted from the uploaded structural model, calculated automatically "
        "against the NZ/AU steel section database.",
        styles["SectionDesc"],
    ))

    cards = [[
        _metric_card(str(len(members)), "Members", styles),
        _metric_card(str(unique_sections), "Sections", styles, accent=BLUE),
        _metric_card(str(len(connections)), "Connections", styles, accent=RUST_DARK),
        _metric_card(f"{total_weight_kg / 1000:.2f}t", "Total Weight", styles, accent=GREEN),
    ]]
    card_table = Table(cards, colWidths=[CONTENT_WIDTH / 4] * 4)
    card_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(card_table)
    story.append(Spacer(1, 10 * mm))

    # === MEMBER SCHEDULE ===
    story.append(Paragraph("SCHEDULE", styles["SectionLabel"]))
    story.append(Paragraph("Steel Member Schedule", styles["SectionHeading"]))
    story.append(Paragraph(
        "Every structural steel member extracted from the design file, matched against the "
        "steel section database with calculated weights.",
        styles["SectionDesc"],
    ))

    header = [Paragraph(h, styles["CellHeader"]) for h in
              ["Mark", "Section", "Grade", "Length (mm)", "Qty", "kg/m", "Total (kg)", "Confidence"]]
    data = [header]
    for m in members:
        w = float(m.get("total_weight_kg") or 0)
        data.append([
            Paragraph(m.get("mark") or "-", styles["CellText"]),
            Paragraph(m.get("section_name") or m.get("section_name_raw") or "?", styles["CellTextMono"]),
            Paragraph(m.get("grade") or "-", styles["CellText"]),
            Paragraph(f'{m.get("length_mm"):.0f}' if m.get("length_mm") else "-", styles["CellTextMono"]),
            Paragraph(str(m.get("quantity") or 1), styles["CellTextMono"]),
            Paragraph(f'{m.get("weight_per_metre"):.1f}' if m.get("weight_per_metre") else "-", styles["CellTextMono"]),
            Paragraph(f"{w:.1f}", styles["CellTextMono"]),
            _confidence_pill(m.get("confidence"), styles),
        ])
    data.append([
        "", "", "", "", "", "",
        Paragraph(f"<b>{total_weight_kg:.1f}</b>", styles["CellText"]), "",
    ])

    col_widths = [16 * mm, 32 * mm, 18 * mm, 22 * mm, 12 * mm, 16 * mm, 20 * mm, 24 * mm]
    story.append(_styled_table(data, col_widths, has_totals_row=True))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f'<font color="{_hex(RUST)}"><b>Total structural steel weight: '
        f'{total_weight_kg:.0f} kg ({total_weight_kg / 1000:.2f} tonnes)</b></font>',
        ParagraphStyle("Tonnage", fontName="Helvetica-Bold", fontSize=12, alignment=TA_RIGHT),
    ))

    # === CONNECTIONS ===
    story.append(PageBreak())
    story.append(Paragraph("CONNECTIONS", styles["SectionLabel"]))
    story.append(Paragraph("Connection Summary", styles["SectionHeading"]))

    if connections:
        story.append(Paragraph(
            "Connections identified in the design file with bolt, weld, and plate details as "
            "specified, cross-referenced to the members they join.",
            styles["SectionDesc"],
        ))

        for idx, c in enumerate(connections, start=1):
            story.append(KeepTogether(_connection_card(c, idx, styles)))
            story.append(Spacer(1, 5 * mm))
    else:
        story.append(Paragraph(
            "No connection details were identified in this file. Connection extraction "
            "(bolts, plates, welds) covers models where the engineer has specified this "
            "information explicitly in the drawing — it may not be present in every source "
            "file.",
            ParagraphStyle("Note", fontName="Helvetica", fontSize=9.5, textColor=GREY,
                           leading=14, backColor=BG, borderPadding=(5 * mm, 5 * mm, 5 * mm, 5 * mm)),
        ))

    unmatched = project.get("unmatched_sections") or []
    if unmatched:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("UNMATCHED SECTIONS", styles["SectionLabel"]))
        story.append(Paragraph(
            "The following section callouts could not be matched to the steel section "
            "database and require manual review:",
            styles["SectionDesc"],
        ))
        for s in unmatched:
            story.append(Paragraph(f"&bull; {s}", styles["CellText"]))

    def _on_page(canvas_obj, doc_obj):
        if doc_obj.page == 1:
            _draw_cover(canvas_obj, project)
        else:
            _page_chrome(canvas_obj, doc_obj, project_name)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buffer.getvalue()


def _connection_card(c: dict, idx: int, styles):
    """Builds a single bordered connection detail card as a flowable list."""
    elements = []

    conn_type = c.get("connection_type") or "unspecified"
    members_linked = [
        cm["steel_members"]["mark"]
        for cm in (c.get("connection_members") or [])
        if cm.get("steel_members")
    ]
    members_str = ", ".join(members_linked) if members_linked else "Not linked to a specific member"

    header_row = Table(
        [[
            Paragraph(f"<b>Connection {idx}</b>", styles["CardTitle"]),
            _type_pill(conn_type, styles),
        ]],
        colWidths=[CONTENT_WIDTH - 40 * mm, 40 * mm],
    )
    header_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    elements.append(header_row)

    meta_bits = []
    if c.get("grid_reference"):
        meta_bits.append(f"Grid {c['grid_reference']}")
    meta_bits.append(f"Connects: {members_str}")
    elements.append(Paragraph(" &nbsp;•&nbsp; ".join(meta_bits), styles["CardMeta"]))

    if c.get("description"):
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(c["description"], styles["CellText"]))

    spec_rows = []
    for bg in c.get("bolt_groups") or []:
        spec_rows.append(["Bolts", f'{bg.get("quantity", "?")}× {bg.get("bolt_size", "?")} Gr{bg.get("bolt_grade", "?")}'])
    for p in c.get("connection_plates") or []:
        dims = f'{p.get("thickness", "?")}mm'
        if p.get("width") and p.get("depth"):
            dims += f' × {p["width"]} × {p["depth"]}'
        spec_rows.append([(p.get("plate_type") or "Plate").replace("_", " ").title(), dims])
    for w in c.get("weld_details") or []:
        wd = f'{(w.get("weld_type") or "").title()} weld, {w.get("size", "?")}mm'
        if w.get("length"):
            wd += f', {w["length"]}mm long'
        spec_rows.append(["Weld", wd])

    if spec_rows:
        spec_table = Table(
            [[Paragraph(f"<b>{label}</b>", styles["CellText"]), Paragraph(val, styles["CellTextMono"])]
             for label, val in spec_rows],
            colWidths=[30 * mm, CONTENT_WIDTH - 30 * mm],
        )
        spec_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(Spacer(1, 2 * mm))
        elements.append(spec_table)

    card = Table([[elements]], colWidths=[CONTENT_WIDTH])
    card.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("LINEBEFORE", (0, 0), (0, 0), 2.5, RUST),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return [card]