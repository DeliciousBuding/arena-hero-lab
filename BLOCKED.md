# BLOCKED.md — W18-A P6-7 competitive evaluation battery

- 顺手活（已裁决不做，2026-08-12）：
  1. `ReferenceWorkloadRunner` 的 known-answer gate 不支持 `repetitions > 1`（replay 载荷绑定 episode 身份，
     重复 episode 触发 artifact_refs mismatch，`test_runner_rejects_order_repetition_and_partial_budget_drift`
     已固化该拒绝语义）。多场次重复采样若要走通 runner，需单独设计 repetition 下的 replay 身份/known-answer
     契约，属规则/契约变更。**已裁决：不做**（2026-08-12：soak/performance 外层已有等价
     机制：`soak.rounds`、`performance.measure_reference_workload` warmup+measured，不依赖
     manifest repetitions）。
  2. bench 层「从原始 match 结果计算 per-match rank」不在本仓（report v3 的 match.rank/winner 由外部评测产物提供），
     converter 只做聚合/排序。若 competitive evaluation 需要 bench 直接产出 match rank，需新增契约。**已裁决：不做**（2026-08-12：外部 `run-arena-report.mts` report.v3 已是
     rank SSOT，lab 再产 rank 会形成双权威）。
- 其余：无。

- 已解决（W19-B 拍板 2026-08-12）：
  3. bench live agent CLI seam：**不允许** bench 依赖 agent 包/SDK（依赖契约维持，bench 仍只依赖 sim），
     改为外部 uv env / 外部数据目录可选集成。`--agent-runs-dir` / manifest `agent_runs_dir` /
     `ARENA_AGENT_RUNS_DIR` 指向 batch 输出目录，布局
     `<runs>/<contestant>/<scenario>/<seed>/<tenant>/ticks.jsonl`；`map_agent_runs_dir` 精确覆盖 +
     tenant/schema 校验，目录/run 缺失或 tenant 不符 fail-closed（CLI exit 2），不回落 fixture。
     详见 PROGRESS.md W19-B。
