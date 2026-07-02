"""Integration tests: full pipeline with the mock provider (no API keys)."""

import asyncio
import re
from pathlib import Path

import pytest

from tests.helpers import make_product
from geo.config import Config, mention_pattern
from geo.llm_client import LLMClient, LLMResponse, MockResponder
from geo.runner import Prompt, run_experiment
from geo.scoring import aggregate_scores
from geo.storage import ResultStorage

_PRODUCT = make_product()


def _make_client(seed: int = 0) -> LLMClient:
    return LLMClient(mock_responder=MockResponder(product=_PRODUCT, seed=seed))


def _run_mock(
    client: LLMClient, prompt_id: str, repetitions: int
) -> list[LLMResponse]:
    cfg = Config()
    return asyncio.run(
        client.run_prompt(
            model_alias="mock",
            model_spec=cfg.get_model("mock"),
            prompt_id=prompt_id,
            prompt_text="What are the best code search tools?",
            repetitions=repetitions,
            temperature=1.0,
            top_p=1.0,
            max_tokens=2048,
        )
    )


class TestMockResponder:
    def test_deterministic_given_seed(self):
        r1 = MockResponder(product=_PRODUCT, seed=7)
        r2 = MockResponder(product=_PRODUCT, seed=7)
        for rep in range(1, 6):
            assert r1.generate("p1", rep) == r2.generate("p1", rep)

    def test_different_seeds_differ(self):
        r1 = MockResponder(product=_PRODUCT, seed=1)
        r2 = MockResponder(product=_PRODUCT, seed=2)
        outputs1 = [r1.generate("p1", rep) for rep in range(1, 11)]
        outputs2 = [r2.generate("p1", rep) for rep in range(1, 11)]
        assert outputs1 != outputs2

    def test_repetitions_vary(self):
        responder = MockResponder(product=_PRODUCT, seed=0)
        texts = {responder.generate("p1", rep) for rep in range(1, 21)}
        assert len(texts) >= 5

    def test_brand_mention_rate_near_55_percent(self):
        responder = MockResponder(product=_PRODUCT, seed=0)
        pattern = mention_pattern(_PRODUCT.brand)
        n = 400
        hits = sum(
            1
            for i in range(n)
            if pattern.search(responder.generate(f"p{i % 20}", i))
        )
        assert 0.45 <= hits / n <= 0.65

    def test_competitor_mention_rates_in_range(self):
        responder = MockResponder(product=_PRODUCT, seed=0)
        n = 400
        texts = [responder.generate(f"p{i % 20}", i) for i in range(n)]
        for comp in _PRODUCT.competitors:
            pattern = mention_pattern(comp)
            rate = sum(1 for t in texts if pattern.search(t)) / n
            assert 0.20 <= rate <= 0.80, f"{comp.name} rate {rate} out of range"

    def test_produces_list_and_prose_shapes(self):
        responder = MockResponder(product=_PRODUCT, seed=0)
        texts = [responder.generate(f"p{i}", 1) for i in range(60)]
        list_style = [t for t in texts if re.search(r"^\d+\. ", t, re.MULTILINE)]
        prose_style = [
            t for t in texts if not re.search(r"^\d+\. ", t, re.MULTILINE)
        ]
        assert list_style, "expected some list-style responses"
        assert prose_style, "expected some prose-style responses"

    def test_mentions_category(self):
        responder = MockResponder(product=_PRODUCT, seed=0)
        texts = [responder.generate(f"p{i}", 1) for i in range(20)]
        assert any("code search tools" in t for t in texts)

    def test_mock_without_responder_raises(self):
        cfg = Config()
        client = LLMClient()
        with pytest.raises(ValueError, match="MockResponder"):
            asyncio.run(
                client.run_prompt(
                    model_alias="mock",
                    model_spec=cfg.get_model("mock"),
                    prompt_id="p1",
                    prompt_text="x",
                    repetitions=1,
                )
            )


class TestMockPipeline:
    def test_mock_client_returns_responses(self):
        responses = _run_mock(_make_client(), "test-1", repetitions=5)
        assert len(responses) == 5
        for r in responses:
            assert r.error is None
            assert len(r.response_text) > 0
            assert r.model_alias == "mock"
            assert r.provider == "mock"

    def test_scoring_on_mock_responses(self):
        responses = _run_mock(_make_client(), "score-test", repetitions=10)
        scores = aggregate_scores(
            responses, _PRODUCT.brand, _PRODUCT.competitors
        )
        # Mock pool has a mix of mentioning and not mentioning the brand
        assert 0.0 <= scores.mention_rate <= 1.0
        assert 0.0 <= scores.share_of_voice <= 1.0
        assert scores.total_responses == 10

    def test_storage_roundtrip_with_mock(self, tmp_path: Path):
        responses = _run_mock(_make_client(), "storage-test", repetitions=3)
        storage = ResultStorage(base_path=tmp_path)
        storage.save("mock_test", responses)
        loaded = storage.load("mock_test")
        assert len(loaded) == 3
        for orig, load in zip(responses, loaded):
            assert orig.response_text == load.response_text

    def test_full_experiment_with_mock(self, tmp_path: Path):
        corpus = [
            Prompt(id="t1", text="Best code search tools?", category="test"),
            Prompt(id="t2", text="AcmeSearch vs CodeHound?", category="test"),
            Prompt(id="t3", text="How to search across repos?", category="test"),
        ]
        summaries, recall_stats = asyncio.run(
            run_experiment(
                corpus=corpus,
                model_aliases=["mock"],
                experiment_name="integration_test",
                product=_PRODUCT,
                repetitions=3,
                storage=ResultStorage(base_path=tmp_path),
                expected_outcomes_path=tmp_path / "absent.json",
            )
        )
        assert len(summaries) == 1
        s = summaries[0]
        assert s.model_alias == "mock"
        assert s.total_responses == 9  # 3 prompts x 3 reps
        assert s.error_count == 0
        assert 0.0 <= s.scores.mention_rate <= 1.0
        assert set(s.scores.competitor_mention_rates) == {
            "CodeHound",
            "FindGrep",
            "SearchLite",
        }
        # No expected outcomes file: recall scoring skipped cleanly
        assert recall_stats == []
