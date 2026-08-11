# PROGRESS.md — W17 P3-8：FFA/tournament/scenarios 收口（多 seed 确定性；旧结果差异分类）

## 任务 0 定位（2026-08-12）
- 基线绿：bench 包 `uv run python -m pytest -q` = **383 passed**；全仓 = **884 passed、skipped 0**；
  `ruff check` / `ty check`（CI 范围）基线全过。
- 逐项核对 P3-8 验收三要素（本文件是唯一任务来源，验收 = 多 seed 确定性；旧结果差异分类）：

### 要素 1：FFA/tournament 执行 —— **已覆盖（确认 converter 聚合即汇总能力，不重复实现）**
- 多 contestant 同场：`packages/arena-hero-sim/src/arena_hero_sim/workload.py` `WorkloadCase.contestant_ids`
  （唯一、≥1 校验）；`reference_contracts.ReferenceScenario.contestant_ids` 由多 player world 派生；canonical
  manifest 含两个双人场景（`hostile-swap-rejection`、`cross-player-contested-target`，alpha+beta）。
- 批量多 seed 请求：`WorkloadManifest.iter_requests`（9 case × seed 101..109，确定性 episode/request 身份）；
  bench `orchestration.ShardPlan`（批量 requests + 内容寻址 plan 身份）。
- 多场结果 → 排名/汇总：**`converter.transform_report` 即该能力**——`apps/leaderboard-web/scripts/input/results.json`
  （7 个 ffa-* 场景 × 5 seed × 10 contestant = 35 场）逐 match 聚合 avgRank/bestRank/worstRank、逐场景
  perEntry、composite leaderboard 排序。证据：
  - `tests/test_leaderboard_golden.py::test_python_producer_matches_legacy_ts_golden_field_by_field`（逐字段对拍 legacy TS golden）；
  - `tests/test_converter_differential.py::test_python_converter_matches_typescript_oracle`（同输入 == TS oracle 输出）、
    `test_converter_is_deterministic_for_fixed_input`（同输入字节级一致）；
  - `tests/test_leaderboard_data.py`（produce_leaderboard_data fail-closed 校验）。
- 结论：converter 的 leaderboard 聚合 = tournament/FFA 汇总 runner，已闭环；不再新增重复 runner。

### 要素 2：多 seed 确定性 —— **已覆盖（canonical 9-seed 冻结）+ 补显式验收测试**
- 已覆盖：canonical manifest 9 case、seed 101..109，冻结 known answer（final_world_sha256/metrics/artifact_refs）；
  `tests/test_reference_workload.py::test_canonical_manifest_and_known_answers_are_frozen`、
  `test_runner_is_batch_size_invariant`（run sha 跨 batch 稳定）、`test_manifest_digest_is_stable_across_mapping_reordering`；
  `tests/test_reference_engine.py::test_repeat_runs_are_byte_deterministic`（单 seed 字节级可复现）；
  `tests/test_workload.py::test_request_expansion_is_stable_and_backend_comparable`。
- 缺口：无显式「多 seed 确定性」验收测试（同输入多 seed manifest 重复执行 → 相同 run；每 episode seed 保留；
  不同 seed → 不同 episode 身份/结果；多 contestant 同场执行完成；seed 是 case↔scenario 一等绑定）。
- 落点：新增 `packages/arena-hero-sim/tests/test_multi_seed_determinism.py`（5 条，见任务 1）。

### 要素 3：旧结果差异分类 —— **已覆盖（P6-2 引用即可，不重写）**
- `packages/arena-hero-bench/src/arena_hero_bench/differential.py`（P6-2）：TS legacy vs Python agent replay
  差异分类（MATCH/MISMATCH/EXPECTED_UNKNOWN/INCONCLUSIVE），确定性内容寻址报告；
  `tests/test_replay_differential.py`（分类计数/确定性/逐字段 mismatch/CLI）。
- `kpi_differential.py`（P6-3）：evolve vs Python Agent KPI 差异分类；`tests/test_kpi_differential.py`。
- 结论：旧结果差异分类已被 P6-2/P6-3 differential 覆盖，引用即可。

## 任务 1 计划与落点
- bench：无源码改动——converter 聚合已确认即 FFA/tournament 汇总能力（既有 golden/oracle/确定性测试闭环）。
- sim：新增 `tests/test_multi_seed_determinism.py` 5 条，全走公开 API、无 RNG：
  1. `test_multi_seed_run_is_deterministic_across_repeated_executions`：两次独立 run → 相同 sha256 + episodes。
  2. `test_multi_seed_run_preserves_every_case_seed`：episode.seed == case.seed（101..109），全 distinct。
  3. `test_distinct_seeds_yield_distinct_episode_identity_and_outcome`：episode id / final world 全 distinct，全 COMPLETE/publishable。
  4. `test_multi_contestant_cases_execute_with_all_contestants`：双人场景请求带 alpha+beta 且执行 COMPLETE。
  5. `test_seed_is_a_first_class_binding_of_case_to_scenario`：改 seed → ReferenceWorkloadError("seed") fail-closed。
- 不触碰：CI、pyproject.toml tool 段、其他包；不改既有 orchestration/converter 行为。

## 收口（2026-08-12）
- sim 新测试：`uv run python -m pytest tests/test_multi_seed_determinism.py -q` → **5 passed**（输出见下）。
- 全量校验（提交后复跑）：
  - bench：383 + 0 = **383 passed**（无新增 bench 测试）。
  - 全仓：884 + 5 = **889 passed、skipped 0**。
  - `ruff check` / `ruff format --check` / `ty check`（CI 范围）/ `git diff --check` 全过。
- commit：`test(sim): multi-seed determinism suite (P3-8)`；`docs(bench): close out W17 P3-8 FFA/tournament/scenarios`。
- 三要素证据落点：FFA/tournament=converter 聚合（golden+oracle）；多 seed 确定性=test_multi_seed_determinism.py；
  差异分类=differential.py/kpi_differential.py（P6-2/P6-3 引用）。
