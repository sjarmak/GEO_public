"""Tests for the power-analysis utilities (offline)."""

from __future__ import annotations

import math

from tests.helpers import make_brand
from geo.power_analysis import (
    PromptStats,
    compute_prompt_stats,
    decompose_variance,
    label_responses,
    recommend_sample_size,
)

_BRAND = make_brand()


def _row(pid: str, text: str, *, category: str = "category_search") -> dict:
    return {
        "prompt_id": pid,
        "response_text": text,
        "category": category,
        "error": None,
    }


def test_label_responses_mention_rule() -> None:
    rows = [
        _row("p1", "AcmeSearch is best for code search."),
        _row("p1", "Try ripgrep, ag, or grep."),
        _row("p2", "AcmeSearch and CodeHound."),
    ]
    labels = label_responses(rows, brand=_BRAND)
    assert labels["p1"] == [(1, "category_search"), (0, "category_search")]
    assert labels["p2"] == [(1, "category_search")]


def test_label_responses_alias_counts_as_mention() -> None:
    rows = [_row("p1", "Acme Search handles multi-repo work.")]
    labels = label_responses(rows, brand=_BRAND)
    assert labels["p1"] == [(1, "category_search")]


def test_label_responses_uses_category_lookup() -> None:
    rows = [_row("p1", "AcmeSearch is great.", category="uncategorized")]
    labels = label_responses(
        rows,
        brand=_BRAND,
        category_lookup={"p1": "category_search"},
    )
    # The lookup overrides the (default/missing) category in the row
    assert labels["p1"][0][1] == "category_search"


def test_compute_prompt_stats_and_decompose_zero_variance() -> None:
    # All reps for both prompts label 1: variance terms collapse
    stats = compute_prompt_stats(
        {"p1": [(1, "a"), (1, "a")], "p2": [(1, "b"), (1, "b")]}
    )
    d = decompose_variance("mention", stats)
    assert d.aggregate_rate == 1.0
    assert d.mean_within_variance == 0.0
    assert d.between_prompt_variance == 0.0


def test_compute_prompt_stats_and_decompose_mixed() -> None:
    # p1: half-and-half (within variance maxed), p2: always positive (within variance 0)
    stats = compute_prompt_stats({
        "p1": [(1, "a"), (0, "a"), (1, "a"), (0, "a")],
        "p2": [(1, "b"), (1, "b"), (1, "b"), (1, "b")],
    })
    d = decompose_variance("mention", stats)
    # 6 of 8 calls positive: aggregate = 0.75
    assert math.isclose(d.aggregate_rate, 6 / 8)
    # mean within variance = (0.5*0.5 + 1.0*0.0)/2 = 0.125
    assert math.isclose(d.mean_within_variance, 0.125)
    # between variance: variance of (0.5, 1.0) with sample-variance formula
    expected_between = ((0.5 - 0.75) ** 2 + (1.0 - 0.75) ** 2) / 1
    assert math.isclose(d.between_prompt_variance, expected_between)


def test_recommend_sample_size_handles_high_between_variance() -> None:
    # Build a dataset where between-prompt variance dominates within
    stats = [
        PromptStats(prompt_id=f"p{i}", category="c", n_reps=5,
                    n_positive=(5 if i < 2 else 0), p_hat=(1.0 if i < 2 else 0.0))
        for i in range(4)
    ]
    d = decompose_variance("mention", stats)
    assert d.between_prompt_variance > 0
    assert d.mean_within_variance == 0.0

    rec = recommend_sample_size(d, target_delta=0.05, fixed_prompts=4)
    # When between variance already exceeds the budget at the corpus size,
    # adding reps cannot solve it; suggested_reps is flagged with -1.
    assert rec.suggested_reps == -1
    assert rec.required_total_calls == -1


def test_recommend_sample_size_returns_positive_reps_when_within_dominates() -> None:
    # Constant rate across prompts: between variance is 0, within dominates
    stats = compute_prompt_stats({
        f"p{i}": [(1, "c"), (0, "c"), (1, "c"), (0, "c")]
        for i in range(10)
    })
    d = decompose_variance("mention", stats)
    assert math.isclose(d.between_prompt_variance, 0.0, abs_tol=1e-12)

    rec = recommend_sample_size(d, target_delta=0.05, fixed_prompts=300)
    assert rec.suggested_reps > 0
    # i.i.d. lower bound check: total calls / arm >= i.i.d. requirement
    total_calls = rec.suggested_prompts * rec.suggested_reps
    assert total_calls >= rec.required_n_iid
