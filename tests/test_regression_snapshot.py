"""Tests for the regression snapshot module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from geo.config import Config
from geo.regression.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    Snapshot,
    build_snapshot,
    build_snapshot_from_storage,
    load_snapshot,
    save_snapshot,
)
from geo.storage import ResultStorage
from tests.helpers import make_product, make_response


def test_build_snapshot_basic_metrics():
    responses = [
        make_response(text="AcmeSearch is great", repetition=1),
        make_response(text="Try AcmeSearch for code search", repetition=2),
        make_response(text="CodeHound is fine", repetition=3),
    ]
    snap = build_snapshot(
        experiment_name="exp1",
        model_alias="mock",
        model_id="mock-v1",
        responses=responses,
        product=make_product(),
    )
    assert snap.schema_version == SNAPSHOT_SCHEMA_VERSION
    assert snap.total_responses == 3
    assert snap.error_count == 0
    # 2 of 3 responses mention AcmeSearch
    assert snap.mention_rate == pytest.approx(2 / 3)
    assert snap.experiment_name == "exp1"
    assert snap.model_id == "mock-v1"
    assert snap.target_brand == "AcmeSearch"


def test_build_snapshot_counts_alias_mentions():
    responses = [
        make_response(text="Check out acmesearch.io for this", repetition=1),
    ]
    snap = build_snapshot(
        experiment_name="exp1",
        model_alias="mock",
        model_id="mock-v1",
        responses=responses,
        product=make_product(),
    )
    assert snap.mention_rate == 1.0


def test_build_snapshot_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        build_snapshot(
            experiment_name="exp1",
            model_alias="mock",
            model_id="mock-v1",
            responses=[],
            product=make_product(),
        )


def test_build_snapshot_rejects_all_errored():
    from dataclasses import replace

    errored = replace(make_response(), error="upstream timeout")
    with pytest.raises(ValueError, match="All .* errored"):
        build_snapshot(
            experiment_name="exp1",
            model_alias="mock",
            model_id="mock-v1",
            responses=[errored],
            product=make_product(),
        )


def test_build_snapshot_excludes_errors_from_metrics():
    from dataclasses import replace

    good = make_response(text="AcmeSearch is great")
    errored = replace(make_response(text="should not count"), error="boom")
    snap = build_snapshot(
        experiment_name="exp1",
        model_alias="mock",
        model_id="mock-v1",
        responses=[good, errored],
        product=make_product(),
    )
    # Total includes errors; metrics only count successful.
    assert snap.total_responses == 2
    assert snap.error_count == 1
    assert snap.mention_rate == 1.0  # only the successful response counts


def test_snapshot_id_is_stable_for_fixed_inputs():
    responses = [make_response(text="AcmeSearch")]
    snap_a = build_snapshot(
        experiment_name="exp1",
        model_alias="mock",
        model_id="mock-v1",
        responses=responses,
        product=make_product(),
        captured_at="2026-04-29T00:00:00Z",
    )
    snap_b = build_snapshot(
        experiment_name="exp1",
        model_alias="mock",
        model_id="mock-v1",
        responses=responses,
        product=make_product(),
        captured_at="2026-04-29T00:00:00Z",
    )
    assert snap_a.snapshot_id == snap_b.snapshot_id


def test_save_and_load_snapshot_roundtrip(tmp_path: Path):
    responses = [make_response(text="AcmeSearch")]
    snap = build_snapshot(
        experiment_name="exp1",
        model_alias="mock",
        model_id="mock-v1",
        responses=responses,
        product=make_product(),
        captured_at="2026-04-29T00:00:00Z",
    )
    out = save_snapshot(snap, tmp_path)
    assert out.exists()
    assert out.parent == tmp_path
    loaded = load_snapshot(out)
    assert loaded == snap


def test_save_snapshot_to_explicit_file(tmp_path: Path):
    responses = [make_response(text="AcmeSearch")]
    snap = build_snapshot(
        experiment_name="exp1",
        model_alias="mock",
        model_id="mock-v1",
        responses=responses,
        product=make_product(),
    )
    target = tmp_path / "nested" / "named.json"
    out = save_snapshot(snap, target)
    assert out == target
    assert target.exists()


def test_load_snapshot_rejects_future_schema(tmp_path: Path):
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION + 1,
        "snapshot_id": "x",
        "captured_at": "2026-04-29T00:00:00Z",
        "experiment_name": "exp1",
        "model_alias": "mock",
        "model_id": "mock-v1",
        "target_brand": "AcmeSearch",
        "total_prompts": 1,
        "total_responses": 1,
        "error_count": 0,
        "mention_rate": 1.0,
        "share_of_voice": 1.0,
        "avg_first_mention_offset": 0,
        "avg_mention_count": 1,
        "list_appearance_rate": 0.0,
        "avg_list_rank": None,
        "competitor_mention_rates": {},
        "misrepresentation_counts": {},
        "recall_by_expectation": [],
        "notes": "",
    }
    p = tmp_path / "future.json"
    with open(p, "w") as fh:
        json.dump(payload, fh)
    with pytest.raises(ValueError, match="newer"):
        load_snapshot(p)


def test_build_snapshot_from_storage(tmp_path: Path):
    storage = ResultStorage(base_path=tmp_path)
    storage.save(
        "exp1",
        [
            make_response(text="AcmeSearch is great", model_alias="mock", repetition=1),
            make_response(text="CodeHound is fine", model_alias="mock", repetition=2),
        ],
    )
    snap = build_snapshot_from_storage(
        experiment_name="exp1",
        model_alias="mock",
        product=make_product(),
        storage=storage,
        config=Config(),
    )
    assert snap.total_responses == 2
    assert snap.mention_rate == pytest.approx(0.5)
    assert snap.competitor_mention_rates["CodeHound"] == pytest.approx(0.5)


def test_build_snapshot_from_storage_rejects_missing(tmp_path: Path):
    storage = ResultStorage(base_path=tmp_path)
    with pytest.raises(ValueError, match="No stored responses"):
        build_snapshot_from_storage(
            experiment_name="missing",
            model_alias="mock",
            product=make_product(),
            storage=storage,
            config=Config(),
        )


def test_snapshot_to_dict_inlines_recall_entries():
    from geo.regression.snapshot import RecallEntry

    snap = Snapshot(
        schema_version=1,
        snapshot_id="x",
        captured_at="2026-04-29T00:00:00Z",
        experiment_name="exp",
        model_alias="mock",
        model_id="mock-v1",
        target_brand="AcmeSearch",
        total_prompts=0,
        total_responses=0,
        error_count=0,
        mention_rate=0.0,
        share_of_voice=0.0,
        avg_first_mention_offset=None,
        avg_mention_count=0.0,
        list_appearance_rate=0.0,
        avg_list_rank=None,
        recall_by_expectation=(RecallEntry("L1", 1, 1, 1, 1.0),),
    )
    d = snap.to_dict()
    assert isinstance(d["recall_by_expectation"], list)
    assert d["recall_by_expectation"][0]["level"] == "L1"
