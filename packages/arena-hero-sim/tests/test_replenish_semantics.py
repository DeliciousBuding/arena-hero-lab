"""补给语义钉死测试：World 补给与官方规则逐条对齐。

官方依据（map-and-vision.md §Resource quotas / §Consumption and replenishment，
world-and-ticks.md §Resolution order 13）：
- 配额 x = max(2, floor(16*8/(8+ring)))，ring 由 chunk 坐标的 axis 和决定；
- 每第 4 个 resolved tick，只补本周期有消耗（affected）chunk 的缺口，
  永不超过 x；未收割点不动；缺口不累积（未补齐的不滚入下周期）；
- 替代位置：可通行、非障碍、在 chunk backbone 通道之外、结算后不被
  Core 占据、可位于 Unit / 地面 Beacon 下；
- 确定性可重放：同世界 / 同 tick / 同 chunk 状态 / 同缺口 → 同位置。

本文件用 state-seed 注入（custom layout）构造确定性小世界，把每条
官方条款钉死在可复现的行为上。
"""

from __future__ import annotations

from arena_hero_sim.ffa.config import CHUNK_SIZE, resource_quota
from arena_hero_sim.ffa.world import World

# chunk (0,0) 的十字主干通道格（本地 x==0 整列 + y==0 整行）
_BACKBONE_CELLS = {(x, y) for x in range(CHUNK_SIZE) for y in range(CHUNK_SIZE) if x == 0 or y == 0}


