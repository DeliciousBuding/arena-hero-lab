"""世界：地形生成、资源层、补给。

本地模拟器使用自建的确定性生成器（官方 HMAC 种子是私有的），
参照官方规则（doc.arenahero.io rules/world-and-ticks）设计：
- 世界中心为坐标原点 [0,0]（与官方双向坐标一致），尺寸 size×size，
  有效坐标范围 [-size//2, size//2)。
- 地形按 32×32 chunk 用派生确定性 PRNG 生成（对应官方 HMAC 协议）；
  每 chunk 保留十字主干通道（x=0 整列 + y=0 整行 EMPTY）——相邻
  chunk 通道对齐 → 全图天然连通（对应官方"区块主干通道"），通道
  交汇处自然出现一格宽瓶颈（官方：瓶颈往往很关键）；[0,0] 永久
  EMPTY（官方：Beacon 不会被围死）。
- 资源按 chunk 配额生成；每 RESOURCE_REPLENISH_EVERY 个已解析 Tick，
  只补给本 Tick 有消耗的 chunk 缺口，位置由派生 PRNG 确定性选出。
"""

import random

from .config import (
    CHUNK_SIZE,
    EMPTY,
    OBSTACLE,
    RESOURCE,
    RESOURCE_REPLENISH_EVERY,
    resource_quota,
)


