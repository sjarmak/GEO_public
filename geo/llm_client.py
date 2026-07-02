"""Multi-model async LLM client with rate limiting and retry logic.

Supported providers:
  * ``claude_cli`` -- Claude Code CLI (OAuth-bound; no API key). Default for
    the ``claude`` alias.
  * ``anthropic`` -- Anthropic Messages API. Requires ``ANTHROPIC_API_KEY``.
    Used by the ``claude-api`` alias only.
  * ``openai`` -- OpenAI Chat Completions. Requires ``OPENAI_API_KEY``.
  * ``google`` -- Google Generative AI. Requires ``GOOGLE_API_KEY``.
  * ``mock`` -- Deterministic seeded responses templated from your
    ``product.yaml`` (used by ``--dry-run``; no keys, no network).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from geo.config import BrandSpec, ModelSpec, ProductConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response data class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMResponse:
    """Immutable record of a single LLM call."""

    model_alias: str
    model_id: str
    provider: str
    timestamp: str
    prompt_id: str
    prompt_text: str
    response_text: str
    temperature: float
    top_p: float
    max_tokens: int
    repetition: int
    latency_ms: float
    error: str | None = None


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Simple token-bucket rate limiter per model."""

    def __init__(self, requests_per_minute: int) -> None:
        self._rpm = requests_per_minute
        self._interval = 60.0 / max(requests_per_minute, 1)
        self._last_request: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()


# ---------------------------------------------------------------------------
# Provider call implementations
# ---------------------------------------------------------------------------


async def _call_anthropic(
    model_id: str,
    prompt: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    message = await client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in message.content if hasattr(block, "text")]
    return "\n".join(parts)


async def _call_openai(
    model_id: str,
    prompt: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
) -> str:
    import openai

    client = openai.AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
    )
    response = await client.chat.completions.create(
        model=model_id,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    choice = response.choices[0]
    return choice.message.content or ""


async def _call_google(
    model_id: str,
    prompt: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
) -> str:
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    model = genai.GenerativeModel(model_id)
    generation_config = genai.types.GenerationConfig(
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_tokens,
    )
    response = await asyncio.to_thread(
        model.generate_content,
        prompt,
        generation_config=generation_config,
    )
    return response.text or ""


async def _call_claude_cli(
    model_id: str,
    prompt: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
) -> str:
    """Call Claude via the CLI using OAuth credentials (no API key needed).

    Uses ``claude -p --output-format json`` in headless mode.
    """
    import json
    import subprocess

    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model_id,
        "--max-turns",
        "1",
    ]

    # Append instruction to prevent tool use (which consumes turns)
    prompt = prompt + "\n\nAnswer directly without using any tools."

    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/tmp",  # avoid picking up project instructions from cwd
    )

    if result.returncode != 0:
        stderr_msg = result.stderr.strip()[:500] if result.stderr else "(no stderr)"
        raise RuntimeError(
            f"claude CLI exited with code {result.returncode}: {stderr_msg}"
        )

    data = json.loads(result.stdout)
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI error: {data.get('result', 'unknown')}")

    return str(data.get("result", ""))


_PROVIDER_DISPATCH = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "google": _call_google,
    "claude_cli": _call_claude_cli,
}


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

_BRAND_MENTION_PROBABILITY = 0.55
_COMPETITOR_PROBABILITY_RANGE = (0.30, 0.70)
_ALIAS_DISPLAY_PROBABILITY = 0.2

_LIST_INTROS = (
    "Here are some {category} worth evaluating:",
    "The options that come up most often for {category} are:",
    "Teams evaluating {category} usually shortlist these:",
)

_BLURBS = (
    "a strong fit for larger teams",
    "lightweight and quick to set up",
    "well documented with an active community",
    "popular with open source projects",
    "worth a look if you need self-hosting",
    "a solid free tier for smaller teams",
)

_PROSE_SENTENCES = (
    "{name} comes up often in this space; it is {blurb}.",
    "Many teams settle on {name} because it is {blurb}.",
    "{name} is another option to consider, since it is {blurb}.",
)

