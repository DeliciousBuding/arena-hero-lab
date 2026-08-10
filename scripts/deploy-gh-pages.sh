#!/usr/bin/env bash
# 手动部署到 gh-pages 分支（GitHub Pages legacy 模式）。
# 用法：pnpm deploy:gh-pages  （自动 build + 推送）
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> build 静态导出"
pnpm build

WORKTREE=".worktrees/gh-pages"
cleanup() {
  git worktree remove -f "$WORKTREE" 2>/dev/null || true
  git worktree prune 2>/dev/null || true
}
trap cleanup EXIT
rm -rf "$WORKTREE"
git worktree add -B gh-pages "$WORKTREE" origin/gh-pages

echo "==> 同步 out/ 到 gh-pages 分支"
find "$WORKTREE" -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +
cp -r out/. "$WORKTREE"/
touch "$WORKTREE/.nojekyll"

cd "$WORKTREE"
git add -A
git -c user.name="deploy" -c user.email="deploy@localhost" commit -m "deploy: $(date -u +%Y-%m-%dT%H:%M:%SZ)" || { echo "无变更，跳过推送"; exit 0; }
git push origin HEAD:gh-pages
cd ..
echo "==> 已部署: https://deliciousbuding.github.io/arena-hero-leaderboard/"