def _blocked_chunk_obstacles(open_cells: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """把 chunk (0,0) 除 open_cells 与主干通道外全部填成障碍。

    主干通道保持 EMPTY（对应官方"backbone 是永久通行地形"），
    因此补给候选池 = open_cells ∪ 主干通道格；落点约束由补给逻辑
    自身过滤，测试断言最终落点集。
    """
    return [
        (x, y)
        for x in range(CHUNK_SIZE)
        for y in range(CHUNK_SIZE)
        if (x, y) not in open_cells and (x, y) not in _BACKBONE_CELLS
    ]


def _chunk_resources(world: World, cx: int = 0, cy: int = 0) -> set[tuple[int, int]]:
    x0, y0 = cx * CHUNK_SIZE, cy * CHUNK_SIZE
    return {
        (x, y)
        for x, y in world.resources
        if x0 <= x < x0 + CHUNK_SIZE and y0 <= y < y0 + CHUNK_SIZE
    }


# ----------------------------------------------------------------------
# 配额公式（官方 §Resource quotas）
# ----------------------------------------------------------------------
def test_resource_quota_matches_official_formula() -> None:
    def official_quota(cx: int, cy: int) -> int:
        def axis(c: int) -> int:
            return c if c >= 0 else -c - 1

        ring = axis(cx) + axis(cy)
        return max(2, (16 * 8) // (8 + ring))

    for cx in range(-6, 7):
        for cy in range(-6, 7):
            assert resource_quota(cx, cy) == official_quota(cx, cy)

    # ring 0 = 原点四周四个 chunk；远离原点配额下降且不低于 2
    assert resource_quota(0, 0) == 16
    assert resource_quota(0, -1) == 16
    assert resource_quota(-1, -1) == 16
    assert resource_quota(1, 0) == 14
    assert resource_quota(1, 1) == 12
    assert resource_quota(5, 5) == 7
    assert resource_quota(60, 60) == 2  # floor(128/128)=1 → max(2, 1)


# ----------------------------------------------------------------------
# 触发与补回数量（官方 §Consumption and replenishment / world-and-ticks §13）
# ----------------------------------------------------------------------
def _full_ring0_world(seed: int = 7, size: int = 64) -> World:
    """chunk (0,0) 初始 16 点（配额整满）、其余为空的自定义世界。"""
    cells = [(1 + i, 5) for i in range(16)]
    assert all(x % CHUNK_SIZE != 0 and y % CHUNK_SIZE != 0 for x, y in cells)
    return World(size=size, seed=seed, obstacles=[], resource_cells=cells)


def test_replenish_restores_quota_exactly_and_never_above() -> None:
    world = _full_ring0_world()
    initial = set(world.resources)
    for cell in [(1, 5), (2, 5), (3, 5)]:
        assert world.consume_resource(*cell)

    world.replenish_if_due(4, lambda x, y: False)

    chunk = _chunk_resources(world)
    assert len(chunk) == resource_quota(0, 0) == 16
    # 未收割的 13 点原地不动
    untouched = initial - {(1, 5), (2, 5), (3, 5)}
    assert untouched <= chunk
    # 新补的 3 点全部落在 chunk 内且非主干通道
    for x, y in chunk - untouched:
        assert 0 <= x < CHUNK_SIZE and 0 <= y < CHUNK_SIZE
        assert x % CHUNK_SIZE != 0 and y % CHUNK_SIZE != 0


def test_replenish_quota_uses_ring_formula_for_far_chunk() -> None:
    # chunk (1,1)：ring=2 → 配额 12；size=128 保证该 chunk 全域在界内
    world = World(
        size=128,
        seed=9,
        obstacles=[],
        resource_cells=[(33 + i, 37) for i in range(12)],
    )
    assert resource_quota(1, 1) == 12
    assert world.consume_resource(33, 37)
    assert world.consume_resource(34, 37)

    world.replenish_if_due(4, lambda x, y: False)

    chunk = _chunk_resources(world, 1, 1)
    assert len(chunk) == 12
    for x, y in chunk:
        assert 32 <= x < 64 and 32 <= y < 64
        assert x % CHUNK_SIZE != 0 and y % CHUNK_SIZE != 0


def test_replenish_only_on_every_fourth_resolved_tick() -> None:
    world = _full_ring0_world()
    assert world.consume_resource(1, 5)

    world.replenish_if_due(3, lambda x, y: False)
    assert len(_chunk_resources(world)) == 15
    assert world.dirty_chunks == {(0, 0)}

    world.replenish_if_due(4, lambda x, y: False)
    assert len(_chunk_resources(world)) == 16
    assert world.dirty_chunks == set()


def test_consumption_on_replenishment_tick_is_removed_then_filled() -> None:
    # 官方：补给 tick 上当轮消耗先移除，随后同 tick 补给补齐缺口
    world = _full_ring0_world()
    assert world.consume_resource(1, 5)
    world.replenish_if_due(4, lambda x, y: False)
    assert len(_chunk_resources(world)) == 16


def test_replenish_only_touches_dirty_chunks() -> None:
    world = _full_ring0_world()
    # chunk (-1,-1) 有 1 点但从未消耗 → 不是 affected chunk，不补
    assert (-10, -10) not in world.resources
    world.resources.add((-10, -10))
    assert world.consume_resource(1, 5)

    world.replenish_if_due(4, lambda x, y: False)

    assert (-10, -10) in world.resources
    assert len(_chunk_resources(world, -1, -1)) == 1  # 未按配额 16 补齐
    assert len(_chunk_resources(world)) == 16


def test_missing_slots_do_not_accumulate() -> None:
    # chunk (0,0) 只剩两个候选格：一次补给最多补 2 点，缺口 14。
    # 下个补给周期（无新消耗）不得再补——缺口不累积、不滚入下周期。
    # 候选格由引擎同源 PRNG（f"{seed}:0:0:4"）序列确定性推出：
    # seed=13 前两个非 backbone 落点 = (30,20),(14,8)，attempt 1、2（< cap）。
    open_cells = {(30, 20), (14, 8)}
    world = World(
        size=64,
        seed=13,
        obstacles=_blocked_chunk_obstacles(open_cells),
        resource_cells=[(0, 5)],
    )
    assert world.consume_resource(0, 5)

    world.replenish_if_due(4, lambda x, y: False)
    assert _chunk_resources(world) == open_cells  # 只有 2 个候选被填

    world.replenish_if_due(8, lambda x, y: False)
    assert _chunk_resources(world) == open_cells  # 下周期不再补
    assert world.dirty_chunks == set()


# ----------------------------------------------------------------------
# 替代位置约束（官方：可通行 / 非障碍 / 非 backbone / 无 Core / 可在 Unit 下）
# ----------------------------------------------------------------------
def test_replenish_positions_are_passable_non_obstacle_non_backbone() -> None:
    # 候选格 = 引擎同源 PRNG（f"{seed}:0:0:4"）前 4 个非 backbone 落点
    # （seed=17：attempt 1-4，确定性）。补给必须正好落满这 4 格——
    # 不落障碍、不落 backbone（backbone 虽有 63 个 EMPTY 格也不得选）。
    open_cells = {(16, 2), (22, 26), (27, 8), (19, 26)}
    world = World(
        size=64,
        seed=17,
        obstacles=_blocked_chunk_obstacles(open_cells),
        resource_cells=[(0, 5)],
    )
    assert world.consume_resource(0, 5)

    world.replenish_if_due(4, lambda x, y: False)

    chunk = _chunk_resources(world)
    assert chunk == open_cells  # 补给只落在候选格，不落障碍/backbone
    for x, y in chunk:
        assert not world.is_obstacle(x, y)
        assert x % CHUNK_SIZE != 0 and y % CHUNK_SIZE != 0


def test_replenish_rejects_backbone_only_candidates() -> None:
    # 全 chunk 除主干通道外都是障碍：修复前补给会落满 backbone，
    # 修复后（官方：替代位置须在 backbone 之外）一个点都不放。
    world = World(
        size=64,
        seed=19,
        obstacles=_blocked_chunk_obstacles(set()),
        resource_cells=[(5, 5)],
    )
    assert world.consume_resource(5, 5)

    world.replenish_if_due(4, lambda x, y: False)

    assert _chunk_resources(world) == set()
    assert all((x, y) not in world.resources for x, y in _BACKBONE_CELLS)


def test_replenish_skips_core_occupied_cells() -> None:
    # 候选格 = 引擎同源 PRNG（f"{seed}:0:0:4"）前两个非 backbone 落点
    # （seed=23：attempt 1、2）。Core 占住第 1 个候选 → 补给必须跳过
    # 它、落在第 2 个候选；其余全部是障碍/backbone，无从旁落。
    first_draw, second_draw = (20, 19), (4, 16)
    open_cells = {first_draw, second_draw}
    world = World(
        size=64,
        seed=23,
        obstacles=_blocked_chunk_obstacles(open_cells),
        resource_cells=[(0, 5)],
    )
    assert world.consume_resource(0, 5)

    world.replenish_if_due(4, lambda x, y: (x, y) == first_draw)

    assert _chunk_resources(world) == {second_draw}
    assert first_draw not in world.resources


def test_replenish_may_place_under_units() -> None:
    # 补给层不感知 Unit/Beacon（只回调 Core 占用）：无 Core 时两个
    # 候选格全部填上（官方：替代位置可位于 Unit / 地面 Beacon 下）。
    # 候选格 = 引擎同源 PRNG（f"{seed}:0:0:4"）前两个非 backbone 落点
    # （seed=29：attempt 1、2）。
    open_cells = {(5, 4), (5, 3)}
    world = World(
        size=64,
        seed=29,
        obstacles=_blocked_chunk_obstacles(open_cells),
        resource_cells=[(0, 5)],
    )
    assert world.consume_resource(0, 5)

    world.replenish_if_due(4, lambda x, y: False)  # 无 Core 占用

    assert _chunk_resources(world) == open_cells


# ----------------------------------------------------------------------
# 确定性可重放（官方：同世界/同 tick/同 chunk 状态/同缺口 → 同位置）
# ----------------------------------------------------------------------
def test_replenish_is_deterministic_same_seed_same_state_same_tick() -> None:
    def run_once(tick: int) -> set[tuple[int, int]]:
        world = _full_ring0_world(seed=31)
        for cell in [(1, 5), (2, 5), (3, 5)]:
            assert world.consume_resource(*cell)
        world.replenish_if_due(tick, lambda x, y: False)
        return set(world.resources)

    assert run_once(4) == run_once(4)
    assert run_once(8) == run_once(8)
    assert len(run_once(4)) == 16  # 满配额，非平凡结果


# ----------------------------------------------------------------------
# backbone 几何定义（lab 模型：每 chunk 本地 x==0 整列 + y==0 整行）
# ----------------------------------------------------------------------
def test_backbone_cell_definition_includes_negative_coordinates() -> None:
    world = World(size=64, seed=37, obstacles=[], resource_cells=[])
    assert world._is_backbone(0, 5)
    assert world._is_backbone(5, 0)
    assert world._is_backbone(32, 7)
    assert world._is_backbone(-32, 7)
    assert world._is_backbone(0, 0)
    assert not world._is_backbone(5, 5)
    assert not world._is_backbone(31, 31)
    assert not world._is_backbone(-1, -1)
