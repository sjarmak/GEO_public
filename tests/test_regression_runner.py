"""Tests for the regression CLI runner.

These exercise the orchestration code path without invoking the upstream
LLM runner. The snapshot/compare commands operate purely on files, so
they're a good fit for end-to-end unit tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from geo.regression.runner import _parse_args, main
from geo.regression.snapshot import (
    RecallEntry,
    Snapshot,
    save_snapshot,
)

_PRODUCT_YAML = """\
product:
  name: AcmeSearch
  aliases:
    - Acme Search
    - acmesearch.io
  category: code search tools

competitors:
  - name: CodeHound
    aliases: [codehound.dev]
  - name: FindGrep
    aliases: []
  - name: SearchLite
    aliases: []
"""


def _write_product(tmp_path: Path) -> Path:
    path = tmp_path / "product.yaml"
    path.write_text(_PRODUCT_YAML, encoding="utf-8")
    return path


def _write_snap(
    tmp_path: Path,
    *,
    name: str,
    mention_rate: float,
    misrep_counts: dict[str, int] | None = None,
    total_responses: int = 500,
) -> Path:
    snap = Snapshot(
        schema_version=1,
        snapshot_id=name,
        captured_at="2026-04-29T00:00:00Z",
        experiment_name="exp",
        model_alias="mock",
        model_id=f"mock-{name}",
        target_brand="AcmeSearch",
        total_prompts=50,
        total_responses=total_responses,
        error_count=0,
        mention_rate=mention_rate,
        share_of_voice=mention_rate,
        avg_first_mention_offset=100.0,
        avg_mention_count=1.0,
        list_appearance_rate=mention_rate,
        avg_list_rank=2.0,
        misrepresentation_counts=misrep_counts or {},
        recall_by_expectation=(
            RecallEntry(
                "must_appear",
                50,
                total_responses,
                int(mention_rate * total_responses),
                mention_rate,
            ),
        ),
    )
    return save_snapshot(snap, tmp_path / f"{name}.json")


def test_compare_pass_returns_zero(tmp_path: Path, capsys):
    base = _write_snap(tmp_path, name="base", mention_rate=0.80)
    cand = _write_snap(tmp_path, name="cand", mention_rate=0.79)
    exit_code = main(
        ["compare", "--baseline", str(base), "--candidate", str(cand)]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "PASS" in out


def test_compare_fail_returns_two(tmp_path: Path, capsys):
    base = _write_snap(tmp_path, name="base", mention_rate=0.80)
    cand = _write_snap(tmp_path, name="cand", mention_rate=0.50)
    exit_code = main(
        ["compare", "--baseline", str(base), "--candidate", str(cand)]
    )
    out = capsys.readouterr().out
    assert exit_code == 2
    assert "FAIL" in out


def test_compare_warn_returns_one(tmp_path: Path, capsys):
    base = _write_snap(tmp_path, name="base", mention_rate=0.80)
    cand = _write_snap(tmp_path, name="cand", mention_rate=0.74)  # 6pp drop
    exit_code = main(
        ["compare", "--baseline", str(base), "--candidate", str(cand)]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "WARN" in out


def test_compare_json_output(tmp_path: Path, capsys):
    base = _write_snap(tmp_path, name="base", mention_rate=0.80)
    cand = _write_snap(tmp_path, name="cand", mention_rate=0.50)
    exit_code = main(
        [
            "compare",
            "--baseline",
            str(base),
            "--candidate",
            str(cand),
            "--json",
        ]
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["overall_severity"] == "FAIL"
    assert payload["exit_code"] == 2
    assert isinstance(payload["findings"], list)
    assert any(f["metric"] == "mention_rate" for f in payload["findings"])
    assert exit_code == 2


def test_compare_writes_report_file(tmp_path: Path):
    base = _write_snap(tmp_path, name="base", mention_rate=0.80)
    cand = _write_snap(tmp_path, name="cand", mention_rate=0.50)
    report_path = tmp_path / "report.txt"
    main(
        [
            "compare",
            "--baseline",
            str(base),
            "--candidate",
            str(cand),
            "--report-out",
            str(report_path),
        ]
    )
    assert report_path.exists()
    content = report_path.read_text()
    assert "Verdict:" in content
    assert "FAIL" in content


def test_compare_threshold_overrides(tmp_path: Path, capsys):
    # Bump n large enough that a 4pp drop is statistically significant.
    base = _write_snap(
        tmp_path, name="base", mention_rate=0.80, total_responses=2000
    )
    cand = _write_snap(
        tmp_path, name="cand", mention_rate=0.76, total_responses=2000
    )
    # Default thresholds (5pp warn) -> PASS
    rc_default = main(
        ["compare", "--baseline", str(base), "--candidate", str(cand)]
    )
    assert rc_default == 0

    # Tightened threshold -> WARN
    rc_tight = main(
        [
            "compare",
            "--baseline",
            str(base),
            "--candidate",
            str(cand),
            "--rate-warn",
            "0.03",
        ]
    )
    assert rc_tight == 1


def test_snapshot_command_writes_file(tmp_path: Path):
    """Snapshot subcommand: read from a fake results dir, emit a snapshot."""
    from geo.storage import ResultStorage
    from tests.helpers import make_response

    storage = ResultStorage(base_path=tmp_path / "raw")
    storage.save(
        "exp_for_snap",
        [make_response(text="AcmeSearch is great", model_alias="mock")],
    )
    product_path = _write_product(tmp_path)
    out_dir = tmp_path / "snaps"
    rc = main(
        [
            "snapshot",
            "--experiment",
            "exp_for_snap",
            "--model",
            "mock",
            "--product",
            str(product_path),
            "--results-dir",
            str(tmp_path / "raw"),
            "--out",
            str(out_dir),
        ]
    )
    assert rc == 0
    files = list(out_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["mention_rate"] == 1.0
    assert payload["model_alias"] == "mock"
    assert payload["target_brand"] == "AcmeSearch"


def test_parse_args_requires_subcommand():
    import pytest

    with pytest.raises(SystemExit):
        _parse_args([])
