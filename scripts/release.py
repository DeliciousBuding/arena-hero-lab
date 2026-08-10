# 一键发布脚本（站点线数据更新链路，Python 版）
#
# 用法：
#   python scripts/release.py                            # 内置副本 scripts/input/results.json
#   python scripts/release.py --source <results.json>    # 指定评测产物
#   python scripts/release.py --latest                   # 自动检测最新产物（arena 主仓库 data/runs/sim）
#   python scripts/release.py --latest --run-root <dir>  # 自定义 run 根目录
#   python scripts/release.py --skip-deploy              # 只转换+构建，不部署
#   python scripts/release.py --force                    # 跳过"无更新"检查强制发布
#
# 流程：检测/校验产物 → convert（确定性转换）→ build → lint → 部署 gh-pages → 打印核对信息
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_SOURCE = os.path.join(SCRIPT_DIR, "input", "results.json")
BENCH_PATH = os.path.join(REPO_ROOT, "src", "data", "bench.json")
ONLINE_URL = "https://deliciousbuding.github.io/arena-hero-leaderboard/"


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    log(f"ERROR: {msg}")
    sys.exit(1)


def run(command: list[str]) -> int:
    log("==> " + " ".join(command))
    return subprocess.run(command, shell=True).returncode


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest_run(run_root: str) -> str:
    """扫描 run 根目录下所有 arena-bench-*/results.json，按 generatedAt 取最新。"""
    if not os.path.isdir(run_root):
        fail(f"run 目录不存在: {run_root}（用 --run-root 指定，或 --source 直接给产物路径）")
    candidates = []
    for entry in sorted(os.listdir(run_root)):
        if not entry.startswith("arena-bench-"):
            continue
        results = os.path.join(run_root, entry, "results.json")
        if not os.path.isfile(results):
            continue
        meta = read_json(results)
        candidates.append((meta.get("generatedAt", ""), entry, results))
    if not candidates:
        fail(f"未找到任何带 results.json 的 run（{run_root}）")
    candidates.sort(reverse=True)
    _, name, results = candidates[0]
    log(f"==> 最新 run: {os.path.join(run_root, name)}")
    return results


def deploy_gh_pages() -> None:
    """用 git worktree 把 out/ 同步到 gh-pages 分支（legacy 部署，不依赖 bash）。"""
    worktree = os.path.join(REPO_ROOT, ".worktrees", "gh-pages")
    run(["git", "worktree", "remove", "-f", worktree])
    run(["git", "worktree", "prune"])
    if run(["git", "worktree", "add", "-B", "gh-pages", worktree, "origin/gh-pages"]) != 0:
        fail("worktree 创建失败（origin/gh-pages 不存在？先手动部署一次）")
    try:
        run(["git", "-C", worktree, "rm", "-rq", "--ignore-unmatch", "."])
        for name in os.listdir(worktree):
            if name != ".git":
                path = os.path.join(worktree, name)
                if os.path.isdir(path) and not os.path.islink(path):
                    import shutil

                    shutil.rmtree(path)
                else:
                    os.remove(path)
        out_dir = os.path.join(REPO_ROOT, "out")
        for name in os.listdir(out_dir):
            src = os.path.join(out_dir, name)
            dst = os.path.join(worktree, name)
            if os.path.isdir(src):
                import shutil

                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        open(os.path.join(worktree, ".nojekyll"), "w").close()
        run(["git", "-C", worktree, "add", "-A"])
        stamp = datetime.now(timezone.utc).isoformat()
        code = run(["git", "-C", worktree, "-c", "user.name=deploy", "-c", "user.email=deploy@localhost", "commit", "-m", f"deploy: {stamp}"])
        if code != 0:
            log("==> 无变更，跳过推送")
        elif run(["git", "-C", worktree, "push", "origin", "HEAD:gh-pages"]) != 0:
            fail("gh-pages 推送失败")
    finally:
        run(["git", "worktree", "remove", "-f", worktree])
        run(["git", "worktree", "prune"])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="站点一键发布（数据更新链路）")
    parser.add_argument("--source", default="", help="评测产物 results.json 路径")
    parser.add_argument("--latest", action="store_true", help="自动检测最新 run")
    parser.add_argument("--run-root", default="", help="run 根目录（--latest 时生效）")
    parser.add_argument("--skip-deploy", action="store_true", help="只转换+构建，不部署")
    parser.add_argument("--force", action="store_true", help="跳过无更新检查")
    args = parser.parse_args()

    log("==> release.py 站点发布")

    # ---------- 1. 确定评测产物 ----------
    if args.source:
        source_path = os.path.abspath(args.source)
        if not os.path.isfile(source_path):
            fail(f"产物不存在: {args.source}")
    elif args.latest:
        run_root = args.run_root or os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "data", "runs", "sim"))
        source_path = find_latest_run(run_root)
    else:
        source_path = DEFAULT_SOURCE
        if not os.path.isfile(source_path):
            fail("内置副本不存在，请用 --source 或 --latest")
    log(f"==> 产物: {source_path}")

    # ---------- 2. 无更新检测（与当前 bench.json 对比） ----------
    meta = read_json(source_path)
    if os.path.isfile(BENCH_PATH) and not args.force:
        cur = read_json(BENCH_PATH)
        if cur.get("generatedAt") == meta.get("generatedAt") and cur.get("schema") == meta.get("schema"):
            log("==> 当前 bench.json 与该产物一致（generatedAt/schema 相同），无更新。用 --force 强制发布。")
            return

    # ---------- 3. 转换 + 构建 + 检查 ----------
    if run(["pnpm", "convert", source_path]) != 0:
        fail("convert 失败（schema 校验不通过？）")
    if run(["pnpm", "build"]) != 0:
        fail("build 失败")
    if run(["pnpm", "lint"]) != 0:
        fail("lint 失败")

    # ---------- 4. 部署 gh-pages ----------
    if args.skip_deploy:
        log("==> --skip-deploy：跳过部署")
    else:
        log("==> 部署到 gh-pages")
        deploy_gh_pages()

    # ---------- 5. 核对信息 ----------
    scenarios = len(meta.get("scenarios", []))
    matches = sum(len(s.get("matches", [])) for s in meta.get("scenarios", []))
    params = meta.get("params", {})
    log("")
    log("==> 发布完成核对")
    log(f"    产物: {source_path}")
    log(f"    schema: {meta.get('schema')} · generatedAt: {meta.get('generatedAt')}")
    log(
        f"    参数: {params.get('players')} 条目 × {len(params.get('seeds', []))} 种子 × "
        f"{params.get('ticks')} ticks · {scenarios} 场景 · {matches} 场"
    )
    if not args.skip_deploy:
        log(f"    线上: {ONLINE_URL}")
        log("    注意: Pages 同步有 ~1-3 分钟延迟")


if __name__ == "__main__":
    main()
