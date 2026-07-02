# Prompt Corpus

This directory holds the prompts the runner sends to each model. The seed corpus ships ready to run against the example `product.yaml` (AcmeSearch, category "code search tools"). Replace it with a corpus generated for your own product before drawing any real conclusions.

## Files

| File                     | Contents                                              | Status              |
| ------------------------ | ----------------------------------------------------- | ------------------- |
| `seed_corpus.json`       | 25 demo prompts, 5 per category                       | ships with the repo |
| `corpus_generator.md`    | Copy-paste LLM recipe: your `product.yaml` to a 300-prompt corpus | ships with the repo |
| `expected_outcomes.json` | Optional list of known false claims to detect         | absent by default   |

## Schema

The corpus is a JSON array. Every entry has all nine fields:

| Field                 | Type           | Meaning                                                                                     |
| --------------------- | -------------- | ------------------------------------------------------------------------------------------- |
| `id`                  | string         | Unique identifier. Category prefix plus a zero-padded sequence number, e.g. `cat-001`.       |
| `category`            | string         | One of `category_search`, `comparison`, `alternative_search`, `use_case`, `problem_framing`. |
| `subcategory`         | string         | Finer classification within the category, e.g. `self_hosted`, `refactoring`. Free-form but keep the set small. |
| `prompt`              | string         | The exact text sent to the model. Write it the way a real user would type it.                |
| `intent`              | string         | One line describing what the user is trying to accomplish. Used for corpus review, not sent to models. |
| `expected_competitors`| array of string| Competitor names (matching `product.yaml`) you expect a good answer to mention. Empty for problem-framing prompts. |
| `persona`             | string or null | Persona modifier if this prompt is role-framed ("as a security engineer, ..."). `null` otherwise. |
| `phrasing_variant_of` | string or null | If this prompt rephrases another entry, the base prompt's `id`. `null` for base prompts.     |
| `tags`                | array of string| Searchable labels for filtering results, e.g. `["security", "regex"]`.                       |

Validate after editing:

```bash
python3 -c "import json; json.load(open('prompts/seed_corpus.json'))"
```

## The Five Categories

Each category tests a different stage of how buyers discover tools, so each measures a different thing.

### `cat-` category_search

The user wants a **type of tool** and names no product ("best tools for searching large codebases"). Measures unaided visibility: does the model surface your product at all when the category comes up? This is the awareness stage and usually the hardest place to appear.

### `cmp-` comparison

The user compares named tools head-to-head or asks for a multi-tool rundown. Measures positioning: when your product appears next to competitors, how does the model rank it and on what criteria? Include competitor-vs-competitor prompts that omit your brand; whether the model adds you unprompted is a strong signal.

### `alt-` alternative_search

The user wants a replacement for a named tool. Two directions matter. Offensive prompts ("alternatives to CodeHound") measure whether you appear when a competitor's users are ready to switch. Defensive prompts ("alternatives to AcmeSearch") measure how the model talks about leaving you. Keep both in every corpus.

### `use-` use_case

The user describes a concrete task your product handles ("find every usage of a deprecated function across all our repos"). Measures need-driven discovery: does the model connect your capabilities to the job, even though no category was named?

### `prb-` problem_framing

The user vents a pain point and names neither a tool nor a category ("nobody can find anything in our codebase"). Measures pre-awareness reach: can the model travel from raw frustration to your product category to you? Leave `expected_competitors` empty here; there is no obvious right answer, which is the point.

## ID Conventions

- Prefix by category: `cat-`, `cmp-`, `alt-`, `use-`, `prb-`.
- Zero-padded three-digit sequence per prefix: `cat-001` through `cat-070`, and so on.
- IDs are stable once assigned. If you delete a prompt, retire the ID; do not reuse it, or historical results become ambiguous.
- Phrasing variants get their own ID and point back via `phrasing_variant_of`.

## How Scoring Uses This File

Scoring detects mentions using the brand and competitor names (plus aliases) from `product.yaml`, not from this file. Every response is checked against every configured brand, on every prompt.

`expected_competitors` is analysis metadata. It lets you slice aggregate results, for example "on prompts where CodeHound was expected, how often did it actually appear, and how often did we appear next to it?" It also keeps corpus authoring honest: a comparison prompt with an empty `expected_competitors` list is probably miscategorized. It does not change how any single response is scored.

## Optional: expected_outcomes.json

Models repeat stale or invented claims about products: wrong pricing, discontinued features described as flagship, acquisitions that never happened. If you know the false claims that circulate about your product, list them here and the scorer will count occurrences per run so you can track whether they are fading or spreading.

The file is a JSON object with two sections. `known_misrepresentations.items` lists the false claims to detect. `scenarios` maps prompts to expectation levels, which powers the "Recall by Expectation Level" table in the dashboard.

```json
{
  "scenarios": [
    {
      "id": "exp-001",
      "expectation": "strong_recommend",
      "example_prompts": ["cat-001", "cat-002"]
    },
    {
      "id": "exp-002",
      "expectation": "neutral",
      "example_prompts": ["prb-003"]
    }
  ],
  "known_misrepresentations": {
    "items": [
      {
        "id": "misrep-001",
        "claim": "AcmeSearch was acquired",
        "detection_patterns": ["acquired by", "acquisition of AcmeSearch"],
        "severity": "high"
      },
      {
        "id": "misrep-002",
        "claim": "Stale pricing from an old plan",
        "detection_patterns": ["$49/month", "$49/mo"],
        "severity": "medium"
      }
    ]
  }
}
```

Scenario fields:

| Field             | Type            | Meaning                                                                                          |
| ----------------- | --------------- | ------------------------------------------------------------------------------------------------ |
| `id`              | string          | Stable identifier, `exp-NNN`.                                                                     |
| `expectation`     | string          | How strongly you expect the brand to appear on these prompts, e.g. `strong_recommend`, `neutral`. Recall is reported per level. |
| `example_prompts` | array of string | Corpus prompt IDs this expectation applies to.                                                    |

Misrepresentation fields (`known_misrepresentations.items`):

| Field                | Type            | Meaning                                                                          |
| -------------------- | --------------- | -------------------------------------------------------------------------------- |
| `id`                 | string          | Stable identifier, `misrep-NNN`.                                                  |
| `claim`              | string          | The false claim in plain words. Shown in reports so readers do not need tribal knowledge. |
| `detection_patterns` | array of string | Literal text fragments matched case-insensitively against each response. Any match counts. Not regular expressions. |
| `severity`           | string          | Your triage label, e.g. `high`, `medium`, `low`. Carried through to reports.      |

The file is absent by default and everything runs without it. Start collecting entries after your first baseline run: read the raw responses, note the recurring false claims, and write detection patterns for each.

## Corpus Size

- **25 prompts (the seed corpus)** is a demo. It proves the pipeline works and produces a dashboard, nothing more. Differences you see at this size are mostly noise.
- **300+ prompts** is the decision-grade floor for comparing interventions or tracking movement between runs. With 300 prompts and a few repetitions per prompt, a 10-point shift in mention rate becomes distinguishable from run-to-run variance.

The math behind those numbers, including how to size a corpus for the effect you care about, is in [playbook/04-statistical-rigor.md](../playbook/04-statistical-rigor.md). To generate a 300-prompt corpus for your product, follow [corpus_generator.md](corpus_generator.md).
