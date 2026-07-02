"""Tests for the regression detector module."""

from __future__ import annotations

import math

import pytest

from geo.regression.detector import (
    RegressionThresholds,
    Severity,
    detect_regressions,
    format_report,
    two_proportion_p_value,
)
from geo.regression.snapshot import RecallEntry, Snapshot


def _snap(
    *,
    snapshot_id: str = "snap",
    model_id: str = "model-v1",
    mention_rate: float = 0.5,
    share_of_voice: float = 0.5,
    avg_first_mention_offset: float | None = 100.0,
    avg_mention_count: float = 1.0,
    list_appearance_rate: float = 0.5,
    avg_list_rank: float | None = 2.0,
    competitor_mention_rates: dict[str, float] | None = None,
    misrepresentation_counts: dict[str, int] | None = None,
    recall_by_expectation: tuple[RecallEntry, ...] = (),
    total_responses: int = 100,
) -> Snapshot:
    return Snapshot(
        schema_version=1,
        snapshot_id=snapshot_id,
        captured_at="2026-04-29T00:00:00Z",
        experiment_name="exp",
        model_alias="mock",
        model_id=model_id,
        target_brand="AcmeSearch",
        total_prompts=10,
        total_responses=total_responses,
        error_count=0,
        mention_rate=mention_rate,
        share_of_voice=share_of_voice,
        avg_first_mention_offset=avg_first_mention_offset,
        avg_mention_count=avg_mention_count,
        list_appearance_rate=list_appearance_rate,
        avg_list_rank=avg_list_rank,
        competitor_mention_rates=competitor_mention_rates or {},
        misrepresentation_counts=misrepresentation_counts or {},
        recall_by_expectation=recall_by_expectation,
    )


# ---------------------------------------------------------------------------
# Statistical helper
# ---------------------------------------------------------------------------


def test_two_proportion_p_value_identical_returns_none():
    # Both rates are 0: variance collapses, p undefined.
    assert two_proportion_p_value(0, 100, 0, 100) is None
    # Both rates are 1.
    assert two_proportion_p_value(50, 50, 50, 50) is None


def test_two_proportion_p_value_zero_sample_returns_none():
    assert two_proportion_p_value(0, 0, 5, 10) is None
    assert two_proportion_p_value(5, 10, 0, 0) is None


def test_two_proportion_p_value_large_difference_significant():
    # 80% vs 20% with n=100 each: should be highly significant.
    p = two_proportion_p_value(80, 100, 20, 100)
    assert p is not None and p < 0.001


def test_two_proportion_p_value_small_difference_not_significant():
    # 50% vs 51% with n=100: not significant.
    p = two_proportion_p_value(50, 100, 51, 100)
    assert p is not None and p > 0.5


def test_two_proportion_p_value_in_unit_interval():
    p = two_proportion_p_value(40, 100, 60, 100)
    assert p is not None
    assert 0.0 <= p <= 1.0
    assert math.isfinite(p)


# ---------------------------------------------------------------------------
# detect_regressions: PASS path
# ---------------------------------------------------------------------------


def test_pass_when_snapshots_identical():
    snap = _snap()
    report = detect_regressions(snap, snap)
    assert report.overall_severity is Severity.PASS
    assert all(f.severity is Severity.PASS for f in report.findings)
    assert report.exit_code == 0


def test_pass_when_changes_below_threshold():
    base = _snap(mention_rate=0.50, list_appearance_rate=0.50)
    cand = _snap(
        snapshot_id="cand",
        mention_rate=0.49,  # 1 pp drop, below warn threshold of 5pp
        list_appearance_rate=0.49,
    )
    report = detect_regressions(base, cand)
    assert report.overall_severity is Severity.PASS


# ---------------------------------------------------------------------------
# detect_regressions: WARN / FAIL boundaries
# ---------------------------------------------------------------------------


def test_warn_on_mention_rate_drop_above_warn_threshold():
    # 60% -> 54% (6pp drop) at n=2000 each: clearly significant, lands
    # between the 5pp warn and 10pp fail thresholds.
    base = _snap(mention_rate=0.60, total_responses=2000)
    cand = _snap(snapshot_id="cand", mention_rate=0.54, total_responses=2000)
    report = detect_regressions(base, cand)
    finding = next(f for f in report.findings if f.metric == "mention_rate")
    assert finding.severity is Severity.WARN
    assert report.overall_severity is Severity.WARN


