# GEO: Measure How AI Models Talk About Your Product

When a prospect asks ChatGPT "what are the best code search tools?", is your product in the answer? You track your Google rankings; you probably have no idea what Claude, GPT-4o, or Gemini say when someone asks about your category. This repo is a measurement harness for exactly that question. You describe your product and its competitors in one YAML file, point the runner at a corpus of buyer-style prompts, and get back a dashboard showing how often each model mentions you, where in the answer you appear, and how you rank against the competition, with enough repetitions to separate signal from sampling noise.

Everything here uses a fictional example product, **AcmeSearch**, so the demo works before you touch anything. Swap in your own product and the same pipeline measures you.

## 10-minute quickstart (no API keys)

```bash
git clone <this-repo> && cd GEO_public
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Open `product.yaml` and replace AcmeSearch with your product, aliases, category, and competitors (or leave it as-is for the demo). Then run the mock lane:

```bash
python -m geo.runner --corpus prompts/seed_corpus.json --dry-run --reps 2 --experiment demo
python -m geo.dashboard --experiment demo
```

Open the HTML file it writes under `results/reports/`. That is the full pipeline: corpus in, scored responses stored, share-of-voice dashboard out.

`--dry-run` uses synthetic responses. The mock provider templates your configured brand and competitor names with seeded-random mention probabilities, so the dashboard is a realistic demo of the pipeline for any `product.yaml`, but the numbers say nothing about real models. Real measurement needs a model lane.

## Real measurement

The default lane is the `claude` alias, which routes through the Claude Code CLI over your existing OAuth login. No API key required:

```bash
python -m geo.runner --corpus prompts/seed_corpus.json --models claude --reps 5 --experiment baseline
python -m geo.dashboard --experiment baseline
```

API lanes need the matching environment variable:

| Alias | Provider | Requires |
|---|---|---|
| `claude` | Claude Code CLI (OAuth) | a logged-in `claude` CLI |
| `claude-api` | Anthropic Messages API | `ANTHROPIC_API_KEY` |
| `chatgpt` | OpenAI Chat Completions | `OPENAI_API_KEY` |
| `gemini` | Google Generative AI | `GOOGLE_API_KEY` |

Run several at once with a comma-separated list: `--models claude,chatgpt,gemini`.

Two knobs matter for cost and confidence. `--reps 5` per prompt-model pair is a sensible first baseline; models are stochastic, and a single response per prompt is an anecdote. `python -m geo.power_analysis --results results/raw/baseline/<model>/<date>/results.jsonl` reads a prior run's results and tells you how many repetitions you need to detect a given effect size, and `python -m geo.spend_estimate --reps 12` prices a run before you commit. The 25-prompt seed corpus proves the pipeline; for decisions you can defend, generate a 300-prompt corpus for your actual category using the copy-paste LLM recipe in `prompts/corpus_generator.md`.

## What gets measured

Every response is scored against the brand and competitor patterns from your `product.yaml` (name plus aliases, word-boundary, case-insensitive), then aggregated per model:

| Metric | Question it answers |
|---|---|
| Mention rate | In what fraction of responses does your brand appear? |
| Share of voice | Of all brand mentions (you + competitors), what share is yours? |
| First-mention offset | How early in the response do you first appear? |
| List appearance + rank | When the model writes a ranked list, are you on it, and at what position? |
| Misrepresentations | Does the model repeat known-false claims about you? (optional, via `prompts/expected_outcomes.json`) |
| Semantic quality | How accurate and favorable is the mention? (opt-in LLM judge, needs `OPENAI_API_KEY`) |

## Catch regressions when models update

Model updates are deployments you did not schedule and cannot roll back. A new model version can silently drop your mention rate or resurrect a misrepresentation you thought was gone, so the regression harness treats your measured baseline the way CI treats a passing build:

```bash
# Freeze a baseline from an experiment you already ran
python -m geo.regression.runner snapshot --experiment baseline --model claude --out snapshots/claude-baseline.json

# Compare any two snapshots
python -m geo.regression.runner compare --baseline snapshots/claude-baseline.json --candidate snapshots/claude-new.json

# Or run, snapshot, and gate in one step
python -m geo.regression.runner verify --corpus prompts/seed_corpus.json --model claude \
    --experiment recheck --baseline snapshots/claude-baseline.json
```

`verify` exits 0 on PASS, 1 on WARN, 2 on FAIL, so a scheduled CI job can page you when a model update moves your numbers. Thresholds (`--rate-warn`, `--rate-fail`, `--misrep-fail`, `--significance-p`) are tunable per run.

## Repo map

| Path | What it is |
|---|---|
| `product.yaml` | The one file you edit: your brand, aliases, category, competitors |
| `geo/` | The engine: runner, scoring, storage, dashboard, power analysis, spend estimate |
| `geo/regression/` | Snapshot, compare, and verify tooling for model-update regression checks |
| `prompts/` | Seed corpus, corpus schema docs, and the 300-prompt corpus generator recipe |
| `playbook/` | The GEO methodology, from first baseline to GTM integration |
| `experiments/` | Worked intervention experiments (llms.txt, benchmark tables, freshness decay) |
| `examples/` | A demo report generated from a mock run |
| `tests/` | Offline test suite (`python -m pytest tests/ -q`), mock lane only |

Start with [`playbook/00-what-is-geo.md`](playbook/00-what-is-geo.md). It explains what GEO is, why the measurement has to come before the optimization, and how the rest of the playbook builds from a baseline run to a quarterly review cadence.

## License

MIT. See [LICENSE](LICENSE).
