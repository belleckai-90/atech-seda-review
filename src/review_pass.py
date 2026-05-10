"""Pass 2 — section-by-section review via Claude API."""

from __future__ import annotations

import json
import time

import json_repair
from pathlib import Path
from typing import Any

import anthropic

REVIEW_MODEL = "claude-opus-4-7"
MAX_TOKENS = 32768
MAX_RETRIES = 3
_RETRY_BASE_SECONDS = 2


def _load_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _build_static_context(
    checklist_path: Path,
    findings_library_path: Path | None,
) -> str:
    """Build the large static reference block — this is what we cache."""
    checklist = _load_text(checklist_path)
    parts: list[str] = [
        "## SEDA CHECKLIST V4 REFERENCE\n\n"
        "Use this as the specification for what each section must contain. "
        "Every section in the report's TOC must appear in your section_findings output.\n\n",
        checklist,
    ]

    if findings_library_path and findings_library_path.exists():
        library = _load_text(findings_library_path).strip()
        if library:
            parts.extend([
                "\n\n---\n\n## INSTITUTIONAL MEMORY — COMMON FINDINGS\n\n"
                "Patterns observed in previous reviews. Check explicitly for each:\n\n",
                library,
            ])

    return "".join(parts)


def _build_dynamic_context(docx_data: dict, verified: dict) -> str:
    """Build the per-review context block — NOT cached (unique per report)."""
    toc: dict = docx_data.get("toc", {})
    section_page_map: dict = docx_data.get("section_page_map", {})
    report_text: str = docx_data.get("text", "")

    # Produce a compact, sorted TOC for the LLM to reference
    if toc:
        sorted_entries = sorted(toc.items(), key=lambda kv: (kv[1] or 999, kv[0]))
        toc_lines = [f"  {title}: page {page}" for title, page in sorted_entries]
    elif section_page_map:
        toc_lines = [
            f"  {title}: page {page}" if page else f"  {title}: (page unknown)"
            for title, page in section_page_map.items()
        ]
    else:
        toc_lines = ["  (No TOC extracted — use section numbering for page references)"]

    verified_json = json.dumps(verified, indent=2, default=str)

    return (
        "## VERIFIED NUMBERS (ground truth from workbook — override any contradicting report claim)\n\n"
        f"```json\n{verified_json}\n```\n\n"
        "---\n\n"
        "## REPORT TABLE OF CONTENTS (section → page map)\n\n"
        + "\n".join(toc_lines)
        + "\n\n---\n\n"
        "## REPORT TEXT\n\n"
        + report_text
    )


def _parse_json_response(text: str) -> dict:
    """Extract and parse JSON from the LLM response text."""
    text = text.strip()
    # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines) - 1
        while end > 0 and lines[end].strip() in ("```", ""):
            end -= 1
        text = "\n".join(lines[1 : end + 1])

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back: let json-repair fix unescaped quotes/newlines in LLM output
    repaired = json_repair.loads(text)
    if isinstance(repaired, dict):
        return repaired

    raise json.JSONDecodeError("Could not extract valid JSON", text, 0)


def run_review_pass(
    docx_data: dict,
    xlsx_data: dict,
    verified: dict,
    report_type: str,
    prompts_dir: Path,
    references_dir: Path,
    findings_library_path: Path | None = None,
    verbose: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run Pass 2: section-by-section LLM review.

    Args:
        docx_data: output of extract_docx (text, toc, section_page_map, headings)
        xlsx_data: output of extract_xlsx (sheets dict)
        verified: output of build_verified_numbers
        report_type: "commercial" | "industrial"
        prompts_dir: path to prompts/ directory
        references_dir: path to references/ directory
        findings_library_path: path to common_findings.md (None = skip)
        verbose: print API usage details

    Returns:
        (findings_dict, usage_info)
        findings_dict — parsed JSON from LLM matching output_schema.json
        usage_info — token counts and estimated cost
    """
    client = anthropic.Anthropic()

    system_prompt = _load_text(prompts_dir / "system_prompt.md")
    if not system_prompt:
        raise FileNotFoundError(f"system_prompt.md not found in {prompts_dir}")

    # Pick checklist variant; fall back to industrial if commercial not available
    if report_type == "commercial":
        checklist_path = references_dir / "seda_checklist_v4_commercial.md"
        if not checklist_path.exists():
            checklist_path = references_dir / "seda_checklist_v4_industrial.md"
    else:
        checklist_path = references_dir / "seda_checklist_v4_industrial.md"

    if not checklist_path.exists():
        raise FileNotFoundError(f"Checklist not found: {checklist_path}")

    static_ctx = _build_static_context(checklist_path, findings_library_path)
    dynamic_ctx = _build_dynamic_context(docx_data, verified)

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                # Large static block — mark for caching
                {
                    "type": "text",
                    "text": static_ctx,
                    "cache_control": {"type": "ephemeral"},
                },
                # Per-review dynamic block — NOT cached
                {
                    "type": "text",
                    "text": dynamic_ctx,
                },
            ],
        }
    ]

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with client.messages.stream(
                model=REVIEW_MODEL,
                max_tokens=MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
            ) as stream:
                raw_text = stream.get_final_text()
                response  = stream.get_final_message()

            try:
                findings = _parse_json_response(raw_text)
            except json.JSONDecodeError as jexc:
                # Save raw response for inspection
                _outputs = Path(__file__).parent.parent / "outputs"
                _outputs.mkdir(exist_ok=True)
                (_outputs / "debug_last_response.txt").write_text(raw_text, encoding="utf-8")
                raise json.JSONDecodeError(
                    f"LLM returned non-JSON (first 500 chars): {raw_text[:500]}",
                    jexc.doc,
                    jexc.pos,
                ) from jexc

            usage = response.usage
            usage_info: dict[str, Any] = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
            }
            # Rough cost estimate: Opus 4.7 pricing (write/read cache)
            # Standard: $15/M input, $75/M output; cache write: $18.75/M; cache read: $1.50/M
            input_cost = usage_info["input_tokens"] * 15 / 1_000_000
            output_cost = usage_info["output_tokens"] * 75 / 1_000_000
            cache_write_cost = usage_info["cache_creation_input_tokens"] * 18.75 / 1_000_000
            cache_read_cost = usage_info["cache_read_input_tokens"] * 1.50 / 1_000_000
            usage_info["estimated_cost_usd"] = round(
                input_cost + output_cost + cache_write_cost + cache_read_cost, 4
            )

            if verbose:
                print(
                    f"  Pass 2 tokens — in:{usage_info['input_tokens']} "
                    f"out:{usage_info['output_tokens']} "
                    f"cache_write:{usage_info['cache_creation_input_tokens']} "
                    f"cache_read:{usage_info['cache_read_input_tokens']} "
                    f"≈USD {usage_info['estimated_cost_usd']}"
                )

            return findings, usage_info

        except (anthropic.APIError, anthropic.APIConnectionError, anthropic.RateLimitError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = _RETRY_BASE_SECONDS ** attempt
                if verbose:
                    print(f"  Pass 2 attempt {attempt} failed ({exc!r}), retrying in {wait}s…")
                time.sleep(wait)

        except json.JSONDecodeError as exc:
            # JSON parse failure — not an API error, don't retry
            raise RuntimeError(f"Pass 2 JSON parse error: {exc}") from exc

    raise RuntimeError(
        f"Pass 2 failed after {MAX_RETRIES} attempts: {last_error}"
    ) from last_error
