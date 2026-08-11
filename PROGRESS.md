# PROGRESS.md — W15 P3-9：分布式分片/合并 fail-closed（缺失/重复/损坏 shard）

## 任务 0 定位（2026-08-12）
- 基线绿：bench 包 336 passed；全仓 837 passed、skipped 0。
- 现状：
  - `packages/arena-hero-bench/src/arena_hero_bench/orchestration.py`：
    - `ShardPlan.verify()`：内容校验（`content_sha256(identity_payload())` vs `plan_sha256`），身份损坏 fail-closed；
      既有 `test_shard_plan_identity.py`（8 条）全覆盖请求字段绑定/规范序/伪造摘要/冻结对象变异/resume 冲突。
    - `merge_shards(expected_shards, results)`：已闭环「缺失/意外 shard → MissingShardError」「重复 expected/重复结果 → DuplicateShardError」
      「多 run → OrchestrationError」「非 COMPLETE/不可 publishable → IncompleteShardError」；内容寻址合并摘要，输入顺序不影响 digest。
      既有 `test_orchestration.py` 覆盖 duplicate/missing/partial/content-addressed。
  - `process_executor.py`：worker envelope 身份/基数/顺序校验 fail-closed（`test_process_executor_conformance.py`）；shard 产物由
    `build_shard_result` 从已验证数据落库（digest 自算），不信任外部摘要。
- 缺口清单（三类 shard 故障各自现状）：
  1. 缺失 shard：**已闭环**（`MissingShardError`，`test_merge_rejects_missing_shard`）。
  2. 重复 shard：**已闭环**（`DuplicateShardError`，`test_merge_rejects_duplicate_shard`；expected 重复分支无独立注入测试）。
  3. 损坏 shard（内容/身份校验失败）：**缺口**。`ShardPlan.verify()` 只覆盖 plan 身份；`merge_shards` **盲信**
     `ShardResult.content_sha256/artifact_ref`，从不取回产物做内容校验——shard 产物被篡改/与声明的 digest 不符（或产物缺失）时，
     merge 照常产出 MergedRun 并把损坏 digest 折进合并摘要。无任何测试覆盖「merge 层内容校验失败」。
- 结论：按让步顺序（fail-closed 完整 > 语义正确 > 写得快），任务 1 只补「损坏 shard 内容校验」缺口，
  并顺带补强三类故障的注入测试矩阵（每类 ≥1 条），不重复实现缺失/重复语义。

## 任务 1 计划（落点）
- `orchestration.py` 最小改动：
  - 新增 `CorruptShardError(OrchestrationError)`（与 Missing/Duplicate/Incomplete 同族，明确错误类型）。
  - `merge_shards(expected_shards, results, *, artifact_store: ArtifactStore | None = None)`：可选提供 store 时，
    在 coverage 检查（缺失/重复优先，保持既有错误优先级）之后、status 检查之前，逐 shard 做**内容校验**：
    `store.get(result.content_sha256)` 取回字节并重算 `content_sha256(payload)`，与声明的 `content_sha256` 不符或取回失败 → `CorruptShardError`。
    不传 store 保持既有行为（向后兼容，既有 2 参调用不变）。
  - 不破坏 `test_shard_plan_identity.py` 既有身份语义；不动 `__init__.py`（不在白名单）。
- 测试（`packages/arena-hero-bench/tests/test_shard_merge_fail_closed.py` 新增）：
  - 损坏内容：篡改 store（get 返回与 digest 不符的字节）→ CorruptShardError（红→绿证据）。
  - 损坏缺失产物：store 取回失败（KeyError/MissingObjectError 语义）→ CorruptShardError。
  - 重复：expected 含重复 shard id → DuplicateShardError（补注入）；重复结果 → 已有测试。
  - 缺失：missing=shard-b → 已有测试；补 unexpected 分支。
  - 正路径：带 store 的 content-addressed merge 摘要不变（回归）。
- 验收：bench pytest ≥336 全绿、skipped 0；全仓 ≥837 全绿、skipped 0；ruff/ty/diff-check 全过；一功能一 commit，不 push。

## 收口（2026-08-12）
（任务 1 完成后补记：commit sha、校验输出。）
