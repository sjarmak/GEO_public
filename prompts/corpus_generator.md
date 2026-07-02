# Corpus Generator

The 25-prompt seed corpus is a demo. Real measurement needs 300 or more prompts written for your product's actual category and competitors. The fastest way to get there is to have a capable LLM write them, constrained by the recipe below.

## How to Use

1. Open Claude, ChatGPT, or another capable model. Use the strongest model you have access to; corpus quality sets the ceiling on everything downstream.
2. Copy the entire fenced block below into the chat.
3. Paste the full contents of your `product.yaml` where marked.
4. Long outputs get truncated, so ask for the corpus in batches ("give me category_search first", then the next category). Concatenate the batches into one array.
5. Save the result as `prompts/corpus.json` and run the validation checklist at the bottom of this page.
6. Read every prompt once. Delete any that a real person in your market would never type. Expect to cut 5 to 10 percent.

## The Prompt

Copy everything inside this block:

````text
You are building a prompt corpus for a Generative Engine Optimization (GEO)
experiment. The corpus measures how often AI models mention a specific product
when users ask questions in its category. The prompts must be neutral: they
represent what real users ask, not marketing for the product.

Here is the product configuration (product.yaml):

--- PASTE YOUR product.yaml HERE ---

From that file, extract:
- BRAND: the product name under `product.name`
- CATEGORY: the string under `product.category`
- COMPETITORS: every name under `competitors`

Generate exactly 300 prompts as a single JSON array. Every entry must have all
nine fields in this exact schema:

{
  "id": "cat-001",
  "category": "category_search",
  "subcategory": "short_snake_case_label",
  "prompt": "The exact text a user would type.",
  "intent": "One line: what the user is trying to accomplish.",
  "expected_competitors": ["CompetitorName"],
  "persona": null,
  "phrasing_variant_of": null,
  "tags": ["one_or_more", "snake_case_tags"]
}

CATEGORY QUOTAS (exactly these counts, these prefixes, these category values):

- 66 prompts, ids cat-001..cat-066, category "category_search":
  user asks for a TYPE of tool in CATEGORY and names no product.
- 60 prompts, ids cmp-001..cmp-060, category "comparison":
  user compares named tools. Mix three kinds: BRAND vs one competitor,
  competitor vs competitor with BRAND unnamed, and multi-tool comparisons
  across 3+ named tools with explicit criteria.
- 48 prompts, ids alt-001..alt-048, category "alternative_search":
  user wants a replacement for a named tool. Include offensive prompts
  (alternatives to each COMPETITOR) and at least 10 defensive prompts
  (alternatives to BRAND itself, with varied motivations: price, missing
  feature, support, vendor risk).
- 63 prompts, ids use-001..use-063, category "use_case":
  user describes a concrete task or scenario that tools in CATEGORY solve,
  without asking for the category by name. Ground each one in a specific,
  plausible situation (team size, repo count, deadline, constraint).
- 63 prompts, ids prb-001..prb-063, category "problem_framing":
  user describes a pain point and names neither a tool nor a category.
  expected_competitors must be an empty array for every prompt here.

FIELD RULES:
- expected_competitors may contain ONLY names from COMPETITORS, spelled
  exactly as they appear in product.yaml. Never include BRAND. Empty array
  for all problem_framing prompts.
- persona and phrasing_variant_of are null for every entry.
- subcategory: reuse a small set of labels (5 to 8 per category), do not
  invent a new one per prompt.
- tags: 2 to 4 per prompt, snake_case, useful for filtering.

REALISM AND VARIETY RULES:
- Write prompts the way real users type. Vary register: roughly a quarter
  casual (lowercase, terse, no punctuation, maybe a typo), half plain
  professional, a quarter expert (precise constraints, numbers, jargon).
- Vary length: one-line questions up to four-sentence scenarios.
- Vary form: questions, instructions ("compare X and Y"), and statements
  ending in an implicit ask.
- No two prompts may share the same core intent. Rephrasing the same
  question does not count as a new prompt. Before writing each prompt,
  check it asks something the corpus does not already ask.
- Do not reuse the same opening words more than 5 times across the corpus
  ("What are the best..." counts as one opening).
- Include prompts where BRAND is NOT the obvious right answer. A corpus
  where every prompt flatters BRAND measures nothing.
- Never mention BRAND in category_search, use_case, or problem_framing
  prompts. BRAND may appear only in comparison prompts and defensive
  alternative_search prompts.
- No placeholder text, no "[product]", no numbered lists inside prompts
  unless a real user would write one.

OUTPUT RULES:
- Output valid JSON only: one array, double quotes, no trailing commas,
  no comments, no markdown fences around the JSON.
- If the full array does not fit in one response, stop at a complete entry
  and wait; I will say "continue" and you resume with the next entry.
````

## After Generating: Validation Checklist

Run these before pointing the runner at the new corpus.

```bash
# 1. Valid JSON
python3 -c "import json; json.load(open('prompts/corpus.json'))"

# 2. Unique ids, correct counts, schema fields present
python3 - <<'EOF'
import json, collections
rows = json.load(open("prompts/corpus.json"))
fields = {"id","category","subcategory","prompt","intent",
          "expected_competitors","persona","phrasing_variant_of","tags"}
ids = [r["id"] for r in rows]
assert len(ids) == len(set(ids)), "duplicate ids"
for r in rows:
    missing = fields - r.keys()
    assert not missing, f"{r.get('id')}: missing {missing}"
    if r["category"] == "problem_framing":
        assert r["expected_competitors"] == [], f"{r['id']}: prb must be empty"
print(collections.Counter(r["category"] for r in rows))
print(f"{len(rows)} prompts OK")
EOF
```

Checklist:

- [ ] File parses as valid JSON (step 1 above).
- [ ] All ids unique, all nine schema fields present on every entry (step 2).
- [ ] Category counts match the quotas (66 / 60 / 48 / 63 / 63).
- [ ] Every `expected_competitors` name matches `product.yaml` exactly.
- [ ] Problem-framing prompts have empty `expected_competitors`.
- [ ] You read every prompt and deleted the unrealistic ones (then re-run step 2; gaps in id sequences are fine, duplicates are not).
- [ ] Spot-check 10 random prompts: would a real person in your market type this?

Then run a baseline:

```bash
python -m geo.runner --corpus prompts/corpus.json --dry-run --reps 2 --experiment baseline
```

## Why the Quotas Look Like That

The split (22% category, 20% comparison, 16% alternatives, 21% use case, 21% problem framing) mirrors the distribution that held up in practice on a 300+ prompt production corpus: heavier on category and problem prompts because unaided visibility is what GEO mostly moves, lighter on alternatives because that category saturates quickly. Adjust if your market differs, but keep every category above 10 percent; each one measures a stage the others cannot see. Sizing math is in [playbook/04-statistical-rigor.md](../playbook/04-statistical-rigor.md).
