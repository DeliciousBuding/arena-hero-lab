"""Observation：模拟器给策略的视野观察（对应正式世界 state 的可见性）。"""

from dataclasses import dataclass


@dataclass(slots=True)
class Observation:
    player_id: int
    tick: int
    core: dict | None
    units: list
    enemies: list  # 可见敌人单位
    enemy_cores: list  # 可见敌人 Core
    resources: set  # 可见自然资源格（不含 cargo 掉落堆）
    obstacles: set  # 本 Tick 可见障碍格
    beacon: dict  # {"position", "status", "carrier_id"?}
    population: int
    visible_cells: set = None  # 本 Tick 所有己方对象可见的格集合
    prev_events: list = None  # 上一 Tick 的解析事件（含 MOVE_BLOCKED 等）
    cargo_cells: set = None  # 可见 cargo 掉落堆（不参与配额，会消失）
    # ---- 官方 state 对齐字段（2026-08-07 架构评审 P0#6）----
    status: str = "ACTIVE"  # "ACTIVE" / "RESPAWNING"
    respawn_at_tick: int | None = None  # 重生等待时的下次尝试 tick
