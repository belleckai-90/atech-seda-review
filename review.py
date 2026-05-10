"""SEDA Audit Reviewer — CLI entry point.

Usage:
    python review.py inputs/report.docx inputs/audit.xlsx [OPTIONS]

Options:
    --type          commercial | industrial  (default: auto-detect)
    --output        override output filename stem (no extension)
    --dry-run       Pass 1 only: print verified-numbers JSON, no API calls
    --verbose       show API call details and token counts
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import date
from pathlib import Path

import click
from rich.console import Console
from rich.syntax import Syntax

# On Windows the legacy console renderer can't handle Unicode box-drawing chars;
# wrapping stdout in UTF-8 and using force_terminal avoids the cp1252 crash.
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(highlight=False, force_terminal=True)

sys.path.insert(0, str(Path(__file__).parent))

from src.extract import extract_docx, extract_xlsx
from src.verify import build_verified_numbers
from src.review_pass import run_review_pass

_HERE = Path(__file__).parent
_PROMPTS_DIR = _HERE / "prompts"
_REFERENCES_DIR = _HERE / "references"
_FINDINGS_LIBRARY = _HERE / "findings_library" / "common_findings.md"
_OUTPUTS_DIR = _HERE / "outputs"


def _auto_detect_type(docx_data: dict) -> str:
    text = docx_data.get("text", "").lower()
    if text.count("commercial") > text.count("industrial"):
        return "commercial"
    return "industrial"


def _output_stem(report_path: Path, output_override: str | None) -> str:
    if output_override:
        return output_override.removesuffix(".docx").removesuffix(".json")
    return f"{report_path.stem}_Review_{date.today().isoformat()}"


@click.command()
@click.argument("report_docx", type=click.Path(exists=True, dir_okay=False))
@click.argument("audit_xlsx", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--type", "report_type",
    type=click.Choice(["commercial", "industrial"], case_sensitive=False),
    default=None,
    help="Report type (default: auto-detect from content).",
)
@click.option(
    "--output", "output_filename",
    default=None,
    help="Override output filename stem.",
)
@click.option(
    "--dry-run", "dry_run",
    is_flag=True,
    default=False,
    help="Run Pass 1 only and print verified-numbers JSON. No API calls.",
)
@click.option(
    "--verbose", "verbose",
    is_flag=True,
    default=False,
    help="Show API call details and token counts.",
)
def main(
    report_docx: str,
    audit_xlsx: str,
    report_type: str | None,
    output_filename: str | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    report_path = Path(report_docx)
    audit_path = Path(audit_xlsx)
    _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Pass 1 — extraction
    # -----------------------------------------------------------------------
    with console.status("[bold cyan]Pass 1 — extracting report text...[/bold cyan]"):
        docx_data = extract_docx(report_path)

    console.print(
        f"[green]OK[/green] Report extracted: "
        f"{len(docx_data['text'])} chars, "
        f"{len(docx_data['headings'])} headings, "
        f"{len(docx_data['toc'])} TOC entries"
    )

    with console.status("[bold cyan]Pass 1 — extracting workbook...[/bold cyan]"):
        xlsx_data = extract_xlsx(audit_path)

    data_sheets = [n for n in xlsx_data["sheet_names"] if n not in xlsx_data["chart_sheets"]]
    console.print(
        f"[green]OK[/green] Workbook extracted: "
        f"{len(data_sheets)} data sheets, "
        f"{len(xlsx_data['chart_sheets'])} chart sheets"
    )

    with console.status("[bold cyan]Pass 1 — building verified-numbers JSON...[/bold cyan]"):
        verified = build_verified_numbers(xlsx_data)

    console.print("[green]OK[/green] Verified-numbers JSON built.")

    if dry_run:
        console.print("\n[bold yellow]--- DRY RUN: verified-numbers JSON ---[/bold yellow]\n")
        json_str = json.dumps(verified, indent=2, default=str)
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)
        console.print(syntax)
        _print_dry_run_summary(verified)
        return

    # Auto-detect report type if not supplied
    if report_type is None:
        report_type = _auto_detect_type(docx_data)
        console.print(f"[dim]Auto-detected report type: {report_type}[/dim]")

    # Check API key before attempting the call
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print(
            "[bold red]ERROR:[/bold red] ANTHROPIC_API_KEY environment variable not set.\n"
            "Set it with: set ANTHROPIC_API_KEY=sk-ant-..."
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Pass 2 — section-by-section LLM review
    # -----------------------------------------------------------------------
    stem = _output_stem(report_path, output_filename)
    findings_json_path = _OUTPUTS_DIR / f"{stem}_findings.json"

    with console.status("[bold cyan]Pass 2 — running section-by-section review (API)...[/bold cyan]"):
        findings, usage_info = run_review_pass(
            docx_data=docx_data,
            xlsx_data=xlsx_data,
            verified=verified,
            report_type=report_type,
            prompts_dir=_PROMPTS_DIR,
            references_dir=_REFERENCES_DIR,
            findings_library_path=_FINDINGS_LIBRARY if _FINDINGS_LIBRARY.exists() else None,
            verbose=verbose,
        )

    findings_json_path.write_text(
        json.dumps(findings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    console.print(f"[green]OK[/green] Pass 2 complete. Findings saved to: {findings_json_path}")

    if verbose:
        console.print(
            f"  [dim]Tokens in={usage_info['input_tokens']} "
            f"out={usage_info['output_tokens']} "
            f"cache_write={usage_info['cache_creation_input_tokens']} "
            f"cache_read={usage_info['cache_read_input_tokens']} "
            f"~USD {usage_info['estimated_cost_usd']}[/dim]"
        )

    _print_findings_summary(findings, usage_info)

    # -----------------------------------------------------------------------
    # Pass 3 / render (Stage C+)
    # -----------------------------------------------------------------------
    console.print(
        "\n[bold yellow]Passes 3-4 not yet implemented (Stage C+).[/bold yellow]\n"
        f"Review findings JSON at: {findings_json_path}\n"
        "Re-run with --dry-run to inspect verified-numbers only."
    )


def _print_findings_summary(findings: dict, usage_info: dict) -> None:
    """Print a human-readable summary of the Pass 2 findings."""
    console.rule("[bold]PASS 2 FINDINGS SUMMARY[/bold]")

    meta = findings.get("metadata", {})
    console.print(f"  Building  : {meta.get('building_name', 'n/a')}")
    console.print(f"  Owner     : {meta.get('building_owner', 'n/a')}")
    console.print(f"  REA       : {meta.get('rea_name', 'n/a')}")
    console.print(f"  Type      : {meta.get('report_type', 'n/a')}")

    section_findings = findings.get("section_findings", [])
    critical = [f for f in section_findings if f.get("severity") == "Critical"]
    major    = [f for f in section_findings if f.get("severity") == "Major"]
    moderate = [f for f in section_findings if f.get("severity") == "Moderate"]
    minor    = [f for f in section_findings if f.get("severity") == "Minor"]

    console.print(f"\n  Sections reviewed : {len(section_findings)}")
    console.print(f"  [bold red]Critical[/bold red]  : {len(critical)}")
    console.print(f"  [yellow]Major[/yellow]     : {len(major)}")
    console.print(f"  [cyan]Moderate[/cyan]  : {len(moderate)}")
    console.print(f"  [dim]Minor[/dim]     : {len(minor)}")

    console.rule("[bold]GROUND RULES[/bold]")
    gr_scorecard = findings.get("ground_rule_scorecard", {})
    for gr in ("GR1", "GR2", "GR3", "GR4"):
        entry = gr_scorecard.get(gr, {})
        verdict = entry.get("verdict", "n/a")
        colour = "green" if verdict.startswith("✓") else "red"
        just = entry.get("justification", "")[:100]
        console.print(f"  [{colour}]{gr}: {verdict}[/{colour}]  {just}")

    console.rule("[bold]CRITICAL FINDINGS[/bold]")
    for i, f in enumerate(critical, 1):
        sec = f.get("section_no", "?")
        name = f.get("section_name", "")
        page = f.get("page_ref", "")
        remarks = f.get("remarks", "")[:120]
        console.print(f"  {i}. [{sec}] {name} {page}")
        console.print(f"     {remarks}")

    if findings.get("recommended_action_order"):
        console.rule("[bold]RECOMMENDED ACTION ORDER[/bold]")
        for item in findings["recommended_action_order"]:
            console.print(f"  {item}")

    cost = usage_info.get("estimated_cost_usd", 0)
    console.rule()
    console.print(f"  Estimated API cost: USD {cost:.4f}")


def _print_dry_run_summary(verified: dict) -> None:
    console.rule("[bold]KEY NUMBERS SUMMARY[/bold]")
    b = verified.get("baseline", {})
    console.print(f"  Baseline SESB sheet : {verified.get('baseline', {}).get('sesb_sheet')}")
    console.print(f"  Baseline year       : {b.get('year')}")
    console.print(f"  Annual kWh          : {b.get('total_kwh'):,.0f}" if b.get("total_kwh") else "  Annual kWh          : n/a")
    console.print(f"  Annual GJ           : {b.get('total_gj'):,.2f}" if b.get("total_gj") else "  Annual GJ           : n/a")
    console.print(f"  Total cost (RM)     : {b.get('total_cost_rm'):,.2f}" if b.get("total_cost_rm") else "  Total cost (RM)     : n/a")
    console.print(f"  tCO2e               : {b.get('total_co2e_tonne'):,.2f}" if b.get("total_co2e_tonne") else "  tCO2e               : n/a")
    console.print(f"  NFA (m2)            : {b.get('nfa_m2'):,.2f}" if b.get("nfa_m2") else "  NFA (m2)            : n/a")
    console.print(f"  BEI (sheet)         : {b.get('bei_kwh_m2_from_sheet')}")
    console.print(f"  BEI (computed)      : {b.get('bei_kwh_m2_computed')}")
    console.print(f"  Avg MD (kW)         : {b.get('avg_md_kw')}")

    all_regs = verified.get("all_regressions", {})
    console.rule("[bold]REGRESSION (all sheets)[/bold]")
    for sheet_name, reg in all_regs.items():
        vars_str = ", ".join(reg.get("independent_variables", [])) or "unknown"
        console.print(
            f"  [{sheet_name}] Multiple R={reg.get('multiple_r')}  "
            f"R-sq={reg.get('r_square')}  n={reg.get('n_observations')}  "
            f"vars=[{vars_str}]"
        )
    if len(all_regs) > 1:
        console.print(
            "  [bold red]WARNING:[/bold red] Multiple regression sheets found — "
            "verify which one the report presents."
        )
    reg = verified.get("regression", {})
    console.print(f"  Primary sheet used  : {reg.get('source_sheet')}")
    console.print(f"  Multiple R          : {reg.get('multiple_r')}")
    console.print(f"  R-sq (stored)       : {reg.get('r_square')}")
    console.print(f"  Multiple R^2 = R^2? : {reg.get('r_square_matches_multiple_r_squared')}")
    console.print(f"  Adj R-sq            : {reg.get('adj_r_square')}")
    console.print(f"  n observations      : {reg.get('n_observations')}")

    esm = verified.get("esm", {})
    console.rule("[bold]ESM SUMMARY[/bold]")
    console.print(f"  Overall baseline kWh: {esm.get('overall_baseline_kwh'):,.0f}" if esm.get("overall_baseline_kwh") else "  Overall baseline kWh: n/a")
    console.print(f"  ESM items found     : {len(esm.get('esm_items', []))}")
    total = esm.get("esm_total_from_table", {})
    console.print(f"  Total energy saving : {total.get('energy_saving_kwh'):,.2f} kWh" if total and total.get("energy_saving_kwh") else "  Total energy saving : n/a")
    console.print(f"  Total cost saving   : RM {total.get('cost_saving_rm'):,.2f}" if total and total.get("cost_saving_rm") else "  Total cost saving   : n/a")
    console.print(f"  Total investment    : RM {total.get('investment_rm'):,.2f}" if total and total.get("investment_rm") else "  Total investment    : n/a")
    console.print(f"  Totals match sum?   : {esm.get('total_matches_sum')}")

    chiller = verified.get("chiller_plant", {})
    console.rule("[bold]CHILLER PLANT[/bold]")
    for u in chiller.get("chiller_units", []):
        console.print(f"  {u['unit']}: {u['rated_rt']:.0f} RT")
    console.print(f"  Total installed RT  : {chiller.get('total_installed_rt')}")

    mismatch = verified.get("cost_year_mismatch")
    if mismatch:
        console.rule("[bold red]!! COST-YEAR MISMATCH DETECTED[/bold red]")
        console.print(f"  {mismatch['warning']}")
        console.print(f"  ESM baseline year         : {mismatch['esm_baseline_year']} ({mismatch['esm_baseline_kwh']:,.0f} kWh)")
        console.print(f"  kWh.Overall baseline year : {mismatch['kwh_overall_baseline_year']} ({mismatch['kwh_overall_baseline_kwh']:,.0f} kWh)")
        console.print(f"  Correct cost for ESM year : RM {mismatch['correct_cost_for_esm_year_rm']:,.2f}" if mismatch.get("correct_cost_for_esm_year_rm") else "  Correct cost for ESM year : n/a")
        console.print(f"  Cost for Baseline year    : RM {mismatch['cost_for_kwh_overall_baseline_year_rm']:,.2f}" if mismatch.get("cost_for_kwh_overall_baseline_year_rm") else "  Cost for Baseline year    : n/a")

    console.rule()


if __name__ == "__main__":
    main()
