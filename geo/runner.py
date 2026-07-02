"""Experiment runner for the GEO measurement pipeline.

Orchestrates: prompt corpus x models x N repetitions, stores results,
and produces summary reports.

CLI usage::

    python -m geo.runner \
        --corpus prompts/seed_corpus.json \
        --models claude,chatgpt,gemini \
        --reps 20 \
        --experiment baseline

Use ``--dry-run`` to exercise the full pipeline with deterministic mock
responses and no API keys.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from geo.config import (
    BrandSpec,
    Config,
    ModelSpec,
    ProductConfig,
    load_product_config,
)
from geo.llm_client import LLMClient, LLMResponse, MockResponder
from geo.scoring import (
    AggregateScores,
    aggregate_scores,
    score_binary_presence,
)
from geo.storage import ResultStorage

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_EXPECTED_OUTCOMES = _REPO_ROOT / "prompts" / "expected_outcomes.json"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpectedOutcomesData:
    """Parsed contents of expected_outcomes.json."""

    prompt_scenarios: dict[str, list[str]]
    scenario_expectations: dict[str, str]
    misrepresentations: tuple[dict, ...]


# ---------------------------------------------------------------------------
# Prompt corpus
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Prompt:
    """A single prompt from the corpus."""

    id: str
    text: str
    category: str
    expected_scenarios: tuple[str, ...] = field(default_factory=tuple)


def load_expected_outcomes(path: Path) -> ExpectedOutcomesData:
    """Load the optional expected-outcomes file and build reverse indices.

    Parses the file once and returns scenarios, expectations, and
    misrepresentations together.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    prompt_scenarios: dict[str, list[str]] = {}
    scenario_expectations: dict[str, str] = {}

    for scenario in data.get("scenarios", []):
        sid = str(scenario["id"])
        scenario_expectations[sid] = str(scenario["expectation"])
        for prompt_id in scenario.get("example_prompts", []):
            pid = str(prompt_id)
            prompt_scenarios.setdefault(pid, []).append(sid)

    misrepresentations = data.get("known_misrepresentations", {}).get("items", [])

    return ExpectedOutcomesData(
        prompt_scenarios=prompt_scenarios,
        scenario_expectations=scenario_expectations,
        misrepresentations=tuple(misrepresentations),
    )


def load_corpus(
    path: Path,
    *,
    expected_outcomes_path: Path | None = None,
) -> list[Prompt]:
    """Load a prompt corpus from a JSON file.

    Expected format::

        [
            {"id": "cat-001", "prompt": "...", "category": "category_search"},
            ...
        ]

    Both ``prompt`` and ``text`` are accepted for the prompt field.
    When *expected_outcomes_path* is provided and exists, each prompt's
    ``expected_scenarios`` field is populated from the reverse index.
    """
    prompt_scenarios: dict[str, list[str]] = {}
    if expected_outcomes_path is not None and expected_outcomes_path.exists():
        outcomes = load_expected_outcomes(expected_outcomes_path)
        prompt_scenarios = outcomes.prompt_scenarios

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    prompts: list[Prompt] = []
    for item in data:
        text = str(item.get("text") or item.get("prompt", ""))
        pid = str(item["id"])
        prompts.append(
            Prompt(
                id=pid,
                text=text,
                category=str(item.get("category", "uncategorized")),
                expected_scenarios=tuple(prompt_scenarios.get(pid, ())),
            )
        )
    if not prompts:
        raise ValueError(f"Corpus at {path} is empty")
    return prompts


# ---------------------------------------------------------------------------
# Recall by expectation level
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecallByExpectation:
    """Recall statistics for a single expectation level."""

    level: str
    total_prompts: int
    total_responses: int
    mention_count: int
    recall_rate: float


