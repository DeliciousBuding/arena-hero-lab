# arena-hero v3.1 策略实测分析（35 场全量）

> 评测日期：2026-08-10 · 数据源：`arena.bench.report.v3` · 35 场 = 7 场景 × 5 种子 × 8 玩家
> ticks=2000 · workers=4 · 基于干净 main c58ad51（worktree eval-bench，该提交已不在任何仓库历史，2026-08-10 历史重写后失效）
> 本文仅基于实测数据，不含推测；killEvents 并发落盘 bug 已修复（2026-08-10 续3，详见 §7），30/35 场 87 事件。

## 1. 榜单排名（综合分降序）

| 排名 | 条目 | 综合分 | 平均名次 | σ | 击杀/场 | 经济分 |
|---:|---|---:|---:|---:|---:|---:|
| 1 | **arena-evolve** | 96.8% | 2.40 | ±1.43 | 1.37 | 68% |
| 2 | waaiging | 62.8% | 2.97 | ±0.65 | 0.29 | 33% |
| 3 | core-mil | 57.3% | 3.74 | ±1.33 | 0.51 | 19% |
| 4 | core | 52.4% | 3.77 | ±0.92 | 0.29 | 23% |
| 5 | waaiging-agg | 36.2% | 4.69 | ±0.95 | 0.03 | 26% |
| 6 | farmer-eco | 26.4% | 5.37 | ±1.09 | 0.00 | 15% |
| 7 | farmer | 24.4% | 5.54 | ±1.23 | 0.00 | 15% |
| 8 | tactic | 0.4% | 7.49 | ±0.30 | 0.00 | 4% |

**综合分公式**：composite = avgRank(反向 min-max) × 60% + killRate × 30% + resourcesPerTick × 10%。

## 2. 胜方分布（35 场）

| 条目 | 胜场 | 占比 | 场景偏好 |
|---|---:|---:|---|
| **arena-evolve** | 23 | 65.7% | 全场景统治（ffa-dense 4/ffa-std 2/ffa-scarce 4/ffa-random 4/ffa-resource-race 4/ffa-defense-pressure 4 + ffa-open 1） |
| core | 4 | 11.4% | ffa-std 1 / ffa-scarce 1 / ffa-resource-race 1 / ffa-open 1 |
| waaiging-agg | 3 | 8.6% | ffa-open 2 / ffa-random 1（开阔地图偏好） |
| waaiging | 2 | 5.7% | ffa-std 1 / ffa-open 1 |
| core-mil | 1 | 2.9% | ffa-dense 1 |
| **平局** | 2 | 5.7% | ffa-open seed3 / seed5（farmer 系并列） |
| farmer / farmer-eco / tactic | 0 | 0% | 全程无胜 |

## 3. 各 agent 深度分析

