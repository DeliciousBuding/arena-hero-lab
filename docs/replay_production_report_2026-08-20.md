# 生产日志停滞重放报告（2026-08-20）

对象：生产服务器四租户 t1-t4 `ticks.jsonl`（部署边界 tick>=136375 后的 `tick_state`，agent 0.1.42）。
工具：lab fa031bf state-seed 重放 harness（`scripts/replay_state_seed.py` / `bench_state_seeds.py`），
本轮修复其真实日志解析并补 6 个回归测试（`test_state_seed_parser.py` 7→13 全绿）。
配置：`--ticks 200 --stall-ticks 60`；原始数据只驻留 wave 工作区，本报告仅含统计数字。

## 1. 样本与批量重放统计

| 租户 | 匹配/抽样 | tick 范围 | bench（8 记录） | 备注 |
|---|---|---|---|---|
| t1 | 64/8 | 136375..136438 | 5 stall [0,1,2,4,6] | pop2/res0；生产 stuckSinceTick=136402 |
| t2 | 66/8 | 136375..136440 | 5 stall [0,1,4,6,7] | pop2/res0；stuckSinceTick=136363 |
| t3 | 66/8 | 136375..136440 | 8 stall 全部 | pop2/res1；worker 距核 111/57→61/24 |
| t4 | 66/8 | 136375..136440 | 7 stall [0,2,3,4,5,6,7] | pop2/res3；barren 激活（rec3 起） |

harness 修复：`core.pos`/`beacon.pos` 生产形状；`resourceCells`/`terrainObstacles` int 计数无坐标显式降级；无坐标时核周合成 12 格确定性资源圈（转录打 APPROXIMATION），否则重放结构性全 stall。

## 2. stall 归因（最严重 3 例，单条重放 150 tick）

| 案例 | 现象 | 归因 | 0.1.42 对照 |
|---|---|---|---|
| t3 rec0（0-6 同型） | 150 tick 零采集零存矿 | worker B 距核 111 格冻结 151 tick；A 距核 57 格被召回走回。stranded-recall 的 `next_step_toward`（BFS 预算 16384≈64²）对 >~65 Chebyshev 返回 None，recall 静默跳过 | **未修**：实测 dist=64 有路、70 无路；生产 t3 同型 |
| t1 rec2 | 72 tick 前存矿 14 次，后 78+ tick 零采集 | 资源圈耗尽后模拟器补给随机散布到 32×32 chunk；4 worker 核周 ~25×18 盒内游走未命中；refill 记忆假设「矿格原地补给」而模拟器是「随机新格」 | **未修**（机制不匹配）：饿模式 200 tick 才触发 |
| t4 rec4 | 84→149 间隙（65 tick）后恢复 | ①找矿间隙 ~51 tick（同上失配）；②载货绕路：135 tick 采到矿后载货 14 tick 绕 ~20 格到 9 格外核心 | **未修**：deposit-stall unblock 只治「核心格被占」旧案 |

## 3. 已修 / 未修小结

- 已修生效：核心格被占 500-tick 存款停滞案不再复现；≤64 格 worker 召回正常（A、近距 worker 均回核采存）。
- 未修/未覆盖：①远距滞留 worker 永久冻结（recall 寻路 64 格天花板，生产 t3 实证）；
  ②补给随机散布后重新找矿失效；③载货 worker 绕路延迟存款。
- 引擎侧观察（仅报告不修）：`World.replenish_if_due` 补给到脏 chunk 随机新位置，与 agent
  「原地 refill」假设冲突——找矿间隙的系统性来源；请 captain 核对官方补给语义后对齐一侧。

## 4. 给下一轮的建议

1. agent 侧：recall 寻路放宽（radius/budget 或分桶渐进召回）覆盖 >65 格 worker，生产 t3 直接受益。
2. harness 侧：合成资源圈改为「chunk 配额 + 补给语义」小世界生成，减少找矿间隙假阳性。
3. 观测侧：tick_state 若并入可见资源格坐标（哪怕哈希），重放保真度大幅上升。
4. 复验：`replay_state_seed.py --jsonl <样本> --record-index N --ticks 150` 可复跑三案例。
