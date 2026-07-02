# What GEO is and why your team should measure it

Ask ChatGPT "what are the best code search tools?" and it answers with three to five product names, a sentence or two about each, and often a recommendation. If your product is AcmeSearch and the answer names CodeHound, FindGrep, and SearchLite but not you, a buyer just finished a vendor shortlist without ever seeing your name. No ad placement, no search ranking, no landing page ever entered the picture.

That answer is the new search results page. Generative Engine Optimization (GEO) is the practice of measuring, and then improving, how AI models surface your product when people ask the questions your buyers actually ask. It stands in the same relation to ChatGPT, Claude, and Gemini that SEO stands in to Google: same goal (be present and well represented at the moment of discovery), different mechanics (model training data and retrieval instead of crawlers and PageRank).

## Why this matters for GTM now

A growing share of product discovery happens inside AI assistants rather than search engines, and those assistants compress the whole first phase of a buying journey into one response. Where a Google results page gave you ten blue links and a fighting chance on page one, an AI answer gives the user a synthesized shortlist of maybe four names with editorial framing attached. Being absent from that shortlist is invisible in your analytics: nobody clicked away from you, they just never heard of you.

Three properties of this channel make it worth measuring rather than guessing about. Answers vary between runs of the same question, so a single spot check tells you almost nothing. Answers shift when model versions update, silently and without any announcement that affects you. And answers carry claims about your product that can be flatly wrong, in which case the model is doing negative marketing on your behalf at scale.

You cannot manage this channel from vibes. You can manage it from a measured baseline, which is what this repository produces.

## The four numbers that describe your AI visibility

Everything in this repo rolls up to a handful of plain metrics. No statistics background required to read them; page [04-statistical-rigor.md](04-statistical-rigor.md) covers when a change in these numbers is real versus noise.

**Mention rate.** Out of all the prompts you tested, what percentage of responses named your product at all? If you run 25 prompts 5 times each and AcmeSearch appears in 69 of the 125 responses, your mention rate is 55.2%. This is the foundation metric: you cannot be recommended if you are not mentioned.

**Share of voice.** Of all brand mentions in the responses (yours plus your tracked competitors), what fraction were yours? Mention rate tells you how often you show up; share of voice tells you how crowded the room is when you do. A 55% mention rate looks less comfortable next to a 22% share of voice, because it means competitors are being named more than twice as often as you across the same questions.

**Prominence.** When you are mentioned, where and how? First-mention position (how far into the response your name first appears), list rank (are you item 1 in the bulleted shortlist or item 6?), and word share (how much of the response is about you). Being mentioned last in one dismissive clause and being the lead recommendation with a paragraph of praise both count as "mentioned," and prominence is what separates them.

**Misrepresentation.** Does the model say things about your product that are wrong? Stale pricing, discontinued features presented as current, a claim that you were acquired, a flagship capability attributed to a competitor. These are tracked as named, countable defects rather than anecdotes, because "the model keeps saying we have no cloud offering" is an actionable content problem once you know it happens in 4% of responses.

## What this repository does end to end

The pipeline has four stages, and you can run all of them in one sitting. It sends a corpus of realistic buyer questions to one or more models (Claude, ChatGPT, Gemini, or a free offline mock lane), repeating each prompt several times because model output varies. It scores every response through three layers, from a simple "was the brand named" check up to an optional LLM judge that rates sentiment and accuracy. It stores the raw responses and scores as JSON under `results/`, so every aggregate number can be traced back to the exact response that produced it. And it renders a self-contained HTML dashboard showing mention rate, share of voice, prominence, and per-category breakdowns for your brand and each competitor side by side.

Everything is parameterized by one file, `product.yaml`, which names your product, its aliases, your category, and the competitors you want tracked. The examples throughout this playbook use a fictional brand, AcmeSearch, competing against CodeHound, FindGrep, and SearchLite in the code search tools category. Swap in your own names and the entire harness, corpus generation included, follows.

On top of the measurement core, the repo includes an experiment pattern for testing interventions (does publishing an llms.txt file move your mention rate? do benchmark tables? see [03-experiment-catalog.md](03-experiment-catalog.md)) and a regression harness that snapshots your metrics per model version and flags drift when a model update quietly changes how you are represented.

## Your first 30 minutes

The README quickstart at the repository root gets you from clone to dashboard with zero API keys, using the mock lane: install dependencies, run `python -m geo.runner --corpus prompts/seed_corpus.json --dry-run --reps 2 --experiment smoke`, then `python -m geo.dashboard --experiment smoke`, and open the HTML file it writes. The mock lane fabricates plausible responses using your configured brand names, so the dashboard you see is structurally identical to a real one. Ten minutes, roughly.

Spend the remaining twenty on two edits. Replace the AcmeSearch block in `product.yaml` with your own product, aliases, and three to five competitors. Then skim `prompts/seed_corpus.json` to see what buyer questions look like in this format, because your next real task is building a corpus for your own category, and that is the subject of [02-designing-a-corpus.md](02-designing-a-corpus.md).

When you are ready to run against real models and establish an actual baseline, continue to [01-baseline-measurement.md](01-baseline-measurement.md).
