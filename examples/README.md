# Examples

`demo_report.html` is a real dashboard produced by two commands from a fresh clone, no API keys:

```bash
python -m geo.runner --corpus prompts/seed_corpus.json --dry-run --reps 3 --experiment demo
python -m geo.dashboard --experiment demo
```

`--dry-run` routes every prompt through the mock provider, which templates synthetic responses
from the brands in `product.yaml`: the target brand appears in roughly 55% of responses and each
competitor at a fixed rate between 30% and 70%, seeded so repeated runs give identical numbers.
The 25-prompt seed corpus times 3 repetitions yields 75 responses, enough for the dashboard to
show non-trivial rates without any model spend. Open the file in a browser; it is a single static
HTML page with inline SVG and no external dependencies.

## What each section shows

**Share of Voice** compares how often the target brand is mentioned relative to the total
mentions of the brand plus all configured competitors, the headline number for whether AI answers
surface your product or someone else's.

**Mention Rate by Model** breaks out the fraction of responses mentioning the brand per model
lane. The demo has one lane (mock); a real run adds a row per model you queried.

**Competitor Comparison** lists each competitor's mention rate side by side with the brand's, so
you can see who dominates the category in AI answers. In the demo these rates come from the mock
provider's per-competitor probabilities, which is why they differ from each other.

**Misrepresentation Detection** scans responses for known-false claims you list in
`prompts/expected_outcomes.json`. That file is optional and absent by default, so the demo shows
"Skipped: no misrepresentation list provided" rather than an empty table.

**Recall by Expectation Level** slices mention rates by how strongly each prompt was expected to
surface the brand, driven by the same optional `prompts/expected_outcomes.json`. Without that
file the demo shows "No expected outcomes data available."

**Response Details** is the raw evidence: per-prompt rows with which brands each response
mentioned, useful for spot-checking why an aggregate number looks the way it does.

To produce this report for your own product, edit `product.yaml`, regenerate a corpus with
`prompts/corpus_generator.md`, and run the same two commands. The playbook starts at
[playbook/00-what-is-geo.md](../playbook/00-what-is-geo.md).
