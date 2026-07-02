"""Tests for the freshness-decay tracker experiment module."""

import asyncio
import math
from datetime import date
from pathlib import Path

import pytest

from experiments.freshness_decay.tracker import (
    DecayFit,
    Marker,
    RecallReport,
    fit_decay_curve,
    load_markers,
    recalled,
    recommend_refresh_cadence,
    run_tracker,
)
from geo.llm_client import MockResponder
from tests.helpers import make_product, make_response

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKERS_PATH = REPO_ROOT / "experiments" / "freshness_decay" / "markers.json"


def make_marker(
    marker_id: str = "fd-test",
    keywords: tuple[str, ...] = ("velocity mode",),
    publish_date: date | None = date(2026, 1, 15),
    is_control: bool = False,
) -> Marker:
    return Marker(
        id=marker_id,
        version="v2.4",
        publish_date=publish_date,
        claim_short="AcmeSearch shipped velocity mode",
        probe_question="What did AcmeSearch ship recently?",
        expected_keywords=keywords,
        is_control=is_control,
    )


class TestLoadMarkers:
    def test_repo_markers_load(self) -> None:
        markers = load_markers(MARKERS_PATH)
        assert len(markers) >= 4
        assert all(m.id for m in markers)
        assert any(m.is_control for m in markers)
        assert any(not m.is_control for m in markers)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_markers(tmp_path / "absent.json")

    def test_empty_markers_raise(self, tmp_path: Path) -> None:
        empty = tmp_path / "markers.json"
        empty.write_text('{"markers": []}', encoding="utf-8")
        with pytest.raises(ValueError, match="No markers"):
            load_markers(empty)


class TestRecalled:
    def test_keyword_present_case_insensitive(self) -> None:
        marker = make_marker(keywords=("Velocity Mode",))
        assert recalled(marker, make_response("They shipped velocity mode last spring."))

    def test_keyword_absent(self) -> None:
        marker = make_marker(keywords=("velocity mode",))
        assert not recalled(marker, make_response("They shipped a dark theme."))

    def test_errored_response_never_recalls(self) -> None:
        marker = make_marker(keywords=("test",))
        response = make_response("test")
        errored = type(response)(**{**response.__dict__, "error": "boom"})
        assert not recalled(marker, errored)


class TestFitDecayCurve:
    def test_recovers_synthetic_decay(self) -> None:
        rate = 0.1
        reports = [
            RecallReport(
                marker_id=f"fd-{i}",
                response_count=1000,
                recall_count=round(1000 * math.exp(-rate * age)),
                is_control=False,
                age_weeks=float(age),
            )
            for i, age in enumerate((2, 8, 16, 30))
        ]
        fit = fit_decay_curve(reports)
        assert fit.sample_count == 4
        assert fit.decay_rate == pytest.approx(rate, abs=0.01)

    def test_controls_and_undated_excluded(self) -> None:
        reports = [
            RecallReport("fd-a", 10, 5, is_control=True, age_weeks=4.0),
            RecallReport("fd-b", 10, 5, is_control=False, age_weeks=None),
        ]
        fit = fit_decay_curve(reports)
        assert fit.sample_count == 0
        assert fit.decay_rate == 0.0

    def test_zero_decay_yields_infinite_half_life(self) -> None:
        assert DecayFit(amplitude=1.0, decay_rate=0.0, sample_count=0).half_life_weeks == float(
            "inf"
        )


class TestRecommendRefreshCadence:
    @pytest.mark.parametrize(
        ("half_life", "expected"),
        [
            (2.0, "Weekly refresh needed"),
            (8.0, "Monthly refresh sufficient"),
            (20.0, "Quarterly refresh sufficient"),
            (52.0, "Semi-annual refresh sufficient"),
            (float("inf"), "Inconclusive: need more dated markers or more runs"),
        ],
    )
    def test_thresholds(self, half_life: float, expected: str) -> None:
        assert recommend_refresh_cadence(half_life) == expected


class TestRunTrackerMockLane:
    def test_mock_lane_runs_offline(self) -> None:
        markers = (make_marker("fd-001"), make_marker("fd-ctrl", is_control=True))
        responder = MockResponder(product=make_product(), seed=0)
        reports = asyncio.run(
            run_tracker(
                markers,
                ["mock"],
                repetitions=2,
                run_date=date(2026, 7, 1),
                mock_responder=responder,
            )
        )
        assert set(reports) == {"mock"}
        assert [r.marker_id for r in reports["mock"]] == ["fd-001", "fd-ctrl"]
        assert all(r.response_count == 2 for r in reports["mock"])

    def test_unknown_alias_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown model alias"):
            asyncio.run(
                run_tracker(
                    (make_marker(),),
                    ["nonexistent"],
                    repetitions=1,
                    run_date=date(2026, 7, 1),
                )
            )
