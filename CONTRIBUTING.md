# Contributing

Thank you for contributing to Arena Hero Lab.

## Development setup

Install Python 3.12, `uv`, Node.js 22 or newer, and pnpm 10.32.1. Then run:

```bash
uv sync --locked --all-groups
pnpm install --frozen-lockfile
```

## Choose the owning component

- Simulator rules and deterministic domain behavior: `packages/arena-hero-sim`
- Benchmark contracts, manifests, runners, and conversion: `packages/arena-hero-bench`
- Statistics and research workflows: `packages/arena-hero-research`
- Browser UI and static export: `apps/leaderboard-web`

Keep changes focused and avoid introducing reverse dependencies. See
[`docs/architecture.md`](docs/architecture.md) for the dependency model.

## Tests

Run the complete local gate before opening a pull request:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check packages scripts apps/leaderboard-web/scripts/release.py
uv run pytest -q
pnpm lint
pnpm build
python scripts/check_public_surface.py
git diff --check
```

Changes to report conversion must keep the TypeScript oracle differential test passing.
Changes to manifests must include positive and rejection tests for publication invariants.

## Data and fixtures

Use synthetic or explicitly public fixtures. Remove machine paths, credentials, private host
names, and user identifiers before committing. Generated artifacts must include provenance
and cryptographic digests where required by their schema.

## Commits and pull requests

Prefer small commits that each preserve a working tree. Describe the owning package, the
observable behavior, and the validation commands in the pull request. Publishing and remote
repository administration are not part of ordinary code review.
