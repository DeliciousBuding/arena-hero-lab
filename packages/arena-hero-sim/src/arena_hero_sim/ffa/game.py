"""游戏循环：世界 + 玩家 + 策略 → 逐步解析。

每个 Tick：
1. 为每个存活玩家构建视野观察（Observation，只含其可见信息）
2. 调 strategy.decide(obs) 得到 plan
3. Engine.resolve 按官方 resolution order 解析
"""

from concurrent.futures import ThreadPoolExecutor

from .config import BEACON_START, RESOURCE_REPLENISH_EVERY
from .engine import Engine
from .entities import Core, Player, Unit
from .vision import visible_cells


class Game:
    def __init__(
        self,
        strategies,
        size=256,
        seed=0,
        max_ticks=500,
        obstacle_density=0.225,
        cluster_iters=400,
        spawn_center=(0, 0),
        spawn_profile=None,
        resource_scale=1.0,
        resource_replenish_every=RESOURCE_REPLENISH_EVERY,
        respawn_style="ring",
    ):
        """strategies: {player_id: Strategy}（至少 2 个玩家）。

        spawn_center: 出生基准点（默认 [0,0] 中央富矿区）。传远离原点的
        坐标（如 (-96, 128)）可复现线上玩家在中等环带出生的场景——
        线上 Core 出生在 chunk ring 6 附近（quota 9），而模拟器默认
        出生在 ring 2-4（quota 9-11），资源分布差异会导致策略在
        模拟器里验证失真（"22 格内无资源"死锁只在偏远环带出现）。

        spawn_profile: {pid: {"res": N, "units": {WORKER: a, RANGER: b}}}}
        预发育出生——模拟线上永久世界的老玩家（出生即带人口/资源），
        新号（被测）则是 1 Worker + 5 资源的标准开局。
        """
        # 每场对局重置 uid 生成器（seed 派生）：保证跨运行确定性，且 uid 分布
        # 与官方 UUID 一致（稀疏大整数，仲裁不被创建顺序偏袒）
        from . import entities as _ent

        _ent.reset_uid_rng(seed)
        from .world import World

        self.world = World(
            size=size,
            seed=seed,
            obstacle_density=obstacle_density,
            cluster_iters=cluster_iters,
            resource_scale=resource_scale,
            replenish_every=resource_replenish_every,
        )
        half = size // 2
        self.bounds = (-half, half - 1, -half, half - 1)
        self.engine = Engine(self.world, respawn_style=respawn_style)
        self.strategies = strategies
        self.max_ticks = max_ticks
        self.tick = 0
        self.players = {pid: Player(pid) for pid in strategies}
        self.beacon = ("ground", BEACON_START[0], BEACON_START[1])
        self.last_events = []
        self.spawn_center = spawn_center
        self.spawn_profile = spawn_profile or {}
        # Strategy failures are kept observable instead of silently turning a
        # player into a permanent WAIT bot.  The simulator remains resilient,
        # while callers/tests can fail fast on an incompatible adapter.
        self.strategy_errors = {}
        self.strategy_last_errors = {}
        # Persistent decide pool: one thread per strategy, created once and
        # reused for every tick.  Creating a ThreadPoolExecutor per tick (the
        # old design) spawned ~N threads 2000x per match and paid a full
        # shutdown/join after every tick; on a 2000-tick match that alone cost
        # ~20s of pure thread churn.  Results are identical because the engine
        # resolves plans in stable player_id order either way.
        self._decide_pool: ThreadPoolExecutor | None = None
        self._initial_spawn()
        # 单位出生当 Tick 不能行动：初始单位允许行动
        for p in self.players.values():
            for u in p.units.values():
                u.just_spawned = False

    def _initial_spawn(self):
        for pid in sorted(self.players):
            # 每个玩家可有独立出生中心（老玩家分散在四周，模拟线上稀疏分布）
            center = self.spawn_center
            prof = (self.spawn_profile or {}).get(pid)
            if prof and prof.get("center"):
                center = tuple(prof["center"])
            pos = self.engine._find_spawn(
                self.players, pid, center, fixed=bool(prof and prof.get("center"))
            )
            if pos is None:
                pos = center
            p = self.players[pid]
            core = Core(pid, pos)
            core.resources = 5
            p.core = core
            w = Unit(pid, "WORKER", pos)
            p.units[w.uid] = w
            # 预发育出生（模拟线上永久世界：老玩家开局就带人口/资源）
            if prof:
                core.resources = prof.get("res", 5)
                for utype, n in (prof.get("units") or {}).items():
                    for _ in range(n):
                        u = Unit(pid, utype, pos)
                        p.units[u.uid] = u

    # ------------------------------------------------------------------
    def run(self):
        for _ in range(self.max_ticks):
            self.tick += 1
            self.step()
        return self.results()

    def close(self) -> None:
        """Shut down the persistent decide pool (threads are non-daemon)."""
        pool = self._decide_pool
        self._decide_pool = None
        if pool is not None:
            pool.shutdown(wait=True)

    @classmethod
    def resume(cls, strategies, prev, max_ticks):
        """从 prev 的终局状态继续（真实 continuation，2026-08-07 架构评审
        P0#9）。策略实例必须与 prev 共用（记忆/内部状态天然继承），世界/
        单位/Beacon/stats/uid 流全部延续，不重新开局。

        run() 的 max_ticks 语义 = 再跑多少 tick（tick 从 prev.tick 继续）。
        results() 的 stats 是累计值——续局增量需与 prev 的 results 相减。
        """
        g = cls.__new__(cls)
        g.world = prev.world  # 地形/资源/补给状态共享
        g.bounds = prev.bounds
        g.engine = prev.engine  # 共享（engine.cargo 掉落堆等状态延续）
        g.strategies = strategies  # 复用策略实例（记忆继承）
        g.max_ticks = max_ticks
        g.tick = prev.tick
        g.players = prev.players  # 单位/Core/stats/alive_ticks 延续
        g.beacon = prev.beacon
        g.last_events = prev.last_events
        g.spawn_center = prev.spawn_center
        g.spawn_profile = prev.spawn_profile
        g.strategy_errors = prev.strategy_errors
        g.strategy_last_errors = prev.strategy_last_errors
        g._decide_pool = None  # lazily recreated in step(); prev's pool is shared
        return g

    def step(self):
        plans = {}
        # Each contestant decides from the same pre-resolution world state, so the
        # decide calls are independent and safe to fan out.  SDK contestants are
        # long-lived subprocesses whose per-tick JSON round-trip dominates the
        # runtime; running them in parallel (one thread per active contestant)
        # collapses the sequential latency of N subprocess round-trips into the
        # slowest single agent instead of their sum.  Results are identical to the
        # sequential path: the engine resolves plans in stable player_id order.
        active = [pid for pid in self.strategies if self.players[pid].core is not None]

        def _decide_one(pid):
            obs = self.build_observation(self.players[pid])
            try:
                return pid, self.strategies[pid].decide(obs), None
            except Exception as exc:
                return pid, {"core": None, "units": {}}, f"{type(exc).__name__}: {exc}"

        if len(active) > 1:
            if self._decide_pool is None:
                self._decide_pool = ThreadPoolExecutor(max_workers=len(self.strategies))
            results = list(self._decide_pool.map(_decide_one, active))
        elif active:
            results = [_decide_one(active[0])]
        else:
            results = []

        for pid, plan, err in results:
            plans[pid] = plan
            if err is not None:
                self.strategy_errors[pid] = self.strategy_errors.get(pid, 0) + 1
                self.strategy_last_errors[pid] = err

        self.beacon, events = self.engine.resolve(self.players, plans, self.beacon, self.tick)
        self.last_events = events
        # 真实存活计数：Core 存在（ACTIVE）的 tick 数
        for p in self.players.values():
            if p.core is not None:
                p.alive_ticks += 1
        # Core 被摧毁重生：清该玩家策略的临时目标（保留世界记忆——障碍永久）
        for ev in events:
            if ev.get("type") == "CORE_DESTROYED":
                pid = ev.get("player")
                strat = self.strategies.get(pid)
                if strat is not None and hasattr(strat, "reset_transient"):
                    strat.reset_transient()

    # ------------------------------------------------------------------
    def build_observation(self, p):
        """构建 p 的视野观察（只含可见信息；自己的对象全量）。"""
        own_objects = [p.core] if p.core is not None else []
        own_objects += list(p.units.values())

        visible = set()
        for o in own_objects:
            r = self._vision_radius(o)
            visible.update(visible_cells(o.pos[0], o.pos[1], r, self.world.is_obstacle))

        # 地形：集合运算，避免逐格回调
        obstacles = {c for c in visible if self.world.is_obstacle(c[0], c[1])}
        # 自然点与 cargo 掉落堆分开：cargo 不计配额且会消失，不能进长期记忆
        resources = visible & self.world.resources
        cargo_cells = visible & self.engine.cargo.keys()
        resources -= obstacles
        # 敌人：遍历敌方对象查可见集合（O(对象数)），而不是遍历可见格再套两层
        # 循环（O(可见格 × 全部单位)）——后者在多人高人口时会平方级恶化。
        enemies = []
        enemy_cores = []
        for q in self.players.values():
            if q.player_id == p.player_id:
                continue
            if q.core is not None and q.core.pos in visible:
                enemy_cores.append(
                    {
                        "uid": q.core.uid,
                        "pos": q.core.pos,
                        "hp": q.core.hp,
                        "shield": q.core.shield,
                        "owner": q.player_id,
                    }
                )
            for u in q.units.values():
                if u.pos in visible:
                    enemies.append({"uid": u.uid, "utype": u.utype, "pos": u.pos, "hp": u.hp})

        # Beacon：坐标永远公开（官方：every state includes the coordinate, always）；
        # 仅 status / carrier_id 受视野限制
        beacon = {"position": [BEACON_START[0], BEACON_START[1]], "status": "UNKNOWN"}
        if self.beacon[0] == "ground":
            bx, by = self.beacon[1], self.beacon[2]
            beacon["position"] = [bx, by]
            if (bx, by) in visible:
                beacon["status"] = "GROUND"
        else:
            carrier_uid = self.beacon[1]
            for q in self.players.values():
                for u in q.units.values():
                    if u.uid == carrier_uid:
                        beacon["position"] = list(u.pos)
                        if u.pos in visible:
                            beacon["status"] = "CARRIED"
                            beacon["carrier_id"] = u.uid
                if q.core is not None and q.core.uid == carrier_uid:
                    beacon["position"] = list(q.core.pos)
                    if q.core.pos in visible:
                        beacon["status"] = "CARRIED"
                        beacon["carrier_id"] = q.core.uid

        core_obs = None
        if p.core is not None:
            c = p.core
            core_obs = {
                "uid": c.uid,
                "pos": c.pos,
                "hp": c.hp,
                "shield": c.shield,
                "resources": c.resources,
                "migration": c.migration,
                "capacity": self._storage_cap(p),
            }
        units_obs = [
            {
                "uid": u.uid,
                "utype": u.utype,
                "pos": u.pos,
                "hp": u.hp,
                "cargo": u.cargo,
                "carries_beacon": u.carries_beacon,
            }
            for u in p.units.values()
        ]

        from .observation import Observation

        return Observation(
            player_id=p.player_id,
            tick=self.tick,
            core=core_obs,
            units=units_obs,
            enemies=enemies,
            enemy_cores=enemy_cores,
            resources=resources,
            cargo_cells=cargo_cells,
            obstacles=obstacles,
            beacon=beacon,
            population=p.population,
            visible_cells=visible,
            prev_events=self._visible_events(p.player_id),
            # population_tier/upkeep_next_tick：rules v0.14 已删除维护费，
            # 字段废弃（dataclass 默认值保持兼容）
        )

    def _visible_events(self, pid):
        """官方 state.events 是发给该玩家的私有结算结果（resolution results
        addressed to this player）——只返回涉及 pid 的事件。旧实现把全局
        last_events 发给每个玩家：策略/统计能看到敌人移动失败、采集、战斗
        事件，线上无法复现（信息泄漏）。"""
        if not self.last_events:
            return self.last_events
        out = []
        for ev in self.last_events:
            pids, o = self._event_players(ev)
            if pid in pids:
                out.append(ev)
                continue
            if o is not None:
                for q in self.players.values():
                    if (q.core is not None and q.core.uid == o) or o in q.units:
                        if q.player_id == pid:
                            out.append(ev)
                        break
        return out

    @staticmethod
    def _event_players(ev):
        """事件涉及玩家集合（引擎事件字段不统一：player/obj_id/unit/target_id）。"""
        pids = set()
        p = ev.get("player")
        if p is not None:
            pids.add(p)
        # Core loot is addressed to the damage winner (the event intentionally
        # has no ``player`` field in the simulator's v0.14 payload).
        winner = ev.get("winner")
        if winner is not None:
            pids.add(winner)
        o = ev.get("obj_id")
        if o is None:
            o = ev.get("unit")
        if o is None:
            o = ev.get("target_id")
        return pids, o

    def _vision_radius(self, o):
        from .config import VISION

        if hasattr(o, "utype"):
            return VISION[o.utype]
        return VISION["CORE"]

    def _storage_cap(self, p):
        from .config import storage_capacity

        return storage_capacity(p.population)

    # ------------------------------------------------------------------
    def results(self):
        out = {}
        for pid, p in self.players.items():
            st = dict(p.stats)
            st.update(
                {
                    "alive": p.core is not None,
                    "final_population": p.population,
                    "final_resources": p.core.resources if p.core is not None else 0,
                    "respawn_count": p.respawn_count,
                    "ticks_alive": p.alive_ticks,
                    "strategy_errors": self.strategy_errors.get(pid, 0),
                    "strategy_last_error": self.strategy_last_errors.get(pid),
                }
            )
            out[pid] = st
        return out