def test_fail_on_mention_rate_drop_above_fail_threshold():
    # 80% -> 60% (20pp) with n=500: solidly significant FAIL.
    base = _snap(mention_rate=0.80, total_responses=500)
    cand = _snap(snapshot_id="cand", mention_rate=0.60, total_responses=500)
    report = detect_regressions(base, cand)
    finding = next(f for f in report.findings if f.metric == "mention_rate")
    assert finding.severity is Severity.FAIL
    assert report.overall_severity is Severity.FAIL
    assert report.exit_code == 2


def test_no_flag_when_drop_is_not_significant():
    # 50% -> 40% with n=10 each: 10pp drop but tiny sample -> not significant.
    base = _snap(mention_rate=0.50, total_responses=10)
    cand = _snap(snapshot_id="cand", mention_rate=0.40, total_responses=10)
    report = detect_regressions(base, cand)
    finding = next(f for f in report.findings if f.metric == "mention_rate")
    assert finding.severity is Severity.PASS, (
        f"Expected PASS due to insignificance, got {finding.severity} "
        f"(p={finding.p_value})"
    )


# ---------------------------------------------------------------------------
# detect_regressions: misrepresentation handling
# ---------------------------------------------------------------------------


def test_warn_when_new_misrep_appears():
    base = _snap(misrepresentation_counts={"misrep-001": 0})
    cand = _snap(
        snapshot_id="cand",
        misrepresentation_counts={"misrep-001": 1},
    )
    report = detect_regressions(base, cand)
    finding = next(f for f in report.findings if f.metric == "misrep::misrep-001")
    assert finding.severity is Severity.WARN


def test_fail_when_misrep_increase_exceeds_fail_threshold():
    base = _snap(misrepresentation_counts={"misrep-001": 0})
    cand = _snap(
        snapshot_id="cand",
        misrepresentation_counts={"misrep-001": 5},
    )
    report = detect_regressions(base, cand)
    finding = next(f for f in report.findings if f.metric == "misrep::misrep-001")
    assert finding.severity is Severity.FAIL


def test_misrep_decrease_is_pass():
    base = _snap(misrepresentation_counts={"misrep-001": 5})
    cand = _snap(
        snapshot_id="cand",
        misrepresentation_counts={"misrep-001": 1},
    )
    report = detect_regressions(base, cand)
    finding = next(f for f in report.findings if f.metric == "misrep::misrep-001")
    assert finding.severity is Severity.PASS


# ---------------------------------------------------------------------------
# detect_regressions: competitor handling
# ---------------------------------------------------------------------------


def test_competitor_increase_warns_when_significant():
    base = _snap(
        competitor_mention_rates={"CodeHound": 0.10},
        total_responses=500,
    )
    cand = _snap(
        snapshot_id="cand",
        competitor_mention_rates={"CodeHound": 0.25},
        total_responses=500,
    )
    report = detect_regressions(base, cand)
    finding = next(
        f for f in report.findings if f.metric == "competitor::CodeHound"
    )
    assert finding.severity is Severity.WARN


def test_competitor_decrease_is_pass():
    base = _snap(
        competitor_mention_rates={"CodeHound": 0.50},
        total_responses=500,
    )
    cand = _snap(
        snapshot_id="cand",
        competitor_mention_rates={"CodeHound": 0.20},
        total_responses=500,
    )
    report = detect_regressions(base, cand)
    finding = next(
        f for f in report.findings if f.metric == "competitor::CodeHound"
    )
    assert finding.severity is Severity.PASS


# ---------------------------------------------------------------------------
# detect_regressions: recall by expectation
# ---------------------------------------------------------------------------


def test_recall_drop_flagged_per_level():
    base = _snap(
        recall_by_expectation=(
            RecallEntry("must_appear", 50, 500, 400, 0.80),
        )
    )
    cand = _snap(
        snapshot_id="cand",
        recall_by_expectation=(
            RecallEntry("must_appear", 50, 500, 250, 0.50),
        ),
    )
    report = detect_regressions(base, cand)
    finding = next(
        f for f in report.findings if f.metric == "recall::must_appear"
    )
    assert finding.severity is Severity.FAIL


