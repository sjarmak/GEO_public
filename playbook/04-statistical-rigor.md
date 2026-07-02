# Statistical rigor, or why one run proves nothing

Run the same 25 prompts against the same model twice and you will get two different mention rates. Ask an LLM "what are the best code search tools?" five times and AcmeSearch might appear in three answers, then two, then four the next day. Models sample their output token by token, so the same prompt legitimately produces different answers on different calls. A single run of your corpus is one draw from a noisy distribution, and any decision made from it, "our llms.txt raised mention rate by 6 points!", is a decision made from noise until you can show the difference is bigger than the noise.

This page gives you the three tools the repo ships for separating signal from noise: repetitions, power analysis, and the regression harness. None of them require statistics training to use. All of them require you to resist announcing results from a single run.

## Repetitions are the unit of evidence

The runner takes a `--reps` flag that repeats every prompt N times per model:

```
python -m geo.runner --corpus prompts/seed_corpus.json --reps 5 --experiment baseline_q3
```

Five repetitions per prompt is the recommended floor for a first baseline; the runner itself defaults to 20 if you omit `--reps`, so pass the flag explicitly unless you want the bigger, more expensive run. With 25 prompts and 5 reps you get 125 responses per model per run, enough for the dashboard's rates to stop jumping around between runs, though not yet enough to detect small effects (more on that below). For a quick smoke test, `--reps 2 --dry-run` is fine; for any number you plan to put in a slide, 5 is the floor.

Reps buy you two things at once. Within each prompt, they average out sampling variance, so a prompt where the brand appears "sometimes" gets an actual rate instead of a coin flip. Across prompts, they reveal which prompts are stable (always mention you, or never do) and which are genuinely uncertain, and that split matters, because the uncertain prompts are both your noise source and your opportunity surface.

## Power analysis tells you how much data you need

Before you commit to a before/after experiment, run the power analysis on a pilot:

```
python -m geo.power_analysis \
  --results results/raw/baseline_q3/mock/2026-07-02/results.jsonl \
  --corpus prompts/seed_corpus.json \
  --target-delta 0.05 --alpha 0.05 --power 0.80
```

It reads a results file from a run you already did, applies the mention labeling rule, and answers the question every experiment plan needs answered first: to reliably detect a shift of `--target-delta` (5 percentage points by default), how many prompts times how many reps do you need? It prints the variance breakdown, the required sample size, and a suggested rep count at your current corpus size, and writes the full analysis to a `power_analysis.json` file next to your results.

Two numbers in its output deserve attention. The **naive SE** treats every response as independent; the **cluster-aware SE** accounts for the fact that responses to the same prompt resemble each other, which they do, a lot. The cluster-aware number is always larger and always the one to believe. When between-prompt variance dominates, the tool will tell you plainly that adding reps cannot reach your target and you need more prompts instead; that is your cue to expand the corpus (see [02-designing-a-corpus.md](02-designing-a-corpus.md)) rather than burn budget on repetitions.

The `--target-delta`, `--alpha`, and `--power` flags default to the conventional 5pp / 0.05 / 0.80. Translated: you want an 80 percent chance of detecting a real 5-point shift, while accepting a 5 percent chance of a false alarm. Tighten `--target-delta` to 0.03 and watch the required sample size climb steeply; detecting smaller effects is quadratically more expensive.

## The two-proportion z-test, in plain language

When you compare before and after, the question is: "the mention rate was 52 percent on 125 responses before, and 61 percent on 125 responses after; could a 9-point gap that size happen by luck?" The two-proportion z-test answers exactly that. It looks at both rates, both sample sizes, and computes the probability (the p-value) of seeing a gap at least that large if nothing had actually changed.

Convention says a p-value under 0.05 counts as significant, meaning luck would produce a gap this size less than one time in twenty. The repo's comparison tooling runs this test for you; you never compute it by hand. What you do need to internalize is the shape of the tradeoff: small samples can only detect large effects. At 125 responses per side, a 9-point swing on a rate near 50 percent sits right around the edge of significance, a 3-point swing is invisible, and a 15-point swing is unmistakable. This is why the power analysis comes first: it tells you whether your planned sample can even see the effect you are hoping for.

## Minimum detectable effect

Every sample size implies a minimum detectable effect (MDE): the smallest true change your experiment can distinguish from noise. If your MDE is 8 points and your intervention produced a real 4-point lift, your experiment will report "no significant change" and it will be correct about the statistics while being useless about the truth. Nothing was wrong with the content; the microscope was too weak.

So calibrate against what the catalog told you: retrieval-layer interventions typically move rates by single-digit percentage points. If you want to detect a 5-point effect, the power analysis will size the run for you. If it says you need 300 prompts times 5 reps and you only have budget for the 25-prompt seed corpus, you have three defensible options: accept that only large effects will be visible, grow the corpus with the generator recipe in `prompts/corpus_generator.md`, or pick a catalog experiment with a larger expected effect. Announcing a sub-MDE "improvement" anyway is the fourth option, and the most common one in the wild.

## The regression harness gates model releases like CI gates code

Model vendors ship new versions constantly, and each release can silently reshuffle how your brand is described. The regression harness treats that the way engineering treats code changes: snapshot a known-good state, compare candidates against it, and gate on the result.

```
# Save a baseline snapshot from a completed run
python -m geo.regression.runner snapshot \
  --experiment baseline_q3 --model mock --out baselines/

# Compare two snapshots, entirely offline
python -m geo.regression.runner compare \
  --baseline baselines/old.json --candidate baselines/new.json

# End to end: run the corpus, snapshot, and gate against the baseline
python -m geo.regression.runner verify \
  --corpus prompts/seed_corpus.json --model mock \
  --experiment release_check --baseline baselines/old.json
```

`verify` exits with a conventional CI code: 0 for PASS, 1 for WARN, 2 for FAIL, so you can wire it into a scheduled job and let the exit code page a human only when something moved. The default thresholds: a drop of 5 percentage points in a tracked rate (mention rate, share of voice, recall) is a WARN, a drop of 10 points is a FAIL, and a finding only fires at all when the drop is also statistically significant at p < 0.05. That significance gate is what keeps the harness quiet through ordinary sampling noise; a 6-point wobble on a small sample without significance stays a PASS, which is exactly right.

The thresholds are tunable if your risk tolerance differs, but start with the defaults and let a quarter of real snapshots teach you whether they are too twitchy or too sleepy for your corpus size.

## What this buys you in a meeting

The difference between "our mention rate went up" and "our mention rate rose 9 points, p = 0.03, on 125 responses per window, above our 5-point MDE" is the difference between an anecdote and a result. The second sentence survives a skeptical VP, a budget review, and your own doubts three months later. The tooling on this page exists so the second sentence costs you one extra command.

Next: [05-gtm-integration.md](05-gtm-integration.md) turns significant results into content and positioning decisions.
