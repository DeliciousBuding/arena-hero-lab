# head_to_head smoke fixtures

These are synthetic smoke fixtures for the `arena.bench.head-to-head.v1` runner.

- `run-head-to-head-v1.json` reuses the committed burnin evolve corpus and Python
  agent records, but each contestant supplies its own observation snapshots.
- `python-beats-evolve-snapshots-v1.jsonl` and `python-dies-snapshots-v1.jsonl`
  are modified copies of `snapshots-burnin-a-v1.jsonl` whose terminal tick values
  were changed only to exercise the win and loss verdict paths. They are NOT real
  match results.

Run: `uv run arena-hero-bench head-to-head --run run-head-to-head-v1.json`
