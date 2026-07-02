"""Power-analysis utilities for GEO pilot runs.

Reads a pilot results file (JSON-lines from :class:`geo.storage.ResultStorage`),
labels each response with the mention rule, and computes:

* per-prompt point estimates and within-prompt variance
* between-prompt variance
* the design-effect / cluster-adjusted variance of the aggregate rate
* the required sample size to detect a target shift at a given alpha and power

CLI usage::

    python -m geo.power_analysis \
        --results results/raw/pilot/mock/<date>/results.jsonl \
        --target-delta 0.05 \
        --alpha 0.05 \
        --power 0.80
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from geo.config import BrandSpec, load_product_config
from geo.scoring import score_binary_presence

# Standard normal critical values for alpha=0.05 (two-sided) and power=0.80
Z_ALPHA_2 = 1.959963984540054
Z_POWER_80 = 0.8416212335729143


@dataclass(frozen=True)
class PromptStats:
    """Per-prompt aggregated statistics for one labeling rule."""

    prompt_id: str
    category: str
    n_reps: int
    n_positive: int
    p_hat: float

    @property
    def within_variance(self) -> float:
        return self.p_hat * (1.0 - self.p_hat)


@dataclass(frozen=True)
class VarianceDecomposition:
    """Variance decomposition for one labeling rule across the pilot."""

    label_name: str
    n_prompts: int
    n_reps_per_prompt: float  # mean reps/prompt (handles light imbalance)
    n_total: int
    aggregate_rate: float
    mean_within_variance: float  # mean_i [p_i(1-p_i)]
    between_prompt_variance: float  # Var_i(p_hat_i)
    naive_se: float  # sqrt[p(1-p)/N_total], assumes all reps i.i.d.
    cluster_se: float  # sqrt[(within/(N_p*K) + between/N_p)]


@dataclass(frozen=True)
class SampleSizeRecommendation:
    """Required sample size for a two-sample two-sided proportion test."""

    label_name: str
    target_delta: float
    alpha: float
    power: float
    p_baseline: float
    required_se: float
    required_n_iid: int  # if reps were i.i.d. across prompts
    required_total_calls: int  # design-adjusted (cluster-aware)
    suggested_prompts: int  # held at corpus default
    suggested_reps: int  # back-computed from required_total_calls


def load_responses(path: Path) -> list[dict]:
    """Load JSON-lines, skipping rows with error != null."""
    rows: list[dict] = []
    with path.open() as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("error"):
                continue
            rows.append(r)
    return rows


def label_responses(
    rows: Sequence[dict],
    *,
    brand: BrandSpec,
    category_lookup: dict[str, str] | None = None,
) -> dict[str, list[tuple[int, str]]]:
    """Return ``{prompt_id: [(label, category), ...]}`` for the mention rule.

    Label is 1 if *brand* (name or any alias) is mentioned, 0 otherwise.
    ``category_lookup`` joins the row's prompt_id back to a category when the
    storage layer doesn't persist the category alongside each response.
    """
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in rows:
        text = r["response_text"]
        label = 1 if score_binary_presence(text, brand).mentioned else 0
        category = (
            (category_lookup or {}).get(r["prompt_id"])
            or r.get("category", "uncategorized")
        )
        out[r["prompt_id"]].append((label, category))
    return out


def compute_prompt_stats(
    labels_by_prompt: dict[str, list[tuple[int, str]]],
) -> list[PromptStats]:
    stats: list[PromptStats] = []
    for pid, items in labels_by_prompt.items():
        labels = [x[0] for x in items]
        cat = items[0][1]
        n = len(labels)
        n_pos = sum(labels)
        stats.append(
            PromptStats(
                prompt_id=pid,
                category=cat,
                n_reps=n,
                n_positive=n_pos,
                p_hat=n_pos / n if n else 0.0,
            )
        )
    return sorted(stats, key=lambda s: s.prompt_id)


def decompose_variance(
    label_name: str, prompt_stats: Sequence[PromptStats]
) -> VarianceDecomposition:
    if not prompt_stats:
        raise ValueError("No prompt stats provided")
    n_prompts = len(prompt_stats)
    n_total = sum(s.n_reps for s in prompt_stats)
    n_reps_per_prompt = n_total / n_prompts
    # Aggregate rate computed at the call level (matches how reports aggregate)
    aggregate_rate = sum(s.n_positive for s in prompt_stats) / n_total
    # Mean within-prompt variance (Bernoulli at each prompt)
    mean_within = statistics.fmean(s.within_variance for s in prompt_stats)
    # Between-prompt variance (across the p_hat_i values; sample variance)
    if n_prompts > 1:
        between = statistics.variance(s.p_hat for s in prompt_stats)
    else:
        between = 0.0
    # Naive SE (treat all reps as i.i.d.)
    naive_se = (
        math.sqrt(aggregate_rate * (1.0 - aggregate_rate) / n_total) if n_total else 0.0
    )
    # Cluster-aware SE for the prompt-balanced mean estimator
    cluster_se = math.sqrt(
        (mean_within / (n_prompts * n_reps_per_prompt)) + (between / n_prompts)
    )
    return VarianceDecomposition(
        label_name=label_name,
        n_prompts=n_prompts,
        n_reps_per_prompt=n_reps_per_prompt,
        n_total=n_total,
        aggregate_rate=aggregate_rate,
        mean_within_variance=mean_within,
        between_prompt_variance=between,
        naive_se=naive_se,
        cluster_se=cluster_se,
    )


def recommend_sample_size(
    decomp: VarianceDecomposition,
    *,
    target_delta: float = 0.05,
    alpha: float = 0.05,
    power: float = 0.80,
    fixed_prompts: int = 300,
) -> SampleSizeRecommendation:
    """Size a two-sided test that detects a shift of ``target_delta``.

    Uses the conservative two-sample form (your brand's rate before vs after
    an intervention, or vs a competitor, on the same corpus), where the
    required SE doubles in variance terms relative to a one-sample test.
    """
    z_alpha = Z_ALPHA_2 if abs(alpha - 0.05) < 1e-9 else _inv_normal_cdf(1 - alpha / 2)
    z_beta = Z_POWER_80 if abs(power - 0.80) < 1e-9 else _inv_normal_cdf(power)
    required_se = target_delta / (z_alpha + z_beta)

    p = decomp.aggregate_rate
    # For two independent proportions with equal allocation, variance of the
    # difference is 2 * p(1-p)/n. Required n per arm so that
    # sqrt(2 p(1-p)/n) = required_se  ->  n = 2 p(1-p)/required_se^2
    required_n_iid = (
        math.ceil(2.0 * p * (1.0 - p) / (required_se**2)) if required_se > 0 else 0
    )

    # Cluster-aware: variance of aggregate = within/(N_p * K) + between/N_p.
    # For a two-sample test on two independent rate estimates we need
    # 2 * single-sample variance <= required_se^2.
    # Hold N_p = corpus size (fixed_prompts). Solve for K:
    target_var = (required_se**2) / 2.0  # per-arm variance budget
    between_term = decomp.between_prompt_variance / fixed_prompts
    remaining = target_var - between_term
    if remaining <= 0:
        # Between-prompt variance alone exceeds the budget at the corpus
        # size; adding reps cannot reach the target. Flag with -1.
        suggested_reps = -1
        required_total_calls = -1
    else:
        suggested_reps = math.ceil(
            decomp.mean_within_variance / (fixed_prompts * remaining)
        )
        required_total_calls = fixed_prompts * suggested_reps

    return SampleSizeRecommendation(
        label_name=decomp.label_name,
        target_delta=target_delta,
        alpha=alpha,
        power=power,
        p_baseline=p,
        required_se=required_se,
        required_n_iid=required_n_iid,
        required_total_calls=required_total_calls,
        suggested_prompts=fixed_prompts,
        suggested_reps=suggested_reps,
    )


def _inv_normal_cdf(p: float) -> float:
    """Inverse of the standard normal CDF (Acklam approximation)."""
    if not 0 < p < 1:
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996,
         3.754408661907416]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
               (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
           ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Power analysis for GEO pilot runs.")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument(
        "--product",
        type=Path,
        default=Path("product.yaml"),
        help="Path to product.yaml (default: product.yaml in the current directory).",
    )
    parser.add_argument("--corpus", type=Path, default=None,
                        help="Corpus JSON used to recover prompt categories not persisted in results.")
    parser.add_argument("--target-delta", type=float, default=0.05)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--power", type=float, default=0.80)
    parser.add_argument("--fixed-prompts", type=int, default=300,
                        help="Number of prompts in the full corpus (held fixed when sizing reps).")
    args = parser.parse_args(argv)

    product = load_product_config(args.product)
    rows = load_responses(args.results)
    print(f"Loaded {len(rows)} successful responses from {args.results}")

    category_lookup: dict[str, str] = {}
    if args.corpus and args.corpus.exists():
        corpus_data = json.loads(args.corpus.read_text())
        category_lookup = {
            str(p["id"]): str(p.get("category", "uncategorized")) for p in corpus_data
        }

    labels = label_responses(
        rows, brand=product.brand, category_lookup=category_lookup
    )
    stats = compute_prompt_stats(labels)
    decomp = decompose_variance("mention", stats)
    rec = recommend_sample_size(
        decomp,
        target_delta=args.target_delta,
        alpha=args.alpha,
        power=args.power,
        fixed_prompts=args.fixed_prompts,
    )

    # Category-level aggregates
    by_cat: dict[str, list[float]] = defaultdict(list)
    for s in stats:
        by_cat[s.category].append(s.p_hat)
    cat_means = {c: statistics.fmean(v) for c, v in by_cat.items()}

    print()
    print(f"=== rule: mention ({product.brand.name}) ===")
    print(f"  N_prompts = {decomp.n_prompts}, mean reps/prompt = {decomp.n_reps_per_prompt:.2f}, N_total = {decomp.n_total}")
    print(f"  aggregate rate = {decomp.aggregate_rate:.3f}")
    print(f"  mean within-prompt variance = {decomp.mean_within_variance:.4f}")
    print(f"  between-prompt variance     = {decomp.between_prompt_variance:.4f}")
    print(f"  naive SE (i.i.d. assumption) = {decomp.naive_se:.4f}")
    print(f"  cluster-aware SE             = {decomp.cluster_se:.4f}")
    print("  Category-level p_hat:")
    for c, v in sorted(cat_means.items()):
        print(f"    {c:25s} {v:.3f}")
    print(f"  --- Sample size for a {args.target_delta*100:.1f}pp two-sample test (alpha={args.alpha}, power={args.power}) ---")
    print(f"  Required SE per arm: {rec.required_se:.4f}")
    print(f"  Required calls (i.i.d. assumption, 2 arms): {rec.required_n_iid} per arm x 2")
    if rec.suggested_reps > 0:
        print(f"  At {rec.suggested_prompts} prompts x {rec.suggested_reps} reps per arm = {rec.required_total_calls} calls")
    else:
        print(f"  Between-prompt variance dominates at {rec.suggested_prompts} prompts; more prompts needed.")

    output = {
        "mention": {
            "decomposition": {
                "n_prompts": decomp.n_prompts,
                "n_reps_per_prompt": decomp.n_reps_per_prompt,
                "n_total": decomp.n_total,
                "aggregate_rate": decomp.aggregate_rate,
                "mean_within_variance": decomp.mean_within_variance,
                "between_prompt_variance": decomp.between_prompt_variance,
                "naive_se": decomp.naive_se,
                "cluster_se": decomp.cluster_se,
                "category_p_hat": cat_means,
            },
            "sample_size": {
                "target_delta": rec.target_delta,
                "alpha": rec.alpha,
                "power": rec.power,
                "required_se": rec.required_se,
                "required_n_iid_per_arm": rec.required_n_iid,
                "suggested_prompts": rec.suggested_prompts,
                "suggested_reps": rec.suggested_reps,
                "required_total_calls_per_arm": rec.required_total_calls,
            },
        }
    }

    out_path = args.results.parent / "power_analysis.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nWrote analysis JSON to {out_path}")


if __name__ == "__main__":
    main()
