"""Freshness-decay tracker: loads markers.json, asks each marker's probe
question through the shared LLM client, scores keyword recall, and fits
``recall = amplitude * exp(-decay_rate * age_weeks)`` to report a half-life
and refresh cadence per model. Run ``--help`` for the CLI; ``--dry-run``
exercises the whole pipeline offline via the mock provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

from geo.config import MODEL_REGISTRY, load_product_config
from geo.llm_client import LLMClient, LLMResponse, MockResponder

_MARKERS_PATH = Path(__file__).resolve().parent / "markers.json"
_RESULTS_DIR = Path("results") / "freshness_decay"


@dataclass(frozen=True)
class Marker:
    """One dated fact-marker probe."""
    id: str
    version: str
    publish_date: date | None
    claim_short: str
    probe_question: str
    expected_keywords: tuple[str, ...]
    is_control: bool  # negative control or adjacent-unpublished


@dataclass(frozen=True)
class RecallReport:
    """Recall statistics for one (marker, model) pair in one run."""
    marker_id: str
    response_count: int
    recall_count: int
    is_control: bool
    age_weeks: float | None

    @property
    def recall_rate(self) -> float:
        return self.recall_count / self.response_count if self.response_count else 0.0


@dataclass(frozen=True)
class DecayFit:
    """Least-squares fit of recall = amplitude * exp(-decay_rate * age_weeks)."""
    amplitude: float
    decay_rate: float
    sample_count: int

    @property
    def half_life_weeks(self) -> float:
        return math.log(2) / self.decay_rate if self.decay_rate > 0 else float("inf")


def load_markers(path: Path = _MARKERS_PATH) -> tuple[Marker, ...]:
    """Load the marker corpus, failing fast on a missing or empty file."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    raw = data.get("markers", [])
    if not raw:
        raise ValueError(f"No markers found in {path}")
    return tuple(
        Marker(
            id=str(m["id"]),
            version=str(m["version"]),
            publish_date=date.fromisoformat(m["publish_date"]) if m.get("publish_date") else None,
            claim_short=str(m["claim_short"]),
            probe_question=str(m["probe_question"]),
            expected_keywords=tuple(str(k) for k in m["expected_keywords"]),
            is_control=bool(m.get("is_negative_control")) or bool(m.get("is_adjacent_unpublished")),
        )
        for m in raw
    )


def recalled(marker: Marker, response: LLMResponse) -> bool:
    """A response recalls a marker when any expected keyword appears in it."""
    return response.error is None and any(
        kw.lower() in response.response_text.lower() for kw in marker.expected_keywords
    )


def fit_decay_curve(reports: Sequence[RecallReport]) -> DecayFit:
    """OLS on log recall vs age; controls, undated, and zero-recall excluded."""
    pts = [
        (r.age_weeks, math.log(r.recall_rate))
        for r in reports
        if not r.is_control and r.age_weeks is not None and r.recall_rate > 0.0
    ]
    if (n := len(pts)) < 2:
        return DecayFit(amplitude=1.0, decay_rate=0.0, sample_count=n)
    sum_x = sum(x for x, _ in pts)
    sum_y = sum(y for _, y in pts)
    denom = n * sum(x * x for x, _ in pts) - sum_x * sum_x
    if denom == 0:
        return DecayFit(amplitude=1.0, decay_rate=0.0, sample_count=n)
    slope = (n * sum(x * y for x, y in pts) - sum_x * sum_y) / denom
    return DecayFit(amplitude=math.exp((sum_y - slope * sum_x) / n), decay_rate=-slope, sample_count=n)


def recommend_refresh_cadence(half_life_weeks: float) -> str:
    """Map a fitted half-life to a content-refresh recommendation."""
    if not math.isfinite(half_life_weeks):
        return "Inconclusive: need more dated markers or more runs"
    if half_life_weeks < 4:
        return "Weekly refresh needed"
    if half_life_weeks < 12:
        return "Monthly refresh sufficient"
    if half_life_weeks < 26:
        return "Quarterly refresh sufficient"
    return "Semi-annual refresh sufficient"