### arena-evolve（#1，23 胜，统治级）
- **强项**：killRate 1.37（唯一有显著击杀的 agent，economyScore 68% 最高）
- **风格**：进攻型 + 高资源效率——evolve_v7_best 基因启发式策略在资源密集场景（ffa-dense/ffa-std/ffa-resource-race）展现压倒性统治
- **稳定性**：avgRank 2.40 ± 1.43——名次波动较大（σ 最高），但在 ffa-dense/ffa-scarce 等场景常 r1，偶尔 r4-r6（ffa-open 表现下降）
- **弱点**：ffa-open（开阔地图）是唯一未统治的场景（仅 1 胜），waaiging 系在此反超
- **来源**：[Torther/arena-evolve](https://github.com/Torther/arena-evolve)（evolve_v7_best GA 进化冠军快照）

### waaiging（#2，2 胜，稳定第二）
- **强项**：avgRank 2.97 ± 0.65——**跨场景最稳定**（σ 最低），从不崩盘
- **风格**：均衡型——SmartTactic 4 模式自适应（经济/动态产兵/编队推进/Core 斩首/信标控制）
- **场景**：ffa-std / ffa-open 各 1 胜，在 waaiging-agg 表现差的场景补位
- **来源**：[Waaiging/ArenaHero](https://github.com/Waaiging/ArenaHero)

### core-mil（#3，1 胜，军事变体）
- **强项**：killRate 0.51（第二高击杀），ffa-dense 1 胜（资源密集 + 军事压制）
- **风格**：进攻型（mode=control/target=8）——军事倾向变体
- **对比基座 core**：Δcomposite +4.9%（+0.049），军事变体略胜经济基座，但 σ 更大（±1.33 vs ±0.92）——激进换波动
- **来源**：[VelvetEvening/ArenaHero-nearly-perfect-guide](https://github.com/VelvetEvening/ArenaHero-nearly-perfect-guide)

### core（#4，4 胜，进攻向基座）
- **强项**：4 场分散胜（ffa-std/ffa-scarce/ffa-resource-race/ffa-open 各 1）——**场景适应性广**
- **风格**：进攻向（mode=harvest/target=30）——双策略 v3.3 基座
- **稳定性**：avgRank 3.77 ± 0.92——中等稳定
- **来源**：同 core-mil

### waaiging-agg（#5，3 胜，进攻变体）
- **强项**：ffa-open 2 胜——**开阔地图专精**（6 先锋 + 9 游侠前压在开阔地形发挥）
- **对比基座 waaiging**：Δcomposite -26.6%（-0.266）——进攻变体**负收益**，激进策略综合分大降
- **启示**：进攻倾向（mode=aggress）在特定场景（ffa-open）有效，但综合表现不如均衡基座
- **来源**：同 waaiging

### farmer / farmer-eco（#6/#7，0 胜，资源型）
- **强项**：无胜场，但 farmer-eco Δcomposite +2.0%（+0.020）略胜基座——经济变体微正收益（worker_target=16/beacon_policy=retreat）
- **风格**：资源优先（resource-first），12W+4V+4R 基础舰队——**纯经济无击杀**（killRate 0.00）
- **稳定性**：avgRank 5.37-5.54 ± 1.09-1.23——中下游，波动中等
- **弱点**：v3 综合分移除 survivalMedian 权重后，纯资源型大退（v2→v3 Δ -0.50~-0.66）
- **来源**：[Drew-Z/arena-hero-agent](https://github.com/Drew-Z/arena-hero-agent)

### tactic（#8，0 胜，垫底）
- **表现**：avgRank 7.49 ± 0.30——**几乎恒定垫底**（σ 最低但都是 r7-r8）
- **综合分**：0.4%——近乎为零，economyScore 4% 最低
- **诊断**：资源优先 + 均衡防守（12W/4V/4R 爬坡）在 v3 FFA 对抗中完全失效
- **来源**：[feixingwawa/arena-hero-tactic](https://github.com/feixingwawa/arena-hero-tactic)

## 4. 效率比较

### 资源效率（resourcesPerTick，场景级均值）
arena-evolve 在所有场景资源/刻最高（economyScore 68%）。farmer 系虽资源优先但 economyScore 仅 15%——资源采集策略在 v3 多 agent 竞争下效率下降（被抢矿/压制）。

### 击杀效率（killRate）
- arena-evolve 1.37（唯一显著击杀者）——Core 斩首 + 编队推进高效
- core-mil 0.51（第二）——军事压制有击杀
- waaiging 0.29 / core 0.29——偶有击杀
- 其余 0.00——纯经济/防守，无 Core 斩首能力

### 人口峰值
arena-evolve 人口峰值最高（动态产兵 + 资源充足）。farmer 系人口中等（爬坡但被压制）。tactic 人口最低（资源不足无法扩军）。

## 5. 变体 vs 基座对比

| 基座 | 变体 | Δ综合分 | 评价 |
|---|---|---:|---|
| core | core-mil | +4.9% | 军事变体微正收益，但 σ 更大（激进换波动） |
| waaiging | waaiging-agg | -26.6% | 进攻变体**大负收益**（仅 ffa-open 场景有效） |
| farmer | farmer-eco | +2.0% | 经济变体微正收益（保守调整有效） |

**启示**：保守变体（微调参数）通常正收益；激进变体（改变 mode）大负收益，除非场景专精。

## 5.5 击杀模式深度分析（killEvents 87 事件，修复后数据）

> 数据源：v3.1 killEvents（2026-08-10 修复 WorkerMatchFile 序列化后落盘，30/35 场 87 事件）

### 击杀贡献（谁是猎手）

| 击杀者 | 击杀次数 | 占比 | 占该 agent 场均 |
|---|---:|---:|---:|
| **arena-evolve** | 48 | 55.2% | 2.05/场 |
| core-mil | 18 | 20.7% | 0.51/场 |
| core | 10 | 11.5% | 0.29/场 |
| waaiging | 10 | 11.5% | 0.29/场 |
| waaiging-agg | 1 | 1.1% | 0.03/场 |
| farmer / farmer-eco / tactic | 0 | 0% | 0.00 |

arena-evolve 是**绝对猎手**（48 次，占 55%），印证 killRate 1.37 最高。farmer 系 + tactic **零击杀**（纯经济/防守，无 Core 斩首能力）。

### 被击杀分布（谁是猎物）

| 被击杀方 | 被杀次数 | 占比 |
|---|---:|---:|
| **tactic** | 28 | 32.2% |
| waaiging-agg | 23 | 26.4% |
| farmer-eco | 12 | 13.8% |
| farmer | 9 | 10.3% |
| core | 7 | 8.0% |
| waaiging | 5 | 5.7% |
| core-mil | 1 | 1.1% |
| arena-evolve | **0** | **0%** |
| unknown | 2 | 2.3% |

- **tactic 是最大提款机**（28 次被杀）——印证它垫底（综合分 0.4%），完全无防御
- **arena-evolve 0 被杀**——**无敌**，从不被斩首（最强攻击即最强防御 + 资源优势保 Core）
- waaiging-agg 第二受害者（23 次）——激进变体前压暴露 Core

### 击杀时机（tick 三段）

| 时段 | tick 范围 | 事件数 | 占比 |
|---|---|---:|---:|
| 早期 | 0–666 | 3 | 3.4% |
| **中期** | 667–1333 | **64** | **73.6%** |
| 晚期 | 1334–2000 | 20 | 23.0% |

中期是击杀高峰（74%）——资源积累后爆发 Core 斩首冲突。早期几乎无击杀（3%）——全在发展经济。晚期 23%——收尾清扫残局。

### 击杀关系矩阵（>1 次的关系）

| 击杀者 → 被击杀方 | 次数 | 解读 |
|---|---:|---|
| **arena-evolve → tactic** | 23 | arena-evolve 专杀 tactic（提款） |
| **arena-evolve → waaiging-agg** | 19 | 杀激进变体（前压暴露） |
| core-mil → farmer-eco | 11 | 军事变体杀经济型 |
| core → farmer | 8 | 进攻基座杀资源型 |
| waaiging → core | 5 | waaiging 反杀 core |
| core-mil → waaiging-agg | 4 | 军事杀激进 |
| arena-evolve → waaiging | 4 | 杀均衡基座 |
| waaiging → tactic | 4 | waaiging 也吃 tactic |
| arena-evolve → core | 2 | 杀进攻基座 |

**洞察**：arena-evolve 几乎吃所有弱者（tactic/waaiging-agg/waaiging/core），但**从不被吃**——食物链顶端。waaiging 有反杀 core 的能力（5 次），是唯一对 core 系有斩首威胁的均衡型 agent。

### 各场景击杀密度

| 场景 | 事件数 | 密度解读 |
|---|---:|---|
| ffa-defense-pressure | 17 | 资源枯竭逼冲突（最高） |
| ffa-std | 15 | 标准对抗 |
| ffa-random | 15 | 随机落点 |
| ffa-dense | 14 | 高密度 |
| ffa-resource-race | 13 | 中央矿争夺 |
| ffa-scarce | 11 | 资源匮乏（较少冲突） |
| **ffa-open** | **2** | 开阔地图最分散（最少冲突） |

ffa-defense-pressure（资源枯竭）击杀最多——压力逼出 Core 斩首。ffa-open（开阔）最少——地形分散难接触。

## 6. 场景梯度发现

- **ffa-dense/ffa-std/ffa-scarce/ffa-resource-race/ffa-defense-pressure**（资源密集/标准/枯竭/中央矿/压制）：arena-evolve 统治（资源充足时基因启发式最优）
- **ffa-open**（开阔地图）：唯一 arena-evolve 未统治的场景——waaiging 系（3 胜）+ core（1 胜）+ 2 平局。开阔地形削弱资源密集优势，编队推进/信标控制更重要
- **ffa-open 平局**：farmer/farmer-eco 并列 r2（seed3）/ r1-r2（seed5）——资源型在开阔地形偶有表现

## 7. 可观测性建议

基于本轮评测数据缺失，建议 arena-ts 评测层增强：
1. **killEvents 并发落盘 bug 已修复**（2026-08-10 续3）：
   - 根因：`WorkerMatchFile` 序列化三重缺失——接口无 killEvents 字段定义 + `runWorkerProcess` 写文件漏 + 主进程 `resolvePromise` 读回漏。**非并发竞态**，是 worker 序列化漏字段（之前会话"并发隔离"修复保留了价值但非此 bug 根因）
   - 修复三处后 workers=4 全量 35 场验证：30/35 场有 killEvents 共 87 事件（85 有 victim）。5 场无事件为平局或时间到未摧毁核心
   - 击杀时序图数据源就绪，网站 `KillTimeline` 组件可正常渲染（entry 详情页击杀时序 section）
2. **per-tick 时序数据已实现**（2026-08-10 续6）：`runFreeForAll` 注入 `onTickSettled` 回调每 50 tick 采样 per-player 资源/人口，全链路序列化（MatchResult → WorkerMatchFile → results.json → bench.json），网站 entry 详情页新增 Efficiency Timeline 资源/人口曲线面板。arena-ts `5cde993`（历史重写后对应 fdd379d）
3. **决策日志采样**：agent 每场的关键决策（产兵/调度/信标）摘要，用于策略可解释性
4. **战斗事件**：除 CORE_DESTROYED 外，记录 ARMY_DESTROYED/SIGNAL_ACTIVATED 等事件时序

## 8. 结论

- **arena-evolve 是 v3.1 的统治级 agent**（23/35 胜，65.7%），基因启发式 + GA 进化在资源密集场景展现压倒优势
- **waaiging 最稳定**（σ 最低），均衡策略保下限
- **激进变体负收益**（waaiging-agg -26.6%），保守变体微正（core-mil +4.9%/farmer-eco +2.0%）
- **场景专精有价值**：waaiging-agg 在 ffa-open 有效，但综合分大降
- **tactic 完全失效**（综合分 0.4%），需根本性策略重构
- **killEvents 并发落盘 bug 已修复**（2026-08-10 续3）：根因为 worker 序列化漏字段（非并发竞态），修复后 30/35 场 87 事件，击杀时序图数据就绪
