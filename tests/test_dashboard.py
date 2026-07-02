"""Tests for the dashboard module (offline, no API keys)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers import make_product, make_response
from geo.dashboard import (
    ResponseDetail,
    _parse_args,
    build_summary,
    generate_html,
    write_html,
    write_json,
)
from geo.llm_client import LLMResponse

_PRODUCT = make_product()

_SAMPLE_MISREPS = {
    "scenarios": [],
    "known_misrepresentations": {
        "items": [
            {
                "id": "misrep-001",
                "claim": "AcmePilot is AcmeSearch's flagship product",
                "severity": "high",
                "detection_patterns": ["AcmePilot"],
            },
            {
                "id": "misrep-002",
                "claim": "AcmeSearch was acquired by another company",
                "severity": "high",
                "detection_patterns": ["acquired by"],
            },
        ]
    },
}


def _make_errored(prompt_id: str = "q1") -> LLMResponse:
    return LLMResponse(
        model_alias="mock",
        model_id="mock-v1",
        provider="mock",
        timestamp="2026-03-31T00:00:00Z",
        prompt_id=prompt_id,
        prompt_text="test prompt",
        response_text="",
        temperature=1.0,
        top_p=1.0,
        max_tokens=2048,
        repetition=1,
        latency_ms=10.0,
        error="API timeout",
    )


def _write_misreps(tmp_path: Path) -> Path:
    path = tmp_path / "expected_outcomes.json"
    path.write_text(json.dumps(_SAMPLE_MISREPS))
    return path


# ---------------------------------------------------------------------------
# JSON summary structure
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_summary_has_all_keys(self) -> None:
        responses = [
            make_response("AcmeSearch is great for code search."),
            make_response("Use CodeHound instead."),
        ]
        summary = build_summary("test_exp", responses, _PRODUCT)

        assert summary.experiment == "test_exp"
        assert summary.brand_name == "AcmeSearch"
        assert summary.generated_at  # non-empty string
        assert summary.total_responses == 2
        assert isinstance(summary.models, list)
        assert len(summary.models) == 1
        assert 0.0 <= summary.overall_mention_rate <= 1.0
        assert 0.0 <= summary.overall_share_of_voice <= 1.0
        assert isinstance(summary.competitor_comparison, dict)
        assert isinstance(summary.misrepresentation_totals, dict)
        assert isinstance(summary.recall_by_expectation, list)

    def test_empty_responses(self) -> None:
        summary = build_summary("empty_exp", [], _PRODUCT)
        assert summary.total_responses == 0
        assert summary.models == []
        assert summary.overall_mention_rate == 0.0
        assert summary.overall_share_of_voice == 0.0

    def test_multiple_models(self) -> None:
        responses = [
            make_response("AcmeSearch rocks.", model_alias="claude"),
            make_response("AcmeSearch is nice.", model_alias="chatgpt"),
            make_response("Use CodeHound.", model_alias="chatgpt"),
        ]
        summary = build_summary("multi_model", responses, _PRODUCT)
        assert summary.total_responses == 3
        aliases = {m.model_alias for m in summary.models}
        assert aliases == {"claude", "chatgpt"}

    def test_mention_rate_correctness(self) -> None:
        responses = [
            make_response("AcmeSearch is great."),
            make_response("Acme Search is awesome."),
            make_response("Use grep instead."),
            make_response("Try FindGrep."),
        ]
        summary = build_summary("rate_test", responses, _PRODUCT)
        assert abs(summary.overall_mention_rate - 0.5) < 0.01

    def test_misrepresentation_detection_with_user_file(self, tmp_path: Path) -> None:
        eo_path = _write_misreps(tmp_path)
        responses = [
            make_response("AcmePilot is their AI coding assistant."),
            make_response("AcmeSearch was acquired by a big vendor."),
            make_response("AcmeSearch is a code search tool."),
        ]
        summary = build_summary(
            "misrep_test", responses, _PRODUCT, expected_outcomes_path=eo_path
        )
        assert summary.misrepresentation_scoring_enabled is True
        assert "misrep-001" in summary.misrepresentation_totals
        assert "misrep-002" in summary.misrepresentation_totals

    def test_misrepresentation_skipped_without_file(self, tmp_path: Path) -> None:
        responses = [make_response("AcmePilot is their AI coding assistant.")]
        summary = build_summary(
            "no_misrep_file",
            responses,
            _PRODUCT,
            expected_outcomes_path=tmp_path / "absent.json",
        )
        assert summary.misrepresentation_scoring_enabled is False
        assert summary.misrepresentation_totals == {}


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestWriteJson:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        responses = [make_response("AcmeSearch is great.")]
        summary = build_summary("json_test", responses, _PRODUCT)
        out_path = tmp_path / "report.json"
        result_path = write_json(summary, out_path)

        assert result_path == out_path
        assert out_path.exists()

        with open(out_path) as fh:
            data = json.load(fh)

        assert data["experiment"] == "json_test"
        assert data["brand_name"] == "AcmeSearch"
        assert "models" in data
        assert "overall_mention_rate" in data
        assert "competitor_comparison" in data
        assert "misrepresentation_totals" in data
        assert "recall_by_expectation" in data

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        responses = [make_response("AcmeSearch is great.")]
        summary = build_summary("nested_test", responses, _PRODUCT)
        out_path = tmp_path / "deep" / "nested" / "report.json"
        write_json(summary, out_path)
        assert out_path.exists()


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------


class TestHtmlOutput:
    def test_contains_expected_sections(self) -> None:
        responses = [
            make_response("AcmeSearch is great."),
            make_response("Use CodeHound."),
        ]
        summary = build_summary("html_test", responses, _PRODUCT)
        html_content = generate_html(summary)

        assert "Share of Voice" in html_content
        assert "Mention Rate by Model" in html_content
        assert "Competitor Comparison" in html_content
        assert "Misrepresentation Detection" in html_content
        assert "Recall by Expectation Level" in html_content

    def test_contains_brand_and_competitor_names(self) -> None:
        responses = [make_response("AcmeSearch vs CodeHound.")]
        summary = build_summary("names_test", responses, _PRODUCT)
        html_content = generate_html(summary)
        assert "AcmeSearch" in html_content
        assert "CodeHound" in html_content
        assert "SearchLite" in html_content

    def test_misrep_skip_notice_without_file(self, tmp_path: Path) -> None:
        responses = [make_response("AcmeSearch is great.")]
        summary = build_summary(
            "skip_notice",
            responses,
            _PRODUCT,
            expected_outcomes_path=tmp_path / "absent.json",
        )
        html_content = generate_html(summary)
        assert "Skipped: no misrepresentation list provided" in html_content

    def test_contains_experiment_name(self) -> None:
        summary = build_summary("my_experiment", [], _PRODUCT)
        html_content = generate_html(summary)
        assert "my_experiment" in html_content

    def test_contains_svg_charts(self) -> None:
        responses = [make_response("AcmeSearch is great.")]
        summary = build_summary("svg_test", responses, _PRODUCT)
        html_content = generate_html(summary)
        assert "<svg" in html_content
        assert "</svg>" in html_content

    def test_self_contained_html(self) -> None:
        responses = [make_response("AcmeSearch is great.")]
        summary = build_summary("self_contained", responses, _PRODUCT)
        html_content = generate_html(summary)
        assert "<!DOCTYPE html>" in html_content
        assert "<style>" in html_content
        # No external CSS/JS references
        assert "link rel=" not in html_content.lower()
        assert "<script src=" not in html_content.lower()

    def test_write_html_creates_file(self, tmp_path: Path) -> None:
        responses = [make_response("AcmeSearch is great.")]
        summary = build_summary("file_test", responses, _PRODUCT)
        out_path = tmp_path / "report.html"
        result_path = write_html(summary, out_path)

        assert result_path == out_path
        assert out_path.exists()
        content = out_path.read_text()
        assert "<!DOCTYPE html>" in content


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCliParsing:
    def test_required_experiment(self) -> None:
        args = _parse_args(["--experiment", "baseline"])
        assert args.experiment == "baseline"
        assert args.format == "both"
        assert args.output is None
        assert args.product == Path("product.yaml")

    def test_format_json(self) -> None:
        args = _parse_args(["--experiment", "x", "--format", "json"])
        assert args.format == "json"

    def test_format_html(self) -> None:
        args = _parse_args(["--experiment", "x", "--format", "html"])
        assert args.format == "html"

    def test_output_dir(self) -> None:
        args = _parse_args(["--experiment", "x", "--output", "/tmp/reports"])
        assert args.output == "/tmp/reports"

    def test_product_flag(self) -> None:
        args = _parse_args(["--experiment", "x", "--product", "/tmp/p.yaml"])
        assert args.product == Path("/tmp/p.yaml")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_responses_with_errors(self) -> None:
        ok = make_response("AcmeSearch is great.")
        summary = build_summary("error_test", [ok, _make_errored()], _PRODUCT)
        assert summary.total_responses == 2
        # Only the successful response should be scored
        assert len(summary.models) == 1

    def test_no_brand_mentions_at_all(self) -> None:
        responses = [
            make_response("Use grep for searching."),
            make_response("Try ripgrep for speed."),
        ]
        summary = build_summary("no_brand", responses, _PRODUCT)
        assert summary.overall_mention_rate == 0.0
        assert summary.overall_share_of_voice == 0.0


# ---------------------------------------------------------------------------
# Response Details
# ---------------------------------------------------------------------------


class TestResponseDetails:
    def test_response_details_populated(self) -> None:
        responses = [
            make_response("AcmeSearch is great for code search."),
            make_response("Use CodeHound instead."),
        ]
        summary = build_summary("detail_test", responses, _PRODUCT)
        assert len(summary.response_details) == 2
        assert all(isinstance(rd, ResponseDetail) for rd in summary.response_details)

    def test_response_details_scoring(self) -> None:
        responses = [
            make_response("AcmeSearch is great.", prompt_id="p1"),
        ]
        summary = build_summary("scoring_test", responses, _PRODUCT)
        rd = summary.response_details[0]
        assert rd.prompt_id == "p1"
        assert rd.mentioned is True
        assert rd.mention_count == 1
        assert rd.prompt_text == "test prompt"
        assert rd.response_text == "AcmeSearch is great."
        assert isinstance(rd.competitor_mentions, dict)

    def test_response_details_filters_errors(self) -> None:
        ok = make_response("AcmeSearch rocks.")
        summary = build_summary("err_detail", [ok, _make_errored()], _PRODUCT)
        assert len(summary.response_details) == 1
        assert summary.response_details[0].mentioned is True

    def test_response_details_empty(self) -> None:
        summary = build_summary("empty_detail", [], _PRODUCT)
        assert summary.response_details == []

    def test_response_details_misrepresentations(self, tmp_path: Path) -> None:
        eo_path = _write_misreps(tmp_path)
        responses = [
            make_response("AcmePilot is their AI coding assistant."),
        ]
        summary = build_summary(
            "misrep_detail", responses, _PRODUCT, expected_outcomes_path=eo_path
        )
        rd = summary.response_details[0]
        assert "misrep-001" in rd.misrepresentations_detected

    def test_response_details_excluded_from_json(self, tmp_path: Path) -> None:
        responses = [make_response("AcmeSearch is great.")]
        summary = build_summary("json_excl", responses, _PRODUCT)
        out_path = tmp_path / "report.json"
        write_json(summary, out_path)
        with open(out_path) as fh:
            data = json.load(fh)
        assert "response_details" not in data

    def test_response_details_in_html(self) -> None:
        responses = [
            make_response("AcmeSearch is great.", prompt_id="p42"),
            make_response("Use grep.", prompt_id="p43"),
        ]
        summary = build_summary("html_detail", responses, _PRODUCT)
        html_content = generate_html(summary)
        assert "Response Details" in html_content
        assert "p42" in html_content
        assert "p43" in html_content
        assert "response-filter" in html_content
        assert "toggleDetail" in html_content
        assert "filterResponses" in html_content

    def test_response_details_html_escapes_content(self) -> None:
        responses = [
            make_response("<script>alert('xss')</script>", prompt_id="xss-test"),
        ]
        summary = build_summary("escape_test", responses, _PRODUCT)
        html_content = generate_html(summary)
        assert "<script>alert(" not in html_content
        assert "&lt;script&gt;" in html_content

    def test_response_details_model_alias(self) -> None:
        responses = [
            make_response("AcmeSearch rocks.", model_alias="claude"),
            make_response("AcmeSearch is nice.", model_alias="chatgpt"),
        ]
        summary = build_summary("model_detail", responses, _PRODUCT)
        aliases = [rd.model_alias for rd in summary.response_details]
        assert "claude" in aliases
        assert "chatgpt" in aliases
