"""Compare two GEO snapshots and classify drift as PASS / WARN / FAIL.

The detector treats a model update like a deployment: a *baseline*
snapshot taken on the previous model is the contract; a *candidate*
snapshot taken on the new model is the deploy artifact. The output
:class:`RegressionReport` is the release gate. PASS means ship the
new model, WARN means flag for review, FAIL means hold.

Drift is classified by combining two signals:

1. **Effect size**: the absolute change in a metric (e.g. mention rate
   dropped 8 percentage points). Thresholds in
   :class:`RegressionThresholds` decide whether the change is large
   enough to matter.
2. **Statistical significance**: a two-proportion z-test on rate
   metrics (mention rate, list appearance rate, recall, competitor
   rates). This filters out noise from small sample sizes.

A finding is only flagged as WARN/FAIL when *both* the effect size
threshold is exceeded *and* the result is significant (p < 0.05). This
keeps the detector mechanical and explainable: every flag is a number
the user can verify by hand. No semantic judgment happens here; that
is delegated to the user reading the report.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from geo.regression.snapshot import RecallEntry, Snapshot


class Severity(str, Enum):
    """Severity of a single regression finding (or overall verdict)."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


# Severity ordering for "max" aggregation. Higher value == worse.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.PASS: 0,
    Severity.WARN: 1,
    Severity.FAIL: 2,
}


def _max_severity(items: Sequence[Severity]) -> Severity:
    if not items:
        return Severity.PASS
    return max(items, key=lambda s: _SEVERITY_RANK[s])


@dataclass(frozen=True)
class RegressionThresholds:
    """Tunable thresholds for classifying regressions.

    All values are expressed as absolute deltas (not percentages of the
    baseline) so they are stable when the baseline is near zero. Lower
    values catch smaller regressions at the cost of more noise; the
    defaults were chosen to surface the kind of multi-percentage-point
    drops a user would notice in a dashboard.
    """

    # Mention rate / list appearance / recall: drop in absolute fraction
    rate_warn_drop: float = 0.05  # 5 percentage points
    rate_fail_drop: float = 0.10  # 10 percentage points

    # Share of voice: drop in absolute fraction
    sov_warn_drop: float = 0.05
    sov_fail_drop: float = 0.10

    # First-mention offset: increase in characters (later mention = worse)
    offset_warn_increase: float = 200.0
    offset_fail_increase: float = 500.0

    # Average list rank: increase (lower in list = worse)
    rank_warn_increase: float = 1.0
    rank_fail_increase: float = 2.0

    # Misrepresentation count: any new misrep is at minimum a WARN.
    # An increase >= fail_increase escalates to FAIL.
    misrep_fail_increase: int = 5

    # Competitor mention rate: rise in absolute fraction
    competitor_warn_increase: float = 0.10
    competitor_fail_increase: float = 0.20

    # Statistical significance threshold for rate-based comparisons.
    significance_p: float = 0.05


@dataclass(frozen=True)
class Finding:
    """A single per-metric regression finding."""

    metric: str
    baseline_value: float | None
    candidate_value: float | None
    delta: float | None  # candidate - baseline (None if either side missing)
    severity: Severity
    p_value: float | None  # None when significance test does not apply
    rationale: str

    @property
    def regressed(self) -> bool:
        return self.severity is not Severity.PASS


@dataclass(frozen=True)
class RegressionReport:
    """Aggregate outcome of comparing two snapshots."""

    baseline_id: str
    candidate_id: str
    baseline_model_id: str
    candidate_model_id: str
    overall_severity: Severity
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    notes: str = ""

    @property
    def regressions(self) -> list[Finding]:
        return [f for f in self.findings if f.regressed]

    @property
    def exit_code(self) -> int:
        """Conventional CI exit code: 0=PASS, 1=WARN, 2=FAIL."""
        return _SEVERITY_RANK[self.overall_severity]


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------


def _normal_sf(z: float) -> float:
    """Two-sided survival function of the standard normal distribution.

    Using ``math.erfc`` keeps this dependency-free; SciPy would be
    overkill for a single z-test.
    """
    return math.erfc(abs(z) / math.sqrt(2.0))


