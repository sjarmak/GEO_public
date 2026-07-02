"""Tests for the three-layer scoring framework (offline, no API keys)."""

from tests.helpers import make_brand, make_competitors, make_response
from geo.config import BrandSpec
from geo.scoring import (
    aggregate_scores,
    score_binary_presence,
    score_misrepresentations,
    score_prominence,
)

_BRAND = make_brand()
_COMPETITORS = make_competitors()

# ---- Layer 1: Binary Presence ----


class TestBinaryPresence:
    def test_mentioned(self):
        result = score_binary_presence("Try AcmeSearch for code search.", _BRAND)
        assert result.mentioned is True
        assert result.mention_count == 1

    def test_not_mentioned(self):
        result = score_binary_presence("Use CodeHound instead.", _BRAND)
        assert result.mentioned is False
        assert result.mention_count == 0

    def test_case_insensitive(self):
        result = score_binary_presence(
            "I recommend ACMESEARCH and acmesearch.", _BRAND
        )
        assert result.mentioned is True
        assert result.mention_count == 2

    def test_multiple_mentions(self):
        result = score_binary_presence(
            "AcmeSearch is great. AcmeSearch offers code search. "
            "Use AcmeSearch for navigation.",
            _BRAND,
        )
        assert result.mention_count == 3

    def test_alias_counts_as_mention(self):
        result = score_binary_presence(
            "Acme Search is popular; see acmesearch.io for pricing.", _BRAND
        )
        assert result.mentioned is True
        assert result.mention_count == 2

    def test_word_boundary_no_partial_match(self):
        result = score_binary_presence(
            "AcmeSearcher is a different product.", _BRAND
        )
        assert result.mentioned is False

    def test_empty_text(self):
        result = score_binary_presence("", _BRAND)
        assert result.mentioned is False
        assert result.mention_count == 0


# ---- Layer 2: Structural Prominence ----


class TestProminence:
    def test_first_mention_offset(self):
        result = score_prominence("Hello world. AcmeSearch is great.", _BRAND)
        assert result.first_mention_offset is not None
        assert result.first_mention_offset == 13  # after "Hello world. "

    def test_no_mention(self):
        result = score_prominence("Use CodeHound.", _BRAND)
        assert result.first_mention_offset is None
        assert result.mention_count == 0

    def test_numbered_list_rank(self):
        text = (
            "Top code search tools:\n"
            "1. CodeHound\n"
            "2. AcmeSearch\n"
            "3. FindGrep\n"
        )
        result = score_prominence(text, _BRAND)
        assert result.appears_in_list is True
        assert result.list_rank == 2

    def test_bullet_list_rank(self):
        text = "Options:\n- AcmeSearch\n- CodeHound\n- SearchLite\n"
        result = score_prominence(text, _BRAND)
        assert result.appears_in_list is True
        assert result.list_rank == 1

    def test_alias_in_list(self):
        text = "Options:\n1. CodeHound\n2. Acme Search\n"
        result = score_prominence(text, _BRAND)
        assert result.appears_in_list is True
        assert result.list_rank == 2

    def test_not_in_list(self):
        result = score_prominence("AcmeSearch is a code search tool.", _BRAND)
        assert result.appears_in_list is False
        assert result.list_rank is None

    def test_competitor_mentions(self):
        text = "Compare AcmeSearch, CodeHound, and FindGrep."
        result = score_prominence(text, _BRAND, _COMPETITORS)
        assert result.competitor_mentions["CodeHound"] == 1
        assert result.competitor_mentions["FindGrep"] == 1
        assert result.competitor_mentions["SearchLite"] == 0

    def test_competitor_alias_counts(self):
        text = "Check codehound.dev for their docs."
        result = score_prominence(text, _BRAND, _COMPETITORS)
        assert result.competitor_mentions["CodeHound"] == 1

    def test_word_count_brand(self):
        text = "AcmeSearch is excellent. Nothing else matters."
        result = score_prominence(text, _BRAND)
        assert result.word_count_brand > 0
        assert result.word_count_total > result.word_count_brand


# ---- Aggregate Scores ----


