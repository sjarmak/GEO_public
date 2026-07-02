# Experiments

An experiment tests one intervention: a change you make to your public content, measured
by how model answers shift before and after. Every experiment in this directory follows
the same pattern, and the three included here are worked examples you can copy.

The full menu of 13 intervention types lives in
[playbook/03-experiment-catalog.md](../playbook/03-experiment-catalog.md). This page
explains the mechanics.

## The pattern

Each experiment is one directory with four parts:

1. **A hypothesis** (in the experiment's `README.md`). One sentence, falsifiable, with a
   named metric. Example: "Publishing /llms.txt raises AcmeSearch's mention rate on
   category-search prompts by at least 10 percentage points."
2. **Probes** (`probes.json`). A small prompt corpus in the standard corpus schema (see
   below), sized 5 to 10 prompts. Probes are the questions you ask models to detect the
   effect. They run through the shared runner exactly like the seed corpus.
3. **Intervention artifacts** (`variants.json`, `markers.json`, template files). The
   content you will publish, in every variant you want to compare, including a control.
4. **A measurement plan** (in the `README.md`). Which commands to run, when, how many
   repetitions, and what comparison decides the result.

Some experiments add an optional `tracker.py` when the analysis needs math beyond mention
counting (freshness_decay fits a decay curve, for example). Most do not need one: the
shared runner plus the dashboard covers mention rate and prominence.

## Directory conventions

```
experiments/
├── README.md                  # this file
└── <experiment_name>/
    ├── README.md              # hypothesis, method, measurement plan
    ├── probes.json            # probe corpus (standard corpus schema)
    ├── variants.json          # intervention variants, if the experiment has them
    ├── markers.json           # dated fact markers, if the experiment tracks decay
    └── tracker.py             # optional analysis CLI, only when needed
```

Names are snake_case. Probe IDs use the standard category prefixes (`cat-`, `cmp-`,
`alt-`, `use-`, `prb-`) plus a short experiment slug, for example `cmp-bt-001`.

## The corpus schema

`probes.json` is a JSON array of entries in the same schema as
[prompts/seed_corpus.json](../prompts/seed_corpus.json):

```json
{
  "id": "cat-llms-001",
  "category": "category_search",
  "subcategory": "code_search",
  "prompt": "What are the best tools for searching across large codebases?",
  "intent": "Discover code search tools",
  "expected_competitors": ["CodeHound", "FindGrep"],
  "persona": null,
  "phrasing_variant_of": null,
  "tags": ["llms_txt", "fact:deployment"]
}
```

Because probes share this schema, the runner needs no experiment-specific code:

```bash
# Baseline, offline mock lane (no API keys)
python -m geo.runner --corpus experiments/llms_txt_adoption/probes.json \
    --dry-run --reps 5 --experiment llms_txt_baseline

# Live measurement
python -m geo.runner --corpus experiments/llms_txt_adoption/probes.json \
    --models claude --reps 10 --experiment llms_txt_baseline

# Report
python -m geo.dashboard --experiment llms_txt_baseline
```

Use tags to record what each probe is checking (`"fact:free_tier"`,
`"negative_control"`). The runner ignores tags; they exist so your analysis and your
future self know what a probe was for.

## Baseline, intervene, remeasure

Every experiment is a before/after comparison:

1. **Baseline.** Run the probes before publishing anything. Store under an experiment
   name like `llms_txt_baseline`.
2. **Intervene.** Publish one variant. One variant per time window; parallel variants on
   the same site contaminate each other.
3. **Wait.** Models with web search can react within days. Training-data effects take
   months. Your experiment README should say which channel it targets.
4. **Remeasure.** Same probes, same models, same reps, new experiment name
   (`llms_txt_post`). Compare mention rates between the two runs.

Ten repetitions per probe per model is the working minimum. A single run is noise. After
the baseline run, check what difference your sample can detect before you intervene:

```bash
python -m geo.power_analysis \
  --results results/raw/llms_txt_baseline/<model>/<date>/results.jsonl \
  --corpus experiments/llms_txt_adoption/probes.json \
  --target-delta 0.15
```

The tool reads pilot data from a run you already did; it does not take hypothetical
rates. `--target-delta` is the smallest mention-rate shift you want to detect.

[playbook/04-statistical-rigor.md](../playbook/04-statistical-rigor.md) covers the
two-proportion z-test used to decide whether a shift is real.

## Adding your own experiment

1. Pick an intervention from
   [playbook/03-experiment-catalog.md](../playbook/03-experiment-catalog.md), or invent
   one.
2. Create `experiments/<name>/` and write the hypothesis in its `README.md` first. If
   you cannot state the metric and the threshold, the experiment is not ready.
3. Write 5 to 10 probes in the corpus schema. Include at least one negative-control
   probe: a question about a fact that appears in no variant, so you can measure the
   hallucination floor.
4. Draft your intervention artifacts with a control variant.
5. Dry-run the probes (`--dry-run`) to confirm the corpus loads and the dashboard
   renders.
6. Run the baseline, publish, wait, remeasure, compare.

The three directories here (`llms_txt_adoption/`, `benchmark_tables/`,
`freshness_decay/`) are templates. Copy the closest one and replace the AcmeSearch
placeholders with your product.
