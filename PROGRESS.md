# PROGRESS — Wave 13 C: Lab P3-11 统计缺口（w13/p3-11）

## 理解的目标
Python research 包复刻 TS bench-stats.mts 配对统计：wilcoxon_signed_rank / cliff_delta 纯函数 + analysis.py 聚合，
产出与 TS 逐字段一致的 pValue / wPlus / cliffDelta / qValue / ci95（误差界 p/cliff 1e-9、q 1e-6）。

## 顺序
1. T0 基线 359 passed + TS 冒烟（已完成）
2. T1 statistics.py 加纯函数 + test_statistics.py 表驱动单测（SciPy 离线参考值）
3. T2 构造 matches fixture → node 现跑 golden stats.json → Python 独立实现逐字段对拍
4. T3 analysis.py 加配对聚合（复用 benjamini_hochberg）+ test_analysis.py 用例
5. 全量门禁：pytest ≥359/0 skip、ruff、ty、git diff --check，提交 + BLOCKED.md

## 最大风险
- TS normalCdf 是 Abramowitz-Stegun 近似（误差<7.5e-8），必须逐位复刻而非用精确正态 CDF，否则小 p 相对误差超 1e-9。
- mulberry32 需精确移植 32 位语义（>>> / imul 回绕），否则 ci95 全偏。
- match 读取顺序：TS 用 readdirSync（OS 目录序），Python 需同序，先实测确认再定 fixture 命名。

## 进展（2026-08-12）
- T1 完成：statistics.py 新增 wilcoxon_signed_rank / cliff_delta（stdlib only），空输入按 TS 语义返回 (1,0,0)（docstring 写明）。
- T2 完成：fixture 4 contestant × 10 场（含 tie、`-s\d+` 后缀、零差、bootstrap），TS 现跑 golden（stats.golden.json 已随 fixture 提交）。
- T3 完成：analysis.py 新增 paired_rank_comparisons（复用 benjamini_hochberg），既有 analyze_preregistered_paired_outcomes 未动、既有测试全绿。
- 门禁：pytest 379 passed / skipped 0；ruff、ty、git diff --check 全过。

## 对拍误差（相对误差，TS stats.golden.json vs Python，逐字段）
- wPlus / pValue / cliffDelta / meanRankDiff / qValue / ci95_lo / ci95_hi：全部 0.000e+00（逐位一致，远低于 1e-9；qValue 1e-6 界未用到）。

## 关键实现决策（为什么）
- normalCdf：TS 用 Abramowitz-Stegun 近似（误差<7.5e-8），Python 逐位复刻同一近似而非精确正态 CDF —— 否则小 p 相对误差超 1e-9。
- mulberry32：按 JS 32 位语义（>>> / imul 回绕 / ToInt32）精确移植，先与 node 输出对拍 4 组 seed × 8 值全部逐位一致。
- match 读取顺序：TS 用 readdirSync（OS 目录序）；实测本机 readdirSync == 文件名排序，Python 测试按文件名排序读取（跨平台确定），fixture 按排序创建。
- wPlus 参考值用 scipy.stats.rankdata（平均秩 tie），非 scipy.wilcoxon 的 min(W+,W-) statistic；p 参考用 scipy method="approx"，与实现仅差 CDF 近似（abs≈3e-8），单测用 abs=1e-6 并注明。
