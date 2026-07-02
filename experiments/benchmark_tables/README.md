# Experiment: benchmark tables

## Hypothesis

Presenting product comparisons as structured markdown tables, rather than prose
paragraphs carrying the same facts, raises your product's inclusion rate and the rate at
which models cite specific cells (prices, yes/no capabilities) in comparison answers.

Metric: Layer-1 mention rate on the `probes.json` comparison probes, plus a manual count
of responses that quote a specific table cell. Decision threshold: a mention-rate gain
of 10 percentage points or more between the control and any table variant, significant
at p < 0.05.

## Background

Models with web search lean on comparison pages when answering buyer-intent questions
("which tool is cheapest", "compare X and Y"). Tables are easier for retrieval systems
to extract than prose. This experiment isolates the presentation format: every variant
states the same facts, only the format changes.

## Artifacts

`variants.json` defines three variants of one comparison page. Each variant's `content`
field is a markdown skeleton with `[FILL]` placeholders:

- `00_control_prose`: facts stated in prose sentences only. The baseline.
- `01_features_table`: same facts plus a capability matrix (yes/no cells).
- `02_pricing_table`: same facts plus a pricing table with numeric cells.

Replace AcmeSearch, CodeHound, and FindGrep with your product and real competitors, and
fill every `[FILL]` cell with verified facts from public documentation. Two rules:

1. **Content invariance.** All prose outside the table sections must be identical
   across variants. If the facts differ, you are testing the facts, not the format.
2. **No invented numbers.** Every cell must trace to a public source. A fabricated
   latency number that a model repeats becomes your problem permanently.

`probes.json` holds 7 probes: comparison and category questions targeting the table
facts, plus one negative control (a capability stated in no variant) to measure the
hallucination floor.

## Method

1. **Baseline.** Publish the control variant at your comparison URL. Wait two weeks for
   indexing, then run the probes.
2. **Swap.** Replace the page content with one table variant. Same URL, same title.
3. **Post window.** Wait two weeks, run the identical probes.
4. Repeat step 2 and 3 per variant you want to test.

One variant per time window. Publishing variants at different URLs simultaneously
splits retrieval signals and makes attribution impossible.

## Measurement

```bash
# Rehearse offline
python -m geo.runner --corpus experiments/benchmark_tables/probes.json \
    --dry-run --reps 5 --experiment benchmark_tables_control

# Control window (live)
python -m geo.runner --corpus experiments/benchmark_tables/probes.json \
    --models claude --reps 10 --experiment benchmark_tables_control

# After swapping to the features-table variant
python -m geo.runner --corpus experiments/benchmark_tables/probes.json \
    --models claude --reps 10 --experiment benchmark_tables_features

python -m geo.dashboard --experiment benchmark_tables_control
python -m geo.dashboard --experiment benchmark_tables_features
```

Score three things per window:

1. **Inclusion**: mention rate from the dashboard.
2. **Cell citations**: read stored responses under `results/` and count answers quoting
   a specific cell (a price, a yes/no capability). Tables should raise this even when
   raw inclusion is flat.
3. **Negative control**: the probe tagged `negative_control` asks about a capability no
   variant claims. Its "yes" rate is your hallucination floor; subtract it mentally
   from any effect you think you see.
