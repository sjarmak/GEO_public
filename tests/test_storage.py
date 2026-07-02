"""Tests for results storage (offline, no API keys)."""

from pathlib import Path

from tests.helpers import make_response
from geo.storage import ResultStorage


class TestStorage:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        storage = ResultStorage(base_path=tmp_path)
        responses = [
            make_response("AcmeSearch is great.", repetition=1),
            make_response("Use CodeHound.", repetition=2),
        ]
        storage.save("test_exp", responses)
        loaded = storage.load("test_exp", model_alias="mock")
        assert len(loaded) == 2
        assert loaded[0].response_text == "AcmeSearch is great."
        assert loaded[1].response_text == "Use CodeHound."

    def test_multiple_models(self, tmp_path: Path):
        storage = ResultStorage(base_path=tmp_path)
        r1 = make_response("resp1", model_alias="claude")
        r2 = make_response("resp2", model_alias="chatgpt")
        storage.save("test_exp", [r1, r2])

        claude_results = storage.load("test_exp", model_alias="claude")
        assert len(claude_results) == 1
        assert claude_results[0].model_alias == "claude"

        all_results = storage.load("test_exp")
        assert len(all_results) == 2

    def test_append_mode(self, tmp_path: Path):
        storage = ResultStorage(base_path=tmp_path)
        storage.save("test_exp", [make_response("first")])
        storage.save("test_exp", [make_response("second")])
        loaded = storage.load("test_exp")
        assert len(loaded) == 2

    def test_list_experiments(self, tmp_path: Path):
        storage = ResultStorage(base_path=tmp_path)
        storage.save("exp_a", [make_response()])
        storage.save("exp_b", [make_response()])
        exps = storage.list_experiments()
        assert "exp_a" in exps
        assert "exp_b" in exps

    def test_load_nonexistent(self, tmp_path: Path):
        storage = ResultStorage(base_path=tmp_path)
        loaded = storage.load("nonexistent")
        assert loaded == []

    def test_data_integrity(self, tmp_path: Path):
        storage = ResultStorage(base_path=tmp_path)
        original = make_response(
            text="AcmeSearch handles 500 repos",
            prompt_id="use-042",
            repetition=7,
        )
        storage.save("integrity_test", [original])
        loaded = storage.load("integrity_test")[0]
        assert loaded.prompt_id == original.prompt_id
        assert loaded.repetition == original.repetition
        assert loaded.temperature == original.temperature
        assert loaded.model_id == original.model_id
