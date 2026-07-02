# Turning measurements into GTM action

A dashboard nobody acts on is a hobby. This page closes the loop: which metric movements map to which content and positioning levers, how to run the whole thing on a quarterly cadence that survives contact with a real marketing calendar, and a checklist you can hand to the team.

## Each metric names its own lever

The dashboard reports a handful of rates per model. Each one, when it disappoints, points at a specific class of GTM work rather than a general "do more content" mandate.

**Low mention rate on category prompts** (the `cat-` prompts: "what are the best code search tools?") means models do not associate you with your own category. The lever is category content: pages that state plainly what category you are in and what you do there, published on your own domain and in the third-party directories and review sites that models crawl. If AcmeSearch appears in 40 percent of category answers while CodeHound appears in 75 percent, the gap is not product quality, it is associative coverage, and it is fixable with writing.

**Low list rank** (you appear, but fourth or fifth when models list options) means you are in the association set but not near the front of it. The levers are comparison pages on your own site and presence in third-party listicles and "top N tools" roundups, because those are the documents models lean on when they compose ranked answers. Rank moves slower than mention rate; treat a persistent rank improvement across two measurement windows as a real win.

**Misrepresentations** (models describing you inaccurately: wrong flagship product, discontinued feature presented as current, wrong pricing model) are the highest-priority finding a run can surface, because every inaccurate answer reaches a buyer you never see. The lever is corrections on owned surfaces: your docs overview, your llms.txt, your FAQ, stated in the same plain declarative language you want models to repeat. Write the correct sentence where crawlers will find it, then verify in the next window that the misrepresentation rate fell. If your team maintains a list of known misrepresentations, encode them in `prompts/expected_outcomes.json` (documented in `prompts/README.md`) so the scorer flags them automatically.

**Competitor share-of-voice gains** (a competitor's rate climbing across two or more windows while yours holds flat) mean their content strategy is landing. That finding belongs in a positioning review, not a content ticket: what are they publishing, which prompt categories are they winning, and does your differentiation story still answer the comparison a model actually generates? The regression harness flags competitor rate increases of 10 points as WARN and 20 as FAIL by default, so sustained gains will not slip past you.

**Weak recommendation despite mention** (the content-gap analysis finding: mentioned but never recommended) means models know you exist but cannot articulate why to choose you. The lever is use-case and problem-framed content, because the gap almost always clusters in the `use-` and `prb-` prompt categories where a model needs a story, not a feature list, to justify a recommendation.

## The quarterly GEO review

GEO does not need daily attention. Model behavior shifts on the cadence of model releases and crawl refreshes, so a quarterly loop with one mid-quarter checkpoint captures nearly everything worth capturing. The loop:

**Week 1: baseline.** Run the full corpus at 5 reps against every model you track, generate the dashboard, and record the headline rates. If this is not your first quarter, this run doubles as the comparison point against last quarter's snapshot.

```
python -m geo.runner --corpus prompts/seed_corpus.json --reps 5 --experiment q3_baseline
python -m geo.dashboard --experiment q3_baseline
python -m geo.regression.runner snapshot --experiment q3_baseline --model mock --out baselines/
```

**Week 1, same meeting: pick one or two experiments** from [03-experiment-catalog.md](03-experiment-catalog.md), chosen by what the baseline actually showed, not by what sounds interesting. Low category mention rate points to llms.txt adoption or category content; comparison losses point to benchmark tables or comparison-page optimization; misrepresentations point to owned-surface corrections plus llms.txt. Run the power analysis on the baseline results so you know the sample size the re-measurement needs before you commit.

**Weeks 2 to 4: ship the content.** This is ordinary content work with one non-negotiable constraint: change only what the experiment varies, and note the ship date. An experiment where the llms.txt, the pricing page, and three blog posts all changed in the same window measures everything and attributes nothing.

**Weeks 8 to 10: re-measure.** Four to six weeks after the content ships, run the identical corpus at the same rep count and compare windows. Earlier than four weeks and web-search caches may not have refreshed; the difference you measure would be partly the old content. Report the deltas with their significance, per [04-statistical-rigor.md](04-statistical-rigor.md), and record what you would do differently next quarter regardless of outcome. A clean null result on a well-powered experiment is real information: that lever does not move your rates, stop investing in it.

**On every model release: regression-gate.** Between quarterly reviews, when a tracked model ships a new version, run `verify` against your last baseline. Exit code 0 means carry on; 1 means someone reads the report this week; 2 means the new model materially changed how you are represented and the finding goes to the next positioning conversation ahead of schedule.

```
python -m geo.regression.runner verify \
  --corpus prompts/seed_corpus.json --model mock \
  --experiment release_check --baseline baselines/q3_baseline.json
```

Quarter over quarter, the snapshots accumulate into the asset this whole practice exists to build: a longitudinal record of how AI models represent your product, tied to the specific content changes that moved it.

## The one-page checklist

Every quarter:

- [ ] Baseline run complete: full corpus, 5 reps, all tracked models
- [ ] Dashboard generated and headline rates recorded (mention rate, list appearance rate, share of voice, misrepresentation count)
- [ ] Deltas vs last quarter's snapshot reviewed, significance checked
- [ ] Misrepresentations triaged first; corrections assigned to owned surfaces
- [ ] Content-gap list reviewed; weak prompt categories named
- [ ] One or two catalog experiments selected, each tied to a specific baseline finding
- [ ] Power analysis run; required sample size known before content ships
- [ ] Experiment content shipped with a recorded ship date, nothing else changed in the measured surface
- [ ] Re-measurement scheduled 4 to 6 weeks after ship
- [ ] Re-measurement run with identical corpus and reps; result recorded as significant lift, null, or regression
- [ ] New baseline snapshot saved to `baselines/`

On every model release:

- [ ] `verify` run against the current baseline
- [ ] Exit code 1 or 2 findings routed to a human within the week
- [ ] Competitor share-of-voice WARNs added to the positioning review agenda

Standing hygiene:

- [ ] Corpus reviewed twice a year: new competitor names added to `product.yaml`, stale prompts retired, categories rebalanced
- [ ] `prompts/expected_outcomes.json` updated whenever a new misrepresentation is discovered in the wild
- [ ] Results and baselines retained; the longitudinal record is the point

The first quarter is the hardest, because the baseline usually stings and none of the levers have been pulled yet. By the third quarter you have something few marketing teams can produce: a measured, versioned answer to "what do AI models say about us, and is it getting better?"
