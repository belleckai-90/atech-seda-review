"""Unit tests for src/extract.py — no API calls."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extract import extract_docx, extract_xlsx

GRANDIS_DIR = Path(__file__).parent / "grandis"
GRANDIS_DOCX = GRANDIS_DIR / "GRANDIS_EACG_Report_Final_Draft_1.docx"
GRANDIS_XLSX = GRANDIS_DIR / "1_Grandis_KK_Audit_EACG.xlsx"


def _grandis_files_present() -> bool:
    return GRANDIS_DOCX.exists() and GRANDIS_XLSX.exists()


# ---------------------------------------------------------------------------
# DOCX tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _grandis_files_present(), reason="Grandis test files not in tests/grandis/")
def test_extract_docx_returns_text():
    result = extract_docx(GRANDIS_DOCX)
    assert "text" in result
    assert len(result["text"]) > 1000, "Expected substantial body text"


@pytest.mark.skipif(not _grandis_files_present(), reason="Grandis test files not in tests/grandis/")
def test_extract_docx_headings():
    result = extract_docx(GRANDIS_DOCX)
    assert "headings" in result
    assert len(result["headings"]) > 5, "Expected multiple headings"


@pytest.mark.skipif(not _grandis_files_present(), reason="Grandis test files not in tests/grandis/")
def test_extract_docx_section_page_map():
    result = extract_docx(GRANDIS_DOCX)
    spm = result["section_page_map"]
    assert isinstance(spm, dict), "section_page_map should be a dict"
    # At least half of headings should have a page number (SDT TOC is present)
    with_pages = sum(1 for p in spm.values() if p is not None)
    assert with_pages >= len(spm) // 2, \
        f"Expected ≥50% of sections to have page numbers, got {with_pages}/{len(spm)}"


@pytest.mark.skipif(not _grandis_files_present(), reason="Grandis test files not in tests/grandis/")
def test_extract_docx_toc_source_is_sdt():
    result = extract_docx(GRANDIS_DOCX)
    assert result["toc_source"] == "sdt", \
        f"Expected TOC source 'sdt', got '{result['toc_source']}'"


@pytest.mark.skipif(not _grandis_files_present(), reason="Grandis test files not in tests/grandis/")
def test_extract_docx_toc_has_key_sections():
    result = extract_docx(GRANDIS_DOCX)
    toc = result["toc"]
    # These sections must be present with page numbers
    assert "EXECUTIVE SUMMARY" in toc, "EXECUTIVE SUMMARY not found in TOC"
    assert "INTRODUCTION" in toc, "INTRODUCTION not found in TOC"
    assert toc["EXECUTIVE SUMMARY"] > 0, "EXECUTIVE SUMMARY page should be > 0"


# ---------------------------------------------------------------------------
# XLSX tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _grandis_files_present(), reason="Grandis test files not in tests/grandis/")
def test_extract_xlsx_sheet_names():
    result = extract_xlsx(GRANDIS_XLSX)
    assert "sheet_names" in result
    sheets = result["sheet_names"]
    # Expect SESB billing sheets
    sesb_sheets = [n for n in sheets if "SESB" in n.upper()]
    assert len(sesb_sheets) >= 2, f"Expected ≥2 SESB sheets, got: {sesb_sheets}"


@pytest.mark.skipif(not _grandis_files_present(), reason="Grandis test files not in tests/grandis/")
def test_extract_xlsx_data_sheets_have_rows():
    result = extract_xlsx(GRANDIS_XLSX)
    non_empty = [n for n, rows in result["sheets"].items() if len(rows) > 0]
    assert len(non_empty) >= 20, \
        f"Expected at least 20 non-empty data sheets, got {len(non_empty)}: {non_empty}"


@pytest.mark.skipif(not _grandis_files_present(), reason="Grandis test files not in tests/grandis/")
def test_extract_xlsx_chart_sheets_excluded_from_data():
    result = extract_xlsx(GRANDIS_XLSX)
    for name in result["chart_sheets"]:
        assert name not in result["sheets"], \
            f"Chart sheet '{name}' should not appear in sheets dict"
