"""Share of Voice dashboard: reads stored experiment results and produces a
JSON summary plus a standalone HTML report (inline SVG, no JS dependencies).

CLI usage::

    python -m geo.dashboard --experiment smoke
    python -m geo.dashboard --experiment smoke --format json
    python -m geo.dashboard --experiment smoke --output results/reports/
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from geo.config import ProductConfig, load_product_config
from geo.llm_client import LLMResponse
from geo.runner import (
    compute_recall_by_expectation,
    load_expected_outcomes,
)
from geo.scoring import (
    aggregate_scores,
    score_binary_presence,
    score_misrepresentations,
    score_prominence,
)
from geo.storage import ResultStorage

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_EXPECTED_OUTCOMES = _REPO_ROOT / "prompts" / "expected_outcomes.json"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelReport:
    """Per-model scoring summary."""

    model_alias: str
    total_responses: int
    mention_rate: float
    share_of_voice: float
    avg_mention_count: float
    competitor_mention_rates: dict[str, float]
    misrepresentation_counts: dict[str, int]


@dataclass(frozen=True)
class ResponseDetail:
    """Per-response scoring detail for the HTML report."""

    prompt_id: str
    prompt_text: str
    response_text: str
    model_alias: str
    mentioned: bool
    mention_count: int
    misrepresentations_detected: list[str]
    competitor_mentions: dict[str, int]


@dataclass(frozen=True)
class DashboardSummary:
    """Complete dashboard data for an experiment."""

    experiment: str
    brand_name: str
    generated_at: str
    total_responses: int
    models: list[ModelReport]
    overall_mention_rate: float
    overall_share_of_voice: float
    competitor_comparison: dict[str, float]
    misrepresentation_totals: dict[str, int]
    misrepresentation_scoring_enabled: bool
    recall_by_expectation: list[dict[str, object]]
    response_details: list[ResponseDetail] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MisrepContext:
    """Optional expected-outcomes data used during summary building."""

    misrepresentations: tuple[dict, ...] = ()
    prompt_scenarios: dict[str, list[str]] = field(default_factory=dict)
    scenario_expectations: dict[str, str] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.misrepresentations)


def _load_misrep_context(expected_outcomes_path: Path | None) -> _MisrepContext:
    """Load the optional expected-outcomes file; empty context when absent."""
    if expected_outcomes_path is None or not expected_outcomes_path.exists():
        return _MisrepContext()
    outcomes = load_expected_outcomes(expected_outcomes_path)
    return _MisrepContext(
        misrepresentations=outcomes.misrepresentations,
        prompt_scenarios=outcomes.prompt_scenarios,
        scenario_expectations=outcomes.scenario_expectations,
    )


def _build_model_reports(
    by_model: dict[str, list[LLMResponse]],
    product: ProductConfig,
    misrepresentations: tuple[dict, ...],
) -> list[ModelReport]:
    """Score each model's responses into a ModelReport."""
    competitor_names = [c.name for c in product.competitors]
    reports: list[ModelReport] = []
    for alias in sorted(by_model):
        model_responses = by_model[alias]
        successful = [r for r in model_responses if r.error is None]
        if not successful:
            reports.append(
                ModelReport(
                    model_alias=alias,
                    total_responses=len(model_responses),
                    mention_rate=0.0,
                    share_of_voice=0.0,
                    avg_mention_count=0.0,
                    competitor_mention_rates={c: 0.0 for c in competitor_names},
                    misrepresentation_counts={},
                )
            )
            continue

        scores = aggregate_scores(
            successful,
            brand=product.brand,
            competitors=product.competitors,
            misrepresentations=misrepresentations or None,
        )
        reports.append(
            ModelReport(
                model_alias=alias,
                total_responses=scores.total_responses,
                mention_rate=scores.mention_rate,
                share_of_voice=scores.share_of_voice,
                avg_mention_count=scores.avg_mention_count,
                competitor_mention_rates=dict(scores.competitor_mention_rates),
                misrepresentation_counts=dict(scores.misrepresentation_counts),
            )
        )
    return reports


