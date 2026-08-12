# BLOCKED.md — W18-A P6-7 competitive evaluation battery

- 顺手活（未做，待拍板）：
  1. `ReferenceWorkloadRunner` 的 known-answer gate 不支持 `repetitions > 1`（replay 载荷绑定 episode 身份，
     重复 episode 触发 artifact_refs mismatch，`test_runner_rejects_order_repetition_and_partial_budget_drift`
     已固化该拒绝语义）。多场次重复采样若要走通 runner，需单独设计 repetition 下的 replay 身份/known-answer
     契约，属规则/契约变更，需拍板。
  2. bench 层「从原始 match 结果计算 per-match rank」不在本仓（report v3 的 match.rank/winner 由外部评测产物提供），
     converter 只做聚合/排序。若 competitive evaluation 需要 bench 直接产出 match rank，需新增契约，需拍板。
- 其余：无。

- 顺手活（未做，待拍板）：
  3. bench 层「直接调用 offline agent CLI（`arena-hero-agent run`）为电池 cell 产 records」
     需要把 agent 包/SDK 装进 bench venv 或经外部 uv env 调用；当前 bench 无该依赖，电池
     提交证据直接消费既有 `agent-run-v1` JSONL fixture，live CLI seam 保持 fail-closed 说明。
     是否允许 bench 依赖 SDK/agent 属依赖契约变更，需拍板（W18-B 落地后接缝更清晰）。