class TestAggregateScores:
    def test_all_mention(self):
        responses = [
            make_response("AcmeSearch is great."),
            make_response("I recommend AcmeSearch."),
            make_response("Use Acme Search for search."),
        ]
        scores = aggregate_scores(responses, _BRAND, _COMPETITORS)
        assert scores.mention_rate == 1.0
        assert scores.total_responses == 3

    def test_partial_mention(self):
        responses = [
            make_response("AcmeSearch is great."),
            make_response("Use CodeHound."),
            make_response("Try FindGrep."),
        ]
        scores = aggregate_scores(responses, _BRAND, _COMPETITORS)
        assert abs(scores.mention_rate - 1 / 3) < 0.01

    def test_share_of_voice(self):
        responses = [
            make_response("1. AcmeSearch\n2. CodeHound\n3. FindGrep"),
        ]
        scores = aggregate_scores(responses, _BRAND, _COMPETITORS)
        assert scores.share_of_voice > 0
        assert scores.share_of_voice < 1.0

    def test_no_mentions_anywhere(self):
        responses = [make_response("Just use grep.")]
        scores = aggregate_scores(responses, _BRAND, _COMPETITORS)
        assert scores.mention_rate == 0.0
        assert scores.share_of_voice == 0.0

    def test_competitor_rates_keyed_by_name(self):
        responses = [
            make_response("CodeHound is the best."),
            make_response("CodeHound and FindGrep."),
        ]
        scores = aggregate_scores(responses, _BRAND, _COMPETITORS)
        assert scores.competitor_mention_rates["CodeHound"] == 1.0
        assert scores.competitor_mention_rates["FindGrep"] == 0.5
        assert scores.competitor_mention_rates["SearchLite"] == 0.0

    def test_no_competitors(self):
        responses = [make_response("AcmeSearch is great.")]
        scores = aggregate_scores(responses, BrandSpec(name="AcmeSearch"))
        assert scores.competitor_mention_rates == {}
        assert scores.share_of_voice == 1.0


# ---- Misrepresentation Detection ----

_SAMPLE_MISREPS = [
    {
        "id": "misrep-001",
        "claim": "AcmePilot is AcmeSearch's flagship product",
        "severity": "high",
        "detection_patterns": [
            "AcmePilot",
            "AcmeSearch's AI assistant",
            "AcmeSearch's AI coding",
        ],
    },
    {
        "id": "misrep-002",
        "claim": "AcmeSearch was acquired by another company",
        "severity": "high",
        "detection_patterns": ["acquired by", "bought by", "merged with"],
    },
]


class TestMisrepresentationDetection:
    def test_detects_pattern(self):
        results = score_misrepresentations(
            "AcmePilot is their AI coding assistant.",
            _SAMPLE_MISREPS,
        )
        assert len(results) == 1
        assert results[0].misrep_id == "misrep-001"
        assert "AcmePilot" in results[0].matched_patterns

    def test_returns_empty_when_no_match(self):
        results = score_misrepresentations(
            "AcmeSearch is a great code search tool.",
            _SAMPLE_MISREPS,
        )
        assert results == []

    def test_case_insensitive_matching(self):
        results = score_misrepresentations(
            "AcmeSearch was ACQUIRED BY a larger company.",
            _SAMPLE_MISREPS,
        )
        assert len(results) == 1
        assert results[0].misrep_id == "misrep-002"
        assert "acquired by" in results[0].matched_patterns

    def test_multiple_misreps_detected(self):
        results = score_misrepresentations(
            "AcmePilot is great. They were acquired by a bigger vendor.",
            _SAMPLE_MISREPS,
        )
        ids = {r.misrep_id for r in results}
        assert ids == {"misrep-001", "misrep-002"}

    def test_empty_detection_patterns_skipped(self):
        misreps_with_empty = [
            {
                "id": "misrep-006",
                "claim": "AcmeSearch is just a code search tool",
                "severity": "low",
                "detection_patterns": [],
            },
        ]
        results = score_misrepresentations(
            "AcmeSearch is just a code search tool.",
            misreps_with_empty,
        )
        assert results == []


class TestAggregateMisrepresentationCounts:
    def test_aggregate_counts_misrepresentations(self):
        responses = [
            make_response("AcmePilot is an AI assistant."),
            make_response("AcmeSearch offers code search."),
            make_response("Try AcmePilot for AI coding."),
        ]
        scores = aggregate_scores(
            responses,
            _BRAND,
            _COMPETITORS,
            misrepresentations=_SAMPLE_MISREPS,
        )
        assert scores.misrepresentation_counts.get("misrep-001") == 2
        assert "misrep-002" not in scores.misrepresentation_counts

    def test_aggregate_no_misrepresentations_param(self):
        # Absent misrepresentation list: scoring is skipped cleanly.
        responses = [make_response("AcmePilot is great.")]
        scores = aggregate_scores(responses, _BRAND, _COMPETITORS)
        assert scores.misrepresentation_counts == {}
