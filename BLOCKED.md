# BLOCKED.md — W16 P3-6

- 真跑接入：bench venv 未安装 `arena_hero` SDK，本次冒烟用故障注入模拟执行路径（fixture 镜像 SDK runner CLI 契约），
  真实 SDK 对拍另用 SDK 自带 venv 手动完成。把 SDK 作为 bench 依赖（或在 fixture 内硬编码 SDK venv 路径）属新增依赖/
  机器相关路径，需拍板后实施。
- 顺手活（未做，待拍板）：
  1. `process_executor` 增加裸命令执行缝 / 信封透传 contestant stdout（当前信封是模拟域协议，stdout 不透传）。
  2. `ContestantRegistry` 与 adapter 的接线（注册表 → spec 的直接查询入口）。
  3. SDK 侧 entry point runner 与 bench worker 的官方合并（减少双层协议转换）。
- 其余：无。
