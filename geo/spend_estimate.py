"""Spend estimator for a full measurement run at a given sample size.

Pricing constants are the published per-token rates for each model, kept
here so the estimate is auditable and easy to update.

CLI usage::

    python -m geo.spend_estimate \
        --reps 12 \
        --prompts 300 \
        --include-judge
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Per-1M-token published rates (USD, 2026-01).
PRICING: dict[str, dict[str, float]] = {
    # gpt-4o-2024-08-06 (the model_id used by geo/config.py)
    "gpt-4o-2024-08-06": {"input_per_m": 2.50, "output_per_m": 10.00},
    # gemini-2.0-flash (Google AI pricing tier 1)
    "gemini-2.0-flash": {"input_per_m": 0.10, "output_per_m": 0.40},
    # gpt-4o judge for the semantic layer (same model as scoring.score_semantic)
    "gpt-4o-judge": {"input_per_m": 2.50, "output_per_m": 10.00},
}

# Typical per-call token usage for short discovery prompts. Rounded
# conservatively (slightly high) so the estimate doesn't under-quote spend.
PROMPT_TOKENS_PER_CALL = 25
RESPONSE_TOKENS_PER_CALL = 500

# Judge call shape (G-Eval rubric in geo/scoring.py):
#   prompt text + response text + rubric template is about 1.5K input tokens
#   structured JSON output is about 150 tokens
JUDGE_INPUT_TOKENS_PER_CALL = 1500
JUDGE_OUTPUT_TOKENS_PER_CALL = 150


@dataclass(frozen=True)
class LaneCost:
    lane: str
    model_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


def estimate_call_cost(model_id: str, n_calls: int) -> LaneCost:
    rates = PRICING[model_id]
    in_tokens = n_calls * PROMPT_TOKENS_PER_CALL
    out_tokens = n_calls * RESPONSE_TOKENS_PER_CALL
    cost = (
        in_tokens / 1_000_000 * rates["input_per_m"]
        + out_tokens / 1_000_000 * rates["output_per_m"]
    )
    return LaneCost(
        lane="generation",
        model_id=model_id,
        calls=n_calls,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cost_usd=round(cost, 4),
    )


def estimate_judge_cost(n_responses_to_judge: int) -> LaneCost:
    rates = PRICING["gpt-4o-judge"]
    in_tokens = n_responses_to_judge * JUDGE_INPUT_TOKENS_PER_CALL
    out_tokens = n_responses_to_judge * JUDGE_OUTPUT_TOKENS_PER_CALL
    cost = (
        in_tokens / 1_000_000 * rates["input_per_m"]
        + out_tokens / 1_000_000 * rates["output_per_m"]
    )
    return LaneCost(
        lane="semantic_judge",
        model_id="gpt-4o-judge",
        calls=n_responses_to_judge,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cost_usd=round(cost, 4),
    )


def estimate_run(
    *, prompts: int, reps: int, include_judge: bool,
) -> dict:
    """Estimate spend for a three-model measurement run.

    Models priced:
        - claude (CLI/OAuth lane): cost = 0 (runs on your subscription)
        - gpt-4o-2024-08-06   (paid)
        - gemini-2.0-flash    (paid)
    Plus the optional LLM-as-judge layer (gpt-4o) applied to every response
    across all three models.
    """
    n_calls_per_model = prompts * reps
    lanes = [
        estimate_call_cost("gpt-4o-2024-08-06", n_calls_per_model),
        estimate_call_cost("gemini-2.0-flash", n_calls_per_model),
    ]
    total_responses = n_calls_per_model * 3  # claude + chatgpt + gemini
    judge_cost = (
        estimate_judge_cost(total_responses) if include_judge else None
    )
    total_cost = sum(lane.cost_usd for lane in lanes) + (
        judge_cost.cost_usd if judge_cost else 0.0
    )
    return {
        "prompts": prompts,
        "reps": reps,
        "calls_per_model": n_calls_per_model,
        "total_responses_generated": total_responses,
        "lanes": [asdict(lane) for lane in lanes],
        "judge_cost": asdict(judge_cost) if judge_cost else None,
        "claude_oauth_cost_usd": 0.0,
        "total_cost_usd": round(total_cost, 2),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Spend estimator for a GEO measurement run."
    )
    parser.add_argument("--prompts", type=int, default=300)
    parser.add_argument("--reps", type=int, required=True)
    parser.add_argument("--include-judge", action="store_true",
                        help="Include the semantic LLM-as-judge layer (gpt-4o on every response).")
    parser.add_argument("--out", type=Path, default=None,
                        help="Write JSON output to this path in addition to printing.")
    args = parser.parse_args(argv)

    result = estimate_run(
        prompts=args.prompts, reps=args.reps, include_judge=args.include_judge,
    )
    print(json.dumps(result, indent=2))
    if args.out:
        args.out.write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
