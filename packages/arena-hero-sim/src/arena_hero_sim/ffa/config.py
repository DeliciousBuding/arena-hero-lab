"""Arena Hero 规则常量（对应当前 gameplay v0.14 的公开数值）。"""

# ---- 时间 ----
TICK_SECONDS = 15.0  # 正式世界命令窗口（模拟器不真实计时）
RESOURCE_REPLENISH_EVERY = 4  # 每 4 个已解析 Tick 补给资源

# ---- 地形 ----
EMPTY = 0
RESOURCE = 1
OBSTACLE = 2

CHUNK_SIZE = 32

# ---- Core ----
CORE_HP = 5
CORE_SHIELD = 5
CORE_SHIELD_CAP_BEACON = 10
CORE_VISION = 5
CORE_START_RESOURCES = 5
CORE_START_WORKERS = 1
CORE_STORAGE_MIN = 10
CORE_STORAGE_PER_UNIT = 5

# ---- Units ----
UNIT_STATS = {
    "WORKER": {"hp": 2, "vision": 3, "cost": 5},
    "VANGUARD": {"hp": 4, "vision": 4, "cost": 10},
    "RANGER": {"hp": 2, "vision": 5, "cost": 12},
}
WORKER = "WORKER"
VANGUARD = "VANGUARD"
RANGER = "RANGER"

# 攻击
VANGUARD_DAMAGE = 1
RANGER_DAMAGE = 1
RANGER_RANGE = 3

# ---- 视野 ----
VISION = {"CORE": CORE_VISION, "WORKER": 3, "VANGUARD": 4, "RANGER": 5}

# ---- 世界 ----
CELL_CAPACITY = 2  # 每格最多两个占用实体
SPAWN_MIN_DIST = 20  # 重生距离最近活 Core 的最小曼哈顿距离
SPAWN_MAX_DIST = 30  # 最大曼哈顿距离
CORE_MIGRATION_TICKS = 4  # Core 每格移动所需 Tick 数
BEACON_START = (0, 0)

# ---- 方向 ----
DIRECTIONS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def resource_quota(cx: int, cy: int) -> int:
    """chunk (cx,cy) 的资源配额：max(2, floor(16*8/(8+ring)))。"""

    def axis(c: int) -> int:
        return c if c >= 0 else -c - 1

    ring = axis(cx) + axis(cy)
    return max(2, (16 * 8) // (8 + ring))


def unit_cost(base_cost: int, population: int) -> int:
    """动态单位价格（rules v0.14，2026-08-06）：前 20 个存活单位基础价；
    第 21 个起每满 5 单位涨 30%。

    N = 生产前的存活单位数（Core 动作解析时）
    k = max(0, floor((N - 20) / 5) + 1)
    price = round_half_up(base × (13/10)^k)，精确分数只舍入一次

    表：Worker 5/7/8/11，Vanguard 10/13/17/22，Ranger 12/16/20/26。
    维护费机制已删除（v0.13 → v0.14）。
    """
    k = max(0, (population - 20) // 5 + 1)
    numerator = base_cost * 13**k
    denominator = 10**k
    # Keep the server's exact rational arithmetic. For positive values,
    # floor(x + 1/2) is round-half-up without any floating-point boundary.
    return (2 * numerator + denominator) // (2 * denominator)


def storage_capacity(population: int) -> int:
    """Core 资源容量：max(10, population*5)。"""
    return max(CORE_STORAGE_MIN, population * CORE_STORAGE_PER_UNIT)
