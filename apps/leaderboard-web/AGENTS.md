<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version may contain breaking API and file-layout changes. Read the relevant guide in
`node_modules/next/dist/docs/` before changing framework behavior and follow deprecation
notices. This block may be refreshed by `next dev`.

<!-- END:nextjs-agent-rules -->

# Leaderboard web rules

- The application is a static export and consumes `src/data/bench.json` at build time.
- Generate benchmark data with the root Python converter (`pnpm convert`).
- Keep `scripts/convert.mts` only as the differential-test oracle.
- Preserve `/arena-hero-lab` as the public repository and Pages base path; any future
  hosting change belongs in a dedicated release.
- Do not deploy from tests, builds, previews, or ordinary development commands.
- Keep community repository and discussion links traceable to public sources.
