"""GEO regression-testing harness.

Treats LLM model updates as deployments that need release-gate testing.
Builds versioned snapshots of GEO metrics and flags regressions when a
new model version's snapshot drifts from a stored baseline.

Modules
-------
snapshot
    Build, save, and load immutable :class:`Snapshot` objects from
    experiment results.
detector
    Compare two snapshots and emit a :class:`RegressionReport` with
    per-metric deltas, statistical significance, and severity.
runner
    CLI entry point exposing ``snapshot``, ``compare``, and ``verify``
    subcommands for use in CI-like pipelines.
"""