class World:
    def __init__(
        self,
        size=256,
        seed=0,
        obstacle_density=0.27,
        cluster_iters=400,
        plain=False,
        resource_scale=1.0,
    ):
        """plain=True：纯平地（全 EMPTY）——引擎/移动类单元测试用。"""
        self.size = size
        self.offset = size // 2
        self.seed = seed
        self.resource_scale = resource_scale
        self.rng = random.Random(seed)
        self.terrain = None  # list of bytearray, 索引 terrain[y+off][x+off]
        self.resources = set()  # 当前可用的自然资源点（真实坐标）{(x, y)}
        self.dirty_chunks = set()  # 本补给周期内有消耗的 chunk
        self.since_replenish = 0
        if plain:
            n = size
            self.terrain = [bytearray([EMPTY]) * n for _ in range(n)]
            self._seed_initial_resources()
        else:
            self._generate(obstacle_density, cluster_iters)

    # ---------- 生成 ----------
    def _generate(self, density, cluster_iters):
        """官方风格 chunk 化生成（doc.arenahero.io rules/world-and-ticks）：

        - 每 32×32 chunk 用派生确定性 PRNG 独立生成（对应官方 HMAC 协议）
        - 每 chunk 保留十字主干通道（x=0 整列 + y=0 整行 EMPTY）——相邻
          chunk 通道对齐 → 全图天然连通（对应官方"区块主干通道"），且
          通道交汇处自然出现"一格宽瓶颈"（官方：瓶颈往往很关键）
        - [0,0] 落在通道上，永久 EMPTY（官方：Beacon 不会被围死）
        - 不再需要主连通分量 BFS（通道网格保证连通），也更贴近线上
        """
        n = self.size
        off = self.offset
        terrain = [bytearray([EMPTY]) * n for _ in range(n)]
        half = n // 2
        # chunk 范围（真实坐标）
        for cy in range(-half // CHUNK_SIZE, half // CHUNK_SIZE + 1):
            for cx in range(-half // CHUNK_SIZE, half // CHUNK_SIZE + 1):
                rng = random.Random(f"{self.seed}:{cx}:{cy}")
                x0, y0 = cx * CHUNK_SIZE, cy * CHUNK_SIZE
                # 每 chunk 密度随机因子（真实 chunk 密度 0-26.3%，波动大）
                chunk_density = density * 0.55 * rng.uniform(0.05, 1.8)
                # 散点 + 簇（成片结构——线上 memory 实测 chunk 内障碍
                # 4%~14% 波动，纯散点太均匀）
                # 簇尺寸按真实数据校准（2026-08-06）：真实障碍最大连通域 14 格、
                # 9-20 格仅 8/11373 个（0.07%）、21+ 为 0——旧 1-3×1-3 簇仍产生
                # 25 格团块；缩小到 1-2×1-2（最大 4 格）、每 chunk 1-2 簇，
                # 大团块只能靠散点随机粘连（罕见，贴合真实分布）
                clusters = [
                    (
                        rng.randrange(1, 31),
                        rng.randrange(1, 31),
                        rng.randint(1, 2),
                        rng.randint(1, 2),
                    )
                    for _ in range(rng.randint(1, 2))
                ]
                for y in range(32):
                    gy = y0 + y
                    if not (-off <= gy < off):
                        continue
                    row = terrain[gy + off]
                    for x in range(32):
                        gx = x0 + x
                        if not (-off <= gx < off):
                            continue
                        if x == 0 or y == 0:
                            row[gx + off] = EMPTY  # 十字主干通道
                        elif rng.random() < chunk_density:
                            row[gx + off] = OBSTACLE
                # 簇（确定性 PRNG 顺序在散点之后，位置由 rng 序列决定）
                for cxx, cyy, cw, ch in clusters:
                    for yy in range(cyy, min(cyy + ch, 31)):
                        for xx in range(cxx, min(cxx + cw, 31)):
                            if xx == 0 or yy == 0:
                                continue
                            gy = y0 + yy
                            gx = x0 + xx
                            if -off <= gy < off and -off <= gx < off:
                                terrain[gy + off][gx + off] = OBSTACLE
        self.terrain = terrain
        # 打碎大团块（校准 2026-08-06）：真实世界 10.8 万格探索最大连通域
        # 14 格（15% 密度下纯随机渗流必然偶发 25+ 格大块——官方生成显然
        # 有结构性抑制）。对 >12 格连通域随机抽稀（保留 12 格，其余删），
        # 大块拆成小块，密度损失 <0.5%。
        self._break_large_clusters(max_size=12)
        self._seed_initial_resources()

    def _break_large_clusters(self, max_size=12):
        """随机渗流大块抑制：扫描连通域，>max_size 的随机抽稀拆碎。"""
        n = self.size
        # 独立派生 RNG（2026-08-07 A1 修复）：绝不能用模块级 random（受进程/
        # 调用顺序影响，同一 seed 地图不确定），也不能用 self.rng——
        # _seed_initial_resources 也用 self.rng 放资源，两处共享会让资源
        # 位置依赖打断顺序，改变修复前的地图分布。
        rng = random.Random(f"{self.seed}:break")
        visited = [[False] * n for _ in range(n)]
        for sy in range(n):
            for sx in range(n):
                if visited[sy][sx] or self.terrain[sy][sx] == EMPTY:
                    continue
                # BFS 收集连通域
                stack = [(sx, sy)]
                visited[sy][sx] = True
                cells = []
                while stack:
                    x, y = stack.pop()
                    cells.append((x, y))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if (
                            0 <= nx < n
                            and 0 <= ny < n
                            and not visited[ny][nx]
                            and self.terrain[ny][nx] != EMPTY
                        ):
                            visited[ny][nx] = True
                            stack.append((nx, ny))
                if len(cells) > max_size:
                    # 随机保留 max_size 格，其余删除（拆成小块）
                    keep = set(rng.sample(cells, max_size))
                    for x, y in cells:
                        if (x, y) not in keep:
                            self.terrain[y][x] = EMPTY

    def _seed_initial_resources(self):
        """每个 chunk 初始放满配额资源点（真实坐标，避开障碍）。

        校准 2026-08-06：真实世界探索区仅 65% chunk 有资源（敌方联盟
        187 个探索 chunk 中 121 个含资源）——旧配置 100% chunk 放资源
        太乐观（资源太好找）。35% chunk 保持空置（永不补给——补给只
        发生在 dirty chunk，空 chunk 永远不脏）。
        """
        n = self.size
        half = n // 2
        for cy in range(-half // CHUNK_SIZE, half // CHUNK_SIZE + 1):
            for cx in range(-half // CHUNK_SIZE, half // CHUNK_SIZE + 1):
                rng = random.Random(f"{self.seed}:res:{cx}:{cy}")
                if rng.random() > 0.65:
                    continue  # 35% chunk 无资源（真实分布）
                quota = max(1, int(resource_quota(cx, cy) * self.resource_scale))
                placed = 0
                attempts = 0
                while placed < quota and attempts < quota * 60:
                    attempts += 1
                    x = cx * CHUNK_SIZE + self.rng.randrange(CHUNK_SIZE)
                    y = cy * CHUNK_SIZE + self.rng.randrange(CHUNK_SIZE)
                    if not self.in_bounds(x, y):
                        continue
                    if (
                        self.terrain[y + self.offset][x + self.offset] == EMPTY
                        and (x, y) not in self.resources
                    ):
                        self.resources.add((x, y))
                        placed += 1

    # ---------- 查询 ----------
    def in_bounds(self, x, y):
        return -self.offset <= x < self.offset and -self.offset <= y < self.offset

    def is_obstacle(self, x, y):
        if not self.in_bounds(x, y):
            return True
        return self.terrain[y + self.offset][x + self.offset] == OBSTACLE

    def terrain_kind(self, x, y):
        if not self.in_bounds(x, y):
            return OBSTACLE
        if (x, y) in self.resources:
            return RESOURCE  # 当前有自然资源点的格（Core 迁移不可入）
        return self.terrain[y + self.offset][x + self.offset]

    def consume_resource(self, x, y):
        """消耗一个自然资源点；返回是否成功。"""
        if (x, y) in self.resources:
            self.resources.discard((x, y))
            self._mark_dirty_chunk(x, y)
            return True
        return False

    def _mark_dirty_chunk(self, x, y):
        self.dirty_chunks.add((x // CHUNK_SIZE, y // CHUNK_SIZE))

    # ---------- 补给 ----------
    def replenish_if_due(self, resolved_tick, occupied_by_core):
        """每个补给周期结束（resolved_tick % 4 == 0）时补齐脏 chunk 的缺口。

        occupied_by_core: 回调 (x, y) -> bool，用于避开 Core 占据的格。
        """
        if resolved_tick % RESOURCE_REPLENISH_EVERY != 0:
            return
        if not self.dirty_chunks:
            return
        for cx, cy in self.dirty_chunks:
            quota = max(1, int(resource_quota(cx, cy) * self.resource_scale))
            missing = quota - self._count_chunk_resources(cx, cy)
            if missing <= 0:
                continue
            r = random.Random(f"{self.seed}:{cx}:{cy}:{resolved_tick}")
            attempts = 0
            while missing > 0 and attempts < missing * 60:
                attempts += 1
                x = cx * CHUNK_SIZE + r.randrange(CHUNK_SIZE)
                y = cy * CHUNK_SIZE + r.randrange(CHUNK_SIZE)
                if (
                    self.in_bounds(x, y)
                    and self.terrain[y + self.offset][x + self.offset] == EMPTY
                    and (x, y) not in self.resources
                    and not occupied_by_core(x, y)
                ):
                    self.resources.add((x, y))
                    missing -= 1
        self.dirty_chunks.clear()

    def _count_chunk_resources(self, cx, cy):
        x0 = cx * CHUNK_SIZE
        y0 = cy * CHUNK_SIZE
        return sum(
            1
            for (x, y) in self.resources
            if x0 <= x < x0 + CHUNK_SIZE and y0 <= y < y0 + CHUNK_SIZE
        )