_GENERIC_ITEMS = (
    "Your editor's built-in search",
    "Standard command line utilities",
)

_NO_MENTION_TEXT = (
    "For {category}, most teams get a long way with built-in tooling before "
    "adopting a dedicated product. Start with what your editor and version "
    "control platform already provide, then evaluate dedicated tools once "
    "you hit their limits."
)


@dataclass(frozen=True)
class MockResponder:
    """Deterministic mock responses templated from a ProductConfig.

    Each response mentions the configured brand with probability
    ~0.55 and each competitor with a fixed per-competitor probability in
    [0.30, 0.70], so a dry run produces a meaningful demo dashboard for any
    product.yaml. Output is fully determined by (seed, prompt_id, repetition).
    """

    product: ProductConfig
    seed: int = 0

    def _competitor_probability(self, name: str) -> float:
        lo, hi = _COMPETITOR_PROBABILITY_RANGE
        return random.Random(f"{self.seed}:prob:{name}").uniform(lo, hi)

    def _display_name(self, spec: BrandSpec, rng: random.Random) -> str:
        if spec.aliases and rng.random() < _ALIAS_DISPLAY_PROBABILITY:
            return rng.choice(spec.aliases)
        return spec.name

    def generate(self, prompt_id: str, repetition: int) -> str:
        """Produce one templated response for a prompt repetition."""
        rng = random.Random(f"{self.seed}:{prompt_id}:{repetition}")
        category = self.product.category

        mentioned: list[str] = []
        if rng.random() < _BRAND_MENTION_PROBABILITY:
            mentioned.append(self._display_name(self.product.brand, rng))
        for comp in self.product.competitors:
            if rng.random() < self._competitor_probability(comp.name):
                mentioned.append(self._display_name(comp, rng))

        if not mentioned:
            return _NO_MENTION_TEXT.format(category=category)

        rng.shuffle(mentioned)
        if rng.random() < 0.5:
            return self._list_response(mentioned, category, rng)
        return self._prose_response(mentioned, category, rng)

    def _list_response(
        self, names: list[str], category: str, rng: random.Random
    ) -> str:
        intro = rng.choice(_LIST_INTROS).format(category=category)
        items = list(names)
        if rng.random() < 0.5:
            items.append(rng.choice(_GENERIC_ITEMS))
        lines = [
            f"{i}. {name} - {rng.choice(_BLURBS)}"
            for i, name in enumerate(items, start=1)
        ]
        closing = "The right choice depends on your team size and workflow."
        return intro + "\n\n" + "\n".join(lines) + "\n\n" + closing

    def _prose_response(
        self, names: list[str], category: str, rng: random.Random
    ) -> str:
        sentences = [f"There are a few good options for {category}."]
        for name in names:
            template = rng.choice(_PROSE_SENTENCES)
            sentences.append(template.format(name=name, blurb=rng.choice(_BLURBS)))
        sentences.append(
            "Try one or two on a real task before committing; day-to-day "
            "ergonomics matter more than feature checklists."
        )
        return " ".join(sentences)


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