def two_proportion_p_value(
    successes_a: int,
    total_a: int,
    successes_b: int,
    total_b: int,
) -> float | None:
    """Two-sided p-value for the two-proportion z-test.

    Returns ``None`` if either sample is empty or if both proportions
    are degenerate (0 or 1 with no variance), since the z-test is
    undefined in those cases.
    """
    if total_a <= 0 or total_b <= 0:
        return None

    p_a = successes_a / total_a
    p_b = successes_b / total_b
    pooled = (successes_a + successes_b) / (total_a + total_b)
    var = pooled * (1 - pooled) * (1 / total_a + 1 / total_b)

    if var <= 0:
        # Both rates are identical and degenerate (both 0 or both 1).
        return None

    z = (p_b - p_a) / math.sqrt(var)
    return _normal_sf(z)


# ---------------------------------------------------------------------------
# Per-metric checks
# ---------------------------------------------------------------------------


def _classify_rate_drop(
    delta: float,
    p_value: float | None,
    thresholds: RegressionThresholds,
    *,
    warn_drop: float,
    fail_drop: float,
) -> Severity:
    """Drop-in rate metric: candidate - baseline. Negative = regression."""
    if p_value is not None and p_value > thresholds.significance_p:
        return Severity.PASS
    if delta <= -fail_drop:
        return Severity.FAIL
    if delta <= -warn_drop:
        return Severity.WARN
    return Severity.PASS


def _check_mention_rate(
    baseline: Snapshot, candidate: Snapshot, thresholds: RegressionThresholds
) -> Finding:
    base = baseline.mention_rate
    cand = candidate.mention_rate
    delta = cand - base
    p = two_proportion_p_value(
        round(base * baseline.total_responses),
        baseline.total_responses,
        round(cand * candidate.total_responses),
        candidate.total_responses,
    )
    severity = _classify_rate_drop(
        delta,
        p,
        thresholds,
        warn_drop=thresholds.rate_warn_drop,
        fail_drop=thresholds.rate_fail_drop,
    )
    return Finding(
        metric="mention_rate",
        baseline_value=base,
        candidate_value=cand,
        delta=delta,
        severity=severity,
        p_value=p,
        rationale=(
            f"mention_rate {base:.1%} -> {cand:.1%} (delta {delta:+.1%}, "
            f"p={p:.4f})" if p is not None
            else f"mention_rate {base:.1%} -> {cand:.1%} (delta {delta:+.1%})"
        ),
    )


def _check_share_of_voice(
    baseline: Snapshot, candidate: Snapshot, thresholds: RegressionThresholds
) -> Finding:
    delta = candidate.share_of_voice - baseline.share_of_voice
    severity = Severity.PASS
    if delta <= -thresholds.sov_fail_drop:
        severity = Severity.FAIL
    elif delta <= -thresholds.sov_warn_drop:
        severity = Severity.WARN
    return Finding(
        metric="share_of_voice",
        baseline_value=baseline.share_of_voice,
        candidate_value=candidate.share_of_voice,
        delta=delta,
        severity=severity,
        p_value=None,
        rationale=(
            f"share_of_voice {baseline.share_of_voice:.1%} -> "
            f"{candidate.share_of_voice:.1%} (delta {delta:+.1%})"
        ),
    )


def _check_first_mention_offset(
    baseline: Snapshot, candidate: Snapshot, thresholds: RegressionThresholds
) -> Finding:
    base = baseline.avg_first_mention_offset
    cand = candidate.avg_first_mention_offset
    if base is None or cand is None:
        return Finding(
            metric="avg_first_mention_offset",
            baseline_value=base,
            candidate_value=cand,
            delta=None,
            severity=Severity.PASS,
            p_value=None,
            rationale="avg_first_mention_offset unavailable on one side; skipping",
        )
    delta = cand - base
    severity = Severity.PASS
    if delta >= thresholds.offset_fail_increase:
        severity = Severity.FAIL
    elif delta >= thresholds.offset_warn_increase:
        severity = Severity.WARN
    return Finding(
        metric="avg_first_mention_offset",
        baseline_value=base,
        candidate_value=cand,
        delta=delta,
        severity=severity,
        p_value=None,
        rationale=(
            f"avg_first_mention_offset {base:.0f} -> {cand:.0f} chars "
            f"(delta {delta:+.0f})"
        ),
    )


