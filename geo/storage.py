"""Results storage for GEO experiments.

Saves and loads raw results as JSON-lines files organised by
experiment / model / date.

Directory layout::

    results/raw/{experiment_name}/{model_alias}/{YYYY-MM-DD}/results.jsonl
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from geo.llm_client import LLMResponse

_DEFAULT_BASE = Path(__file__).resolve().parents[1] / "results" / "raw"


class ResultStorage:
    """Append-only JSON-lines storage for experiment results."""

    def __init__(self, base_path: Path | str | None = None) -> None:
        self._base = Path(base_path) if base_path else _DEFAULT_BASE

    # -- writing -----------------------------------------------------------

    def save(
        self,
        experiment_name: str,
        responses: Sequence[LLMResponse],
    ) -> Path:
        """Append *responses* as JSON-lines to the appropriate file.

        Returns the path of the file written to.
        """
        if not responses:
            raise ValueError("Cannot save an empty response list")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Group by model alias so each goes to its own directory
        by_model: dict[str, list[LLMResponse]] = {}
        for resp in responses:
            by_model.setdefault(resp.model_alias, []).append(resp)

        last_path = Path()
        for alias, resps in by_model.items():
            dir_path = self._base / experiment_name / alias / today
            dir_path.mkdir(parents=True, exist_ok=True)
            file_path = dir_path / "results.jsonl"
            with open(file_path, "a", encoding="utf-8") as fh:
                for resp in resps:
                    line = json.dumps(asdict(resp), ensure_ascii=False)
                    fh.write(line + "\n")
            last_path = file_path
        return last_path

    # -- reading -----------------------------------------------------------

    def load(
        self,
        experiment_name: str,
        model_alias: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[LLMResponse]:
        """Load results, optionally filtered by model and date range.

        Parameters
        ----------
        experiment_name:
            Name of the experiment directory.
        model_alias:
            If provided, only load results for this model.
        date_from:
            Inclusive lower bound as ``YYYY-MM-DD``. ``None`` means no lower
            bound.
        date_to:
            Inclusive upper bound as ``YYYY-MM-DD``. ``None`` means no upper
            bound.
        """
        exp_dir = self._base / experiment_name
        if not exp_dir.exists():
            return []

        results: list[LLMResponse] = []
        model_dirs = (
            [exp_dir / model_alias] if model_alias else sorted(exp_dir.iterdir())
        )

        for model_dir in model_dirs:
            if not model_dir.is_dir():
                continue
            for date_dir in sorted(model_dir.iterdir()):
                if not date_dir.is_dir():
                    continue
                date_str = date_dir.name
                if date_from and date_str < date_from:
                    continue
                if date_to and date_str > date_to:
                    continue
                jsonl = date_dir / "results.jsonl"
                if not jsonl.exists():
                    continue
                with open(jsonl, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        results.append(LLMResponse(**data))
        return results

    def list_experiments(self) -> list[str]:
        """Return names of all experiments that have stored results."""
        if not self._base.exists():
            return []
        return sorted(d.name for d in self._base.iterdir() if d.is_dir())

    def list_dates(
        self,
        experiment_name: str,
        model_alias: str,
    ) -> list[str]:
        """Return sorted list of date strings with results."""
        model_dir = self._base / experiment_name / model_alias
        if not model_dir.exists():
            return []
        return sorted(
            d.name
            for d in model_dir.iterdir()
            if d.is_dir() and (d / "results.jsonl").exists()
        )