@dataclass
class LLMClient:
    """Async multi-provider LLM client with rate limiting and retries.

    For the mock provider, pass a :class:`MockResponder` built from your
    ProductConfig; real providers need no extra setup beyond their API keys.
    """

    max_retries: int = 5
    base_backoff: float = 1.0
    mock_responder: MockResponder | None = None
    _limiters: dict[str, _RateLimiter] = field(
        default_factory=dict, init=False, repr=False
    )

    def _get_limiter(self, model_alias: str, rpm: int) -> _RateLimiter:
        if model_alias not in self._limiters:
            self._limiters[model_alias] = _RateLimiter(rpm)
        return self._limiters[model_alias]

    def _error_response(
        self,
        *,
        model_alias: str,
        spec: ModelSpec,
        prompt_id: str,
        prompt_text: str,
        repetition: int,
        temperature: float,
        top_p: float,
        max_tokens: int,
        error: str,
    ) -> LLMResponse:
        return LLMResponse(
            model_alias=model_alias,
            model_id=spec.model_id,
            provider=spec.provider,
            timestamp=datetime.now(timezone.utc).isoformat(),
            prompt_id=prompt_id,
            prompt_text=prompt_text,
            response_text="",
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            repetition=repetition,
            latency_ms=0.0,
            error=error,
        )

    async def _call_mock(
        self,
        *,
        model_alias: str,
        spec: ModelSpec,
        prompt_id: str,
        prompt_text: str,
        repetition: int,
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> LLMResponse:
        if self.mock_responder is None:
            raise ValueError(
                "Mock provider requires a MockResponder built from a "
                "ProductConfig. Pass LLMClient(mock_responder=...)."
            )
        start = time.monotonic()
        await asyncio.sleep(0.005)  # simulate tiny latency
        text = self.mock_responder.generate(prompt_id, repetition)
        elapsed_ms = (time.monotonic() - start) * 1000
        return LLMResponse(
            model_alias=model_alias,
            model_id=spec.model_id,
            provider=spec.provider,
            timestamp=datetime.now(timezone.utc).isoformat(),
            prompt_id=prompt_id,
            prompt_text=prompt_text,
            response_text=text,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            repetition=repetition,
            latency_ms=elapsed_ms,
        )

    async def _call_with_retry(
        self,
        model_alias: str,
        spec: ModelSpec,
        prompt_id: str,
        prompt_text: str,
        repetition: int,
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Call an LLM provider with exponential backoff retry."""
        if spec.provider == "mock":
            return await self._call_mock(
                model_alias=model_alias,
                spec=spec,
                prompt_id=prompt_id,
                prompt_text=prompt_text,
                repetition=repetition,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )

        limiter = self._get_limiter(model_alias, spec.requests_per_minute)
        call_fn = _PROVIDER_DISPATCH.get(spec.provider)
        if call_fn is None:
            return self._error_response(
                model_alias=model_alias,
                spec=spec,
                prompt_id=prompt_id,
                prompt_text=prompt_text,
                repetition=repetition,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                error=f"Unsupported provider: {spec.provider}",
            )

        last_error: str | None = None
        for attempt in range(self.max_retries):
            await limiter.acquire()
            start = time.monotonic()
            try:
                text = await call_fn(
                    model_id=spec.model_id,
                    prompt=prompt_text,
                    temperature=temperature,
                    top_p=top_p,
                    max_tokens=max_tokens,
                )
                elapsed_ms = (time.monotonic() - start) * 1000
                return LLMResponse(
                    model_alias=model_alias,
                    model_id=spec.model_id,
                    provider=spec.provider,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    prompt_id=prompt_id,
                    prompt_text=prompt_text,
                    response_text=text,
                    temperature=temperature,
                    top_p=top_p,
                    max_tokens=max_tokens,
                    repetition=repetition,
                    latency_ms=elapsed_ms,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                backoff = self.base_backoff * (2**attempt)
                logger.warning(
                    "Attempt %d/%d for %s prompt=%s rep=%d failed: %s "
                    "(backoff %.1fs)",
                    attempt + 1,
                    self.max_retries,
                    model_alias,
                    prompt_id,
                    repetition,
                    last_error,
                    backoff,
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(backoff)

        # All retries exhausted -- return error record (never silently drop)
        return self._error_response(
            model_alias=model_alias,
            spec=spec,
            prompt_id=prompt_id,
            prompt_text=prompt_text,
            repetition=repetition,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            error=f"All {self.max_retries} retries exhausted. Last: {last_error}",
        )

    async def run_prompt(
        self,
        *,
        model_alias: str,
        model_spec: ModelSpec,
        prompt_id: str,
        prompt_text: str,
        repetitions: int = 20,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 2048,
    ) -> list[LLMResponse]:
        """Run a single prompt N times against one model.

        Returns a list of LLMResponse (one per repetition), including any
        that failed after exhausting retries.
        """
        tasks = [
            self._call_with_retry(
                model_alias=model_alias,
                spec=model_spec,
                prompt_id=prompt_id,
                prompt_text=prompt_text,
                repetition=rep,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
            for rep in range(1, repetitions + 1)
        ]
        return list(await asyncio.gather(*tasks))
