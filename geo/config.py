"""Configuration for the GEO experiment harness.

Two kinds of configuration live here:

* Product identity (:class:`ProductConfig`), loaded from ``product.yaml``.
  This is the one file users edit to point the harness at their product.
* Model registry and run parameters (:class:`ModelSpec`, :class:`RunConfig`,
  :class:`Config`), which rarely need editing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml

# ---------------------------------------------------------------------------
# Product identity (loaded from product.yaml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrandSpec:
    """A brand name plus alternate spellings that count as a mention."""

    name: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("BrandSpec.name must be a non-empty string")


def _bounded(term: str) -> str:
    """Escape a term and add word boundaries only where they can match.

    ``\\b`` requires a word character on the term side of the boundary, so a
    term that starts or ends with punctuation (``C++``, ``.NET``) would never
    match if wrapped in ``\\b`` unconditionally. On a punctuation side we use
    a lookaround that forbids an adjacent word character instead.
    """
    left = r"\b" if re.match(r"\w", term) else r"(?<!\w)"
    right = r"\b" if re.search(r"\w$", term) else r"(?!\w)"
    return f"{left}{re.escape(term)}{right}"


@lru_cache(maxsize=None)
def mention_pattern(spec: BrandSpec) -> re.Pattern[str]:
    """Compile a word-boundary, case-insensitive pattern for a brand.

    Matches the brand name or any alias as a whole word. Terms that start or
    end with non-word characters (``C++``, ``.NET``) are bounded by
    lookarounds instead of ``\\b`` so they still match next to whitespace
    and punctuation.
    """
    terms = sorted({spec.name, *spec.aliases}, key=len, reverse=True)
    joined = "|".join(_bounded(t) for t in terms if t.strip())
    return re.compile(rf"(?:{joined})", re.IGNORECASE)


@dataclass(frozen=True)
class ProductConfig:
    """Product identity for a run: your brand, its competitors, its category."""

    brand: BrandSpec
    competitors: tuple[BrandSpec, ...]
    category: str
    semantic_judge_model: str = "gpt-4o"


def _parse_brand(raw: object, label: str) -> BrandSpec:
    """Build a BrandSpec from one YAML mapping, failing fast on bad shape."""
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a mapping with a 'name' key")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError(f"{label}.name must be a non-empty string")
    aliases_raw = raw.get("aliases") or []
    if not isinstance(aliases_raw, list):
        raise ValueError(f"{label}.aliases must be a list of strings")
    aliases = tuple(str(a).strip() for a in aliases_raw if str(a).strip())
    return BrandSpec(name=name, aliases=aliases)


def load_product_config(path: Path = Path("product.yaml")) -> ProductConfig:
    """Load and validate ``product.yaml``.

    Fails fast with a clear message on a missing file, missing keys, or an
    empty product name. Scoring settings may default; product identity may not.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Product config not found at '{path}'. Run from the repo root, "
            "or pass --product with the path to your product.yaml."
        )
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"'{path}' must contain a YAML mapping at the top level")

    product_raw = data.get("product")
    if product_raw is None:
        raise ValueError(f"'{path}' is missing the required 'product' section")
    brand = _parse_brand(product_raw, "product")

    category = str(product_raw.get("category") or "").strip()
    if not category:
        raise ValueError(f"'{path}': product.category must be a non-empty string")

    competitors_raw = data.get("competitors")
    if competitors_raw is None:
        raise ValueError(
            f"'{path}' is missing the required 'competitors' section "
            "(use an empty list if you track no competitors)"
        )
    if not isinstance(competitors_raw, list):
        raise ValueError(f"'{path}': competitors must be a list")
    competitors = tuple(
        _parse_brand(item, f"competitors[{i}]")
        for i, item in enumerate(competitors_raw)
    )

    scoring_raw = data.get("scoring") or {}
    if not isinstance(scoring_raw, dict):
        raise ValueError(f"'{path}': scoring must be a mapping")
    judge_model = str(scoring_raw.get("semantic_judge_model") or "gpt-4o").strip()

    return ProductConfig(
        brand=brand,
        competitors=competitors,
        category=category,
        semantic_judge_model=judge_model,
    )


# ---------------------------------------------------------------------------
# Model specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """Immutable specification for a single LLM model."""

    provider: str  # "anthropic" | "openai" | "google" | "claude_cli" | "mock"
    model_id: str  # version-pinned model string sent to the API
    display_name: str  # human-friendly label
    requests_per_minute: int = 60
    tokens_per_minute: int = 100_000
    max_output_tokens: int = 4096


# Version-pinned model registry.
#
# The default ``claude`` alias uses the OAuth-bound Claude Code CLI
# (provider=``claude_cli``) so experiments do not require ANTHROPIC_API_KEY.
# Use ``claude-api`` to run through the Anthropic Messages API instead
# (requires ANTHROPIC_API_KEY).
MODEL_REGISTRY: Final[dict[str, ModelSpec]] = {
    "claude": ModelSpec(
        provider="claude_cli",
        model_id="claude-sonnet-4-20250514",
        display_name="Claude Sonnet 4 (CLI/OAuth)",
        requests_per_minute=20,
        tokens_per_minute=80_000,
        max_output_tokens=8192,
    ),
    "claude-api": ModelSpec(
        provider="anthropic",
        model_id="claude-sonnet-4-20250514",
        display_name="Claude Sonnet 4 (API key)",
        requests_per_minute=50,
        tokens_per_minute=80_000,
        max_output_tokens=8192,
    ),
    "chatgpt": ModelSpec(
        provider="openai",
        model_id="gpt-4o-2024-08-06",
        display_name="GPT-4o (2024-08-06)",
        requests_per_minute=60,
        tokens_per_minute=150_000,
        max_output_tokens=4096,
    ),
    "gemini": ModelSpec(
        provider="google",
        model_id="gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        requests_per_minute=60,
        tokens_per_minute=120_000,
        max_output_tokens=8192,
    ),
    "mock": ModelSpec(
        provider="mock",
        model_id="mock-v1",
        display_name="Mock (offline testing)",
        requests_per_minute=9999,
        tokens_per_minute=999_999,
        max_output_tokens=4096,
    ),
}


# ---------------------------------------------------------------------------
# Run configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunConfig:
    """Immutable configuration for a single experiment run."""

    repetitions: int = 20
    temperature: float = 1.0
    top_p: float = 1.0
    max_tokens: int = 2048
    enable_semantic_scoring: bool = False


@dataclass(frozen=True)
class Config:
    """Root configuration for models and run parameters."""

    models: dict[str, ModelSpec] = field(default_factory=lambda: dict(MODEL_REGISTRY))
    run: RunConfig = field(default_factory=RunConfig)

    def get_model(self, alias: str) -> ModelSpec:
        """Look up a model by its short alias.

        Raises KeyError with the available aliases if not found.
        """
        if alias not in self.models:
            available = ", ".join(sorted(self.models))
            raise KeyError(f"Unknown model alias '{alias}'. Available: {available}")
        return self.models[alias]