async def run_tracker(
    markers: Sequence[Marker],
    model_aliases: Sequence[str],
    *,
    repetitions: int,
    run_date: date,
    mock_responder: MockResponder | None = None,
) -> dict[str, list[RecallReport]]:
    """Run every marker against every model. Returns reports keyed by alias."""
    for alias in model_aliases:
        if alias not in MODEL_REGISTRY:
            raise KeyError(f"Unknown model alias {alias!r}; known: {sorted(MODEL_REGISTRY)}")
    client = LLMClient(mock_responder=mock_responder)
    out: dict[str, list[RecallReport]] = {alias: [] for alias in model_aliases}
    for marker in markers:
        age = None if marker.publish_date is None else (run_date - marker.publish_date).days / 7.0
        for alias in model_aliases:
            responses = await client.run_prompt(
                model_alias=alias, model_spec=MODEL_REGISTRY[alias], prompt_id=marker.id,
                prompt_text=marker.probe_question, repetitions=repetitions,
            )
            valid = [r for r in responses if r.error is None]
            out[alias].append(RecallReport(
                marker_id=marker.id, response_count=len(valid),
                recall_count=sum(1 for r in valid if recalled(marker, r)),
                is_control=marker.is_control, age_weeks=age,
            ))
    return out


def write_report(run_id: str, run_date: date, repetitions: int,
                 reports: dict[str, list[RecallReport]], fits: dict[str, DecayFit],
                 output_dir: Path = _RESULTS_DIR) -> Path:
    """Write one run's reports and fits to results/freshness_decay/<run_id>.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{run_id}.json"
    payload = {
        "run_id": run_id, "run_date": run_date.isoformat(), "repetitions": repetitions,
        "reports": {a: [{**asdict(r), "recall_rate": r.recall_rate} for r in rs]
                    for a, rs in reports.items()},
        "fits": {a: {**asdict(f), "half_life_weeks": f.half_life_weeks,
                     "refresh_cadence": recommend_refresh_cadence(f.half_life_weeks)}
                 for a, f in fits.items()},
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the freshness-decay tracker.")
    parser.add_argument("--markers", type=Path, default=_MARKERS_PATH)
    parser.add_argument("--models", type=str, default="claude", help="Comma-separated aliases.")
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--run-id", type=str, default=None, help="Run identifier, e.g. 2026-07.")
    parser.add_argument("--run-date", type=str, default=None, help="ISO date for age computation.")
    parser.add_argument("--dry-run", action="store_true", help="Use the mock provider.")
    parser.add_argument("--product", type=Path, default=Path("product.yaml"),
                        help="Product config used to template mock responses.")
    parser.add_argument("--seed", type=int, default=0, help="Seed for the mock provider.")
    args = parser.parse_args(argv)
    markers = load_markers(args.markers)
    aliases = ["mock"] if args.dry_run else [m.strip() for m in args.models.split(",")]
    run_date = date.fromisoformat(args.run_date) if args.run_date else datetime.now(timezone.utc).date()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    mock_responder = None
    if "mock" in aliases:
        product = load_product_config(args.product)
        mock_responder = MockResponder(product=product, seed=args.seed)

    reports = asyncio.run(run_tracker(markers, aliases, repetitions=args.reps,
                                      run_date=run_date, mock_responder=mock_responder))
    fits = {alias: fit_decay_curve(rs) for alias, rs in reports.items()}
    out_path = write_report(run_id, run_date, args.reps, reports, fits)
    print(f"Freshness-decay run {run_id} (run_date={run_date.isoformat()})")
    for alias, rs in reports.items():
        print(f"\nModel: {alias}")
        for r in rs:
            age = f"{r.age_weeks:5.1f}w" if r.age_weeks is not None else "n/a"
            print(f"  {r.marker_id:10s} age={age} recall={r.recall_rate:5.1%} "
                  f"({r.recall_count}/{r.response_count}){' [CTRL]' if r.is_control else ''}")
        print(f"  -> half_life={fits[alias].half_life_weeks:.1f}w ({fits[alias].sample_count} pts): "
              f"{recommend_refresh_cadence(fits[alias].half_life_weeks)}")
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
