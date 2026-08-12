# melee smoke fixtures

These are synthetic smoke fixtures for the `arena.bench.melee.v1` runner.

- `run-melee-v1.json` reuses the committed burnin evolve corpus and Python
  agent records, but each contestant supplies its own observation snapshots.
- `melee-champion-snapshots-v1.jsonl`, `melee-mid-snapshots-v1.jsonl`, and
  `melee-dead-snapshots-v1.jsonl` are modified copies of
  `snapshots-burnin-a-v1.jsonl` whose terminal tick values were changed only to
  exercise the survive / survive / die ranking paths. They are NOT real match
  results.

Expected ranking (survival first, then aggregate terminal score):

1. `melee-champion` (survives, highest terminal resources/population/cargo)
2. `melee-mid` (survives, lower terminal values)
3. `melee-dead` (core destroyed)

Run: `uv run arena-hero-bench melee --run run-melee-v1.json`
