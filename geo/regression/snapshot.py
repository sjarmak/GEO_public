"""Immutable GEO metric snapshots for regression testing.

A :class:`Snapshot` is a frozen, JSON-serialisable record of every metric
the runner already produces (mention rate, share of voice, recall by
expectation level, competitor rates, misrep counts) plus the metadata
needed to identify *which* model version produced it. Snapshots are the
unit the regression detector consumes. Once written they are never
mutated, so two snapshots taken at different points in time can be
compared directly to spot drift introduced by a model update.

A snapshot can be built two ways:

* :func:`build_snapshot` from an in-memory list of ``LLMResponse``
  objects (used immediately after an experiment run).
* :func:`build_snapshot_from_storage` by reading previously saved
  results out of :class:`ResultStorage` (used by the CLI when the user
  wants to snapshot a historical run without re-executing it).

Brand and competitor identity comes from :class:`ProductConfig`
(``product.yaml``), never from constants.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from geo.config import Config, ProductConfig
from geo.llm_client import LLMResponse
from geo.runner import compute_recall_by_expectation, load_expected_outcomes
from geo.scoring import aggregate_scores
from geo.storage import ResultStorage

SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RecallEntry:
    """Recall rate for one expectation level."""

    level: str
    total_prompts: int
    total_responses: int
    mention_count: int
    recall_rate: float


@dataclass(frozen=True)
class Snapshot:
    """A point-in-time record of GEO metrics for one model version.

    The dataclass is frozen so callers cannot accidentally mutate a
    snapshot after it has been used as a baseline. All numeric fields
    align 1:1 with :class:`AggregateScores` so the detector can diff
    them positionally without a translation layer.
    """

    schema_version: int
    snapshot_id: str
    captured_at: str
    experiment_name: str
    model_alias: str
    model_id: str
    target_brand: str
    total_prompts: int
    total_responses: int
    error_count: int

    mention_rate: float
    share_of_voice: float
    avg_first_mention_offset: float | None
    avg_mention_count: float
    list_appearance_rate: float
    avg_list_rank: float | None

    competitor_mention_rates: dict[str, float] = field(default_factory=dict)
    misrepresentation_counts: dict[str, int] = field(default_factory=dict)
    recall_by_expectation: tuple[RecallEntry, ...] = field(default_factory=tuple)
    notes: str = ""

    def to_dict(self) -> dict:
        """Serialise to a JSON-friendly dict (recall entries inlined)."""
        d = asdict(self)
        d["recall_by_expectation"] = [asdict(r) for r in self.recall_by_expectation]
        return d


def _make_snapshot_id(model_alias: str, model_id: str, captured_at: str) -> str:
    """Stable per-snapshot identifier suitable for filenames."""
    safe_model = model_id.replace("/", "_").replace(":", "_")
    safe_ts = captured_at.replace(":", "").replace("-", "")
    return f"{model_alias}__{safe_model}__{safe_ts}"


def build_snapshot(
    *,
    experiment_name: str,
    model_alias: str,
    model_id: str,
    responses: Sequence[LLMResponse],
    product: ProductConfig,
    expected_outcomes_path: Path | None = None,
    notes: str = "",
    captured_at: str | None = None,
) -> Snapshot:
    """Compute aggregate metrics for *responses* and return a frozen Snapshot.

    Empty input is rejected. A snapshot with zero responses cannot
    serve as a meaningful baseline and would produce divide-by-zero
    surprises later.
    """
    if not responses:
        raise ValueError("Cannot build snapshot from an empty response list")

    successful = [r for r in responses if r.error is None]
    error_count = len(responses) - len(successful)
    if not successful:
        raise ValueError(
            f"All {len(responses)} responses errored; cannot build snapshot"
        )

    misrepresentations: list[dict] = []
    recall_entries: tuple[RecallEntry, ...] = ()

    if expected_outcomes_path is not None and expected_outcomes_path.exists():
        outcomes = load_expected_outcomes(expected_outcomes_path)
        misrepresentations = list(outcomes.misrepresentations)

        responses_by_prompt: dict[str, list[LLMResponse]] = {}
        for r in successful:
            responses_by_prompt.setdefault(r.prompt_id, []).append(r)

        recall_stats = compute_recall_by_expectation(
            responses_by_prompt,
            outcomes.prompt_scenarios,
            outcomes.scenario_expectations,
            brand=product.brand,
        )
        recall_entries = tuple(
            RecallEntry(
                level=r.level,
                total_prompts=r.total_prompts,
                total_responses=r.total_responses,
                mention_count=r.mention_count,
                recall_rate=r.recall_rate,
            )
            for r in recall_stats
        )

    scores = aggregate_scores(
        successful,
        brand=product.brand,
        competitors=product.competitors,
        misrepresentations=misrepresentations or None,
    )

    captured = captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot_id = _make_snapshot_id(model_alias, model_id, captured)

    unique_prompts = len({r.prompt_id for r in successful})

    return Snapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=snapshot_id,
        captured_at=captured,
        experiment_name=experiment_name,
        model_alias=model_alias,
        model_id=model_id,
        target_brand=product.brand.name,
        total_prompts=unique_prompts,
        total_responses=len(responses),
        error_count=error_count,
        mention_rate=scores.mention_rate,
        share_of_voice=scores.share_of_voice,
        avg_first_mention_offset=scores.avg_first_mention_offset,
        avg_mention_count=scores.avg_mention_count,
        list_appearance_rate=scores.list_appearance_rate,
        avg_list_rank=scores.avg_list_rank,
        competitor_mention_rates=dict(scores.competitor_mention_rates),
        misrepresentation_counts=dict(scores.misrepresentation_counts),
        recall_by_expectation=recall_entries,
        notes=notes,
    )


def build_snapshot_from_storage(
    *,
    experiment_name: str,
    model_alias: str,
    product: ProductConfig,
    storage: ResultStorage | None = None,
    config: Config | None = None,
    expected_outcomes_path: Path | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    notes: str = "",
) -> Snapshot:
    """Build a snapshot by re-reading stored experiment results.

    Useful when a baseline needs to be captured retroactively (e.g.
    treating an existing "baseline_v1" run as the reference point for
    future comparisons) without paying to re-run the whole corpus.
    """
    cfg = config or Config()
    storage = storage or ResultStorage()

    responses = storage.load(
        experiment_name=experiment_name,
        model_alias=model_alias,
        date_from=date_from,
        date_to=date_to,
    )
    if not responses:
        raise ValueError(
            f"No stored responses for experiment={experiment_name!r} "
            f"model={model_alias!r} in date range "
            f"[{date_from or 'start'} .. {date_to or 'end'}]"
        )

    spec = cfg.get_model(model_alias)
    return build_snapshot(
        experiment_name=experiment_name,
        model_alias=model_alias,
        model_id=spec.model_id,
        responses=responses,
        product=product,
        expected_outcomes_path=expected_outcomes_path,
        notes=notes,
    )


def save_snapshot(snapshot: Snapshot, dest: Path) -> Path:
    """Write *snapshot* as pretty-printed JSON to *dest*.

    If *dest* is an existing directory, or a not-yet-existing path with
    no ``.json`` suffix, the file is written as ``{snapshot_id}.json``
    inside it. Otherwise *dest* is treated as the literal file path.
    Returns the final file path.
    """
    if dest.is_dir() or (not dest.exists() and dest.suffix != ".json"):
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / f"{snapshot.snapshot_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(snapshot.to_dict(), fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    return dest


def load_snapshot(path: Path) -> Snapshot:
    """Load a previously-saved snapshot from disk.

    Raises ValueError if the file's schema version is newer than this
    module knows how to read. Callers should bail rather than silently
    accept fields they cannot interpret.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    schema = int(data.get("schema_version", 0))
    if schema > SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"Snapshot {path} has schema_version={schema} which is newer "
            f"than this module's max supported version "
            f"{SNAPSHOT_SCHEMA_VERSION}"
        )

    recall_entries = tuple(
        RecallEntry(**entry) for entry in data.pop("recall_by_expectation", [])
    )
    return Snapshot(recall_by_expectation=recall_entries, **data)