def _derive_overall_aggregates(
    model_reports: list[ModelReport],
    competitor_names: list[str],
) -> tuple[float, float, dict[str, float], dict[str, int]]:
    """Response-weighted overall rates derived from per-model reports.

    Returns ``(mention_rate, share_of_voice, competitor_comparison,
    misrepresentation_totals)``.
    """
    total_resp = sum(m.total_responses for m in model_reports)
    if total_resp == 0:
        return 0.0, 0.0, {c: 0.0 for c in competitor_names}, {}

    mention_rate = (
        sum(m.mention_rate * m.total_responses for m in model_reports) / total_resp
    )
    sov = (
        sum(m.share_of_voice * m.total_responses for m in model_reports) / total_resp
    )
    competitor_comparison = {
        comp: sum(
            m.competitor_mention_rates.get(comp, 0.0) * m.total_responses
            for m in model_reports
        )
        / total_resp
        for comp in competitor_names
    }
    misrep_totals: dict[str, int] = {}
    for m in model_reports:
        for mid, count in m.misrepresentation_counts.items():
            misrep_totals[mid] = misrep_totals.get(mid, 0) + count
    return mention_rate, sov, competitor_comparison, misrep_totals


def _compute_recall_dicts(
    by_prompt: dict[str, list[LLMResponse]],
    ctx: _MisrepContext,
    product: ProductConfig,
) -> list[dict[str, object]]:
    """Recall-by-expectation rows for the report, empty without scenarios."""
    if not (ctx.prompt_scenarios and ctx.scenario_expectations):
        return []
    recall_stats = compute_recall_by_expectation(
        by_prompt,
        ctx.prompt_scenarios,
        ctx.scenario_expectations,
        brand=product.brand,
    )
    return [
        {
            "level": r.level,
            "total_prompts": r.total_prompts,
            "total_responses": r.total_responses,
            "mention_count": r.mention_count,
            "recall_rate": r.recall_rate,
        }
        for r in recall_stats
    ]


def _build_response_details(
    responses: list[LLMResponse],
    product: ProductConfig,
    misrepresentations: tuple[dict, ...],
) -> list[ResponseDetail]:
    """Per-response scoring detail rows (successful responses only)."""
    details: list[ResponseDetail] = []
    for resp in responses:
        if resp.error is not None:
            continue
        bp = score_binary_presence(resp.response_text, product.brand)
        prom = score_prominence(resp.response_text, product.brand, product.competitors)
        misrep_ids: list[str] = []
        if misrepresentations:
            detected = score_misrepresentations(resp.response_text, misrepresentations)
            misrep_ids = [mr.misrep_id for mr in detected]
        details.append(
            ResponseDetail(
                prompt_id=resp.prompt_id,
                prompt_text=resp.prompt_text,
                response_text=resp.response_text,
                model_alias=resp.model_alias,
                mentioned=bp.mentioned,
                mention_count=bp.mention_count,
                misrepresentations_detected=misrep_ids,
                competitor_mentions=dict(prom.competitor_mentions),
            )
        )
    return details


def _empty_summary(
    experiment_name: str,
    product: ProductConfig,
    ctx: _MisrepContext,
) -> DashboardSummary:
    """Zeroed summary for an experiment with no stored responses."""
    return DashboardSummary(
        experiment=experiment_name,
        brand_name=product.brand.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_responses=0,
        models=[],
        overall_mention_rate=0.0,
        overall_share_of_voice=0.0,
        competitor_comparison={c.name: 0.0 for c in product.competitors},
        misrepresentation_totals={},
        misrepresentation_scoring_enabled=ctx.enabled,
        recall_by_expectation=[],
    )


