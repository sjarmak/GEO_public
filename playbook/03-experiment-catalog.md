# The experiment catalog

Thirteen interventions, one measurement loop. Every experiment below follows the same shape: establish a baseline with the runner, change exactly one thing about your public content, wait for the change to propagate, and re-measure with the identical prompt battery. The catalog exists so you pick interventions deliberately instead of shipping content changes and hoping.

Three of these ship as working exemplars in `experiments/` (marked below). The rest follow the same pattern; `experiments/README.md` explains how to build a tracker for any of them.

## How to read each entry

Every entry answers five questions: what the experiment tests, the hypothesis behind it, the effort level (content-only means your marketing team can run it alone; code+content means you need an engineer for site or scorer changes), what signal to expect and when, and which repo machinery measures it. "Runner + corpus" means the standard loop, `python -m geo.runner --corpus <file> --experiment <name>`, with a probe corpus you write for the experiment. "Custom tracker" means a small Python module following the pattern in `experiments/README.md`.

A calibration note before you pick: retrieval-layer interventions (anything a model with web search reads at answer time) show signal in 2 to 6 weeks. Training-data interventions (anything that only matters once it lands in a model's training set) show signal across model releases, so quarters, not weeks. Effect sizes for both are usually single-digit percentage points, which is why [04-statistical-rigor.md](04-statistical-rigor.md) matters.

## Quick reference

| # | Experiment | Effort | Timescale | Machinery |
|---|------------|--------|-----------|-----------|
| 1 | llms.txt adoption | content-only | weeks | runner + corpus (exemplar) |
| 2 | Benchmark tables | content-only | weeks | runner + corpus (exemplar) |
| 3 | Schema markup | code+content | weeks | runner + corpus |
| 4 | Freshness and decay | content-only | months | custom tracker (exemplar) |
| 5 | Category creation framing | content-only | quarters | custom tracker |
| 6 | Transcript seeding | content-only | quarters | custom tracker |
| 7 | Social proof quantification | content-only | weeks | runner + corpus |
| 8 | Use-case vs feature-list framing | content-only | weeks | runner + corpus |
| 9 | Error message SEO | content-only | weeks to quarters | runner + corpus |
| 10 | API doc completeness | code+content | weeks | custom tracker |
| 11 | Agent tool selection | code+content | weeks | custom tracker |
| 12 | Comparison-page optimization | content-only | weeks | runner + corpus |
| 13 | Content gap analysis | analysis-only | immediate | runner output + review |

## 1. llms.txt adoption (exemplar in `experiments/`)

**Tests:** whether publishing an `llms.txt` file at your site root changes how models with web search describe and recommend your product. The llms.txt convention gives crawling models a curated, machine-readable summary of what you are and what you make.

**Hypothesis:** a model that finds a clear, structured self-description at a standard path produces more accurate answers and recommends you more often on product-overview prompts than one left to synthesize your identity from scattered pages.

**Effort:** content-only. Writing the file takes an afternoon; deploying it is a single static file at `/llms.txt`.

**Signal and timescale:** mention rate and description accuracy (the Layer-3 judge's accuracy score) on prompts like "what is AcmeSearch?" and "should I use AcmeSearch for cross-repo work?". Because the file lives at one canonical path, variants run sequentially in time windows, not side by side. Allow 7 to 14 days after each deployment for web-search caches to refresh before the next measurement window.

**Machinery:** runner + a product-overview probe corpus. The exemplar includes a variant ladder from a minimal file through a full structured version, so you can isolate whether presence alone moves anything or the structure does the work.

## 2. Benchmark tables (exemplar in `experiments/`)

**Tests:** whether presenting the same comparison facts as structured tables (HTML or Markdown) instead of prose changes how often models cite those facts in buyer-intent answers.

**Hypothesis:** models extract and repeat facts from tables more reliably than from prose paragraphs, so a feature matrix or pricing table on your comparison page raises your inclusion rate in side-by-side answers.

**Effort:** content-only. The discipline is holding everything outside the table byte-identical across variants, so the format is the only thing that changes.

**Signal and timescale:** per-fact citation rate and inclusion rate on comparison prompts ("compare AcmeSearch and CodeHound on pricing"). Weeks, gated on crawl refresh.

**Machinery:** runner + corpus, using the `cmp-` comparison prompts from your corpus plus fact-specific probes.

## 3. Schema markup

**Tests:** whether structured metadata in your page heads (JSON-LD, Open Graph tags, microdata) improves model extraction, with the visible page content held byte-identical across variants.

**Hypothesis:** models trained on the open web have learned to trust the metadata standards the web already uses, so a well-formed JSON-LD block naming your organization and products raises citation accuracy even though no human ever sees it.

**Effort:** code+content. Someone has to edit page templates, and the byte-identical-body constraint needs engineering discipline.

**Signal and timescale:** citation rate and accuracy on category and overview prompts, in weeks. Expect this to be one of the subtler effects in the catalog; size your sample accordingly.

**Machinery:** runner + corpus, comparing measurement windows before and after each markup variant ships.

## 4. Freshness and decay (exemplar in `experiments/`)

**Tests:** how fast model recall of your published claims fades with content age. You publish dated, distinctive claims (a versioned feature announcement works well), then probe for recall at increasing ages and fit a decay curve.

**Hypothesis:** recall of a specific claim decays roughly exponentially with time since publication, which means there is a measurable half-life, and that half-life tells you how often to refresh cornerstone content.

**Effort:** content-only, but it rides on your existing release-notes cadence rather than requiring new content.

**Signal and timescale:** recall rate plotted against content age in weeks. The curve takes months of repeated measurement to fill in, which is why this one runs on a schedule instead of as a one-shot.

**Machinery:** custom tracker (the exemplar ships one) that loads a marker corpus, probes each claim, scores keyword and version recall, and fits the decay curve.

## 5. Category creation framing

**Tests:** whether consistently framing your product inside a category label you coin ("code intelligence platform" rather than plain "code search") gets models to adopt the term and to name you when asked about the category.

**Hypothesis:** models learn category vocabulary from repeated co-occurrence in training data, so sustained use of a term across your owned surfaces eventually makes the term, and your position in it, part of how models answer category questions.

**Effort:** content-only, but sustained. This is a positioning commitment, not a one-page change.

**Signal and timescale:** term citation rate and category-leader rate ("which products are in this category?"). Movement tracks model training cuts, so re-measure every 90 days or on each major model release and compare across cuts.

**Machinery:** custom tracker with a term registry: each candidate term carries a definition, probe questions, and expected keywords, plus negative-control terms you never promoted so you can tell adoption from noise.

## 6. Transcript seeding

**Tests:** whether publishing question-and-answer style content (FAQ pages, support transcripts, structured Q&A) gets specific claims into future model training data, measured as claim citation rates across training cuts.

**Hypothesis:** content shaped like the questions users actually ask is disproportionately likely to surface verbatim in model answers to those questions.

**Effort:** content-only.

**Signal and timescale:** per-claim citation rate, tracked across model releases. Quarters. Pair each seeded claim with an unseeded control claim of similar specificity so you can attribute movement to the seeding.

**Machinery:** custom tracker that loads a probe corpus of claims and computes per-claim citation rates, re-run on a 90-day schedule.

## 7. Social proof quantification

**Tests:** whether the kind of authority signal on a page changes model behavior: no proof at all, qualitative proof ("trusted by leading teams"), or quantitative proof (customer counts, scale numbers, named logos). Everything else on the page stays byte-identical.

**Hypothesis:** models weight specific numbers differently than narrative authority language, so "used by 400 engineering teams" earns more citation weight than "widely trusted".

**Effort:** content-only.

**Signal and timescale:** recommendation and citation rate on category and comparison prompts, in weeks.

**Machinery:** runner + corpus, with the three page variants deployed in sequential windows.

## 8. Use-case vs feature-list framing

**Tests:** whether the same set of capabilities lands better as a narrative use-case story, a structured feature list, or a hybrid story with inline capability callouts. All variants make identical factual claims; only the format varies.

**Hypothesis:** format changes what a model can extract. Feature lists may win on capability recall while narratives win on recommendation for problem-framed prompts, and the hybrid may capture both.

**Effort:** content-only.

**Signal and timescale:** capability recall rate and mention rate, split by prompt category (compare `use-` and `prb-` prompts against `cat-` prompts). Weeks.

**Machinery:** runner + corpus, with a per-variant probe battery.

## 9. Error message SEO

**Tests:** whether publishing resolution content keyed to exact error strings ("error: index out of range in distributed query") makes models cite your product as the fix when a user pastes that error.

**Hypothesis:** developers paste error messages verbatim, models match on those strings, and the page that owns the canonical answer to an error owns the recommendation that comes with it.

**Effort:** content-only. Each entry is one focused page per error class, plus a negative-control page that resolves the error without mentioning your product.

**Signal and timescale:** citation rate on error-string prompts. Weeks for web-search models, quarters for the effect to reach trained-in knowledge.

**Machinery:** runner + a corpus of error-string prompts with aliases (the same error phrased three ways), scored for whether your brand appears in the resolution.

## 10. API doc completeness

**Tests:** whether more complete API documentation reduces the rate at which models hallucinate your API when generating code against it. Probes ask a model to write real code targeting your API; a mechanical scorer validates the generated field names and paths against your actual schema and counts known hallucination traps.

**Hypothesis:** models fill documentation gaps with plausible inventions, so every documented endpoint and field measurably reduces wrong-code rate.

**Effort:** code+content. The docs work is content; the schema-validation scorer is a small engineering task.

**Signal and timescale:** valid-field rate and trap-hit rate per probe, in weeks after docs ship (for web-search models reading current docs).

**Machinery:** custom tracker with a probe corpus and a schema file to validate against.

## 11. Agent tool selection

**Tests:** whether package metadata (description, keywords, README structure) changes how often a coding agent picks your library when given a task it could solve with yours or a competitor's. Functionality stays constant; only metadata varies.

**Hypothesis:** agents choose tools by reading metadata the way developers skim it, so a capability-listing description or a use-case-narrative README shifts selection rate without any code change.

**Effort:** code+content, and the most involved entry in this catalog: you need task prompts, frozen competitor metadata, and an agent harness to observe selections.

**Signal and timescale:** selection rate per metadata variant, in weeks.

**Machinery:** custom tracker. This experiment borders the coding-agent lane described below.

## 12. Comparison-page optimization

**Tests:** whether owning well-structured "AcmeSearch vs CodeHound" pages, covering the comparisons buyers actually ask about, raises your inclusion and ranking in model-generated comparisons, including comparisons you are currently absent from.

**Hypothesis:** when a model answers "X vs Y", it leans on pages that already frame that exact comparison, and the party that wrote the page frames the conclusion.

**Effort:** content-only.

**Signal and timescale:** inclusion rate and list rank on `cmp-` prompts, plus share of voice against each named competitor. Weeks.

**Machinery:** runner + corpus. Your seed corpus already carries the comparison category; extend it with one prompt per competitor pairing you care about.

## 13. Content gap analysis

**Tests:** nothing on its own. This is the diagnostic that tells you which of the other twelve to run. It aggregates a baseline run into per-prompt summaries and surfaces two lists: prompts where you never appeared across any repetition, and prompts where you were mentioned but never recommended.

**Hypothesis:** your weakest prompt categories are not random; they cluster around content you have not written, and the cluster names the content plan.

**Effort:** analysis-only. Run a baseline, read the aggregation, hand the weak-prompt list to whoever plans content.

**Signal and timescale:** immediate. The output is a prioritized gap list, not a rate.

**Machinery:** runner output plus review. Aggregate mechanically, then have a human (or an LLM with the summary in front of it) judge why each weak prompt fails and what content would fix it.

## The coding-agent lane

Everything above measures what models say when asked. A separate, harder question is what coding agents do mid-task: when an agent is fixing a build or choosing a dependency, does your product enter the session at all, and in what role? Measuring that requires an agent harness that runs scripted scenarios, collects full session transcripts, and analyzes tool and brand surfacing within them. That lane is not included in this template. Treat it as the advanced follow-on once the twelve intervention experiments above are producing stable baselines.

Pick one or two entries, not five. The statistics page explains why running fewer experiments with more repetitions beats the reverse.
