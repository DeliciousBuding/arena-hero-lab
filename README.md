# Arena Hero Lab

Arena Hero Lab is a public monorepo for deterministic simulation, reproducible
benchmarks, research workflows, replay artifacts, and the Arena Hero leaderboard web app.

![Python](https://img.shields.io/badge/Python-3.12-3776ab)
![Next.js](https://img.shields.io/badge/Next.js-16-black)
![React](https://img.shields.io/badge/React-19-61dafb)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178c6)
![Static Export](https://img.shields.io/badge/Static-Export-ffffff)
![License](https://img.shields.io/badge/License-Apache--2.0-blue)

The repository is organized around replaceable packages with one-way dependencies:

```text
arena-hero-sim <- arena-hero-bench <- arena-hero-research
                         |
                         +-> apps/leaderboard-web
```

## Repository layout

- `packages/arena-hero-sim` — deterministic domain and serialization foundations.
- `packages/arena-hero-bench` — benchmark contracts, manifests, orchestration, and report conversion.
- `packages/arena-hero-research` — statistical and research tooling over immutable artifacts.
- `apps/leaderboard-web` — Next.js static benchmark and replay explorer.
- `docs` — architecture, design-system, and research documentation.

The Python packages use `uv`; the browser application uses `pnpm`. TypeScript is limited
to browser code, generated types, and a temporary converter oracle retained for migration
verification.

## Quick start

Requirements:

- Python 3.12
- `uv`
- Node.js 22 or newer
- pnpm 10.32.1

```bash
uv sync --locked --all-groups
pnpm install --frozen-lockfile

uv run pytest -q
pnpm lint
pnpm build
```

Run the web application locally:

```bash
pnpm dev
```

The GitHub Pages-compatible base path defaults to `/arena-hero-lab`, matching the
public repository and Pages site. Override it for another host:

```bash
NEXT_PUBLIC_BASE_PATH="" pnpm build
```

## Benchmark report conversion

Python is the authoritative converter:

```bash
pnpm convert
```

It transforms `arena.bench.report.v3` into the static web dataset at
`apps/leaderboard-web/src/data/bench.json`. The former TypeScript converter remains
available only as a differential-test oracle. Its public compatibility source lives in [arena-hero-agent-ts](https://github.com/DeliciousBuding/arena-hero-agent-ts):

```bash
pnpm convert:oracle
```

The release command validates locally by default and publishes only when `--deploy` is
provided explicitly (a manual legacy path that updates the `gh-pages` branch):

```bash
python apps/leaderboard-web/scripts/release.py --force
```

Note: pushing to `master` also triggers the existing GitHub Actions Pages workflow
(`.github/workflows/deploy.yml`), which builds and deploys the static export to GitHub
Pages automatically. The `--deploy` flag is an additional manual path and is not required
for CI deployments.

## Public leaderboard

The current static site remains available at
<https://deliciousbuding.github.io/arena-hero-lab/>.

## Platform status evidence

The repository generates `apps/leaderboard-web/src/data/platform.json` as versioned,
fail-closed release evidence for Python agent conformance, simulator differential checks,
and the research fit -> certificate -> report chain. The public Leaderboard intentionally
renders rankings only; it does not expose a platform overview, platform navigation item, or
platform detail page.

The document schema is `arena.platform.status.v2`. Regenerate and verify it with:

```bash
pnpm convert:platform
```

This artifact is evidence about reproducibility, not a competitive result or production
performance claim.


## Architecture and policies

- [Architecture](docs/architecture.md)
- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [License](LICENSE)
- [Security policy](SECURITY.md)
- [Web design system](docs/design-system.md)

## Attribution

Arena Hero Lab builds on the public [Arena Hero](https://doc.arenahero.io/)
ecosystem. Benchmark fixtures include references to community projects from
[Drew-Z](https://github.com/Drew-Z/arena-hero-agent),
[VelvetEvening](https://github.com/VelvetEvening/ArenaHero-nearly-perfect-guide),
[Waaiging](https://github.com/Waaiging/ArenaHero),
[feixingwawa](https://github.com/feixingwawa/arena-hero-tactic), and
[Torther](https://github.com/Torther/arena-evolve). Their code and content remain governed
by their respective repositories and licenses.

The leaderboard visual design was informed by the public
[LM Arena leaderboard](https://arena.ai/leaderboard).
