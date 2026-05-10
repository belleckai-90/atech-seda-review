"""Pass 1 — build the verified-numbers JSON from extracted xlsx data.

All numbers here come deterministically from openpyxl (data_only=True).
The LLM is never trusted to verify numbers; it receives this JSON as ground truth.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

# kWh → GJ conversion factor
KWH_TO_GJ = 0.0036


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _find_row_by_label(rows: list[list], label: str, col: int = 0) -> list | None:
    """Return first row where rows[row][col] matches label (case-insensitive)."""
    label_l = label.lower()
    for row in rows:
        if col < len(row) and row[col] is not None:
            cell = str(row[col]).strip().lower()
            if label_l in cell:
                return row
    return None


def _find_value_by_label(
    rows: list[list], label: str, label_col: int, value_col: int
) -> float | None:
    """Search rows for a label in label_col, return numeric value in value_col."""
    row = _find_row_by_label(rows, label, label_col)
    if row and value_col < len(row) and _is_numeric(row[value_col]):
        return float(row[value_col])
    return None


def _safe_round(v: float | None, dp: int = 4) -> float | None:
    return round(v, dp) if v is not None else None


# ---------------------------------------------------------------------------
# SESB billing sheet parsing
# ---------------------------------------------------------------------------

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _is_month_cell(v: Any) -> bool:
    if isinstance(v, datetime.datetime):
        return True
    if isinstance(v, str) and v.strip().lower() in _MONTH_NAMES:
        return True
    return False


def _parse_sesb_sheet(rows: list[list]) -> dict[str, Any] | None:
    """Parse a SESB billing sheet.

    Returns a dict with year, monthly data, and summary totals, or None if
    the sheet doesn't look like a SESB billing sheet.
    """
    # Find the year cell (an integer 20xx in the first few rows)
    year = None
    for row in rows[:5]:
        for cell in row:
            if isinstance(cell, int) and 2000 <= cell <= 2100:
                year = cell
                break
        if year:
            break
    if year is None:
        return None

    # Find the header row: must contain "Month" or "month"
    header_row_idx = None
    for i, row in enumerate(rows):
        for cell in row[:6]:
            if isinstance(cell, str) and "month" in cell.lower():
                header_row_idx = i
                break
        if header_row_idx is not None:
            break
    if header_row_idx is None:
        return None

    # Identify column indices from the header row
    header = rows[header_row_idx]
    col_idx: dict[str, int] = {}
    for j, cell in enumerate(header):
        if cell is None:
            continue
        s = str(cell).lower().replace("\n", " ")
        if "total kwh" in s or ("total" in s and "kwh" in s and "day" not in s):
            col_idx.setdefault("total_kwh", j)
        if "energy cost" in s or ("total rm" in s):
            col_idx.setdefault("energy_cost_rm", j)
        if "md cost" in s or "rm md" in s or ("md" in s and "rm" in s):
            col_idx.setdefault("md_cost_rm", j)
        if s.strip().startswith("md") and "cost" not in s and "rm" not in s:
            col_idx.setdefault("md_kw", j)

    # Parse monthly data rows (rows where first non-None cell is a datetime or month name)
    monthly: list[dict] = []
    totals_row = None
    for row in rows[header_row_idx + 1:]:
        # Find the "month" cell
        month_val = None
        for cell in row[:4]:
            if _is_month_cell(cell):
                month_val = cell
                break
        if month_val is not None:
            entry: dict[str, Any] = {}
            if isinstance(month_val, datetime.datetime):
                entry["month"] = month_val.month
            else:
                entry["month"] = _MONTH_NAMES.get(str(month_val).lower())
            for key, j in col_idx.items():
                if j < len(row) and _is_numeric(row[j]):
                    entry[key] = float(row[j])
            monthly.append(entry)
        else:
            # Check for a totals row (all None in month column, but numeric totals)
            nums = [row[j] for j in col_idx.values() if j < len(row) and _is_numeric(row[j])]
            if len(nums) >= 2 and totals_row is None:
                totals_row = row

    if not monthly:
        return None

    # Compute totals from monthly data (prefer explicit totals_row if present)
    def _sum_col(key: str) -> float | None:
        vals = [m[key] for m in monthly if key in m]
        return round(sum(vals), 4) if vals else None

    total_kwh = _sum_col("total_kwh")
    total_energy_cost = _sum_col("energy_cost_rm")
    total_md_cost = _sum_col("md_cost_rm")
    total_cost = None
    if total_energy_cost is not None and total_md_cost is not None:
        total_cost = round(total_energy_cost + total_md_cost, 4)
    elif total_energy_cost is not None:
        total_cost = total_energy_cost

    # Extract BEI, tCO2e, NFA from summary block below the monthly data.
    # Labels vary slightly (e.g. "tCO2e/annum"), so we match by prefix.
    _SUMMARY_PREFIXES = {
        "BEI": "BEI",
        "tCO2e": "tCO2e",
        "NFA": "NFA",
        "Ave. RM/kWh": "Ave. RM/kWh",
        "Ave. MD (kW)": "Ave. MD (kW)",
    }
    summary_labels: dict[str, float | None] = {k: None for k in _SUMMARY_PREFIXES}
    for row in rows:
        for j, cell in enumerate(row):
            if isinstance(cell, str):
                label = cell.strip()
                # Value is typically in the cell immediately to the LEFT
                for key, prefix in _SUMMARY_PREFIXES.items():
                    if label.startswith(prefix) and j > 0 and _is_numeric(row[j - 1]):
                        if summary_labels[key] is None:
                            summary_labels[key] = round(float(row[j - 1]), 6)

    md_by_month: dict[int, float] = {}
    for m in monthly:
        if "month" in m and "md_kw" in m:
            md_by_month[m["month"]] = m["md_kw"]

    avg_md = (
        round(sum(md_by_month.values()) / len(md_by_month), 4)
        if md_by_month else None
    )

    return {
        "year": year,
        "total_kwh": total_kwh,
        "total_energy_cost_rm": _safe_round(total_energy_cost),
        "total_md_cost_rm": _safe_round(total_md_cost),
        "total_cost_rm": _safe_round(total_cost),
        "total_co2e_tonne": _safe_round(summary_labels.get("tCO2e")),
        "nfa_m2": _safe_round(summary_labels.get("NFA")),
        "bei_kwh_m2": _safe_round(summary_labels.get("BEI")),
        "avg_rm_per_kwh": _safe_round(summary_labels.get("Ave. RM/kWh")),
        "avg_md_kw": _safe_round(summary_labels.get("Ave. MD (kW)")) or avg_md,
        "md_by_month": md_by_month,
        "monthly_kwh": {m["month"]: m.get("total_kwh") for m in monthly if "month" in m},
    }


def _parse_all_sesb_sheets(sheets: dict[str, list[list]]) -> dict[str, dict]:
    """Parse all SESB-style billing sheets, keyed by sheet name."""
    results: dict[str, dict] = {}
    for name, rows in sheets.items():
        if re.match(r"sesb", name, re.IGNORECASE):
            parsed = _parse_sesb_sheet(rows)
            if parsed:
                results[name] = parsed
    return results


# ---------------------------------------------------------------------------
# Regression sheet parsing
# ---------------------------------------------------------------------------

def _parse_regression_sheet(rows: list[list]) -> dict[str, Any] | None:
    """Extract Multiple R, R², Adj R², n, and coefficient names from a regression sheet."""
    result: dict[str, Any] = {}
    in_coefficients = False
    coefficient_names: list[str] = []

    for row in rows:
        if not row:
            continue
        label = str(row[0]).strip() if row[0] is not None else ""
        val = row[1] if len(row) > 1 else None

        if "Multiple R" in label and _is_numeric(val):
            result["multiple_r"] = round(float(val), 8)
        elif "R Square" in label and "Adjusted" not in label and _is_numeric(val):
            result["r_square"] = round(float(val), 8)
        elif "Adjusted R Square" in label and _is_numeric(val):
            result["adj_r_square"] = round(float(val), 8)
        elif "Observations" in label and _is_numeric(val):
            result["n_observations"] = int(val)
        elif "Standard Error" in label and _is_numeric(val):
            result["std_error"] = round(float(val), 4)
        elif any(
            isinstance(cell, str) and "Coefficients" in cell
            for cell in row[:4] if cell is not None
        ):
            # Header row of the coefficients table (col B = "Coefficients")
            in_coefficients = True
        elif in_coefficients and label and label != "Intercept" and _is_numeric(val):
            # Variable name rows: non-empty label in col A, numeric coefficient in col B
            coefficient_names.append(label.strip())

    if not result:
        return None

    result["independent_variables"] = coefficient_names
    result["n_independent_variables"] = len(coefficient_names)

    if "multiple_r" in result and "r_square" in result:
        computed = round(result["multiple_r"] ** 2, 6)
        result["multiple_r_squared"] = computed
        result["r_square_matches_multiple_r_squared"] = (
            abs(computed - round(result["r_square"], 6)) < 1e-4
        )
    return result


def _parse_all_regression_sheets(sheets: dict[str, list[list]]) -> dict[str, Any]:
    """Parse every sheet that looks like a regression summary output.

    Returns {sheet_name: regression_dict}. Also flags if multiple regression
    sheets exist with different variable configurations.
    """
    regressions: dict[str, Any] = {}
    for name, rows in sheets.items():
        # A regression sheet has a row starting with "SUMMARY OUTPUT" or "Regression Statistics"
        has_summary = any(
            row and row[0] is not None
            and "regression" in str(row[0]).lower()
            for row in rows[:5]
        )
        if not has_summary:
            continue
        parsed = _parse_regression_sheet(rows)
        if parsed:
            regressions[name] = parsed

    if len(regressions) > 1:
        # Flag the discrepancy
        r_squares = {name: v.get("r_square") for name, v in regressions.items()}
        for name, v in regressions.items():
            v["note"] = (
                f"Multiple regression sheets found. R² values: {r_squares}. "
                "Confirm with the report which sheet's analysis is presented."
            )
    return regressions


# ---------------------------------------------------------------------------
# ESM sheet parsing
# ---------------------------------------------------------------------------

_ESM_LABEL_RE = re.compile(r"^ESM\s*[\d.]+$", re.IGNORECASE)


def _parse_esm_sheet(rows: list[list]) -> dict[str, Any]:
    """Extract ESM line items and total saving from the ESM sheet."""
    esm_items: list[dict] = []
    total_row: dict | None = None

    # Find the header row: contains "ESM Description" or "Estimated Yearly Saving"
    header_idx = None
    for i, row in enumerate(rows[:10]):
        row_text = " ".join(str(c) for c in row if c is not None)
        if "ESM Description" in row_text or "Estimated Yearly Saving" in row_text:
            header_idx = i
            break

    # Column map from the header structure in the Grandis workbook:
    # B=No., E=ESM Description, F=Overall Baseline kWh, G=System Baseline kWh,
    # H=Energy Saving kWh, J=Cost Saving RM, K=Investment RM, L=Payback years,
    # M=Carbon reduction tonne, N=Overall % saving, O=System % saving
    # (0-indexed: B=1, E=4, F=5, G=6, H=7, J=9, K=10, L=11, M=12, N=13, O=14)
    COL = {
        "no": 1,
        "description": 4,
        "overall_baseline_kwh": 5,
        "system_baseline_kwh": 6,
        "energy_saving_kwh": 7,
        "cost_saving_rm": 9,
        "investment_rm": 10,
        "payback_years": 11,
        "carbon_reduction_t": 12,
        "overall_pct_saving": 13,
        "system_pct_saving": 14,
    }

    found_total = False
    for row in rows:
        if found_total:
            break
        if len(row) <= COL["no"]:
            continue
        label = row[COL["no"]]
        if label is None:
            continue
        label_str = str(label).strip()

        if _ESM_LABEL_RE.match(label_str):
            item: dict[str, Any] = {"esm_id": label_str}
            for key, j in COL.items():
                if key == "no":
                    continue
                v = row[j] if j < len(row) else None
                if key == "description" and isinstance(v, str):
                    item[key] = v.strip()
                elif _is_numeric(v):
                    item[key] = round(float(v), 4)
                else:
                    item[key] = v
            esm_items.append(item)

        elif label_str.lower() == "total":
            total_row = {}
            for key, j in COL.items():
                v = row[j] if j < len(row) else None
                if _is_numeric(v):
                    total_row[key] = round(float(v), 4)
            found_total = True  # stop parsing — rows below are BEI-accumulation block

    # Compute checksum
    sum_energy_kwh = sum(
        i["energy_saving_kwh"] for i in esm_items
        if isinstance(i.get("energy_saving_kwh"), float)
    )
    sum_cost_rm = sum(
        i["cost_saving_rm"] for i in esm_items
        if isinstance(i.get("cost_saving_rm"), float)
    )

    # Overall baseline from first ESM item with a plausible kWh value (>1000)
    overall_baseline_kwh = None
    for item in esm_items:
        v = item.get("overall_baseline_kwh")
        if _is_numeric(v) and float(v) > 1000:
            overall_baseline_kwh = v
            break

    return {
        "overall_baseline_kwh": overall_baseline_kwh,
        "esm_items": esm_items,
        "esm_total_from_table": total_row,
        "computed_sum_energy_saving_kwh": round(sum_energy_kwh, 4),
        "computed_sum_cost_saving_rm": round(sum_cost_rm, 4),
        "total_matches_sum": (
            total_row is not None
            and abs(total_row.get("energy_saving_kwh", 0) - sum_energy_kwh) < 1.0
        ),
    }


# ---------------------------------------------------------------------------
# Benchmarking sheet parsing
# ---------------------------------------------------------------------------

def _parse_benchmarking_sheet(rows: list[list]) -> dict[str, Any]:
    """Extract sub-system kWh summary from the Benchmarking sheet."""
    # The right-side summary table has columns: No. | Description | kWh
    # Look for a run of rows where col[9] is an integer 1..N and col[11] is kWh
    subsystems: list[dict] = []

    for i, row in enumerate(rows):
        if len(row) < 12:
            continue
        no_val = row[9]
        desc_val = row[10]
        kwh_val = row[11]
        if isinstance(no_val, int) and 1 <= no_val <= 20:
            if isinstance(desc_val, str) and _is_numeric(kwh_val):
                subsystems.append({
                    "no": no_val,
                    "description": desc_val.strip(),
                    "kwh": round(float(kwh_val), 4),
                })

    total_kwh = sum(s["kwh"] for s in subsystems)

    # Also extract overall BEI from the standard block
    bei = _find_value_by_label(rows, "Building Energy Index", 2, 4)

    return {
        "subsystems": subsystems,
        "subsystem_total_kwh": round(total_kwh, 4) if subsystems else None,
        "bei_kwh_m2": _safe_round(bei),
    }


# ---------------------------------------------------------------------------
# Chiller equipment parsing
# ---------------------------------------------------------------------------

def _parse_chiller_sheet(rows: list[list]) -> dict[str, Any]:
    """Extract chiller unit capacities (RT) from the Chiller.P sheet."""
    chillers: list[dict] = []
    for row in rows:
        if len(row) < 11:
            continue
        no_val = row[6]
        item_val = row[7]
        rated_rt = row[9]
        if (isinstance(no_val, int)
                and isinstance(item_val, str)
                and "chiller" in item_val.lower()
                and _is_numeric(rated_rt)):
            chillers.append({
                "unit": item_val.strip(),
                "rated_rt": float(rated_rt),
            })
    total_rt = sum(c["rated_rt"] for c in chillers) if chillers else None
    return {
        "chiller_units": chillers,
        "total_installed_rt": total_rt,
        "unit_count": len(chillers),
    }


# ---------------------------------------------------------------------------
# kWh.Overall / multi-year summary parsing
# ---------------------------------------------------------------------------

def _parse_kwh_overall(rows: list[list]) -> dict[str, Any]:
    """Extract multi-year kWh and cost data from the kWh.Overall sheet."""
    # Row structure (0-indexed after header):
    #   row[1] = label ('Baseline', 2023, 2024, 2025, ...)
    # Data rows:
    #   'Energy Consumption (kWh)' | baseline_kwh | yr1_kwh | yr2_kwh | ...
    #   'Energy Cost (RM)'        | None          | yr1_cost | yr2_cost | ...

    # Find the header row containing year values
    header_row = None
    for row in rows:
        years_found = [c for c in row if isinstance(c, int) and 2000 <= c <= 2100]
        if len(years_found) >= 2:
            header_row = row
            break
    if header_row is None:
        return {}

    # Map column index → year (or 'Baseline')
    col_year: dict[int, Any] = {}
    for j, cell in enumerate(header_row):
        if isinstance(cell, int) and 2000 <= cell <= 2100:
            col_year[j] = cell
        elif isinstance(cell, str) and "baseline" in cell.lower():
            col_year[j] = "Baseline"

    result: dict[str, Any] = {"years": sorted(v for v in col_year.values() if isinstance(v, int))}

    # Search for data rows BELOW the header row only (avoids false prefix matches above it)
    header_idx = rows.index(header_row) if header_row in rows else 0
    data_rows = rows[header_idx + 1:]

    kwh_row = _find_row_by_label(data_rows, "Energy Consumption", 0)
    cost_row = _find_row_by_label(data_rows, "Energy Cost", 0)

    if kwh_row:
        result["kwh_by_year"] = {
            col_year[j]: round(float(kwh_row[j]), 2)
            for j in col_year
            if j < len(kwh_row) and _is_numeric(kwh_row[j])
        }
    if cost_row:
        result["cost_by_year"] = {
            col_year[j]: round(float(cost_row[j]), 4)
            for j in col_year
            if j < len(cost_row) and _is_numeric(cost_row[j])
        }

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_verified_numbers(xlsx_data: dict[str, Any]) -> dict[str, Any]:
    """Build the verified-numbers JSON from extracted xlsx data.

    This is ground truth passed to the LLM. Never ask the LLM to verify
    numbers — it gets this JSON and cross-checks report prose against it.
    """
    sheets = xlsx_data["sheets"]

    # --- SESB billing sheets ---
    sesb = _parse_all_sesb_sheets(sheets)

    # Identify the baseline SESB sheet: the one whose total_kwh matches
    # the ESM overall_baseline_kwh (or failing that, the most recent complete year)
    esm_data: dict[str, Any] = {}
    if "ESM" in sheets:
        esm_data = _parse_esm_sheet(sheets["ESM"])

    esm_baseline_kwh = esm_data.get("overall_baseline_kwh")
    baseline_sesb_name: str | None = None
    baseline_sesb: dict | None = None

    for name, s in sesb.items():
        if esm_baseline_kwh and s.get("total_kwh") is not None:
            if abs(s["total_kwh"] - esm_baseline_kwh) < 10:  # within 10 kWh
                baseline_sesb_name = name
                baseline_sesb = s
                break

    # Fall back to most recent year if no exact match
    if baseline_sesb is None and sesb:
        latest_name = max(sesb, key=lambda n: sesb[n].get("year", 0))
        baseline_sesb_name = latest_name
        baseline_sesb = sesb[latest_name]

    # --- Multi-year summary ---
    kwh_overall: dict[str, Any] = {}
    if "kWh.Overall" in sheets:
        kwh_overall = _parse_kwh_overall(sheets["kWh.Overall"])

    # --- Regression ---
    # Parse ALL regression-shaped sheets; the report may reference one specific sheet.
    all_regressions = _parse_all_regression_sheets(sheets)
    # For backward compatibility, "regression" is the primary result.
    # Prefer the sheet that matches the report's R² (≈0.56 for Grandis = Sheet1).
    # In general: prefer "Sheet1" if present, then "Reg.", then first found.
    primary_reg_sheet = next(
        (n for n in ("Sheet1", "Reg.") if n in all_regressions), None
    ) or (next(iter(all_regressions), None))
    regression = all_regressions.get(primary_reg_sheet, {})
    if primary_reg_sheet:
        regression["source_sheet"] = primary_reg_sheet

    # --- Benchmarking ---
    benchmarking: dict[str, Any] = {}
    bench_sheet = next(
        (n for n in sheets if re.match(r"benchmark", n, re.IGNORECASE)), None
    )
    if bench_sheet:
        benchmarking = _parse_benchmarking_sheet(sheets[bench_sheet])

    # --- Chiller ---
    chiller: dict[str, Any] = {}
    chiller_sheet = next(
        (n for n in sheets if re.match(r"chiller", n, re.IGNORECASE)), None
    )
    if chiller_sheet:
        chiller = _parse_chiller_sheet(sheets[chiller_sheet])

    # --- NFA ---
    nfa_m2 = None
    if "NFA" in sheets:
        for row in sheets["NFA"]:
            for j, cell in enumerate(row):
                if isinstance(cell, str) and "nfa" in cell.lower():
                    if j + 1 < len(row) and _is_numeric(row[j + 1]):
                        nfa_m2 = round(float(row[j + 1]), 4)
                        break
            if nfa_m2:
                break

    # Fall back: NFA from SESB baseline
    if nfa_m2 is None and baseline_sesb:
        nfa_m2 = baseline_sesb.get("nfa_m2")

    # --- GJ conversion ---
    baseline_kwh = baseline_sesb["total_kwh"] if baseline_sesb else None
    baseline_gj = round(baseline_kwh * KWH_TO_GJ, 2) if baseline_kwh else None

    # --- BEI verification ---
    baseline_bei = baseline_sesb.get("bei_kwh_m2") if baseline_sesb else None
    computed_bei = None
    if baseline_kwh and nfa_m2 and nfa_m2 > 0:
        computed_bei = round(baseline_kwh / nfa_m2, 4)

    # --- Cost-year cross-check ---
    # Identify if kWh.Overall's "Baseline" column uses a different year than ESM kWh
    kwh_by_year = kwh_overall.get("kwh_by_year", {})
    cost_by_year = kwh_overall.get("cost_by_year", {})
    baseline_year = baseline_sesb.get("year") if baseline_sesb else None

    # Find which year in kWh.Overall matches the ESM baseline kWh
    kwh_overall_baseline_year: int | None = None
    kwh_overall_baseline_kwh: float | None = kwh_by_year.get("Baseline")
    for yr, kwh in kwh_by_year.items():
        if isinstance(yr, int) and kwh_overall_baseline_kwh and abs(kwh - kwh_overall_baseline_kwh) < 10:
            kwh_overall_baseline_year = yr

    cost_mismatch_flag = None
    if (baseline_year and kwh_overall_baseline_year
            and baseline_year != kwh_overall_baseline_year):
        cost_mismatch_flag = {
            "warning": "ESM baseline kWh is from a different year than kWh.Overall's Baseline column",
            "esm_baseline_year": baseline_year,
            "esm_baseline_kwh": baseline_kwh,
            "kwh_overall_baseline_year": kwh_overall_baseline_year,
            "kwh_overall_baseline_kwh": kwh_overall_baseline_kwh,
            "correct_cost_for_esm_year_rm": cost_by_year.get(baseline_year),
            "cost_for_kwh_overall_baseline_year_rm": cost_by_year.get(kwh_overall_baseline_year),
        }

    return {
        "_note": "All values extracted deterministically from openpyxl (data_only=True). LLM ground truth.",
        "baseline": {
            "sesb_sheet": baseline_sesb_name,
            "year": baseline_year,
            "total_kwh": baseline_kwh,
            "total_gj": baseline_gj,
            "total_cost_rm": baseline_sesb.get("total_cost_rm") if baseline_sesb else None,
            "total_energy_cost_rm": baseline_sesb.get("total_energy_cost_rm") if baseline_sesb else None,
            "total_md_cost_rm": baseline_sesb.get("total_md_cost_rm") if baseline_sesb else None,
            "total_co2e_tonne": baseline_sesb.get("total_co2e_tonne") if baseline_sesb else None,
            "nfa_m2": nfa_m2,
            "bei_kwh_m2_from_sheet": baseline_bei,
            "bei_kwh_m2_computed": computed_bei,
            "avg_md_kw": baseline_sesb.get("avg_md_kw") if baseline_sesb else None,
            "md_by_month": baseline_sesb.get("md_by_month") if baseline_sesb else None,
            "monthly_kwh": baseline_sesb.get("monthly_kwh") if baseline_sesb else None,
        },
        "all_sesb_years": {
            name: {k: v for k, v in s.items() if k not in ("md_by_month", "monthly_kwh")}
            for name, s in sesb.items()
        },
        "kwh_overall_summary": kwh_overall,
        "cost_year_mismatch": cost_mismatch_flag,
        "regression": regression,
        "all_regressions": all_regressions,
        "benchmarking": benchmarking,
        "esm": esm_data,
        "chiller_plant": chiller,
    }
