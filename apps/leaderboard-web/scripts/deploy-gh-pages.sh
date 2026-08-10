#!/usr/bin/env bash
# Publish the static web export to the legacy gh-pages branch.
# This script is intentionally manual and never runs as part of build or test.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
APP_DIR="$REPO_ROOT/apps/leaderboard-web"
WORKTREE="$REPO_ROOT/.worktrees/gh-pages"

cd "$REPO_ROOT"
echo "==> build static export"
pnpm --filter @arena-hero/leaderboard-web build

cleanup() {
  git worktree remove -f "$WORKTREE" 2>/dev/null || true
  git worktree prune 2>/dev/null || true
}
trap cleanup EXIT

git worktree remove -f "$WORKTREE" 2>/dev/null || true
git worktree prune 2>/dev/null || true
if [[ -e "$WORKTREE" ]]; then
  echo "refusing to reuse residual worktree path: $WORKTREE" >&2
  exit 1
fi
git worktree add -B gh-pages "$WORKTREE" origin/gh-pages

echo "==> sync static export"
find "$WORKTREE" -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +
cp -r "$APP_DIR/out/." "$WORKTREE"/
touch "$WORKTREE/.nojekyll"

cd "$WORKTREE"
git add -A
git -c user.name="deploy" -c user.email="deploy@localhost" commit -m "deploy: $(date -u +%Y-%m-%dT%H:%M:%SZ)" || {
  echo "no changes; skipping push"
  exit 0
}
git push origin HEAD:gh-pages
echo "==> deployed to the configured GitHub Pages site"
