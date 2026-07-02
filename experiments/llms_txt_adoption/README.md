# Experiment: llms.txt adoption

## Hypothesis

Publishing an `llms.txt` file at your site root raises your product's mention rate and
fact accuracy in model answers to category-level questions, compared with a baseline
where the file returns 404.

Metric: Layer-1 mention rate on the probes in `probes.json`, plus fact recall on the
tagged fact probes. Decision threshold: a mention-rate gain of 10 percentage points or
more, significant at p < 0.05 on a two-proportion z-test.

## Background

[llms.txt](https://llmstxt.org/) is a proposed convention: a markdown file at
`https://yourdomain.com/llms.txt` that gives language models a curated summary of what
your product is, with links to canonical documentation. Models and agents with web
access increasingly check this path. The open question this experiment answers for YOUR
product: does the file actually change what models say?

## Artifacts

- `llms.txt` in this directory is a complete template with AcmeSearch placeholder
  content. Every line you need to change carries an inline HTML comment. Replace the
  placeholders, delete the comments, and deploy the file at your site root as
  `/llms.txt`.
- `probes.json` holds 6 probes. Each targets a specific fact stated in the template
  (deployment model, free tier, positioning versus competitors), so post-intervention
  answers can be checked against what the file claims.

## Method

Only one `llms.txt` can be live at a time; the path is fixed at the site root. That
rules out parallel A/B testing. Use sequential time windows instead:

1. **Baseline window.** Confirm `/llms.txt` returns 404. Run the probes.
2. **Deploy.** Publish your filled-in `llms.txt`. Verify it serves with
   `curl https://yourdomain.com/llms.txt`.
3. **Post window.** Wait at least two weeks (web-search-enabled models need time to
   pick up the file), then run the identical probes.

Keep everything else about your site constant across the two windows. If you ship a
major docs rewrite mid-experiment, the comparison is contaminated.

## Measurement

```bash
# Rehearse offline first (no API keys)
python -m geo.runner --corpus experiments/llms_txt_adoption/probes.json \
    --dry-run --reps 5 --experiment llms_txt_baseline

# Baseline (live models)
python -m geo.runner --corpus experiments/llms_txt_adoption/probes.json \
    --models claude --reps 10 --experiment llms_txt_baseline

# After deployment + two weeks
python -m geo.runner --corpus experiments/llms_txt_adoption/probes.json \
    --models claude --reps 10 --experiment llms_txt_post

# Reports
python -m geo.dashboard --experiment llms_txt_baseline
python -m geo.dashboard --experiment llms_txt_post
```

Compare the two dashboards on three signals:

1. **Mention rate** (Layer 1): does your brand appear more often?
2. **Prominence** (Layer 2): does it appear earlier and more centrally?
3. **Fact recall**: on the probes tagged `fact:*`, do answers now state the specific
   claims your llms.txt makes? Score this by reading the stored responses under
   `results/`, or enable the Layer-3 judge with `OPENAI_API_KEY`.

With 6 probes at 10 reps you have 60 samples per window per model. Run
`python -m geo.power_analysis --results results/raw/llms_txt_baseline/<model>/<date>/results.jsonl --corpus experiments/llms_txt_adoption/probes.json --target-delta 0.15`
on the baseline data to see what that sample can detect; add reps if your expected
effect is small.
