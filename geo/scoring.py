"""Three-layer scoring framework for GEO experiments.

Layer 1 -- Binary Presence:  alias-aware detection of your brand.
Layer 2 -- Structural Prominence: position, counts, list rank, word share.
Layer 3 -- Semantic Quality:  LLM-as-judge (G-Eval style, opt-in).
Aggregate -- Share of Voice across a run.

All brand matching goes through :func:`geo.config.mention_pattern`, so a
mention of any configured alias counts the same as the canonical name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from geo.config import BrandSpec, mention_pattern
from geo.llm_client import LLMResponse

# ---------------------------------------------------------------------------
# Misrepresentation Detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MisrepresentationResult:
    """A detected misrepresentation in an LLM response."""

    misrep_id: str
    claim: str
    severity: str
    matched_patterns: tuple[str, ...]


def score_misrepresentations(
    text: str,
    misrepresentations: Sequence[dict],
) -> list[MisrepresentationResult]:
    """Check *text* against known misrepresentation detection patterns.

    The misrepresentation list is user-supplied via
    ``prompts/expected_outcomes.json`` (optional; scoring is skipped when the
    file is absent). Returns one result per misrepresentation with at least
    one case-insensitive pattern match.
    """
    results: list[MisrepresentationResult] = []
    for misrep in misrepresentations:
        patterns = misrep.get("detection_patterns", [])
        matched: list[str] = []
        for pattern in patterns:
            if re.search(re.escape(pattern), text, re.IGNORECASE):
                matched.append(pattern)
        if matched:
            results.append(
                MisrepresentationResult(
                    misrep_id=str(misrep.get("id", "unknown")),
                    claim=str(misrep.get("claim", "")),
                    severity=str(misrep.get("severity", "unknown")),
                    matched_patterns=tuple(matched),
                )
            )
    return results


# ---------------------------------------------------------------------------
# Layer 1 -- Binary Presence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BinaryPresenceResult:
    """Result of Layer 1 scoring."""

    mentioned: bool
    mention_count: int


def score_binary_presence(text: str, brand: BrandSpec) -> BinaryPresenceResult:
    """Check whether *brand* (name or any alias) appears in *text*."""
    matches = mention_pattern(brand).findall(text)
    return BinaryPresenceResult(
        mentioned=len(matches) > 0,
        mention_count=len(matches),
    )


# ---------------------------------------------------------------------------
# Layer 2 -- Structural Prominence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProminenceResult:
    """Result of Layer 2 scoring."""

    first_mention_offset: int | None  # char offset, None if absent
    mention_count: int
    appears_in_list: bool
    list_rank: int | None  # 1-based rank in a numbered list
    word_count_brand: int  # words in sentences mentioning brand
    word_count_total: int
    competitor_mentions: dict[str, int]  # competitor name -> mention count


def _count_brand_words(text: str, pattern: re.Pattern[str]) -> int:
    """Count words in sentences that match *pattern*."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    total = 0
    for sentence in sentences:
        if pattern.search(sentence):
            total += len(sentence.split())
    return total


def _detect_list_rank(
    text: str, pattern: re.Pattern[str]
) -> tuple[bool, int | None]:
    """Detect whether *pattern* matches inside a numbered or bulleted list.

    Returns (appears_in_list, rank). Rank is 1-based position in the
    first list where it appears, or ``None`` if not in a list.
    """
    # Numbered list items: "1. ...", "1) ..."
    list_items = re.findall(r"^[ \t]*(\d+)[.)]\s+(.+)$", text, re.MULTILINE)
    for ordinal, (_, content) in enumerate(list_items, start=1):
        if pattern.search(content):
            return True, ordinal

    # Bullet list items: "- ...", "* ..."
    bullet_items = re.findall(r"^[ \t]*[-*]\s+(.+)$", text, re.MULTILINE)
    for idx, content in enumerate(bullet_items, start=1):
        if pattern.search(content):
            return True, idx

    return False, None


def score_prominence(
    text: str,
    brand: BrandSpec,
    competitors: Sequence[BrandSpec] = (),
) -> ProminenceResult:
    """Compute structural prominence metrics for *brand* in *text*."""
    pattern = mention_pattern(brand)
    matches = list(pattern.finditer(text))

    first_offset = matches[0].start() if matches else None
    appears_in_list, list_rank = _detect_list_rank(text, pattern)

    comp_mentions: dict[str, int] = {}
    for comp in competitors:
        comp_mentions[comp.name] = len(mention_pattern(comp).findall(text))

    return ProminenceResult(
        first_mention_offset=first_offset,
        mention_count=len(matches),
        appears_in_list=appears_in_list,
        list_rank=list_rank,
        word_count_brand=_count_brand_words(text, pattern),
        word_count_total=len(text.split()),
        competitor_mentions=comp_mentions,
    )


# ---------------------------------------------------------------------------
# Layer 3 -- Semantic Quality (LLM-as-judge, opt-in)
# ---------------------------------------------------------------------------

_GEVAL_RUBRIC = """You are an expert evaluator assessing how an AI assistant \
describes {brand} in its response.

Evaluate the response on the following dimensions, each scored 1-5:

**Sentiment** (1-5):
  1 = Strongly negative about {brand}
  2 = Somewhat negative
  3 = Neutral
  4 = Somewhat positive
  5 = Strongly positive

**Accuracy** (1-5):
  1 = Major factual errors about {brand}
  2 = Several inaccuracies
  3 = Mostly accurate with minor issues
  4 = Accurate with negligible issues
  5 = Fully accurate

**Completeness** (1-5):
  1 = Barely mentions {brand} capabilities
  2 = Covers one capability
  3 = Covers a few key capabilities
  4 = Good coverage of core capabilities
  5 = Comprehensive coverage

**Competitive Framing**:
  Classify as exactly one of: "leader", "alternative", "afterthought"
  - "leader": presented as the top or recommended choice
  - "alternative": presented as one of several viable options
  - "afterthought": mentioned briefly or dismissively

Return your evaluation as JSON:
{{
  "sentiment": <int 1-5>,
  "accuracy": <int 1-5>,
  "completeness": <int 1-5>,
  "competitive_framing": "<leader|alternative|afterthought>"
}}

**Prompt given to the AI:**
{prompt}

**AI response to evaluate:**
{response}
"""


