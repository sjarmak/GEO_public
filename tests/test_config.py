"""Tests for configuration and the product.yaml loader (offline)."""

import dataclasses
from pathlib import Path

import pytest

from geo.config import (
    MODEL_REGISTRY,
    BrandSpec,
    Config,
    ProductConfig,
    load_product_config,
    mention_pattern,
)

_VALID_YAML = """\
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

scoring:
  semantic_judge_model: gpt-4o
"""


class TestModelRegistry:
    def test_has_claude_oauth_default(self):
        # Default `claude` alias runs through the OAuth-bound CLI so
        # experiments do not require ANTHROPIC_API_KEY.
        assert "claude" in MODEL_REGISTRY
        assert MODEL_REGISTRY["claude"].provider == "claude_cli"

    def test_has_claude_api(self):
        assert "claude-api" in MODEL_REGISTRY
        assert MODEL_REGISTRY["claude-api"].provider == "anthropic"

    def test_has_chatgpt(self):
        assert "chatgpt" in MODEL_REGISTRY
        assert MODEL_REGISTRY["chatgpt"].provider == "openai"

    def test_has_gemini(self):
        assert "gemini" in MODEL_REGISTRY
        assert MODEL_REGISTRY["gemini"].provider == "google"

    def test_has_mock(self):
        assert "mock" in MODEL_REGISTRY
        assert MODEL_REGISTRY["mock"].provider == "mock"

    def test_version_pinned(self):
        for alias, spec in MODEL_REGISTRY.items():
            assert spec.model_id, f"{alias} has empty model_id"
            if alias != "mock":
                assert (
                    "-" in spec.model_id or "." in spec.model_id
                ), f"{alias} model_id '{spec.model_id}' doesn't look version-pinned"


class TestModelSpec:
    def test_frozen(self):
        spec = MODEL_REGISTRY["mock"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.provider = "changed"  # type: ignore[misc]


class TestConfig:
    def test_get_model_valid(self):
        cfg = Config()
        spec = cfg.get_model("mock")
        assert spec.provider == "mock"

    def test_get_model_invalid(self):
        cfg = Config()
        with pytest.raises(KeyError, match="Unknown model alias"):
            cfg.get_model("nonexistent")


class TestBrandSpec:
    def test_frozen(self):
        brand = BrandSpec(name="AcmeSearch")
        with pytest.raises(dataclasses.FrozenInstanceError):
            brand.name = "other"  # type: ignore[misc]

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            BrandSpec(name="   ")


class TestMentionPattern:
    def test_matches_name(self):
        pattern = mention_pattern(BrandSpec(name="AcmeSearch"))
        assert pattern.search("Try AcmeSearch for code search.")

    def test_matches_aliases(self):
        brand = BrandSpec(name="AcmeSearch", aliases=("Acme Search", "acmesearch.io"))
        pattern = mention_pattern(brand)
        assert pattern.search("Acme Search is popular.")
        assert pattern.search("See acmesearch.io for details.")

    def test_case_insensitive(self):
        pattern = mention_pattern(BrandSpec(name="AcmeSearch"))
        assert pattern.search("ACMESEARCH is great.")
        assert pattern.search("acmesearch is great.")

    def test_word_boundary(self):
        pattern = mention_pattern(BrandSpec(name="FindGrep"))
        assert pattern.search("Use FindGrep today.")
        assert not pattern.search("Use FindGrepper today.")

    def test_counts_all_mentions(self):
        brand = BrandSpec(name="AcmeSearch", aliases=("Acme Search",))
        pattern = mention_pattern(brand)
        text = "AcmeSearch is one option. Acme Search has a free tier."
        assert len(pattern.findall(text)) == 2

    def test_name_with_trailing_punctuation(self):
        pattern = mention_pattern(BrandSpec(name="C++"))
        assert pattern.search("We recommend C++ for performance-critical code.")
        assert pattern.search("(C++)")
        assert not pattern.search("C++11 is a different term.")

    def test_name_with_leading_punctuation(self):
        pattern = mention_pattern(BrandSpec(name=".NET"))
        assert pattern.search("Build it on .NET today.")
        assert not pattern.search("ASP.NET is a different product.")

    def test_alias_with_punctuation_edges(self):
        brand = BrandSpec(name="AcmeSearch", aliases=("C#",))
        pattern = mention_pattern(brand)
        assert pattern.search("Written in C# for speed.")
        assert pattern.search("AcmeSearch supports C#.")


class TestLoadProductConfig:
    def test_happy_path(self, tmp_path: Path):
        path = tmp_path / "product.yaml"
        path.write_text(_VALID_YAML)
        product = load_product_config(path)

        assert isinstance(product, ProductConfig)
        assert product.brand.name == "AcmeSearch"
        assert product.brand.aliases == ("Acme Search", "acmesearch.io")
        assert product.category == "code search tools"
        assert [c.name for c in product.competitors] == [
            "CodeHound",
            "FindGrep",
            "SearchLite",
        ]
        assert product.competitors[0].aliases == ("codehound.dev",)
        assert product.semantic_judge_model == "gpt-4o"

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Product config not found"):
            load_product_config(tmp_path / "nope.yaml")

    def test_missing_product_section(self, tmp_path: Path):
        path = tmp_path / "product.yaml"
        path.write_text("competitors: []\n")
        with pytest.raises(ValueError, match="'product' section"):
            load_product_config(path)

    def test_missing_competitors_section(self, tmp_path: Path):
        path = tmp_path / "product.yaml"
        path.write_text(
            "product:\n  name: AcmeSearch\n  category: code search tools\n"
        )
        with pytest.raises(ValueError, match="'competitors' section"):
            load_product_config(path)

    def test_empty_name(self, tmp_path: Path):
        path = tmp_path / "product.yaml"
        path.write_text(
            "product:\n  name: ''\n  category: code search tools\ncompetitors: []\n"
        )
        with pytest.raises(ValueError, match="product.name"):
            load_product_config(path)

    def test_missing_category(self, tmp_path: Path):
        path = tmp_path / "product.yaml"
        path.write_text("product:\n  name: AcmeSearch\ncompetitors: []\n")
        with pytest.raises(ValueError, match="category"):
            load_product_config(path)

    def test_empty_competitor_name(self, tmp_path: Path):
        path = tmp_path / "product.yaml"
        path.write_text(
            "product:\n  name: AcmeSearch\n  category: code search tools\n"
            "competitors:\n  - name: ''\n"
        )
        with pytest.raises(ValueError, match=r"competitors\[0\]"):
            load_product_config(path)

    def test_scoring_defaults(self, tmp_path: Path):
        path = tmp_path / "product.yaml"
        path.write_text(
            "product:\n  name: AcmeSearch\n  category: code search tools\n"
            "competitors: []\n"
        )
        product = load_product_config(path)
        assert product.semantic_judge_model == "gpt-4o"
        assert product.competitors == ()

    def test_frozen(self, tmp_path: Path):
        path = tmp_path / "product.yaml"
        path.write_text(_VALID_YAML)
        product = load_product_config(path)
        with pytest.raises(dataclasses.FrozenInstanceError):
            product.category = "other"  # type: ignore[misc]

    def test_repo_default_product_yaml_loads(self):
        # The checked-in example product.yaml must satisfy the loader.
        repo_yaml = Path(__file__).resolve().parents[1] / "product.yaml"
        product = load_product_config(repo_yaml)
        assert product.brand.name == "AcmeSearch"
        assert len(product.competitors) == 3
