# BLOCKED.md — W17 P3-8

- 顺手活（未做，待拍板）：
  1. `ReferenceWorkloadRunner` 的 known-answer gate 不支持 `repetitions > 1`（replay 载荷绑定 episode 身份，
     重复 episode 触发 artifact_refs mismatch，`test_runner_rejects_order_repetition_and_partial_budget_drift`
     已固化该拒绝语义）。多场次重复采样若要走通 runner，需单独设计 repetition 下的 replay 身份/known-answer
     契约，属规则/契约变更，需拍板。
  2. bench 层「从原始 match 结果计算 per-match rank」不在本仓（report v3 的 match.rank/winner 由外部评测产物提供），
     converter 只做聚合/排序。若 competitive evaluation 需要 bench 直接产出 match rank，需新增契约，需拍板。
- 其余：无。
