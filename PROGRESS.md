# PROGRESS.md — W16 P3-6：entry point contestant adapter（bench 域，B 线）

## 任务 0 定位（2026-08-12）
- 基线绿：bench 包 342 passed；全仓基线 843 passed（master@e7d3c8e）。
- 现状：
  - `contestant.py`：`ContestantManifest`（schema_version/contestant_id/version/entry_point/language/runtime/
    protocol_version/artifact_sha256/config_schema/resources/capabilities/isolation）＋ `ContestantRegistry`。
  - `process_executor.py`：`BackendProcessSpec`（worker_module/worker_script 二选一）＋ `ProcessExecutor.execute(ShardPlan)`
    提供子进程隔离（per-task 超时/进程树回收/输出上限/退出码/结果信封校验 fail-closed）；唯一执行入口是 `execute(plan)`。
  - SDK `arena-hero-sdk-py` v0.3.0a4（P1-5）：`arena_hero.agent.io.v1.runner`，round 分类 ok/timeout/crash/protocol/error，
    in-process / subprocess 两模式；本机有 checkout 可读，但 `arena_hero` 未装入 lab venv（实测 ModuleNotFoundError）。
- 理解（≤10 行）：
  1. 目标：`contestant_adapter.py` 把 manifest 解析成 process_executor 可执行的 spec（`BackendProcessSpec` ＋ 单请求
     信封，参数编码 entry point 命令/环境白名单/超时），经 `ProcessExecutor.execute` 全链路执行，结果规范化为统一
     `ContestantRunResult`（stdout/stderr/退出码/超时/崩溃）。
  2. 复用路：不新造执行引擎；超时/崩溃/退出码由 process_executor 的 spawn/超时/树回收/退出码路径隔离捕获。
  3. 冒烟：SDK 未装 bench venv（加依赖需写 BLOCKED 等拍板）→ 冒烟用故障注入模拟执行路径（fixture 模拟 SDK runner 的
     CLI 契约），真跑对拍用 SDK 自带 venv 手动执行留证据。
  4. 顺序：PROGRESS → adapter 核心 → 单测 → 冒烟 fixture → 全链路冒烟 → 全仓校验 → 收口文档。
  5. 最大风险：process_executor 信封是模拟域协议，contestant stdout 不经信封透传 → normalize 的 stdout 字段在信封
     路径留空并记录；错误分类依赖 process_executor 稳定消息（"worker exceeded per-task timeout"/"worker exited with code N"），
     已做成常量+表驱动测试。

## 任务 1 计划（落点）
- `contestant_adapter.py`（新建，白名单内）：
  - `build_spec(manifest, *, worker_script=None)` → `BackendProcessSpec`：保守校验（language=python、
    isolation.subprocess_required=True、entry_point 可解析），entry_point 只认两种 spawn 形式
    （`python -m <module> [args...]` / `<path>.py [args...]`），未知/缺失/不支持 fail-closed（`ContestantAdapterError`）。
  - `build_request(manifest, ...)` → 单请求信封：backend_id=contestant_id、engine_version=version、
    protocol_version、requested_features=capabilities；`config.parameters` 编码 entry_point/environment_allowlist/timeout_seconds。
  - `build_plan(...)` → 单请求 `ShardPlan.create`（内容寻址身份）。
  - `normalize_result(ContestantRawOutcome)` → `ContestantRunResult`：优先 executor 隔离的 timeout/crash 标志，
    其次 worker 上报的 round_status（ok/timeout/crash/protocol/error），再 worker 错误消息（超时/退出码/回收窗口/非法载荷），
    最后裸非零退出码 → CRASH。
  - `execute_contestant(manifest, executor, ...)` / `run_contestant(manifest, *, worker_script, stores, ...)` 全链路。
- 不动 `__init__.py`/process_executor/ContestantRegistry/SDK（白名单外；顺手活进 BLOCKED.md）。

## 任务 2 计划（冒烟）
- fixture（`tests/fixtures/contestant/`）：
  - `entry_point_worker.py`：信封 worker（读 `arena.process.work.v1` → 进程内跑 entry point、有界捕获 stdout/stderr →
    写 `arena.process.result.v1`），镜像 SDK runner 的 CLI 契约。
  - `simulated_runner.py`：故障注入 entry point（--mode ok/timeout/crash/protocol/error/hang/hard_exit）。
- 冒烟：ok/timeout/crash/hard_exit/hang 五条全链路；真跑对拍用 SDK venv 手动执行 runner。

## 收口（2026-08-12）
- 完成：
  - `contestant_adapter.py`（新建）：spec 生成（表驱动）、fail-closed、结果规范化、全链路执行；commit `45d76a7`。
  - `tests/test_contestant_adapter.py`（新建，41 条）：spec 表驱动 14 + 请求/计划 4 + normalize 表驱动 16 + 全链路 7；
    fixtures `entry_point_worker.py`/`simulated_runner.py`；commit `72c6752`。
  - 冒烟证据（全链路，模拟 entry point 故障注入；SDK 未装 bench venv）：
    - ok → status=ok exit_code=0 artifact_ref=sha256:48b834f5...
    - timeout → status=timeout error=simulated timeout round
    - crash → status=crash error=simulated crash round
    - hard_exit → status=crash exit_code=3 error=worker exited with code 3: no diagnostics provided
    - hang(timeout=1s) → status=timeout error=worker exceeded per-task timeout of 1 seconds
  - 真跑对拍（SDK v0.3.0a4 自带 venv）：`python -m arena_hero.agent.io.v1.runner --mode subprocess`
    → `status=ok digest=4fa154a332a7709bd4d97ef40dc4a20aea162b0d8789e09b87391859e8f8a0dc`，EXIT=0
    （digest 与 P1-5 记录一致）。bench venv 未装 SDK，adapter 链路的真跑接入留 BLOCKED 拍板。
- 校验（实际输出）：
  - bench：383 passed（基线 342，+41，skipped 0）。
  - 全仓：884 passed（基线 843，+41，skipped 0）。
  - ruff format --check：全过；ruff check：All checks passed；ty：All checks passed；git diff --check：干净；
    public surface scan 通过。
- 降级记录：信封路径不透传 contestant stdout/transcript digest（`ContestantRunResult.stdout` 在信封路径为空，
  已注释说明）；真实 SDK 接入需新增 `arena_hero` 依赖或机器相关路径，均需拍板（BLOCKED.md）。


