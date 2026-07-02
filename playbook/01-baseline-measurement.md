# Baseline measurement: your first real numbers

Before you change anything (content, positioning, llms.txt files, benchmark pages), you need to know where you stand today, measured carefully enough that you will be able to tell later whether anything you did actually moved the needle. That measurement is a baseline run: your full prompt corpus, sent to real models, repeated enough times to average out randomness, scored, and rendered as a dashboard you can put in front of your team.

This page explains how the scoring works in plain language, gives you the exact commands, and shows you how to read what comes out.

## Three scoring layers, from cheap to smart

Every model response passes through up to three scoring layers. Each layer answers a different question, and each is more expensive than the last, which is why the third one is opt-in.

**Layer 1: binary presence.** Was the brand named, yes or no? This is a word-boundary text match against your product name and every alias you listed in `product.yaml` (so "AcmeSearch", "Acme Search", and "acmesearch.io" all count as one brand). The same check runs for each competitor. It is deterministic, free, and instant, and it feeds the two headline metrics: mention rate and share of voice. Its limitation is obvious: it cannot tell an endorsement from a dismissal. "Avoid AcmeSearch, it is overpriced" scores as a mention.

**Layer 2: structural prominence.** Given that the brand was mentioned, how prominently? This layer measures position and shape without interpreting meaning: how many characters into the response the first mention appears, whether the brand shows up inside a numbered or bulleted list and at what rank, how many times it is named, and what share of the response's words sit in sentences about it. These are mechanical measurements, still free and deterministic, and they capture most of what "featured versus footnoted" means in practice. A brand at list rank 1 with 30% word share is the recommendation; a brand at rank 6 with one sentence is an afterthought.

**Layer 3: semantic quality (opt-in).** What does the response actually say about you? This layer sends the response to a judge model (configured in `product.yaml`, `gpt-4o` by default, requires `OPENAI_API_KEY`) which rates sentiment, accuracy, completeness, and competitive framing (leader, alternative, or afterthought). It is the only layer that can distinguish "the leading choice for large codebases" from "a legacy option most teams have moved past." It costs real API money per response, which is why layers 1 and 2 carry the routine reporting and layer 3 is reserved for runs where you want the deeper read.

Alongside these, the scorer can check responses against a list of known misrepresentations you supply in `prompts/expected_outcomes.json` (documented in [prompts/README.md](../prompts/README.md)). If the model keeps claiming AcmeSearch has no cloud offering, you write that pattern down once and every future run counts how often it recurs.

## Running the baseline

A baseline run is defined by four choices: which corpus, which models, how many repetitions, and a name. Model responses vary between identical calls, so a single pass over your corpus is a coin flip photograph, not a measurement. Five repetitions per prompt is a sensible default (pass `--reps 5`; the runner defaults to 20 if you omit the flag). [04-statistical-rigor.md](04-statistical-rigor.md) shows the full power-analysis invocation that tells you precisely how many you need for the effect sizes you care about.

Rehearse with the mock lane first. It costs nothing, needs no keys, and confirms your `product.yaml` and corpus are wired correctly:

```bash
python -m geo.runner --corpus prompts/seed_corpus.json --dry-run --reps 2 --experiment smoke
python -m geo.dashboard --experiment smoke
```

Then run the real thing. The `claude` lane uses the Claude CLI's existing login, so it needs no API key:

```bash
python -m geo.runner \
  --corpus prompts/seed_corpus.json \
  --models claude \
  --reps 5 \
  --concurrency 5 \
  --experiment baseline_2026q3
```

For a cross-model baseline, export `OPENAI_API_KEY` and `GOOGLE_API_KEY` and pass `--models claude,chatgpt,gemini`. Cross-model matters more than it first appears: your mention rate can differ by 20 points between models, and knowing which assistant your buyers favor tells you which number is the one to manage. Before a large paid run, check the cost first with `python -m geo.spend_estimate --reps 5`.

Name experiments so future-you can sort them: `baseline_2026q3` beats `test2`. Raw responses and scores land under `results/` as JSON, one row per response, so nothing is lost between the run and the report.

Then render the dashboard:

```bash
python -m geo.dashboard --experiment baseline_2026q3
```

This writes a single self-contained HTML file (no server, no JavaScript dependencies) that you can open locally, attach to a document, or drop into a shared drive.

## Reading the dashboard

Start at the headline: mention rate and share of voice for your brand, with each competitor's numbers beside them. Suppose the baseline shows AcmeSearch at a 55% mention rate and 24% share of voice, with CodeHound at 68% and 31%. That one row already frames the quarter: you are absent from nearly half of buyer-relevant answers, and your loudest competitor is present in two thirds of them.

Then read the per-category breakdown, because the aggregate hides the story. A 55% overall mention rate might decompose into 80% on comparison prompts ("AcmeSearch vs CodeHound") and 30% on category searches ("best code search tools"), which means people who already know your name find you fine while people discovering the category do not. Those two problems have completely different fixes, and the category table is what tells them apart. The prominence panel adds the second dimension: if you are mentioned often but your median list rank is 4, you are the also-ran in shortlists you technically appear in.

Two habits keep dashboard readings honest. Check the response counts behind any percentage before repeating it in a meeting, since a category with 5 prompts moves 20 points on a single flipped response. And when a number surprises you, click through to the underlying responses; the raw text is stored precisely so that "the model keeps recommending SearchLite for our best use case" can be verified by reading what the model actually wrote.

## Cadence: baselines age

A baseline is a photograph of specific model versions on a specific date, and model updates repaint the picture without telling you. A cadence that works in practice: a full baseline once a quarter, aligned with your planning cycle so the numbers feed real decisions; a before-and-after run around any deliberate intervention you ship (that is the experiment pattern in [03-experiment-catalog.md](03-experiment-catalog.md)); and an unscheduled re-run whenever a major model version ships in an assistant your buyers use. The regression harness in `geo/regression/` automates that last case by snapshotting metrics per model version and flagging statistically significant drift, and [05-gtm-integration.md](05-gtm-integration.md) shows how to fold it into a quarterly GEO review.

The quality of everything measured here is bounded by the quality of the questions you ask the models. Building that question set is the next page: [02-designing-a-corpus.md](02-designing-a-corpus.md).
