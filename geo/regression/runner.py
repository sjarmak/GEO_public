"""CLI orchestrator for the GEO regression harness.

Usage::

    # Capture a baseline snapshot from a previous run already on disk
    python -m geo.regression.runner snapshot \\
        --experiment baseline_v1 --model mock --out baselines/

    # Compare two snapshot files (offline, no API calls)
    python -m geo.regression.runner compare \\
        --baseline baselines/mock__mock-v1__*.json \\
        --candidate baselines/mock__mock-v2__*.json

    # End-to-end: run the experiment, snapshot it, and gate against
    # the baseline. Exit code 0=PASS, 1=WARN, 2=FAIL. Wire into CI.
    python -m geo.regression.runner verify \\
        --corpus prompts/seed_corpus.json --model mock \\
        --experiment release_check \\
        --baseline baselines/mock_baseline.json

``snapshot`` and ``compare`` operate entirely on files and never call
out to a provider. Only ``verify`` calls the upstream runner, and the
``mock`` model alias lets even that path run offline with no API keys.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

from geo.config import Config, load_product_config
from geo.regression.detector import (
    RegressionThresholds,
    detect_regressions,
    format_report,
)
from geo.regression.snapshot import (
    Snapshot,
    build_snapshot,
    build_snapshot_from_storage,
    load_snapshot,
    save_snapshot,
)
from geo.runner import load_corpus, run_experiment
from geo.storage import ResultStorage

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_EXPECTED_OUTCOMES = _REPO_ROOT / "prompts" / "expected_outcomes.json"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subcommand: snapshot
# ---------------------------------------------------------------------------


def _cmd_snapshot(args: argparse.Namespace) -> int:
    """Build a Snapshot from already-stored experiment results."""
    storage = ResultStorage(base_path=args.results_dir) if args.results_dir else ResultStorage()
    product = load_product_config(args.product)

    snapshot = build_snapshot_from_storage(
        experiment_name=args.experiment,
        model_alias=args.model,
        product=product,
        storage=storage,
        config=Config(),
        expected_outcomes_path=args.expected_outcomes,
        date_from=args.date_from,
        date_to=args.date_to,
        notes=args.notes or "",
    )

    out_path = save_snapshot(snapshot, args.out)
    print(f"Wrote snapshot {snapshot.snapshot_id} -> {out_path}")
    print(
        f"  total_responses={snapshot.total_responses} "
        f"mention_rate={snapshot.mention_rate:.1%} "
        f"sov={snapshot.share_of_voice:.1%}"
    )
    return 0


# ---------------------------------------------------------------------------
# Subcommand: compare
# ---------------------------------------------------------------------------


def _build_thresholds(args: argparse.Namespace) -> RegressionThresholds:
    """Construct thresholds from CLI overrides, falling back to defaults."""
    overrides: dict[str, float | int] = {}
    if args.rate_warn is not None:
        overrides["rate_warn_drop"] = args.rate_warn
    if args.rate_fail is not None:
        overrides["rate_fail_drop"] = args.rate_fail
    if args.misrep_fail is not None:
        overrides["misrep_fail_increase"] = args.misrep_fail
    if args.significance_p is not None:
        overrides["significance_p"] = args.significance_p
    return RegressionThresholds(**overrides) if overrides else RegressionThresholds()


def _emit_report(
    *,
    baseline: Snapshot,
    candidate: Snapshot,
    args: argparse.Namespace,
) -> int:
    thresholds = _build_thresholds(args)
    report = detect_regressions(baseline, candidate, thresholds)

    if args.json:
        payload = {
            "baseline_id": report.baseline_id,
            "candidate_id": report.candidate_id,
            "baseline_model_id": report.baseline_model_id,
            "candidate_model_id": report.candidate_model_id,
            "overall_severity": report.overall_severity.value,
            "exit_code": report.exit_code,
            "notes": report.notes,
            "findings": [
                {
                    "metric": f.metric,
                    "baseline_value": f.baseline_value,
                    "candidate_value": f.candidate_value,
                    "delta": f.delta,
                    "severity": f.severity.value,
                    "p_value": f.p_value,
                    "rationale": f.rationale,
                }
                for f in report.findings
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_report(report))

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.report_out, "w", encoding="utf-8") as fh:
            fh.write(format_report(report))
            fh.write("\n")

    return report.exit_code


def _cmd_compare(args: argparse.Namespace) -> int:
    """Compare two existing snapshot files."""
    baseline = load_snapshot(args.baseline)
    candidate = load_snapshot(args.candidate)
    return _emit_report(baseline=baseline, candidate=candidate, args=args)


# ---------------------------------------------------------------------------
# Subcommand: verify
# ---------------------------------------------------------------------------


def _cmd_verify(args: argparse.Namespace) -> int:
    """Run a fresh experiment against a model and gate against a baseline."""
    cfg = Config()
    spec = cfg.get_model(args.model)
    product = load_product_config(args.product)

    expected_outcomes_path = args.expected_outcomes
    corpus = load_corpus(args.corpus, expected_outcomes_path=expected_outcomes_path)
    if args.limit:
        corpus = corpus[: args.limit]

    logger.info(
        "Verify: running %d prompts on %s for experiment '%s'",
        len(corpus),
        args.model,
        args.experiment,
    )

    summaries, _ = asyncio.run(
        run_experiment(
            corpus=corpus,
            model_aliases=[args.model],
            experiment_name=args.experiment,
            product=product,
            repetitions=args.reps,
            concurrency=args.concurrency,
            seed=args.seed,
            expected_outcomes_path=expected_outcomes_path,
        )
    )

    storage = ResultStorage()
    responses = storage.load(experiment_name=args.experiment, model_alias=args.model)
    if not responses:
        print(
            f"verify: no responses found for experiment={args.experiment!r} "
            f"model={args.model!r} after run_experiment; aborting",
            file=sys.stderr,
        )
        return 2

    candidate = build_snapshot(
        experiment_name=args.experiment,
        model_alias=args.model,
        model_id=spec.model_id,
        responses=responses,
        product=product,
        expected_outcomes_path=expected_outcomes_path,
        notes=f"verify run: {summaries[0].total_responses} responses",
    )

    if args.snapshot_out:
        out_path = save_snapshot(candidate, args.snapshot_out)
        logger.info("Saved candidate snapshot to %s", out_path)

    baseline = load_snapshot(args.baseline)
    return _emit_report(baseline=baseline, candidate=candidate, args=args)


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "GEO regression harness: snapshot, compare, and verify model "
            "versions against a baseline."
        ),
    )
    parser.add_argument("--verbose", action="store_true", default=False)
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ---- snapshot ------------------------------------------------------
    snap = sub.add_parser(
        "snapshot",
        help="Build a snapshot from previously stored experiment results.",
    )
    snap.add_argument("--experiment", required=True)
    snap.add_argument("--model", required=True, help="Model alias (e.g. mock, claude)")
    snap.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output file or directory.",
    )
    snap.add_argument(
        "--product",
        type=Path,
        default=Path("product.yaml"),
        help="Path to product.yaml (default: product.yaml in the current directory).",
    )
    snap.add_argument("--results-dir", type=Path, default=None)
    snap.add_argument("--date-from", type=str, default=None)
    snap.add_argument("--date-to", type=str, default=None)
    snap.add_argument("--notes", type=str, default=None)
    snap.add_argument(
        "--expected-outcomes",
        type=Path,
        default=_DEFAULT_EXPECTED_OUTCOMES,
    )
    snap.set_defaults(func=_cmd_snapshot)

    # ---- compare -------------------------------------------------------
    cmp_p = sub.add_parser(
        "compare",
        help="Compare two snapshot files and emit a regression report.",
    )
    cmp_p.add_argument("--baseline", type=Path, required=True)
    cmp_p.add_argument("--candidate", type=Path, required=True)
    cmp_p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    cmp_p.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Also write the human-readable report to this path.",
    )
    cmp_p.add_argument("--rate-warn", type=float, default=None)
    cmp_p.add_argument("--rate-fail", type=float, default=None)
    cmp_p.add_argument("--misrep-fail", type=int, default=None)
    cmp_p.add_argument("--significance-p", type=float, default=None)
    cmp_p.set_defaults(func=_cmd_compare)

    # ---- verify --------------------------------------------------------
    ver = sub.add_parser(
        "verify",
        help=(
            "Run an experiment, snapshot it, and gate it against a "
            "baseline. Exit code 0=PASS, 1=WARN, 2=FAIL."
        ),
    )
    ver.add_argument("--corpus", type=Path, required=True)
    ver.add_argument("--model", required=True)
    ver.add_argument("--experiment", required=True)
    ver.add_argument("--baseline", type=Path, required=True)
    ver.add_argument(
        "--product",
        type=Path,
        default=Path("product.yaml"),
        help="Path to product.yaml (default: product.yaml in the current directory).",
    )
    ver.add_argument("--reps", type=int, default=None)
    ver.add_argument("--limit", type=int, default=None)
    ver.add_argument("--concurrency", type=int, default=1)
    ver.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Seed for the deterministic mock provider (default: 0).",
    )
    ver.add_argument(
        "--snapshot-out",
        type=Path,
        default=None,
        help="Optional path to save the candidate snapshot for archiving.",
    )
    ver.add_argument(
        "--expected-outcomes",
        type=Path,
        default=_DEFAULT_EXPECTED_OUTCOMES,
    )
    ver.add_argument("--json", action="store_true", default=False)
    ver.add_argument("--report-out", type=Path, default=None)
    ver.add_argument("--rate-warn", type=float, default=None)
    ver.add_argument("--rate-fail", type=float, default=None)
    ver.add_argument("--misrep-fail", type=int, default=None)
    ver.add_argument("--significance-p", type=float, default=None)
    ver.set_defaults(func=_cmd_verify)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
