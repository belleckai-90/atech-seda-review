"""Unit tests for src/verify.py — no API calls.

Validates against the Grandis acceptance criteria from BUILD_SPEC.md §6.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extract import extract_xlsx
from src.verify import build_verified_numbers

GRANDIS_DIR = Path(__file__).parent / "grandis"
GRANDIS_XLSX = GRANDIS_DIR / "1_Grandis_KK_Audit_EACG.xlsx"


def _grandis_present() -> bool:
    return GRANDIS_XLSX.exists()


@pytest.fixture(scope="module")
def verified():
    if not _grandis_present():
        pytest.skip("Grandis xlsx not in tests/grandis/")
    xlsx_data = extract_xlsx(GRANDIS_XLSX)
    return build_verified_numbers(xlsx_data)


# ---------------------------------------------------------------------------
# BEI
# ---------------------------------------------------------------------------

def test_bei_matches_spec(verified):
    """BEI = 103.13 kWh/m²/year (acceptance criterion §6.2)."""
    bei_sheet = verified["baseline"]["bei_kwh_m2_from_sheet"]
    bei_comp = verified["baseline"]["bei_kwh_m2_computed"]
    assert bei_sheet is not None, "BEI not extracted from sheet"
    assert abs(bei_sheet - 103.13) < 0.1, f"BEI from sheet = {bei_sheet}, expected ≈ 103.13"
    if bei_comp is not None:
        assert abs(bei_comp - 103.13) < 0.1, f"Computed BEI = {bei_comp}"


# ---------------------------------------------------------------------------
# Cost-year mismatch
# ---------------------------------------------------------------------------

def test_cost_year_mismatch_detected(verified):
    """The tool must flag the 1,338,574.89 vs 1,189,936.38 mismatch (§6.2)."""
    mismatch = verified.get("cost_year_mismatch")
    assert mismatch is not None, \
        "Cost-year mismatch not detected — check SESB sheet parsing and kWh.Overall parsing"

    correct_cost = mismatch.get("correct_cost_for_esm_year_rm")
    wrong_cost = mismatch.get("cost_for_kwh_overall_baseline_year_rm")

    assert correct_cost is not None
    assert wrong_cost is not None
    assert abs(correct_cost - 1_189_936.38) < 5.0, \
        f"Correct (ESM year) cost = {correct_cost}, expected ≈ 1,189,936.38"
    assert abs(wrong_cost - 1_338_574.89) < 5.0, \
        f"Wrong (Baseline year) cost = {wrong_cost}, expected ≈ 1,338,574.89"


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------

def test_regression_r_square_extracted(verified):
    """R² < 0.75 on the primary regression sheet (§6.2 — report uses Sheet1)."""
    reg = verified["regression"]
    assert reg.get("r_square") is not None, "R² not extracted from primary regression sheet"
    assert reg["r_square"] < 0.75, \
        f"R² = {reg['r_square']} should be < 0.75"


def test_regression_primary_sheet_is_sheet1(verified):
    """Sheet1 is the regression the report presents (R² ≈ 0.5618, Multiple R ≈ 0.75)."""
    reg = verified["regression"]
    assert reg.get("source_sheet") == "Sheet1", \
        f"Expected primary regression sheet = Sheet1, got {reg.get('source_sheet')}"
    assert abs(reg["r_square"] - 0.5618) < 0.001, \
        f"Sheet1 R² = {reg['r_square']}, expected ≈ 0.5618"
    assert abs(reg["multiple_r"] - 0.7496) < 0.001, \
        f"Sheet1 Multiple R = {reg['multiple_r']}, expected ≈ 0.7496"


def test_regression_multiple_r_ne_r_square(verified):
    """Multiple R ≠ R² — auditors commonly confuse these (§2.3 GR3)."""
    reg = verified["regression"]
    assert "multiple_r" in reg and "r_square" in reg
    assert abs(reg["multiple_r"] - reg["r_square"]) > 0.1, \
        "Multiple R and R² are suspiciously close — check parsing"


def test_regression_r_square_matches_multiple_r_squared(verified):
    """Sanity check: stored R² ≈ Multiple R² (verifies openpyxl read is consistent)."""
    reg = verified["regression"]
    assert reg.get("r_square_matches_multiple_r_squared") is True, \
        f"R² stored ({reg.get('r_square')}) ≠ Multiple R² ({reg.get('multiple_r_squared')})"


def test_regression_multiple_sheets_detected(verified):
    """Both Sheet1 and Reg. regression sheets are detected with their variable lists."""
    all_regs = verified.get("all_regressions", {})
    assert "Sheet1" in all_regs, "Sheet1 regression not found"
    assert "Reg." in all_regs, "Reg. regression sheet not found"

    sheet1 = all_regs["Sheet1"]
    assert "CDD" in sheet1["independent_variables"], \
        f"CDD not in Sheet1 vars: {sheet1['independent_variables']}"
    assert len(sheet1["independent_variables"]) == 2, \
        f"Sheet1 should have 2 variables, got: {sheet1['independent_variables']}"


def test_regression_multiple_r_close_to_claimed_075(verified):
    """Sheet1 Multiple R ≈ 0.75 — likely quoted as R² in the report (§2.3 GR3 finding)."""
    reg = verified["regression"]
    mr = reg.get("multiple_r", 0)
    assert 0.74 < mr < 0.76, \
        f"Expected Multiple R ≈ 0.75 (the value likely mis-quoted as R²), got {mr}"


# ---------------------------------------------------------------------------
# Chiller capacity
# ---------------------------------------------------------------------------

def test_chiller_units_extracted(verified):
    """Two 520 RT chillers identified (§6.2: 520 RT × 2 vs claimed 840 RT)."""
    ch = verified["chiller_plant"]
    units = ch.get("chiller_units", [])
    chiller_only = [u for u in units if "chiller" in u["unit"].lower()]
    assert len(chiller_only) >= 2, \
        f"Expected ≥ 2 chiller units, got: {chiller_only}"
    for u in chiller_only:
        assert abs(u["rated_rt"] - 520) < 10, \
            f"Expected each chiller ≈ 520 RT, got {u['rated_rt']} for {u['unit']}"


def test_chiller_total_rt(verified):
    ch = verified["chiller_plant"]
    chiller_only = [u for u in ch.get("chiller_units", []) if "chiller" in u["unit"].lower()]
    total = sum(u["rated_rt"] for u in chiller_only)
    assert abs(total - 1040) < 20, \
        f"Expected total chiller RT ≈ 1040 (520×2), got {total}"


# ---------------------------------------------------------------------------
# ESM
# ---------------------------------------------------------------------------

def test_esm_items_extracted(verified):
    esm = verified["esm"]
    assert len(esm.get("esm_items", [])) >= 5, "Expected at least 5 ESM items"


def test_esm_baseline_kwh(verified):
    esm = verified["esm"]
    kwh = esm.get("overall_baseline_kwh")
    assert kwh is not None
    # Should match the SESB baseline year total
    baseline_kwh = verified["baseline"]["total_kwh"]
    assert abs(kwh - baseline_kwh) < 10, \
        f"ESM baseline kWh ({kwh}) doesn't match SESB baseline ({baseline_kwh})"


# ---------------------------------------------------------------------------
# Benchmarking
# ---------------------------------------------------------------------------

def test_benchmarking_subsystems(verified):
    bench = verified["benchmarking"]
    subs = bench.get("subsystems", [])
    assert len(subs) >= 5, f"Expected ≥5 sub-systems in Benchmarking, got {len(subs)}"
    # Chiller plant should be the dominant consumer
    chiller_sub = next((s for s in subs if "chiller" in s["description"].lower()), None)
    assert chiller_sub is not None, "No chiller entry in benchmarking subsystems"
