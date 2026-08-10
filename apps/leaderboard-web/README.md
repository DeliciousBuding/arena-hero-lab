# @arena-hero/leaderboard-web

Static Arena Hero benchmark and replay explorer built with Next.js static export.

## Data source

- The application consumes `src/data/bench.json` at build time.
- `bench.json` is generated from a v3 benchmark report by the authoritative Python
  converter in `arena-hero-bench` (run `pnpm convert` from the repository root).
- The application renders generated reports; it never recomputes authoritative
  rankings, significance, or publication eligibility.
- `scripts/convert.mts` is retained only as a differential-test oracle for the Python
  converter and must not become a second production pipeline.

## Development

```bash
pnpm install --frozen-lockfile   # from the repository root
pnpm dev                        # local dev server
pnpm lint                       # eslint
pnpm build                      # static export into out/
pnpm preview                    # serve the static export locally
```

The GitHub Pages-compatible base path defaults to `/arena-hero-leaderboard` while the
existing public URL is in use; override with `NEXT_PUBLIC_BASE_PATH=""` for another host.

## Deployment

- Pushing to `master` triggers the repository GitHub Actions Pages workflow, which builds
  and deploys the static export automatically.
- `scripts/deploy-gh-pages.sh` is a manual legacy path that updates the `gh-pages` branch;
  on Windows it must be run with an explicit Git Bash or WSL bash.

Last updated: 2026-08-10.
