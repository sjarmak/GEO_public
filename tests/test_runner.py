"""Tests for experiment runner: expected outcomes mapping and recall scoring."""

import json
from pathlib import Path

from tests.helpers import make_brand, make_response
from geo.llm_client import LLMResponse
from geo.runner import (
    compute_recall_by_expectation,
    load_corpus,
    load_expected_outcomes,
)

_BRAND = make_brand()


def _write_expected_outcomes(path: Path) -> None:
    """Write a minimal expected_outcomes.json for testing."""
    data = {
        "scenarios": [
            {
                "id": "exp-001",
                "situation": "Enterprise code search",
                "expectation": "strong_recommend",
                "example_prompts": ["q1", "q2"],
            },
            {
                "id": "exp-002",
                "situation": "Single repo search",
                "expectation": "neutral",
                "example_prompts": ["q3"],
            },
            {
                "id": "exp-003",
                "situation": "Security scanning",
                "expectation": "strong_recommend",
                "example_prompts": ["q2", "q4"],
            },
        ],
        "known_misrepresentations": {"items": []},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _write_corpus(path: Path) -> None:
    """Write a minimal corpus JSON for testing."""
    data = [
        {"id": "q1", "prompt": "Best enterprise code search?", "category": "search"},
        {"id": "q2", "prompt": "Cross-repo security scan?", "category": "security"},
        {"id": "q3", "prompt": "Search in one repo?", "category": "search"},
        {"id": "q4", "prompt": "Find vulnerable patterns?", "category": "security"},
        {"id": "q5", "prompt": "Unrelated question?", "category": "other"},
    ]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ---- load_expected_outcomes ----


class TestLoadExpectedOutcomes:
    def test_returns_correct_reverse_index(self, tmp_path: Path):
        eo_path = tmp_path / "expected_outcomes.json"
        _write_expected_outcomes(eo_path)

        outcomes = load_expected_outcomes(eo_path)
        prompt_scenarios = outcomes.prompt_scenarios

        # q1 appears in exp-001 only
        assert prompt_scenarios["q1"] == ["exp-001"]
        # q2 appears in exp-001 and exp-003
        assert sorted(prompt_scenarios["q2"]) == ["exp-001", "exp-003"]
        # q3 appears in exp-002 only
        assert prompt_scenarios["q3"] == ["exp-002"]

    def test_returns_correct_scenario_expectations(self, tmp_path: Path):
        eo_path = tmp_path / "expected_outcomes.json"
        _write_expected_outcomes(eo_path)

        outcomes = load_expected_outcomes(eo_path)
        scenario_expectations = outcomes.scenario_expectations

        assert scenario_expectations["exp-001"] == "strong_recommend"
        assert scenario_expectations["exp-002"] == "neutral"
        assert scenario_expectations["exp-003"] == "strong_recommend"

    def test_prompts_not_in_any_scenario_absent_from_index(self, tmp_path: Path):
        eo_path = tmp_path / "expected_outcomes.json"
        _write_expected_outcomes(eo_path)

        outcomes = load_expected_outcomes(eo_path)
        assert "q5" not in outcomes.prompt_scenarios


# ---- load_corpus ----


class TestLoadCorpus:
    def test_populates_expected_scenarios(self, tmp_path: Path):
        corpus_path = tmp_path / "corpus.json"
        eo_path = tmp_path / "expected_outcomes.json"
        _write_corpus(corpus_path)
        _write_expected_outcomes(eo_path)

        prompts = load_corpus(corpus_path, expected_outcomes_path=eo_path)

        by_id = {p.id: p for p in prompts}
        assert by_id["q1"].expected_scenarios == ("exp-001",)
        assert sorted(by_id["q2"].expected_scenarios) == ["exp-001", "exp-003"]
        assert by_id["q3"].expected_scenarios == ("exp-002",)

    def test_prompts_without_scenarios_get_empty_tuple(self, tmp_path: Path):
        corpus_path = tmp_path / "corpus.json"
        eo_path = tmp_path / "expected_outcomes.json"
        _write_corpus(corpus_path)
        _write_expected_outcomes(eo_path)

        prompts = load_corpus(corpus_path, expected_outcomes_path=eo_path)

        by_id = {p.id: p for p in prompts}
        assert by_id["q5"].expected_scenarios == ()

    def test_without_expected_outcomes_all_empty(self, tmp_path: Path):
        corpus_path = tmp_path / "corpus.json"
        _write_corpus(corpus_path)

        prompts = load_corpus(corpus_path)

        for p in prompts:
            assert p.expected_scenarios == ()

    def test_nonexistent_expected_outcomes_path(self, tmp_path: Path):
        corpus_path = tmp_path / "corpus.json"
        _write_corpus(corpus_path)
        missing_path = tmp_path / "does_not_exist.json"

        prompts = load_corpus(corpus_path, expected_outcomes_path=missing_path)

        for p in prompts:
            assert p.expected_scenarios == ()

    def test_accepts_prompt_or_text_field(self, tmp_path: Path):
        corpus_path = tmp_path / "corpus.json"
        data = [
            {"id": "a", "prompt": "Prompt-style entry", "category": "x"},
            {"id": "b", "text": "Text-style entry", "category": "x"},
        ]
        corpus_path.write_text(json.dumps(data))

        prompts = load_corpus(corpus_path)
        by_id = {p.id: p for p in prompts}
        assert by_id["a"].text == "Prompt-style entry"
        assert by_id["b"].text == "Text-style entry"


# ---- compute_recall_by_expectation ----


class TestComputeRecallByExpectation:
    def test_basic_recall_computation(self):
        prompt_scenarios = {
            "q1": ["exp-001"],
            "q2": ["exp-002"],
        }
        scenario_expectations = {
            "exp-001": "strong_recommend",
            "exp-002": "neutral",
        }
        responses_by_prompt = {
            "q1": [
                make_response("Try AcmeSearch for code search.", "q1"),
                make_response("Use CodeHound.", "q1"),
            ],
            "q2": [
                make_response("Just use grep.", "q2"),
                make_response("Just use grep.", "q2"),
            ],
        }

        results = compute_recall_by_expectation(
            responses_by_prompt, prompt_scenarios, scenario_expectations, _BRAND
        )

        by_level = {r.level: r for r in results}
        sr = by_level["strong_recommend"]
        assert sr.total_prompts == 1
        assert sr.total_responses == 2
        assert sr.mention_count == 1
        assert abs(sr.recall_rate - 0.5) < 0.01

        neutral = by_level["neutral"]
        assert neutral.total_prompts == 1
        assert neutral.total_responses == 2
        assert neutral.mention_count == 0
        assert neutral.recall_rate == 0.0

    def test_alias_mentions_count_toward_recall(self):
        prompt_scenarios = {"q1": ["exp-001"]}
        scenario_expectations = {"exp-001": "strong_recommend"}
        responses_by_prompt = {
            "q1": [make_response("Acme Search handles this well.", "q1")],
        }

        results = compute_recall_by_expectation(
            responses_by_prompt, prompt_scenarios, scenario_expectations, _BRAND
        )

        assert results[0].recall_rate == 1.0

    def test_multiple_prompts_same_level(self):
        prompt_scenarios = {
            "q1": ["exp-001"],
            "q2": ["exp-002"],
        }
        scenario_expectations = {
            "exp-001": "strong_recommend",
            "exp-002": "strong_recommend",
        }
        responses_by_prompt = {
            "q1": [make_response("AcmeSearch rocks!", "q1")],
            "q2": [make_response("Use AcmeSearch.", "q2")],
        }

        results = compute_recall_by_expectation(
            responses_by_prompt, prompt_scenarios, scenario_expectations, _BRAND
        )

        assert len(results) == 1
        r = results[0]
        assert r.level == "strong_recommend"
        assert r.total_prompts == 2
        assert r.total_responses == 2
        assert r.mention_count == 2
        assert r.recall_rate == 1.0

    def test_empty_responses(self):
        prompt_scenarios = {"q1": ["exp-001"]}
        scenario_expectations = {"exp-001": "strong_recommend"}
        responses_by_prompt: dict[str, list[LLMResponse]] = {}

        results = compute_recall_by_expectation(
            responses_by_prompt, prompt_scenarios, scenario_expectations, _BRAND
        )

        assert len(results) == 1
        assert results[0].total_responses == 0
        assert results[0].recall_rate == 0.0

    def test_no_scenarios_returns_empty(self):
        results = compute_recall_by_expectation(
            {"q1": [make_response("text", "q1")]}, {}, {}, _BRAND
        )
        assert results == []

    def test_prompt_in_multiple_scenarios_different_levels(self):
        prompt_scenarios = {
            "q1": ["exp-001", "exp-002"],
        }
        scenario_expectations = {
            "exp-001": "strong_recommend",
            "exp-002": "should_mention",
        }
        responses_by_prompt = {
            "q1": [make_response("AcmeSearch is great.", "q1")],
        }

        results = compute_recall_by_expectation(
            responses_by_prompt, prompt_scenarios, scenario_expectations, _BRAND
        )

        by_level = {r.level: r for r in results}
        # q1 appears under both levels
        assert by_level["strong_recommend"].total_prompts == 1
        assert by_level["strong_recommend"].recall_rate == 1.0
        assert by_level["should_mention"].total_prompts == 1
        assert by_level["should_mention"].recall_rate == 1.0