def _check_list_appearance_rate(
    baseline: Snapshot, candidate: Snapshot, thresholds: RegressionThresholds
) -> Finding:
    base = baseline.list_appearance_rate
    cand = candidate.list_appearance_rate
    delta = cand - base
    p = two_proportion_p_value(
        round(base * baseline.total_responses),
        baseline.total_responses,
        round(cand * candidate.total_responses),
        candidate.total_responses,
    )
    severity = _classify_rate_drop(
        delta,
        p,
        thresholds,
        warn_drop=thresholds.rate_warn_drop,
        fail_drop=thresholds.rate_fail_drop,
    )
    return Finding(
        metric="list_appearance_rate",
        baseline_value=base,
        candidate_value=cand,
        delta=delta,
        severity=severity,
        p_value=p,
        rationale=(
            f"list_appearance_rate {base:.1%} -> {cand:.1%} (delta {delta:+.1%})"
        ),
    )


def _check_avg_list_rank(
    baseline: Snapshot, candidate: Snapshot, thresholds: RegressionThresholds
) -> Finding:
    base = baseline.avg_list_rank
    cand = candidate.avg_list_rank
    if base is None or cand is None:
        return Finding(
            metric="avg_list_rank",
            baseline_value=base,
            candidate_value=cand,
            delta=None,
            severity=Severity.PASS,
            p_value=None,
            rationale="avg_list_rank unavailable on one side; skipping",
        )
    delta = cand - base
    severity = Severity.PASS
    if delta >= thresholds.rank_fail_increase:
        severity = Severity.FAIL
    elif delta >= thresholds.rank_warn_increase:
        severity = Severity.WARN
    return Finding(
        metric="avg_list_rank",
        baseline_value=base,
        candidate_value=cand,
        delta=delta,
        severity=severity,
        p_value=None,
        rationale=f"avg_list_rank {base:.2f} -> {cand:.2f} (delta {delta:+.2f})",
    )


def _check_misrepresentations(
    baseline: Snapshot, candidate: Snapshot, thresholds: RegressionThresholds
) -> list[Finding]:
    findings: list[Finding] = []
    misrep_ids = sorted(
        set(baseline.misrepresentation_counts) | set(candidate.misrepresentation_counts)
    )
    for mid in misrep_ids:
        base = baseline.misrepresentation_counts.get(mid, 0)
        cand = candidate.misrepresentation_counts.get(mid, 0)
        delta = cand - base
        if delta <= 0:
            severity = Severity.PASS
        elif delta >= thresholds.misrep_fail_increase:
            severity = Severity.FAIL
        else:
            severity = Severity.WARN
        findings.append(
            Finding(
                metric=f"misrep::{mid}",
                baseline_value=float(base),
                candidate_value=float(cand),
                delta=float(delta),
                severity=severity,
                p_value=None,
                rationale=f"misrep {mid} count {base} -> {cand} (delta {delta:+d})",
            )
        )
    return findings


def _check_competitor_rates(
    baseline: Snapshot, candidate: Snapshot, thresholds: RegressionThresholds
) -> list[Finding]:
    findings: list[Finding] = []
    competitors = sorted(
        set(baseline.competitor_mention_rates) | set(candidate.competitor_mention_rates)
    )
    for comp in competitors:
        base = baseline.competitor_mention_rates.get(comp, 0.0)
        cand = candidate.competitor_mention_rates.get(comp, 0.0)
        delta = cand - base
        # Only flag *increases* in competitor mentions (they erode share of voice).
        severity = Severity.PASS
        if delta >= thresholds.competitor_fail_increase:
            severity = Severity.FAIL
        elif delta >= thresholds.competitor_warn_increase:
            severity = Severity.WARN

        p = two_proportion_p_value(
            round(base * baseline.total_responses),
            baseline.total_responses,
            round(cand * candidate.total_responses),
            candidate.total_responses,
        )
        if p is not None and p > thresholds.significance_p:
            severity = Severity.PASS

        findings.append(
            Finding(
                metric=f"competitor::{comp}",
                baseline_value=base,
                candidate_value=cand,
                delta=delta,
                severity=severity,
                p_value=p,
                rationale=(
                    f"competitor {comp!r} {base:.1%} -> {cand:.1%} "
                    f"(delta {delta:+.1%})"
                ),
            )
        )
    return findings