@dataclass(frozen=True)
class SemanticResult:
    """Result of Layer 3 scoring."""

    sentiment: int  # 1-5
    accuracy: int  # 1-5
    completeness: int  # 1-5
    competitive_framing: str  # "leader" | "alternative" | "afterthought"
    raw_judge_response: str  # full text from the judge LLM


async def score_semantic(
    prompt_text: str,
    response_text: str,
    brand: BrandSpec,
    judge_model_id: str,
) -> SemanticResult:
    """Score semantic quality using an LLM-as-judge (G-Eval style).

    Opt-in and billable: requires ``OPENAI_API_KEY``. The judge model comes
    from ``scoring.semantic_judge_model`` in product.yaml. Uses OpenAI for
    the judge to avoid self-evaluation bias.

    Raises
    ------
    RuntimeError
        If the judge response cannot be parsed.
    """
    import json
    import os

    import openai

    rubric = _GEVAL_RUBRIC.format(
        brand=brand.name,
        prompt=prompt_text,
        response=response_text,
    )

    client = openai.AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    judge_response = await client.chat.completions.create(
        model=judge_model_id,
        temperature=0.0,
        max_tokens=512,
        messages=[{"role": "user", "content": rubric}],
    )
    raw = judge_response.choices[0].message.content or ""

    # Parse JSON from the judge response (tolerant of markdown fences)
    json_match = re.search(r"\{[^}]+\}", raw, re.DOTALL)
    if not json_match:
        raise RuntimeError(f"Could not parse judge JSON from response: {raw!r}")
    data = json.loads(json_match.group())

    return SemanticResult(
        sentiment=int(data["sentiment"]),
        accuracy=int(data["accuracy"]),
        completeness=int(data["completeness"]),
        competitive_framing=str(data["competitive_framing"]),
        raw_judge_response=raw,
    )


# ---------------------------------------------------------------------------
# Aggregate scoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AggregateScores:
    """Aggregate scores across a batch of responses."""

    total_responses: int
    mention_rate: float  # fraction of responses mentioning brand
    share_of_voice: float  # brand mentions / total brand mentions
    avg_first_mention_offset: float | None
    avg_mention_count: float
    list_appearance_rate: float  # fraction appearing in a list
    avg_list_rank: float | None
    competitor_mention_rates: dict[str, float]  # keyed by competitor name
    misrepresentation_counts: dict[str, int] = field(default_factory=dict)


def aggregate_scores(
    responses: Sequence[LLMResponse],
    brand: BrandSpec,
    competitors: Sequence[BrandSpec] = (),
    misrepresentations: Sequence[dict] | None = None,
) -> AggregateScores:
    """Compute aggregate scoring metrics across a batch of responses."""
    if not responses:
        raise ValueError("Cannot aggregate empty response list")

    n = len(responses)
    mentioned_count = 0
    total_brand_mentions = 0
    total_all_mentions = 0
    first_offsets: list[int] = []
    mention_counts: list[int] = []
    list_appearances = 0
    list_ranks: list[int] = []
    comp_totals: dict[str, int] = {c.name: 0 for c in competitors}
    comp_mentioned_count: dict[str, int] = {c.name: 0 for c in competitors}
    misrep_counts: dict[str, int] = {}

    for resp in responses:
        text = resp.response_text
        bp = score_binary_presence(text, brand)
        prom = score_prominence(text, brand, competitors)

        if bp.mentioned:
            mentioned_count += 1
        mention_counts.append(bp.mention_count)
        total_brand_mentions += bp.mention_count

        if prom.first_mention_offset is not None:
            first_offsets.append(prom.first_mention_offset)
        if prom.appears_in_list:
            list_appearances += 1
        if prom.list_rank is not None:
            list_ranks.append(prom.list_rank)

        for comp_name, cnt in prom.competitor_mentions.items():
            comp_totals[comp_name] += cnt
            total_all_mentions += cnt
            if cnt > 0:
                comp_mentioned_count[comp_name] += 1

        if misrepresentations:
            detected = score_misrepresentations(text, misrepresentations)
            for mr in detected:
                misrep_counts[mr.misrep_id] = misrep_counts.get(mr.misrep_id, 0) + 1

    total_all_mentions += total_brand_mentions

    sov = total_brand_mentions / total_all_mentions if total_all_mentions > 0 else 0.0
    avg_offset = sum(first_offsets) / len(first_offsets) if first_offsets else None
    avg_list_rank = sum(list_ranks) / len(list_ranks) if list_ranks else None

    comp_rates: dict[str, float] = {
        c.name: comp_mentioned_count[c.name] / n for c in competitors
    }

    return AggregateScores(
        total_responses=n,
        mention_rate=mentioned_count / n,
        share_of_voice=sov,
        avg_first_mention_offset=avg_offset,
        avg_mention_count=sum(mention_counts) / n,
        list_appearance_rate=list_appearances / n,
        avg_list_rank=avg_list_rank,
        competitor_mention_rates=comp_rates,
        misrepresentation_counts=misrep_counts,
    )
