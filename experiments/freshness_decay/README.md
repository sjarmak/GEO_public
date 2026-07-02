# Experiment: freshness decay

## Hypothesis

Model recall of a published product fact decays with the age of the content that states
it. Recall follows an approximately exponential curve,
`recall = amplitude * exp(-decay_rate * age_weeks)`, and fitting that curve yields a
half-life: the age at which half your published facts stop being cited. The half-life
tells you how often content needs refreshing.

Metric: keyword recall rate per marker (share of responses containing any expected
keyword), plotted against content age in weeks. Decision output: a fitted half-life and
a refresh-cadence recommendation per model.

## Why this matters

A benchmark table or changelog entry that moved the needle in March may be invisible to
models by September. Freshness decay quantifies that erosion so you can budget refresh
work instead of guessing. If your fitted half-life is 8 weeks, quarterly content reviews
are too slow.

## Artifacts

`markers.json` holds six example markers for fictional AcmeSearch releases. Each marker
is one dated fact:

- `version` and `publish_date`: the release and the date its claim went public.
- `claim_short`: the fact in one line.
- `probe_question`: a question whose correct answer requires the fact.
- `expected_keywords`: strings whose presence in a response counts as recall.
- Two controls with `publish_date: null`: a **negative control** (a fabricated feature
  that was never announced; any recall is hallucination) and an **adjacent unpublished**
  marker (a real internal plan with no public trace; recall means the model is guessing
  from category priors, not reading your content).

Replace the four dated markers with entries from your own changelog. Spread the publish
dates: you need markers at meaningfully different ages (weeks, months, quarters) for the
curve fit to mean anything. Keep both controls.

## Method

Two complementary reads of the same data:

1. **Cross-sectional** (one run): markers of different ages measured on the same day
   trace the decay curve directly. This is what `tracker.py` fits.
2. **Longitudinal** (monthly re-runs): the same marker measured across months shows its
   individual decay and catches revivals (a fact re-cited after a docs refresh).

## Measurement plan

Run the identical probes on a fixed monthly cadence. Same markers, same models, same
repetitions; only `--run-id` changes.

```bash
# Rehearse offline first (no API keys)
python -m experiments.freshness_decay.tracker --dry-run --reps 3

# Monthly live run, first of the month
python -m experiments.freshness_decay.tracker --models claude --reps 10 --run-id 2026-07
# ... one month later
python -m experiments.freshness_decay.tracker --models claude --reps 10 --run-id 2026-08
```

Each run writes `results/freshness_decay/<run_id>.json` containing per-marker recall
rates, the fitted decay curve, the half-life, and a refresh-cadence recommendation.
`results/` is gitignored; archive the monthly JSON files somewhere durable, because the
longitudinal comparison is the whole point.

Monthly checklist:

1. Run the tracker with this month's `--run-id`.
2. Record any content changes since the last run (refreshed pages, new changelog
   entries). A refresh resets a marker's effective age; note it next to the run.
3. After three or more runs, compare each marker's recall across months. Falling recall
   on aging content confirms decay; flat recall on old content means that fact has
   entered training data or is widely mirrored.
4. Revisit the half-life once a quarter and adjust your refresh cadence to match.

Interpretation guards:

- **Negative control** recall above roughly 10 percent means keyword matching is too
  loose for that marker; tighten `expected_keywords`.
- **Adjacent unpublished** recall tells you the guessing floor. Subtract it from dated
  marker recall before celebrating.
- Fewer than two usable (dated, non-zero recall) markers produces a flat fit and an
  "Inconclusive" cadence. Add markers rather than over-reading a two-point line.