def build_summary(
    experiment_name: str,
    responses: list[LLMResponse],
    product: ProductConfig,
    expected_outcomes_path: Path | None = _DEFAULT_EXPECTED_OUTCOMES,
) -> DashboardSummary:
    """Build a complete dashboard summary from experiment responses.

    *expected_outcomes_path* is optional; when the file is absent,
    misrepresentation scoring is skipped and the report says so.
    """
    competitor_names = [c.name for c in product.competitors]
    ctx = _load_misrep_context(expected_outcomes_path)

    if not responses:
        return _empty_summary(experiment_name, product, ctx)

    by_model: dict[str, list[LLMResponse]] = {}
    by_prompt: dict[str, list[LLMResponse]] = {}
    for resp in responses:
        by_model.setdefault(resp.model_alias, []).append(resp)
        by_prompt.setdefault(resp.prompt_id, []).append(resp)

    model_reports = _build_model_reports(by_model, product, ctx.misrepresentations)
    mention_rate, sov, competitor_comparison, misrep_totals = (
        _derive_overall_aggregates(model_reports, competitor_names)
    )

    return DashboardSummary(
        experiment=experiment_name,
        brand_name=product.brand.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_responses=len(responses),
        models=model_reports,
        overall_mention_rate=mention_rate,
        overall_share_of_voice=sov,
        competitor_comparison=competitor_comparison,
        misrepresentation_totals=misrep_totals,
        misrepresentation_scoring_enabled=ctx.enabled,
        recall_by_expectation=_compute_recall_dicts(by_prompt, ctx, product),
        response_details=_build_response_details(
            responses, product, ctx.misrepresentations
        ),
    )


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def write_json(summary: DashboardSummary, output_path: Path) -> Path:
    """Write the summary as a JSON file.

    Returns the path written to.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(summary)
    data.pop("response_details", None)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return output_path


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------


def _color_for_rate(rate: float) -> str:
    """Return a CSS color based on a 0-1 rate value."""
    if rate >= 0.6:
        return "#2d7d46"  # green
    if rate >= 0.3:
        return "#b8860b"  # dark goldenrod / yellow
    return "#c0392b"  # red


def _svg_bar_chart(
    labels: list[str],
    values: list[float],
    *,
    title: str = "",
    width: int = 600,
    bar_height: int = 28,
    max_val: float | None = None,
    format_pct: bool = True,
) -> str:
    """Generate an inline SVG horizontal bar chart."""
    top_margin = 30 if title else 10
    padding_left = 160
    bar_area_width = width - padding_left - 60
    chart_height = top_margin + len(labels) * (bar_height + 8) + 10

    cap = max_val if max_val is not None else (max(values) if values else 1.0)
    if cap <= 0:
        cap = 1.0

    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{chart_height}" '
        f'style="font-family: sans-serif; font-size: 13px;">'
    )
    if title:
        lines.append(
            f'<text x="{width // 2}" y="20" text-anchor="middle" '
            f'font-weight="bold" font-size="14">{html.escape(title)}</text>'
        )

    for i, (label, val) in enumerate(zip(labels, values)):
        y = top_margin + i * (bar_height + 8)
        bar_w = max(1, int(bar_area_width * min(val / cap, 1.0)))
        color = _color_for_rate(val) if format_pct else "#3498db"
        display = f"{val:.1%}" if format_pct else f"{val:.2f}"

        lines.append(
            f'<text x="{padding_left - 8}" y="{y + bar_height // 2 + 4}" '
            f'text-anchor="end" font-size="12">{html.escape(label)}</text>'
        )
        lines.append(
            f'<rect x="{padding_left}" y="{y}" '
            f'width="{bar_w}" height="{bar_height}" '
            f'fill="{color}" rx="3" />'
        )
        lines.append(
            f'<text x="{padding_left + bar_w + 6}" '
            f'y="{y + bar_height // 2 + 4}" font-size="12">{html.escape(display)}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def _html_table(
    headers: list[str],
    rows: list[list[str]],
) -> str:
    """Generate a simple HTML table."""
    parts: list[str] = ['<table class="data-table">']
    parts.append("<thead><tr>")
    for h in headers:
        parts.append(f"<th>{html.escape(h)}</th>")
    parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{html.escape(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def _render_sov_section(summary: DashboardSummary) -> str:
    """Share of Voice bar chart: brand vs competitors."""
    labels = [summary.brand_name] + list(summary.competitor_comparison)
    values = [summary.overall_share_of_voice] + list(
        summary.competitor_comparison.values()
    )
    chart = _svg_bar_chart(
        labels,
        values,
        title=f"Mention Rate: {summary.brand_name} vs Competitors",
        max_val=1.0,
    )
    return f"<h2>Share of Voice</h2>\n{chart}"


def _render_model_section(summary: DashboardSummary) -> str:
    """Mention rate per model, or empty when there are no models."""
    if not summary.models:
        return ""
    chart = _svg_bar_chart(
        [m.model_alias for m in summary.models],
        [m.mention_rate for m in summary.models],
        title=f"{summary.brand_name} Mention Rate per Model",
        max_val=1.0,
    )
    return f"<h2>Mention Rate by Model</h2>\n{chart}"


def _render_competitor_table(summary: DashboardSummary) -> str:
    """Brand-first mention-rate comparison table."""
    rows = [[summary.brand_name, f"{summary.overall_mention_rate:.1%}"]]
    rows.extend(
        [comp, f"{rate:.1%}"]
        for comp, rate in summary.competitor_comparison.items()
    )
    table = _html_table(["Brand", "Mention Rate"], rows)
    return f"<h2>Competitor Comparison</h2>\n{table}"


def _render_misrep_section(summary: DashboardSummary) -> str:
    """Misrepresentation counts, or a note when scoring was skipped."""
    if not summary.misrepresentation_scoring_enabled:
        body = (
            "<p>Skipped: no misrepresentation list provided "
            "(prompts/expected_outcomes.json).</p>"
        )
    elif summary.misrepresentation_totals:
        rows = [
            [mid, str(count)]
            for mid, count in sorted(summary.misrepresentation_totals.items())
        ]
        body = _html_table(["Misrepresentation ID", "Count"], rows)
    else:
        body = "<p>No misrepresentations detected.</p>"
    return f"<h2>Misrepresentation Detection</h2>\n{body}"


def _render_recall_section(summary: DashboardSummary) -> str:
    """Recall by expectation level, or a note when no scenarios exist."""
    if summary.recall_by_expectation:
        rows = [
            [
                str(r["level"]),
                str(r["total_prompts"]),
                str(r["total_responses"]),
                str(r["mention_count"]),
                f"{float(r['recall_rate']):.1%}",
            ]
            for r in summary.recall_by_expectation
        ]
        body = _html_table(
            ["Level", "Prompts", "Responses", "Mentions", "Recall Rate"], rows
        )
    else:
        body = "<p>No expected outcomes data available.</p>"
    return f"<h2>Recall by Expectation Level</h2>\n{body}"


def _render_response_rows(idx: int, rd: ResponseDetail) -> str:
    """One clickable summary row plus its hidden detail row."""
    mentioned_icon = (
        '<span style="color:#2d7d46;font-weight:bold;">&#10003;</span>'
        if rd.mentioned
        else '<span style="color:#c0392b;font-weight:bold;">&#10007;</span>'
    )
    misrep_display = ", ".join(rd.misrepresentations_detected) or "-"
    comp_parts = [
        f"{comp}: {cnt}" for comp, cnt in rd.competitor_mentions.items() if cnt > 0
    ]
    comp_display = ", ".join(comp_parts) if comp_parts else "-"
    detail_id = f"resp-detail-{idx}"

    summary_row = (
        f'<tr class="resp-summary" data-target="{detail_id}" '
        f"onclick=\"toggleDetail('{detail_id}')\" "
        f'style="cursor:pointer;" id="resp-row-{idx}">'
        f"<td>{html.escape(rd.prompt_id)}</td>"
        f"<td>{html.escape(rd.model_alias)}</td>"
        f"<td>{mentioned_icon}</td>"
        f"<td>{rd.mention_count}</td>"
        f"<td>{html.escape(misrep_display)}</td>"
        f"</tr>"
    )
    pre_style = (
        'style="white-space:pre-wrap;word-wrap:break-word;'
        "background:#f9f9f9;padding:8px;border-radius:4px;"
        'font-size:12px;margin:4px 0 12px;"'
    )
    detail_row = (
        f'<tr class="resp-detail" id="{detail_id}" style="display:none;">'
        f'<td colspan="5"><div style="margin:8px 0;">'
        f"<strong>Prompt:</strong>"
        f"<pre {pre_style}>{html.escape(rd.prompt_text)}</pre>"
        f"<strong>Response:</strong>"
        f"<pre {pre_style}>{html.escape(rd.response_text)}</pre>"
        f"<strong>Competitor mentions:</strong> {html.escape(comp_display)}"
        f"</div></td></tr>"
    )
    return f"{summary_row}\n{detail_row}"


def _render_response_details(summary: DashboardSummary) -> str:
    """Filterable table of per-response scoring details."""
    if not summary.response_details:
        return "<h2>Response Details</h2>\n<p>No response details available.</p>"

    parts = [
        "<h2>Response Details</h2>",
        '<input type="text" id="response-filter" '
        'placeholder="Filter by prompt ID or text…" '
        'oninput="filterResponses()" '
        'style="width:100%;padding:8px 12px;font-size:13px;'
        'border:1px solid #ccc;border-radius:4px;margin-bottom:12px;">',
        '<table class="data-table" id="response-table">',
        "<thead><tr>"
        "<th>Prompt ID</th>"
        "<th>Model</th>"
        "<th>Mentioned?</th>"
        "<th>Mentions</th>"
        "<th>Misrepresentations</th>"
        "</tr></thead>",
        "<tbody>",
    ]
    parts.extend(
        _render_response_rows(idx, rd)
        for idx, rd in enumerate(summary.response_details)
    )
    parts.append("</tbody></table>")
    return "\n".join(parts)


def _page_shell(summary: DashboardSummary, body: str) -> str:
    """Wrap rendered sections in the standalone HTML page skeleton."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GEO Dashboard: {html.escape(summary.experiment)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         color: #333; background: #f5f5f5; }}
  header {{ background: #1a1a2e; color: #fff; padding: 24px 32px; }}
  header h1 {{ font-size: 22px; font-weight: 600; }}
  header .meta {{ font-size: 13px; color: #aaa; margin-top: 6px; }}
  .container {{ max-width: 900px; margin: 24px auto; padding: 0 16px; }}
  h2 {{ margin: 28px 0 12px; font-size: 18px; color: #1a1a2e;
        border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; }}
  .data-table {{ width: 100%; border-collapse: collapse; margin: 8px 0 20px; }}
  .data-table th {{ background: #1a1a2e; color: #fff; padding: 8px 12px;
                    text-align: left; font-size: 13px; font-weight: 600; }}
  .data-table td {{ padding: 8px 12px; border-bottom: 1px solid #e0e0e0;
                    font-size: 13px; }}
  .data-table tr:nth-child(even) {{ background: #fafafa; }}
  .data-table tr:hover {{ background: #f0f0f0; }}
  svg {{ display: block; margin: 8px 0 20px; }}
  p {{ font-size: 14px; color: #666; margin: 8px 0; }}
  .resp-summary:hover {{ background: #e8e8e8 !important; }}
  .resp-detail pre {{ max-height: 400px; overflow-y: auto; }}
</style>
</head>
<body>
<header>
  <h1>GEO Share of Voice Dashboard: {html.escape(summary.brand_name)}</h1>
  <div class="meta">
    Experiment: {html.escape(summary.experiment)}
    | Generated: {html.escape(summary.generated_at)}
    | Total responses: {html.escape(str(summary.total_responses))}
  </div>
</header>
<div class="container">
{body}
</div>
<script>
function toggleDetail(id) {{
  var el = document.getElementById(id);
  if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
}}
function filterResponses() {{
  var q = document.getElementById('response-filter').value.toLowerCase();
  var rows = document.querySelectorAll('#response-table tbody tr.resp-summary');
  rows.forEach(function(row) {{
    var text = row.textContent.toLowerCase();
    var detailId = row.getAttribute('data-target');
    var detail = document.getElementById(detailId);
    var match = text.indexOf(q) !== -1;
    if (!match && detail) {{
      match = detail.textContent.toLowerCase().indexOf(q) !== -1;
    }}
    row.style.display = match ? '' : 'none';
    if (detail && !match) detail.style.display = 'none';
  }});
}}
</script>
</body>
</html>"""


def generate_html(summary: DashboardSummary) -> str:
    """Generate a standalone HTML report from the dashboard summary."""
    sections = [
        _render_sov_section(summary),
        _render_model_section(summary),
        _render_competitor_table(summary),
        _render_misrep_section(summary),
        _render_recall_section(summary),
        _render_response_details(summary),
    ]
    body = "\n".join(s for s in sections if s)
    return _page_shell(summary, body)


def write_html(summary: DashboardSummary, output_path: Path) -> Path:
    """Write the HTML report to a file.

    Returns the path written to.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_html(summary)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate GEO Share of Voice dashboard reports.",
    )
    parser.add_argument(
        "--experiment",
        type=str,
        required=True,
        help="Name of the experiment to report on.",
    )
    parser.add_argument(
        "--product",
        type=Path,
        default=Path("product.yaml"),
        help="Path to product.yaml (default: product.yaml in the current directory).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory for reports (default: results/reports/).",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["json", "html", "both"],
        default="both",
        help="Output format: json, html, or both (default: both).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point."""
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    product = load_product_config(args.product)

    output_dir = (
        Path(args.output) if args.output else _REPO_ROOT / "results" / "reports"
    )

    # Load results
    storage = ResultStorage()
    responses = storage.load(args.experiment)

    if not responses:
        logger.warning("No results found for experiment '%s'.", args.experiment)
        print(f"No results found for experiment '{args.experiment}'.")
        sys.exit(1)

    logger.info(
        "Loaded %d responses for experiment '%s'.",
        len(responses),
        args.experiment,
    )

    summary = build_summary(args.experiment, responses, product)

    fmt = args.format
    if fmt in ("json", "both"):
        json_path = write_json(summary, output_dir / f"{args.experiment}_summary.json")
        print(f"JSON summary written to: {json_path}")

    if fmt in ("html", "both"):
        html_path = write_html(summary, output_dir / f"{args.experiment}_report.html")
        print(f"HTML report written to: {html_path}")


if __name__ == "__main__":
    main()
