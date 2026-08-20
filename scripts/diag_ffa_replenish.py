"""FFA 补给诊断：逐 tick 打印补给事件（chunk、缺口数、新位置）。

用途：核对引擎补给行为与官方规则（map-and-vision.md §Consumption and
replenishment / world-and-ticks.md §Resolution order 13）——
每第 4 个 resolved tick、只补有消耗 chunk 的缺口、替代位置可通行/
非障碍/在 backbone 通道之外、确定性可重放。

小世界（size=128，custom layout）：chunk (0,0) 放满配额 16、
chunk (1,0) 放满配额 14，其余为空。每 tick 模拟一次成功收割
（chunk (0,0) 每 tick、chunk (1,0) 每 2 tick 消耗 1 点），
逐 tick 打印脏 chunk 的配额/补给前数量/缺口/新落点，并标记
任何落在 backbone 或障碍上的违规落点。结尾用同一 seed 重放
一遍校验确定性：最终资源点集必须逐字节一致。

用法（arena-hero-lab 仓库根）：
    uv run python scripts/diag_ffa_replenish.py --seed 11 --ticks 12
"""

from __future__ import annotations

import argparse
import sys

from arena_hero_sim.ffa.config import CHUNK_SIZE, resource_quota
from arena_hero_sim.ffa.world import World


def is_backbone(x: int, y: int) -> bool:
    """chunk 十字主干通道（每 chunk 本地 x==0 整列 + y==0 整行）。"""
    return x % CHUNK_SIZE == 0 or y % CHUNK_SIZE == 0


def chunk_cells(world: World, cx: int, cy: int) -> set[tuple[int, int]]:
    x0, y0 = cx * CHUNK_SIZE, cy * CHUNK_SIZE
    return {
        (x, y)
        for x, y in world.resources
        if x0 <= x < x0 + CHUNK_SIZE and y0 <= y < y0 + CHUNK_SIZE
    }


def harvest_one(world: World, cx: int, cy: int) -> tuple[int, int] | None:
    """模拟一次成功收割：消耗该 chunk 当前字典序最小的点。"""
    cells = sorted(chunk_cells(world, cx, cy))
    if not cells:
        return None
    cell = cells[0]
    world.consume_resource(*cell)
    return cell


def run_scenario(seed: int, ticks: int, verbose: bool) -> tuple[set[tuple[int, int]], int]:
    """跑一遍场景，返回 (最终资源点集, 违规落点数)。"""
    world = World(
        size=128,
        seed=seed,
        obstacles=[],
        resource_cells=[(1 + i, 5) for i in range(16)] + [(33 + i, 5) for i in range(14)],
    )
    violations = 0

    if verbose:
        print(
            f"world size=128 seed={seed} "
            f"quota(0,0)={resource_quota(0, 0)} quota(1,0)={resource_quota(1, 0)}"
        )
        print("tick | dirty chunk | quota | before | missing | placed | new positions")

    for tick in range(1, ticks + 1):
        harvest_one(world, 0, 0)
        if tick % 2 == 0:
            harvest_one(world, 1, 0)
        dirty_before = sorted(world.dirty_chunks)
        before_map = {chunk: chunk_cells(world, *chunk) for chunk in dirty_before}
        world.replenish_if_due(tick, lambda x, y: False)

        for chunk in dirty_before:
            before = before_map[chunk]
            after = chunk_cells(world, *chunk)
            new_cells = sorted(after - before)
            quota = resource_quota(*chunk)
            missing = max(0, quota - len(before))
            bad = [cell for cell in new_cells if is_backbone(*cell) or world.is_obstacle(*cell)]
            violations += len(bad)
            if verbose and new_cells:
                marks = " ".join(f"{cell}{'!' if cell in bad else ''}" for cell in new_cells)
                print(
                    f"{tick:4d} | {chunk} | {quota:4d} | {len(before):4d} | "
                    f"{missing:4d} | {len(new_cells):4d} | {marks}"
                )

    return set(world.resources), violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--ticks", type=int, default=12)
    args = parser.parse_args()

    first, violations = run_scenario(args.seed, args.ticks, verbose=True)
    second, _ = run_scenario(args.seed, args.ticks, verbose=False)

    if violations:
        print(f"\nVIOLATIONS: {violations} placement(s) on backbone/obstacle")
        return 1
    if first != second:
        print("\nDETERMINISM FAIL: replay with same seed diverged")
        return 2
    print(f"\nok: {len(first)} resource points, deterministic replay verified, 0 violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
