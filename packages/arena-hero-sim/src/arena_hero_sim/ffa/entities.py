"""实体：Core、Unit、玩家状态。uid 用 seed 派生的伪随机稀疏整数（模拟
官方 UUID.int 分布，仲裁按 uid 排序时不被创建顺序偏袒）。"""

import random as _random

from .config import CORE_HP, CORE_SHIELD, CORE_START_RESOURCES, UNIT_STATS

# UID 生成（2026-08-07 架构评审 P0#3）：官方 UID 是不可枚举的 UUID，仲裁按
# raw UUID 排序。旧的全局递增整数（1,2,3...）让 slot 0 的实体永远拿最小
# UID——容量竞争/Beacon 拾取/战斗目标（全部按 uid 排序/取 min）系统性偏袒
# 先创建的玩家，且策略的 uid%8/%16 扇区分配过拟合递增序列。改为 seed 派生的
# 伪随机稀疏大整数（模拟 UUID.int 分布）：仲裁均匀化、%8/%16 均匀。
_UID_RNG = _random.Random(0)
_UID_SEEN = set()


def reset_uid_rng(seed):
    """每局用世界 seed 重置 UID 流（跨进程/跨运行确定性）。"""
    global _UID_RNG, _UID_SEEN
    _UID_RNG = _random.Random(f"uid:{seed}")
    _UID_SEEN = set()


def next_id():
    # 64~120 bit 稀疏整数：碰撞概率 ~2^-56（单局实体数远小于此），仍兜底去重
    while True:
        uid = _UID_RNG.randint(1 << 64, 1 << 120)
        if uid not in _UID_SEEN:
            _UID_SEEN.add(uid)
            return uid


class Core:
    __slots__ = (
        "carries_beacon",
        "hp",
        "just_respawned",
        "migration",
        "owner",
        "pos",
        "resources",
        "shield",
        "uid",
    )

    def __init__(self, owner, pos):
        self.uid = next_id()
        self.owner = owner
        self.pos = pos
        self.hp = CORE_HP
        self.shield = CORE_SHIELD
        self.resources = CORE_START_RESOURCES
        self.migration = None  # (direction_str, progress)
        self.just_respawned = True  # 出生当 Tick 不可行动
        self.carries_beacon = False

    @property
    def is_migrating(self):
        return self.migration is not None


class Unit:
    __slots__ = (
        "cargo",
        "carries_beacon",
        "hp",
        "just_spawned",
        "owner",
        "pos",
        "uid",
        "utype",
    )

    def __init__(self, owner, utype, pos):
        self.uid = next_id()
        self.owner = owner
        self.utype = utype
        self.pos = pos
        self.hp = UNIT_STATS[utype]["hp"]
        self.cargo = 0
        self.carries_beacon = False
        self.just_spawned = True


class Player:
    """一个玩家（Agent）的完整状态。"""

    def __init__(self, player_id):
        self.player_id = player_id
        self.core = None
        self.units = {}  # uid -> Unit
        self.respawning = False  # 重生失败，等待下 Tick 重试
        self.respawn_count = 0
        self.alive_ticks = 0  # Core 实际存活（ACTIVE）的 tick 数（真实值）
        # 累计统计（用于适应度评估）
        self.stats = {
            "damage_dealt": 0,
            "units_lost": 0,
            "harvested": 0,
            "beacon_ticks": 0,
            # 资源成本账本（2026-08-07 架构评审 P0#11：线上成本
            # 必须进入选择压力，不能只靠终局存量）
            "heal_cost": 0,  # 单位 + Core 治疗消耗
            "repair_cost": 0,  # 修盾消耗
            "spawn_cost": 0,  # 生产消耗
            "overflow_destroyed": 0,  # 容量溢出销毁
            "resources_lost": 0,  # 战斗损失（掠夺+摧毁）
        }

    # ---- 派生量 ----
    @property
    def population(self):
        return len(self.units)

    @property
    def alive(self):
        return self.core is not None or self.respawning

    def get_unit(self, uid):
        return self.units.get(uid)
