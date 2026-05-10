"""Render findings JSON → PDF using reportlab."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

_NAVY   = colors.HexColor("#1A2E4A")
_TEAL   = colors.HexColor("#007B8A")
_ORANGE = colors.HexColor("#E8631A")
_RED    = colors.HexColor("#CC2A2A")
_GREEN  = colors.HexColor("#1E8A44")
_LGRAY  = colors.HexColor("#F4F6F8")
_MGRAY  = colors.HexColor("#5A6A7A")
_YELLOW = colors.HexColor("#F5A623")
_WHITE  = colors.white

_LOGO_PATH = Path(__file__).parent.parent / "assets" / "logo.png"

_SEV_COLOR = {
    "Critical": _RED,
    "Major":    _ORANGE,
    "Moderate": _TEAL,
    "Minor":    _MGRAY,
}


def _styles():
    base = getSampleStyleSheet()
    return {
        "title":   ParagraphStyle("title",   fontSize=16, textColor=_NAVY,  leading=20, spaceAfter=4, fontName="Helvetica-Bold"),
        "sub":     ParagraphStyle("sub",     fontSize=10, textColor=_MGRAY, leading=13, spaceAfter=2),
        "section": ParagraphStyle("section", fontSize=11, textColor=_TEAL,  leading=14, spaceBefore=10, spaceAfter=4, fontName="Helvetica-Bold"),
        "body":    ParagraphStyle("body",    fontSize=8,  textColor=colors.black, leading=11),
        "small":   ParagraphStyle("small",   fontSize=7,  textColor=_MGRAY, leading=10),
        "bold":    ParagraphStyle("bold",    fontSize=8,  textColor=colors.black, leading=11, fontName="Helvetica-Bold"),
        "center":  ParagraphStyle("center",  fontSize=8,  textColor=colors.black, leading=11, alignment=TA_CENTER),
    }


def render_pdf(findings: dict[str, Any], verified: dict[str, Any],
               report_filename: str = "") -> bytes:
    """Render findings JSON to PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=2*cm,    bottomMargin=2*cm,
    )

    S = _styles()
    story = []

    meta     = findings.get("metadata", {})
    building = meta.get("building_name", report_filename)
    owner    = meta.get("building_owner", "")
    rea      = meta.get("rea_name", "")
    rtype    = meta.get("report_type", "")
    rev_date = meta.get("review_date", "")

    # ── Title block ──────────────────────────────────────────────────────────
    title_data = [
        [Paragraph("SEDA ENERGY AUDIT REPORT — INTERNAL REVIEW", S["title"]),
         Paragraph("Atech Energy Sdn Bhd<br/>Atech.AI Review System", S["sub"])],
    ]
    title_tbl = Table(title_data, colWidths=["70%", "30%"])
    title_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), _NAVY),
        ("TEXTCOLOR",  (0,0), (-1,-1), _WHITE),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("PADDING",    (0,0), (-1,-1), 10),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [_NAVY]),
    ]))
    story.append(title_tbl)
    story.append(Spacer(1, 0.3*cm))

    # Meta row
    meta_data = [[
        Paragraph(f"<b>Building:</b> {building}", S["body"]),
        Paragraph(f"<b>Owner:</b> {owner}", S["body"]),
        Paragraph(f"<b>REA:</b> {rea}", S["body"]),
        Paragraph(f"<b>Type:</b> {rtype}", S["body"]),
        Paragraph(f"<b>Review Date:</b> {rev_date}", S["body"]),
    ]]
    meta_tbl = Table(meta_data, colWidths=["22%","20%","20%","15%","23%"])
    meta_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), _LGRAY),
        ("BOX",        (0,0), (-1,-1), 0.5, _TEAL),
        ("PADDING",    (0,0), (-1,-1), 6),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 0.4*cm))

    # ── Verified numbers ─────────────────────────────────────────────────────
    story.append(Paragraph("VERIFIED NUMBERS (from workbook)", S["section"]))
    b   = verified.get("baseline", {})
    reg = verified.get("regression", {})
    ch  = verified.get("chiller_plant", {})
    mm  = verified.get("cost_year_mismatch")

    num_data = [
        ["Annual kWh", f"{b.get('total_kwh', 0):,.0f}" if b.get("total_kwh") else "—",
         "Total Cost (RM)", f"{b.get('total_cost_rm', 0):,.2f}" if b.get("total_cost_rm") else "—",
         "BEI (kWh/m²/yr)", str(b.get("bei_kwh_m2_from_sheet", "—")),
         "tCO2e", f"{b.get('total_co2e_tonne', 0):,.1f}" if b.get("total_co2e_tonne") else "—",
         "R² (actual)", f"{reg.get('r_square', 0):.4f}" if reg.get("r_square") else "—"],
    ]
    num_tbl = Table(
        [[Paragraph(str(c), S["bold"] if i % 2 == 0 else S["body"]) for i, c in enumerate(num_data[0])]],
        colWidths=["9%","12%","10%","14%","11%","10%","8%","9%","8%","9%"],
    )
    num_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), _LGRAY),
        ("BOX",        (0,0), (-1,-1), 0.5, _MGRAY),
        ("PADDING",    (0,0), (-1,-1), 5),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(num_tbl)

    if mm:
        warn = (
            f"⚠ COST-YEAR MISMATCH: ESM baseline year {mm['esm_baseline_year']} "
            f"(RM {mm.get('correct_cost_for_esm_year_rm', 0):,.2f}) vs "
            f"kWh.Overall year {mm['kwh_overall_baseline_year']} "
            f"(RM {mm.get('cost_for_kwh_overall_baseline_year_rm', 0):,.2f}). "
            f"Overstatement: RM {abs(mm.get('cost_for_kwh_overall_baseline_year_rm',0) - mm.get('correct_cost_for_esm_year_rm',0)):,.2f}."
        )
        warn_tbl = Table([[Paragraph(warn, S["body"])]], colWidths=["100%"])
        warn_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#FFF3CD")),
            ("BOX",        (0,0), (-1,-1), 1, _ORANGE),
            ("PADDING",    (0,0), (-1,-1), 6),
        ]))
        story.append(Spacer(1, 0.2*cm))
        story.append(warn_tbl)

    story.append(Spacer(1, 0.4*cm))

    # ── Finding counts ───────────────────────────────────────────────────────
    sf       = findings.get("section_findings", [])
    critical = [f for f in sf if f.get("severity") == "Critical"]
    major    = [f for f in sf if f.get("severity") == "Major"]
    moderate = [f for f in sf if f.get("severity") == "Moderate"]
    minor    = [f for f in sf if f.get("severity") == "Minor"]

    count_data = [
        [Paragraph("CRITICAL", S["center"]), Paragraph("MAJOR", S["center"]),
         Paragraph("MODERATE", S["center"]), Paragraph("MINOR", S["center"]),
         Paragraph("TOTAL SECTIONS", S["center"])],
        [Paragraph(str(len(critical)), ParagraphStyle("big", fontSize=18, alignment=TA_CENTER, textColor=_RED, fontName="Helvetica-Bold")),
         Paragraph(str(len(major)),    ParagraphStyle("big", fontSize=18, alignment=TA_CENTER, textColor=_ORANGE, fontName="Helvetica-Bold")),
         Paragraph(str(len(moderate)), ParagraphStyle("big", fontSize=18, alignment=TA_CENTER, textColor=_TEAL, fontName="Helvetica-Bold")),
         Paragraph(str(len(minor)),    ParagraphStyle("big", fontSize=18, alignment=TA_CENTER, textColor=_MGRAY, fontName="Helvetica-Bold")),
         Paragraph(str(len(sf)),       ParagraphStyle("big", fontSize=18, alignment=TA_CENTER, textColor=_NAVY, fontName="Helvetica-Bold"))],
    ]
    cw = ["20%","20%","20%","20%","20%"]
    count_tbl = Table(count_data, colWidths=cw)
    count_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (0,0), colors.HexColor("#FDEAEA")),
        ("BACKGROUND",   (1,0), (1,0), colors.HexColor("#FFF3E0")),
        ("BACKGROUND",   (2,0), (2,0), colors.HexColor("#E8F4FD")),
        ("BACKGROUND",   (3,0), (3,0), _LGRAY),
        ("BACKGROUND",   (4,0), (4,0), colors.HexColor("#EAF0FB")),
        ("BACKGROUND",   (0,1), (0,1), colors.HexColor("#FDEAEA")),
        ("BACKGROUND",   (1,1), (1,1), colors.HexColor("#FFF3E0")),
        ("BACKGROUND",   (2,1), (2,1), colors.HexColor("#E8F4FD")),
        ("BACKGROUND",   (3,1), (3,1), _LGRAY),
        ("BACKGROUND",   (4,1), (4,1), colors.HexColor("#EAF0FB")),
        ("GRID",         (0,0), (-1,-1), 0.5, colors.white),
        ("PADDING",      (0,0), (-1,-1), 8),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(count_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── Section findings table ────────────────────────────────────────────────
    story.append(Paragraph("SECTION-BY-SECTION FINDINGS", S["section"]))
    story.append(HRFlowable(width="100%", thickness=1, color=_TEAL))
    story.append(Spacer(1, 0.2*cm))

    hdr = [
        Paragraph("No.", S["bold"]),
        Paragraph("Section", S["bold"]),
        Paragraph("Page", S["bold"]),
        Paragraph("Verdict", S["bold"]),
        Paragraph("Severity", S["bold"]),
        Paragraph("Remarks", S["bold"]),
    ]
    rows_pdf = [hdr]
    for f in sf:
        sev = f.get("severity", "")
        sc  = _SEV_COLOR.get(sev, _MGRAY)
        rows_pdf.append([
            Paragraph(f.get("section_no", ""), S["small"]),
            Paragraph(f.get("section_name", ""), S["body"]),
            Paragraph(f.get("page_ref", ""), S["small"]),
            Paragraph(f.get("verdict", ""), S["center"]),
            Paragraph(sev, ParagraphStyle("sev", fontSize=7, textColor=sc,
                                          fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph(f.get("remarks", "")[:400], S["small"]),
        ])

    findings_tbl = Table(rows_pdf, colWidths=["5%","16%","8%","6%","8%","57%"],
                         repeatRows=1)
    row_styles = [
        ("BACKGROUND", (0,0), (-1,0), _NAVY),
        ("TEXTCOLOR",  (0,0), (-1,0), _WHITE),
        ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#DDDDDD")),
        ("PADDING",    (0,0), (-1,-1), 4),
        ("VALIGN",     (0,0), (-1,-1), "TOP"),
    ]
    for i, f in enumerate(sf, start=1):
        sev = f.get("severity", "")
        if sev == "Critical":
            row_styles.append(("BACKGROUND", (0,i), (-1,i), colors.HexColor("#FFF0F0")))
        elif sev == "Major":
            row_styles.append(("BACKGROUND", (0,i), (-1,i), colors.HexColor("#FFF8F0")))
        elif i % 2 == 0:
            row_styles.append(("BACKGROUND", (0,i), (-1,i), _LGRAY))
    findings_tbl.setStyle(TableStyle(row_styles))
    story.append(findings_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── Ground rule scorecard ─────────────────────────────────────────────────
    story.append(Paragraph("GROUND RULE SCORECARD", S["section"]))
    story.append(HRFlowable(width="100%", thickness=1, color=_TEAL))
    story.append(Spacer(1, 0.2*cm))

    gr_labels = {
        "GR1": "Clarity for non-technical readers",
        "GR2": "Justified ESM savings",
        "GR3": "Regression R² ≥ 0.75 or justified",
        "GR4": "No Renewable Energy in EACG ESMs",
    }
    gr_scorecard = findings.get("ground_rule_scorecard", {})
    gr_data = [[Paragraph("Ground Rule", S["bold"]),
                Paragraph("Requirement", S["bold"]),
                Paragraph("Verdict", S["bold"]),
                Paragraph("Justification", S["bold"])]]
    for gr, label in gr_labels.items():
        entry   = gr_scorecard.get(gr, {})
        verdict = entry.get("verdict", "—")
        just    = entry.get("justification", "")
        vc = _GREEN if verdict.startswith("✓") else (_ORANGE if verdict.startswith("⚠") else _RED)
        gr_data.append([
            Paragraph(gr, S["bold"]),
            Paragraph(label, S["body"]),
            Paragraph(verdict, ParagraphStyle("vd", fontSize=8, textColor=vc, fontName="Helvetica-Bold")),
            Paragraph(just[:300], S["small"]),
        ])
    gr_tbl = Table(gr_data, colWidths=["8%","22%","12%","58%"])
    gr_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), _NAVY),
        ("TEXTCOLOR",  (0,0), (-1,0), _WHITE),
        ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#DDDDDD")),
        ("PADDING",    (0,0), (-1,-1), 5),
        ("VALIGN",     (0,0), (-1,-1), "TOP"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [_WHITE, _LGRAY]),
    ]))
    story.append(gr_tbl)
    story.append(Spacer(1, 0.5*cm))

    # ── Recommended action order ──────────────────────────────────────────────
    actions = findings.get("recommended_action_order", [])
    if actions:
        story.append(Paragraph("RECOMMENDED ACTION ORDER", S["section"]))
        story.append(HRFlowable(width="100%", thickness=1, color=_TEAL))
        story.append(Spacer(1, 0.2*cm))
        for a in actions:
            story.append(Paragraph(f"• {a}", S["body"]))
            story.append(Spacer(1, 0.15*cm))

    # ── Footer note ───────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_MGRAY))
    story.append(Paragraph(
        "This review was generated by Atech.AI SEDA Audit Reviewer (Atech Energy Sdn Bhd). "
        "All numerical claims are verified against the submitted workbook. "
        "Final sign-off remains with the senior Registered Energy Auditor.",
        S["small"],
    ))

    doc.build(story)
    return buf.getvalue()
