# PROGRESS.md — W14 P3-14：report converter → leaderboard data

## 任务 0 定位（2026-08-12）
- 基线绿：bench 包 313 passed；全仓 814 passed、skipped 0。
- 旧转换（legacy TS）：`apps/leaderboard-web/scripts/convert.mts`（确定性 report v3 → bench.json 变换）。
- leaderboard 消费结构：`apps/leaderboard-web/src/lib/bench.ts` `BenchmarkData`（schema/generatedAt/convertedAt/source/params/contestants/leaderboard/scenarios/entryScenarioStats/scenarioOrder）；`converter.py::transform_report` 已产出同结构，与旧转换逐字段一致（现有 oracle 测试精确相等通过）。
- 对拍输入 fixture：`apps/leaderboard-web/scripts/input/results.json`（10 contestants、leaderboard 8 + control 2、7 场景、35 matches、含 killEvents/perTickSamples）。

## 理解的目标／顺序／最大风险
- 目标：官方 Python 产出链 = 新建 `packages/arena-hero-bench/src/arena_hero_bench/leaderboard_data.py`，纯函数 `produce_leaderboard_data(raw, ...)`：复用 transform_report 聚合逻辑（不重复实现），前置 fail-closed 校验（空输入/缺字段抛错），无 IO 副作用；选独立模块比扩展 converter.py 更内聚（converter 保持稳定入口不动）。
- 顺序：1) leaderboard_data.py + 单测（结构 + fail-closed）；2) 旧转换现跑生成 golden 入库；3) golden 逐字段对拍测试（数值界 1e-9）；4) 全量校验（pytest >=814、ruff、ty、diff-check、public-surface）；5) 一功能一 commit，不 push。
- 最大风险：fail-closed 校验误伤合法产物（以现有 fixture 校准）；golden 与活跑旧转换不一致（生成后立即对拍验证）；防作弊红线（不动 CI/既有测试/不放松断言）。

## 收口（2026-08-12）
- 完成：leaderboard_data.py（produce_leaderboard_data，fail-closed）已提交 3e33381；golden（旧转换现跑生成）+ 逐字段对拍测试已提交 20f8f59。
- 校验：bench 336 passed；全仓 837 passed（基线 814）、skipped 0；ruff/ty/diff-check/public-surface 全过。BLOCKED.md = 无。
