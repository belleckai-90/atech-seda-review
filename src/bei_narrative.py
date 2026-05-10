"""Generate BEI report narrative sections via Claude API (streaming)."""
from __future__ import annotations

import json
import os
from typing import Any

import anthropic
import json_repair

_MODEL      = "claude-sonnet-4-6"
_MAX_TOKENS = 8192

_SYSTEM = """You are a professional energy auditor at Atech Energy Sdn Bhd writing a \
Building Energy Intensity (BEI) report for submission to Suruhanjaya Tenaga (ST), Malaysia.

Write in formal engineering report style. Use professional Malaysian English. Be factual, \
precise, and reference the specific BEI figures, periods, and building details provided.

This report must reference compliance with the applicable energy efficiency reporting \
requirements under the Energy Efficiency and Conservation Act 2024 (EECA 2024). \
Mention EECA 2024 explicitly in the executive_summary and conclusions_recommendations sections.

Return ONLY a valid JSON object with exactly these five string keys:
- executive_summary        : 3-4 paragraphs covering building overview, BEI result, star rating, EECA 2024 compliance
- intro_narrative          : 1-2 paragraphs on report scope and data collection period
- bei_analysis             : 2-3 paragraphs analysing BEI result vs ST thresholds
- landlord_tenant_analysis : 1-2 paragraphs on landlord vs tenant consumption split
- conclusions_recommendations : bullet points as a single string, each separated by \\n

Do not include any text outside the JSON object."""


def _star_rating(bei: float) -> str:
    if bei <= 100:  return "5-Star"
    if bei <= 135:  return "4-Star"
    if bei <= 175:  return "3-Star"
    if bei <= 220:  return "2-Star"
    return "1-Star"


def generate_bei_narrative(
    profile: dict[str, Any],
    energy_data: dict[str, Any],
    api_key: str,
) -> dict[str, str]:
    """Call Claude to write all narrative sections. Returns dict of section texts."""
    os.environ["ANTHROPIC_API_KEY"] = api_key
    client = anthropic.Anthropic()

    gfa     = float(profile.get("gfa", 1) or 1)
    total_a = float(energy_data.get("total_a", 0) or 0)
    total_b = float(energy_data.get("total_b", 0) or 0)
    total_c = float(energy_data.get("total_c", 0) or 0)
    bei     = round(total_a / gfa, 2)           # BEI = Total Supply (A) ÷ GFA

    context = {
        "building_name":        profile.get("building_name", ""),
        "client_name":          profile.get("client_name", ""),
        "address":              profile.get("address", ""),
        "building_type":        profile.get("building_type", "Commercial"),
        "year_completed":       profile.get("year_completed", ""),
        "gfa_m2":               gfa,
        "ac_pct":               profile.get("ac_pct", 0),
        "nfa_m2":               profile.get("nfa", 0),
        "actual_occupant_load_pct": profile.get("actual_load_pct", 0),
        "certifications":       profile.get("certifications", "None"),
        "operating_hours":      profile.get("operating_hours", ""),
        "period":               energy_data.get("period_label", ""),
        "num_months":           len(energy_data.get("months", [])),
        "total_supply_kwh":     total_a,
        "total_tenant_kwh":     total_b,
        "net_landlord_load_kwh": total_c,
        "bei_kwh_m2_yr":        bei,
        "bei_gj_m2_yr":         round(bei * 0.0036, 3),
        "star_rating":          _star_rating(bei),
        "landlord_pct":         round(total_c / total_a * 100, 1) if total_a else 0,
        "tenant_pct":           round(total_b / total_a * 100, 1) if total_a else 0,
        "preparer":             profile.get("preparer_name", "Atech Energy Sdn Bhd"),
        "submission_date":      profile.get("submission_date", ""),
        "bei_basis":            "Total Supply to Building (A) divided by GFA. Net Landlord Load (C = A minus B) is separately reported.",
    }

    user_msg = (
        "Write the BEI report narrative sections for this building:\n\n"
        + json.dumps(context, indent=2)
    )

    with client.messages.stream(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        raw = stream.get_final_text()

    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        end = len(lines) - 1
        while end > 0 and lines[end].strip() in ("```", ""):
            end -= 1
        raw = "\n".join(lines[1:end + 1])

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        result = json_repair.loads(raw)
        if isinstance(result, dict):
            return result
        raise RuntimeError(f"Could not parse narrative JSON: {raw[:300]}")