def test_recall_only_one_side_present_is_skipped():
    base = _snap(
        recall_by_expectation=(
            RecallEntry("must_appear", 50, 100, 80, 0.80),
        )
    )
    cand = _snap(
        snapshot_id="cand",
        recall_by_expectation=(),
    )
    report = detect_regressions(base, cand)
    finding = next(
        f for f in report.findings if f.metric == "recall::must_appear"
    )
    assert finding.severity is Severity.PASS
    assert finding.delta is None


# ---------------------------------------------------------------------------
# detect_regressions: avg_first_mention_offset / avg_list_rank
# ---------------------------------------------------------------------------


def test_first_mention_offset_increase_warns():
    base = _snap(avg_first_mention_offset=100.0)
    cand = _snap(snapshot_id="cand", avg_first_mention_offset=350.0)
    report = detect_regressions(base, cand)
    finding = next(
        f for f in report.findings if f.metric == "avg_first_mention_offset"
    )
    assert finding.severity is Severity.WARN


def test_first_mention_offset_huge_increase_fails():
    base = _snap(avg_first_mention_offset=100.0)
    cand = _snap(snapshot_id="cand", avg_first_mention_offset=1000.0)
    report = detect_regressions(base, cand)
    finding = next(
        f for f in report.findings if f.metric == "avg_first_mention_offset"
    )
    assert finding.severity is Severity.FAIL


def test_offset_skipped_when_unavailable():
    base = _snap(avg_first_mention_offset=None)
    cand = _snap(snapshot_id="cand", avg_first_mention_offset=500.0)
    report = detect_regressions(base, cand)
    finding = next(
        f for f in report.findings if f.metric == "avg_first_mention_offset"
    )
    assert finding.severity is Severity.PASS


def test_avg_list_rank_increase_warns():
    base = _snap(avg_list_rank=2.0)
    cand = _snap(snapshot_id="cand", avg_list_rank=3.5)
    report = detect_regressions(base, cand)
    finding = next(f for f in report.findings if f.metric == "avg_list_rank")
    assert finding.severity is Severity.WARN


# ---------------------------------------------------------------------------
# detect_regressions: validation
# ---------------------------------------------------------------------------


def test_rejects_mismatched_brands():
    base = _snap()
    cand = Snapshot(
        schema_version=1,
        snapshot_id="cand",
        captured_at="2026-04-29T00:00:00Z",
        experiment_name="exp",
        model_alias="mock",
        model_id="mock-v1",
        target_brand="OtherBrand",
        total_prompts=10,
        total_responses=100,
        error_count=0,
        mention_rate=0.5,
        share_of_voice=0.5,
        avg_first_mention_offset=100.0,
        avg_mention_count=1.0,
        list_appearance_rate=0.5,
        avg_list_rank=2.0,
    )
    with pytest.raises(ValueError, match="different brands"):
        detect_regressions(base, cand)


def test_notes_warn_when_model_id_unchanged():
    base = _snap(model_id="model-v1")
    cand = _snap(snapshot_id="cand", model_id="model-v1", mention_rate=0.4)
    report = detect_regressions(base, cand)
    assert "share model_id" in report.notes


def test_thresholds_can_be_tightened():
    # Default would PASS a 4pp drop; with a tighter threshold it should WARN.
    base = _snap(mention_rate=0.50, total_responses=2000)
    cand = _snap(snapshot_id="cand", mention_rate=0.46, total_responses=2000)
    default_report = detect_regressions(base, cand)
    assert default_report.overall_severity is Severity.PASS

    tight = RegressionThresholds(rate_warn_drop=0.03, rate_fail_drop=0.10)
    tight_report = detect_regressions(base, cand, tight)
    finding = next(
        f for f in tight_report.findings if f.metric == "mention_rate"
    )
    assert finding.severity is Severity.WARN


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def test_format_report_contains_verdict_and_findings():
    base = _snap(mention_rate=0.80, total_responses=500)
    cand = _snap(snapshot_id="cand", mention_rate=0.50, total_responses=500)
    report = detect_regressions(base, cand)
    text = format_report(report)
    assert "FAIL" in text
    assert "mention_rate" in text
    assert "Verdict:" in text