def compute_recall_by_expectation(
    responses_by_prompt: dict[str, list[LLMResponse]],
    prompt_scenarios: dict[str, list[str]],
    scenario_expectations: dict[str, str],
    brand: BrandSpec,
) -> list[RecallByExpectation]:
    """Compute recall rate grouped by expectation level.

    Groups prompts by their expectation level (via their scenarios),
    then computes what fraction of responses for those prompts actually
    mentioned *brand*.
    """
    level_prompts: dict[str, set[str]] = {}
    for prompt_id, scenario_ids in prompt_scenarios.items():
        for sid in scenario_ids:
            level = scenario_expectations.get(sid, "unknown")
            level_prompts.setdefault(level, set()).add(prompt_id)

    results: list[RecallByExpectation] = []
    for level in sorted(level_prompts):
        prompt_ids = level_prompts[level]
        total_responses = 0
        mention_count = 0
        for pid in prompt_ids:
            for resp in responses_by_prompt.get(pid, []):
                total_responses += 1
                if score_binary_presence(resp.response_text, brand).mentioned:
                    mention_count += 1
        recall = mention_count / total_responses if total_responses > 0 else 0.0
        results.append(
            RecallByExpectation(
                level=level,
                total_prompts=len(prompt_ids),
                total_responses=total_responses,
                mention_count=mention_count,
                recall_rate=recall,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSummary:
    """Per-model summary statistics."""

    model_alias: str
    model_id: str
    total_responses: int
    error_count: int
    scores: AggregateScores


def _build_summary(
    model_alias: str,
    spec: ModelSpec,
    responses: list[LLMResponse],
    product: ProductConfig,
    misrepresentations: Sequence[dict] | None = None,
) -> ModelSummary:
    """Build a summary for a single model's results."""
    successful = [r for r in responses if r.error is None]
    error_count = len(responses) - len(successful)

    scores = (
        aggregate_scores(
            successful,
            brand=product.brand,
            competitors=product.competitors,
            misrepresentations=misrepresentations,
        )
        if successful
        else AggregateScores(
            total_responses=0,
            mention_rate=0.0,
            share_of_voice=0.0,
            avg_first_mention_offset=None,
            avg_mention_count=0.0,
            list_appearance_rate=0.0,
            avg_list_rank=None,
            competitor_mention_rates={c.name: 0.0 for c in product.competitors},
        )
    )

    return ModelSummary(
        model_alias=model_alias,
        model_id=spec.model_id,
        total_responses=len(responses),
        error_count=error_count,
        scores=scores,
    )


def print_report(
    summaries: Sequence[ModelSummary],
    recall_by_expectation: Sequence[RecallByExpectation] | None = None,
) -> None:
    """Print a human-readable summary report to stdout."""
    sep = "-" * 72
    print(f"\n{'GEO Experiment Summary Report':^72}")
    print(sep)

    for s in summaries:
        print(f"\nModel: {s.model_alias} ({s.model_id})")
        print(f"  Responses:       {s.total_responses}")
        print(f"  Errors:          {s.error_count}")
        sc = s.scores
        print(f"  Mention rate:    {sc.mention_rate:.1%}")
        print(f"  Share of Voice:  {sc.share_of_voice:.1%}")
        print(f"  Avg mentions:    {sc.avg_mention_count:.2f}")
        if sc.avg_first_mention_offset is not None:
            print(f"  Avg 1st offset:  {sc.avg_first_mention_offset:.0f} chars")
        print(f"  List appearance: {sc.list_appearance_rate:.1%}")
        if sc.avg_list_rank is not None:
            print(f"  Avg list rank:   {sc.avg_list_rank:.1f}")
        print("  Competitor mention rates:")
        for comp, rate in sc.competitor_mention_rates.items():
            print(f"    {comp:25s} {rate:.1%}")
        if sc.misrepresentation_counts:
            print("  Misrepresentations detected:")
            for misrep_id, count in sorted(sc.misrepresentation_counts.items()):
                print(f"    {misrep_id:15s} {count}/{sc.total_responses} responses")

    if recall_by_expectation:
        print(f"\n{'Recall by Expectation Level':^72}")
        print(sep)
        print(
            f"  {'Level':25s} {'Prompts':>8s} {'Responses':>10s} {'Mentions':>9s} {'Recall':>8s}"
        )
        for r in recall_by_expectation:
            print(
                f"  {r.level:25s} {r.total_prompts:8d} {r.total_responses:10d}"
                f" {r.mention_count:9d} {r.recall_rate:7.1%}"
            )

    print(sep)


# ---------------------------------------------------------------------------
# Main run logic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RunParams:
    """Sampling parameters resolved from explicit overrides plus config."""

    reps: int
    temperature: float
    top_p: float
    max_tokens: int


def _resolve_run_params(
    cfg: Config,
    repetitions: int | None,
    temperature: float | None,
    top_p: float | None,
    max_tokens: int | None,
) -> _RunParams:
    """Merge caller overrides with RunConfig defaults."""
    return _RunParams(
        reps=repetitions or cfg.run.repetitions,
        temperature=temperature if temperature is not None else cfg.run.temperature,
        top_p=top_p if top_p is not None else cfg.run.top_p,
        max_tokens=max_tokens or cfg.run.max_tokens,
    )


async def _execute_batches(
    *,
    corpus: list[Prompt],
    model_aliases: list[str],
    experiment_name: str,
    cfg: Config,
    params: _RunParams,
    client: LLMClient,
    store: ResultStorage,
    concurrency: int,
) -> tuple[dict[str, list[LLMResponse]], dict[str, list[LLMResponse]]]:
    """Run every prompt x model batch concurrently and persist results.

    Returns ``(responses_by_model, responses_by_prompt)``.
    """
    all_responses: dict[str, list[LLMResponse]] = {a: [] for a in model_aliases}
    responses_by_prompt: dict[str, list[LLMResponse]] = {}
    storage_lock = asyncio.Lock()
    sem = asyncio.Semaphore(concurrency)
    total_batches = len(corpus) * len(model_aliases)
    completed = 0

    async def _run_batch(prompt: Prompt, alias: str) -> None:
        nonlocal completed
        spec = cfg.get_model(alias)
        async with sem:
            responses = await client.run_prompt(
                model_alias=alias,
                model_spec=spec,
                prompt_id=prompt.id,
                prompt_text=prompt.text,
                repetitions=params.reps,
                temperature=params.temperature,
                top_p=params.top_p,
                max_tokens=params.max_tokens,
            )

        async with storage_lock:
            store.save(experiment_name, responses)
            all_responses[alias].extend(responses)
            responses_by_prompt.setdefault(prompt.id, []).extend(responses)
            completed += 1
            logger.info(
                "[%d/%d] Finished prompt '%s' on %s (%d reps)",
                completed,
                total_batches,
                prompt.id,
                alias,
                params.reps,
            )

            error_count = sum(1 for r in responses if r.error)
            if error_count:
                logger.warning(
                    "%d/%d calls failed for prompt=%s model=%s",
                    error_count,
                    len(responses),
                    prompt.id,
                    alias,
                )

    tasks = [_run_batch(prompt, alias) for prompt in corpus for alias in model_aliases]
    await asyncio.gather(*tasks)
    return all_responses, responses_by_prompt


def _build_all_summaries(
    *,
    model_aliases: list[str],
    cfg: Config,
    all_responses: dict[str, list[LLMResponse]],
    responses_by_prompt: dict[str, list[LLMResponse]],
    product: ProductConfig,
    expected_outcomes_path: Path | None,
) -> tuple[list[ModelSummary], list[RecallByExpectation]]:
    """Score stored responses into per-model summaries plus recall stats."""
    misrepresentations: tuple[dict, ...] = ()
    recall_stats: list[RecallByExpectation] = []

    if expected_outcomes_path is not None and expected_outcomes_path.exists():
        outcomes = load_expected_outcomes(expected_outcomes_path)
        misrepresentations = outcomes.misrepresentations
        recall_stats = compute_recall_by_expectation(
            responses_by_prompt,
            outcomes.prompt_scenarios,
            outcomes.scenario_expectations,
            brand=product.brand,
        )
    else:
        logger.info(
            "No expected outcomes file at %s; misrepresentation and "
            "recall-by-expectation scoring skipped.",
            expected_outcomes_path,
        )

    summaries = [
        _build_summary(
            alias,
            cfg.get_model(alias),
            all_responses[alias],
            product,
            misrepresentations=misrepresentations or None,
        )
        for alias in model_aliases
    ]
    return summaries, recall_stats


async def run_experiment(
    *,
    corpus: list[Prompt],
    model_aliases: list[str],
    experiment_name: str,
    product: ProductConfig,
    config: Config | None = None,
    repetitions: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
    concurrency: int = 1,
    seed: int = 0,
    storage: ResultStorage | None = None,
    expected_outcomes_path: Path | None = _DEFAULT_EXPECTED_OUTCOMES,
) -> tuple[list[ModelSummary], list[RecallByExpectation]]:
    """Run all prompts x models x N repetitions and store results.

    Parameters
    ----------
    product:
        Product identity (brand, competitors) that all scoring threads
        through. Load it with :func:`geo.config.load_product_config`.
    concurrency:
        Maximum number of prompt/model batches to run in parallel.
        Set >1 for CLI-based providers where each call is a subprocess.
    seed:
        Seed for the deterministic mock provider (ignored by real providers).
    storage:
        Result storage override; defaults to the repo ``results/`` directory.
    expected_outcomes_path:
        Optional expected-outcomes file. When absent, misrepresentation and
        recall-by-expectation scoring are skipped.

    Returns a tuple of (per-model summaries, recall-by-expectation stats).
    """
    cfg = config or Config()
    params = _resolve_run_params(cfg, repetitions, temperature, top_p, max_tokens)

    logger.info(
        "Starting experiment '%s': %d prompts x %d models x %d reps = %d calls "
        "(concurrency=%d)",
        experiment_name,
        len(corpus),
        len(model_aliases),
        params.reps,
        len(corpus) * len(model_aliases) * params.reps,
        concurrency,
    )

    all_responses, responses_by_prompt = await _execute_batches(
        corpus=corpus,
        model_aliases=model_aliases,
        experiment_name=experiment_name,
        cfg=cfg,
        params=params,
        client=LLMClient(mock_responder=MockResponder(product=product, seed=seed)),
        store=storage or ResultStorage(),
        concurrency=concurrency,
    )

    return _build_all_summaries(
        model_aliases=model_aliases,
        cfg=cfg,
        all_responses=all_responses,
        responses_by_prompt=responses_by_prompt,
        product=product,
        expected_outcomes_path=expected_outcomes_path,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a GEO measurement experiment.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        required=True,
        help="Path to the prompt corpus JSON file.",
    )
    parser.add_argument(
        "--product",
        type=Path,
        default=Path("product.yaml"),
        help="Path to product.yaml (default: product.yaml in the current directory).",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help="Comma-separated model aliases (e.g. mock,claude,claude-api,chatgpt,gemini).",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=None,
        help="Number of repetitions per prompt-model pair (default: 20).",
    )
    parser.add_argument(
        "--experiment",
        type=str,
        default="default",
        help="Experiment name for result storage.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Sampling temperature.",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=None,
        help="Top-p (nucleus) sampling parameter.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Max output tokens.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit to first N prompts from the corpus (for testing).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Max parallel prompt batches (default: 1, set higher for CLI providers).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Seed for the deterministic mock provider (default: 0).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Use mock LLM responses (no API keys needed). Exercises full pipeline offline.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose logging.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point."""
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    product = load_product_config(args.product)
    corpus = load_corpus(
        args.corpus,
        expected_outcomes_path=_DEFAULT_EXPECTED_OUTCOMES,
    )

    if args.dry_run:
        model_aliases = ["mock"]
        print("[DRY RUN] Using mock LLM responses; no API keys required")
    elif args.models:
        model_aliases = [m.strip() for m in args.models.split(",")]
    else:
        print("Error: --models is required (or use --dry-run for offline testing)")
        sys.exit(2)

    if args.limit:
        corpus = corpus[: args.limit]
    logger.info("Loaded %d prompts from %s", len(corpus), args.corpus)
    logger.info("Product: %s (%s)", product.brand.name, product.category)
    logger.info("Models: %s", ", ".join(model_aliases))

    if not _DEFAULT_EXPECTED_OUTCOMES.exists():
        print(
            "No prompts/expected_outcomes.json found; misrepresentation and "
            "recall-by-expectation scoring skipped."
        )

    summaries, recall_stats = asyncio.run(
        run_experiment(
            corpus=corpus,
            model_aliases=model_aliases,
            experiment_name=args.experiment,
            product=product,
            repetitions=args.reps,
            temperature=args.temperature,
            top_p=args.top_p,
            max_tokens=args.max_tokens,
            concurrency=args.concurrency,
            seed=args.seed,
        )
    )

    print_report(summaries, recall_by_expectation=recall_stats)


if __name__ == "__main__":
    main()
