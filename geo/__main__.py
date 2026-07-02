"""Usage pointer for ``python -m geo``."""

USAGE = """GEO measurement harness. Run a subcommand module directly:

  python -m geo.runner --corpus prompts/seed_corpus.json --dry-run --reps 2 --experiment smoke
  python -m geo.dashboard --experiment smoke
  python -m geo.power_analysis --results results/raw/<experiment>/<model>/<date>/results.jsonl
  python -m geo.spend_estimate --reps 12

Edit product.yaml to point the harness at your product. Add --help to any
subcommand for its full options.
"""

print(USAGE)
