"""Shared test fixtures and helpers."""

from geo.config import BrandSpec, ProductConfig
from geo.llm_client import LLMResponse


def make_brand() -> BrandSpec:
    """The fictional target brand used across test fixtures."""
    return BrandSpec(name="AcmeSearch", aliases=("Acme Search", "acmesearch.io"))


def make_competitors() -> tuple[BrandSpec, ...]:
    """The fictional competitor set used across test fixtures."""
    return (
        BrandSpec(name="CodeHound", aliases=("codehound.dev",)),
        BrandSpec(name="FindGrep"),
        BrandSpec(name="SearchLite"),
    )


def make_product() -> ProductConfig:
    """A complete fictional ProductConfig for testing."""
    return ProductConfig(
        brand=make_brand(),
        competitors=make_competitors(),
        category="code search tools",
        semantic_judge_model="gpt-4o",
    )


def make_response(
    text: str = "test response",
    prompt_id: str = "q1",
    model_alias: str = "mock",
    repetition: int = 1,
) -> LLMResponse:
    """Build a minimal LLMResponse for testing."""
    return LLMResponse(
        model_alias=model_alias,
        model_id="mock-v1",
        provider="mock",
        timestamp="2026-03-31T00:00:00Z",
        prompt_id=prompt_id,
        prompt_text="test prompt",
        response_text=text,
        temperature=1.0,
        top_p=1.0,
        max_tokens=2048,
        repetition=repetition,
        latency_ms=10.0,
    )
