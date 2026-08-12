# PROGRESS.md — W18-A P6-7：competitive evaluation battery 首条证据闭环

## 任务 0 定位（2026-08-12）
- 基线绿：全仓 `uv run pytest -q` = **889 passed**（W17 收口后）。
- W18-A 目标（`docs/plan/python-first-task-breakdown-v4.md` Wave 18）：scenario x seed x
  contestant 电池编排 → Python Agent 离线 run → `import_agent_run` → KPI differential（P6-3）
  /stats/leaderboard/report artifact，首条证据闭环；fail-closed、确定性、未分类=0、反向验证。
- 接缝约束（captain 裁定）：bench 不 import agent 包/SDK（venv 无 `arena_hero`），提交证据
  直接消费既有 `agent-run-v1` JSONL fixture；live agent CLI 调用为跨仓 seam，写进 BLOCKED 待拍板。

## 任务 1 实现与落点
- 新增 `packages/arena-hero-bench/src/arena_hero_bench/competitive_eval.py`：
  - `arena.bench.competitive-eval.v1` 电池 manifest（scenarios[]/seeds[]/contestants[]，
    覆盖校验 fail-closed：每 contestant records 必须恰好覆盖全部 scenario x seed）。
  - `run_battery_from_manifest`：按 manifest 固定序（scenario -> seed -> contestant）逐 cell
    复用 `build_kpi_differential_run`（P6-3）分类；私有 `cell_factory` seam 供反向验证，
    注入 cell 标记 `injected_cells=true` / `attested=false`。
  - 报告 `arena.bench.competitive-eval-report.v1`：per-cell digest、跨 seed 每维度聚合、
    presentation-level ranking（`aggregate_match_count`，score=总 MATCH，tie-break=MISMATCH 升序
    + contestant id）、content-addressed `artifact_sha256`、未分类恒 0（防御性 fail）。
  - 确定性：payload 无 wall-clock 时间戳；同 manifest 两次运行 digest 一致（含跨进程 CLI）。
- CLI：`arena-hero-bench competitive-eval --run <manifest>`（pass=0 / fail=1 / 无效 manifest=2）。
- 公共 API：`__init__.py` + `test_public_api.py` 冻结集加入 20 个导出名。
- 提交 fixture `tests/fixtures/competitive_eval/`：
  - 2 scenario（`burnin-a` 8 tick 全量 / `burnin-b` 5 tick 子集，evolve corpus + decision trace
    从既有 differential/kpi fixture 派生，manifest inputs sha256 逐字节核对）。
  - 2 seed x 2 contestant（`python-agent-ffa` 对齐 / `python-agent-soft` 激进变体）共 8 个
    `agent-run-v1` records + 2 个 observation snapshot 文件。
- 测试 `tests/test_competitive_eval.py`（15 条）：全电池分类计数（42 MATCH / 6 MISMATCH /
  未分类 0）、确定性、manifest fail-closed（schema/evidence_kind/空数组/覆盖缺失/重复 key）、
  corrupt cell fail-closed、注入 raising cell / unclassified report → attested=false、
  CLI pass/fail/无效。

## 反向验证（亲手制造失败）
- corrupt cell（torn tail）→ status=fail、issue `cell_error`、attested=false、该 cell
  error 保留、其余 7 cell 仍 ok。
- 注入 raising cell（soft 全部 cell）→ status=fail、attested=false、`injected_cells=true`，
  零成功 contestant 仍出现在 aggregates（samples=0）与 ranking，不崩。
- 注入 unclassified report（unclassified_count=1）→ status=fail、issue `unclassified`。
- 未剥离时间戳的原始 digest 依赖由 Agent 侧 W18-B canonicalizer 单独验收（本仓不重复）。

## 收口（2026-08-12）
- 全量门禁（提交后复跑）：
  - bench：`uv run pytest packages/arena-hero-bench -q` = **398 passed**（基线 383 + 新增 15）。
  - 全仓：889 + 15 = **904 passed、skipped 0**。
  - `ruff format --check` / `ruff check` / `ty check`（CI 范围）/ `python scripts/check_public_surface.py`
    / `git diff --check` 全过。
- 确定性证据：同一 manifest 两次运行 artifact_sha256 =
  `9bb3681c2116ea0a5fdde83cd7f0c5980937a9559f864c775fe68dcf1a0f2ea4`（API 与 CLI 跨进程一致）。
- commit：`feat(bench): competitive evaluation battery (P6-7)`；
  `docs(bench): competitive-eval README/BLOCKED + W18-A closeout`。
- 接缝：live agent CLI（`arena-hero-agent batch/run`，W18-B）为跨仓 seam，未接线；
  bench 依赖 SDK/agent 需拍板，已记 BLOCKED。

## W19-B（2026-08-12）：live agent CLI seam + process_executor 信封透传复核

### 任务 1：competitive-eval live agent CLI seam（DONE）
- commit `9d7f6f7`（worktree `.worktrees/w19-live-seam`，branch `w19/live-seam`；未 push）。
- 新增可选集成 seam，**不加 bench 依赖**（bench 仍只依赖 sim）：
  - `agent_runs_dir` 解析优先级：CLI `--agent-runs-dir` > manifest `agent_runs_dir` > env
    `ARENA_AGENT_RUNS_DIR`；布局 `<runs>/<contestant>/<scenario>/<seed>/<tenant>/ticks.jsonl`
    （即 `arena-hero-agent run --data-root <cell-dir>` 的原生输出）。
  - `map_agent_runs_dir`：batch 输出目录 → per-cell records 映射；精确覆盖（缺 cell / 多 cell 均
    fail-closed），每条 run 首记录 schemaVersion/tenantId 校验。
  - fail-closed：目录不可用 / 缺 run / tenant 不符 → `AgentRunsError`（CLI exit 2），
    不伪造、不静默回落 fixture。
- 测试：新增 11 条（映射 / fail-closed / CLI / manifest / env / 优先级）+ env-gated 集成测试
  `test_live_agent_runs_dir_integration`（默认 skip；无 agent 可跑保持 skip，设
  `ARENA_AGENT_RUNS_DIR` 才跑）。README 新增 seam 用法。
- 门禁：全仓 pytest 904 → **915 passed、1 skipped**；`ruff format --check` / `ruff check` /
  `ty check` / `scripts/check_public_surface.py` / `git diff --check` 全绿。

### 任务 2：process_executor 信封透传（SKIP）
- 读码结论：`arena.process.work.v1` / `arena.process.result.v1` 已透传并强校验 request id /
  operation id / shard / plan_sha256 / backend / engine / protocol（`_parse_result_envelope`
  逐项对拍 + `test_process_executor_conformance.py` 已固化 tamper/乱序/cardinality 拒绝）。
  唯一未透传的 contestant 身份属请求侧属性（work 信封每 request 已带 `contestant_ids`），
  补到 result 信封需改 `SimulationResult`（sim 契约）与 result 信封 schema 字段 →
  版本化信封协议变化，按指令 SKIP，不改协议。无代码改动。
