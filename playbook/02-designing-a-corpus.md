# Designing a corpus: the questions decide what you learn

Your corpus is the set of questions you send to the models, and it is the single largest quality lever in the whole harness. Score a corpus of softball questions and you get a flattering dashboard that predicts nothing; score a corpus that mirrors what real buyers actually type into ChatGPT and you get numbers your GTM team can plan against. This page covers the five prompt categories, how to write prompts that sound like users instead of marketers, and the workflow for generating a full corpus for your own product.

The exact JSON schema for corpus entries lives in [prompts/README.md](../prompts/README.md). The shipped starter set, `prompts/seed_corpus.json`, holds 25 prompts (5 per category) written for the fictional AcmeSearch category, so you can watch the format in action before writing your own.

## Five categories, one buyer journey

The categories are not arbitrary bins. They trace the path a buyer walks from "I have a problem" to "I am choosing between vendors," and your visibility usually differs sharply between stages. Each category gets an id prefix so results stay sortable.

**Problem framing (`prb-`).** The earliest stage: the user describes a pain and has no idea a product category exists for it. "Our engineers waste hours grepping through a huge monorepo, what can we do?" No brand names appear in the question, which makes this the hardest category to show up in and the most valuable, because the model's answer is where the user first learns that tools like yours exist at all.

**Category search (`cat-`).** The user has named the category and wants options. "What are the best tools for searching across large codebases?" This is the classic shortlist moment, the closest analogue to a head-term Google search, and share of voice here is the number most GTM teams care about first.

**Use case (`use-`).** The user has a concrete job to be done. "How do I find every place our deprecated payments API is still called?" These prompts test whether models associate your product with the workflows it is actually good at, rather than just with its category label. Weak use-case visibility with strong category visibility usually means your content describes what you are but not what you are for.

**Comparison (`cmp-`).** The user is weighing named options. "AcmeSearch vs CodeHound: which handles multi-repo search better?" Here mention rate is nearly guaranteed (your name is in the question), so the interesting signals move to prominence and layer-3 semantics: does the model frame you as the stronger choice, the budget option, or the legacy one?

**Alternative search (`alt-`).** The user wants out of a competitor. "What are good alternatives to CodeHound?" This is the highest-intent discovery moment in the whole journey, a buyer actively shopping away from a rival, and being absent from these answers is leaving switchers on the table.

Aim for rough balance across the five (the seed corpus is exactly 5/5/5/5/5; a full corpus of 300 lands near 60 per category), because a corpus dominated by comparison prompts will overstate your visibility and one dominated by problem framing will understate it.

## Write like a user, not like your website

The models are answering real people, so your prompts have to sound like real people, and real people do not talk in your positioning language. A user asks "how do I search all our repos at once," not "what solutions offer enterprise-scale unified code intelligence." If you seed the corpus with marketing phrasing, you measure your visibility on questions nobody asks, which is worse than not measuring.

A few tests each prompt should pass before it enters the corpus. It should be answerable by someone who has never heard of your product; if the prompt only makes sense to your existing customers, it belongs in a support FAQ, not here. It should carry the vocabulary of the asker, including their imprecision ("something like grep but for our whole company"). It should not smuggle in your differentiators as presuppositions; "what tools index a billion lines in under a minute" is an ad wearing a question costume. And it should be a question you can imagine a specific person typing on a specific bad day, which is what the `intent` field records: "Discover code search tools" or "Escape CodeHound pricing" pins down who is asking and why.

Steal phrasing from primary sources where you can: sales call notes, support tickets, community threads, the exact words prospects use when they describe the problem to you. Those beat anything you or an LLM will invent from scratch.

## Personas and phrasing variants

Two schema fields deserve special attention because they control for the two biggest sources of hidden bias.

`persona` reframes the same underlying need from a different asker. A staff engineer, an engineering manager, and a CTO all shopping for code search will phrase the question differently and, it turns out, often get different answers with different brands in them. Setting `persona` on a prompt ("engineering_manager", "security_lead") lets you slice the dashboard later and discover, for instance, that AcmeSearch shows up for practitioner phrasings but vanishes when the question sounds like it came from procurement.

`phrasing_variant_of` marks a prompt as a reworded twin of another prompt, pointing at the original's id. "Best code search tools?" and "What should we use to search our codebase?" are the same question wearing different clothes, and models sometimes answer them with different shortlists. Variants measure that sensitivity directly, and the field keeps them linked so aggregate stats can treat them as one question family rather than double-counting.

## expected_competitors: writing down your predictions

Each prompt carries an `expected_competitors` list: the rival brands you would expect a well-informed answer to name. For "What are good alternatives to CodeHound?" you might expect `["FindGrep", "SearchLite"]`. The field does two jobs. During scoring it sharpens the competitive read, since a competitor appearing where you expected them is background while a competitor appearing where you did not is news. And during corpus review it forces honesty about the battlefield: if you cannot name who should appear in an answer, you probably have not thought hard enough about what the prompt is measuring.

Only list brands that are in your tracked competitor set in `product.yaml`, since those are the names the scorer watches for.

## From 25 prompts to 300: the generator workflow

Hand-writing 300 prompts is a slog, and the repo ships a better path: [prompts/corpus_generator.md](../prompts/corpus_generator.md) contains a copy-paste LLM prompt that takes your filled-in `product.yaml` and emits a full corpus in the correct schema, with category balance and id conventions handled for you. The workflow that works in practice runs in four passes: fill in `product.yaml` with your real product, aliases, category, and competitors; paste the generator prompt plus your yaml into a capable model and collect the output; then edit like an editor, deleting prompts that sound like your website, deduplicating near-twins that are not deliberate phrasing variants, and checking that `expected_competitors` entries are plausible rather than autocompleted; finally, spot-run the result through the mock lane (`python -m geo.runner --corpus prompts/my_corpus.json --dry-run --reps 2 --experiment corpus_check`) to catch schema errors before any paid run.

Budget most of your time on the editing pass. Generated prompts arrive grammatical and well-formed, and perhaps a quarter of them will still be questions nobody would ask; the 30 minutes you spend cutting those is worth more than any other 30 minutes in this playbook.

Corpus size trades against cost and statistical power: more prompts and more repetitions mean tighter error bars and bigger bills. Once your corpus draft exists, run a small pilot and point the power analysis at its results file (the full invocation is in [04-statistical-rigor.md](04-statistical-rigor.md)); it will tell you how large a run you actually need before the numbers become trustworthy.