def _check_recall_by_expectation(
    baseline: Snapshot, candidate: Snapshot, thresholds: RegressionThresholds
) -> list[Finding]:
    base_by_level: dict[str, RecallEntry] = {
        e.level: e for e in baseline.recall_by_expectation
    }
    cand_by_level: dict[str, RecallEntry] = {
        e.level: e for e in candidate.recall_by_expectation
    }
    findings: list[Finding] = []
    for level in sorted(set(base_by_level) | set(cand_by_level)):
        base = base_by_level.get(level)
        cand = cand_by_level.get(level)
        if base is None or cand is None:
            findings.append(
                Finding(
                    metric=f"recall::{level}",
                    baseline_value=base.recall_rate if base else None,
                    candidate_value=cand.recall_rate if cand else None,
                    delta=None,
                    severity=Severity.PASS,
                    p_value=None,
                    rationale=f"recall::{level} only present on one side; skipping",
                )
            )
            continue
        delta = cand.recall_rate - base.recall_rate
        p = two_proportion_p_value(
            base.mention_count,
            base.total_responses,
            cand.mention_count,
            cand.total_responses,
        )
        severity = _classify_rate_drop(
            delta,
            p,
            thresholds,
            warn_drop=thresholds.rate_warn_drop,
            fail_drop=thresholds.rate_fail_drop,
        )
        findings.append(
            Finding(
                metric=f"recall::{level}",
                baseline_value=base.recall_rate,
                candidate_value=cand.recall_rate,
                delta=delta,
                severity=severity,
                p_value=p,
                rationale=(
                    f"recall::{level} {base.recall_rate:.1%} -> "
                    f"{cand.recall_rate:.1%} (delta {delta:+.1%})"
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_regressions(
    baseline: Snapshot,
    candidate: Snapshot,
    thresholds: RegressionThresholds | None = None,
) -> RegressionReport:
    """Compare *candidate* against *baseline* and return a full report.

    The report is the source of truth for the release-gate decision.
    Callers should look at ``report.overall_severity`` (or
    ``report.exit_code`` for shell scripts) rather than re-classifying
    findings themselves.
    """
    if baseline.target_brand != candidate.target_brand:
        raise ValueError(
            f"Cannot compare snapshots for different brands: "
            f"baseline={baseline.target_brand!r} vs "
            f"candidate={candidate.target_brand!r}"
        )

    th = thresholds or RegressionThresholds()

    findings: list[Finding] = [
        _check_mention_rate(baseline, candidate, th),
        _check_share_of_voice(baseline, candidate, th),
        _check_first_mention_offset(baseline, candidate, th),
        _check_list_appearance_rate(baseline, candidate, th),
        _check_avg_list_rank(baseline, candidate, th),
    ]
    findings.extend(_check_misrepresentations(baseline, candidate, th))
    findings.extend(_check_competitor_rates(baseline, candidate, th))
    findings.extend(_check_recall_by_expectation(baseline, candidate, th))

    overall = _max_severity([f.severity for f in findings])

    notes_parts: list[str] = []
    if baseline.model_id == candidate.model_id:
        notes_parts.append(
            "Snapshots share model_id; comparison reflects sampling noise, "
            "not a model update."
        )
    if baseline.experiment_name != candidate.experiment_name:
        notes_parts.append(
            f"Experiments differ ({baseline.experiment_name!r} vs "
            f"{candidate.experiment_name!r}); ensure prompt corpora match."
        )

    return RegressionReport(
        baseline_id=baseline.snapshot_id,
        candidate_id=candidate.snapshot_id,
        baseline_model_id=baseline.model_id,
        candidate_model_id=candidate.model_id,
        overall_severity=overall,
        findings=tuple(findings),
        notes="\n".join(notes_parts),
    )


def format_report(report: RegressionReport) -> str:
    """Render *report* as a human-readable string."""
    lines: list[str] = []
    lines.append("GEO Regression Report")
    lines.append("=" * 72)
    lines.append(f"Baseline:  {report.baseline_id} ({report.baseline_model_id})")
    lines.append(f"Candidate: {report.candidate_id} ({report.candidate_model_id})")
    lines.append(f"Verdict:   {report.overall_severity.value}")
    if report.notes:
        lines.append("")
        lines.append(f"Notes:\n{report.notes}")
    lines.append("")
    lines.append(f"Findings ({len(report.regressions)} regressions / "
                 f"{len(report.findings)} checked):")
    lines.append("-" * 72)
    for f in report.findings:
        marker = {
            Severity.PASS: "  ",
            Severity.WARN: "* ",
            Severity.FAIL: "! ",
        }[f.severity]
        lines.append(f"{marker}[{f.severity.value:4s}] {f.rationale}")
    lines.append("=" * 72)
    return "\n".join(lines)
