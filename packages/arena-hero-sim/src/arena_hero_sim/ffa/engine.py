"""确定性规则引擎：按官方 resolution order 解析一个 Tick。

约定：
- plan: {"core": (action_type, kwargs) | None, "units": {uid: (action_type, kwargs)}}
- 每玩家每 Tick 一个 plan。
- 事件为 list[dict]，全局顺序记录（模拟器不区分玩家私有事件）。
- cargo 堆（死 Worker 掉落的资源）持久保存在 Engine 实例中。
- Beacon 状态贯穿整个 resolve：("ground", x, y) | ("carried", uid)。
"""

from .config import (
    CELL_CAPACITY,
    CORE_HP,
    CORE_MIGRATION_TICKS,
    CORE_SHIELD,
    CORE_SHIELD_CAP_BEACON,
    DIRECTIONS,
    EMPTY,
    OBSTACLE,
    RANGER_RANGE,
    RESOURCE,
    UNIT_STATS,
    storage_capacity,
    unit_cost,
)
from .entities import Core, Unit
from .vision import is_shot_line, shot_intermediate_cells


class Engine:
    def __init__(self, world):
        self.world = world
        self.cargo = {}  # (x, y) -> amount（死 Worker 掉落）
        self._beacon = None
        self._uid_owner = {}  # uid -> player_id（本 Tick 有效）

    # =====================================================================
    # 主入口
    # =====================================================================
    def resolve(self, players, plans, beacon, tick):
        """players: {player_id: Player}; plans: {player_id: plan}。

        返回 (beacon, events)。
        """
        self._beacon = beacon
        self._beacon_dropped_this_tick = False  # 掉落当 Tick 冷却（不可拾取）
        self._uid_owner = {}
        self._players = players
        for p in players.values():
            if p.core is not None:
                self._uid_owner[p.core.uid] = p.player_id
            for u in p.units.values():
                self._uid_owner[u.uid] = p.player_id

        events = []
        actives = [p for p in players.values() if p.core is not None]

        # 上 Tick 已存在的单位恢复行动能力（SPAWN 当 Tick 由 step12 重新标记）
        for p in players.values():
            for u in p.units.values():
                u.just_spawned = False

        self._step2_unit_self_destruct(actives, plans, events)
        self._step4_movement(actives, plans, events)
        self._step5_start_move(actives, plans, events)
        self._step6_beacon(actives, plans, events)
        self._step7_worker_actions(actives, plans, events)
        dead_cores = self._step8_9_combat(actives, plans, events)
        # 战斗死亡导致人口下降 → 立即销毁超容量库存（官方 v0.6：
        # population falls → excess inventory destroyed immediately），
        # 这样后续治疗/生产只能花销毁后的资源
        self._enforce_capacity(players, events)
        self._step10_core_self_destruct(actives, plans, dead_cores, events)
        self._step11_unit_heal(actives, plans, events)
        self._step12_core_actions(actives, plans, events)
        self._step13_respawn(players, dead_cores, events, tick)
        self.world.replenish_if_due(tick, lambda x, y: self._occupied_by_core(players, x, y))
        # 丢失 Beacon 的玩家盾值钳制回 5（官方：Lose the Beacon and anything
        # above 5 is clamped straight back down to 5）
        for p in players.values():
            if p.core is not None:
                has = (
                    self._beacon[0] == "carried"
                    and self._uid_owner.get(self._beacon[1]) == p.player_id
                )
                if not has and p.core.shield > CORE_SHIELD:
                    p.core.shield = CORE_SHIELD
        # 兜底清算（覆盖任意其余人口下降来源）
        self._enforce_capacity(players, events)
        # Beacon 持有 Tick 统计（resolution 结束时仍持有才计入）
        if self._beacon[0] == "carried":
            owner_id = self._uid_owner.get(self._beacon[1])
            if owner_id is not None and owner_id in players:
                players[owner_id].stats["beacon_ticks"] += 1
        return self._beacon, events

    # =====================================================================
    # Beacon 掉落辅助
    # =====================================================================
    def _drop_beacon_at(self, pos):
        self._beacon = ("ground", pos[0], pos[1])
        self._beacon_dropped_this_tick = True

    # =====================================================================
    # 人口变化后的资源容量清算
    # =====================================================================
    def _enforce_capacity(self, players, events):
        """人口下降导致容量超限：销毁超出资源（CORE_RESOURCE_OVERFLOW_DESTROYED）。"""
        for p in players.values():
            if p.core is not None:
                cap = storage_capacity(p.population)
                if p.core.resources > cap:
                    destroyed = p.core.resources - cap
                    p.core.resources = cap
                    p.stats["overflow_destroyed"] += destroyed
                    events.append(
                        {
                            "type": "CORE_RESOURCE_OVERFLOW_DESTROYED",
                            "player": p.player_id,
                            "amount": destroyed,
                            "capacity": cap,
                        }
                    )

    # =====================================================================
    # Step 2: Unit SELF_DESTRUCT
    # =====================================================================
    def _step2_unit_self_destruct(self, actives, plans, events):
        for p in actives:
            plan = plans.get(p.player_id)
            if not plan:
                continue
            for uid, (atype, _args) in plan["units"].items():
                if atype != "SELF_DESTRUCT":
                    continue
                u = p.units.get(uid)
                if u is None:
                    continue
                self._remove_unit(p, u, events, "UNIT_SELF_DESTRUCTED")

    # =====================================================================
    # Step 3: 移动（单位 MOVE + Core 迁移第 4 Tick）
    # =====================================================================
    def _step4_movement(self, actives, plans, events):
        movers = []  # [(obj, (dx, dy))]
        for p in actives:
            plan = plans.get(p.player_id)
            if plan:
                for uid, (atype, args) in plan["units"].items():
                    if atype == "MOVE" and uid in p.units:
                        d = DIRECTIONS[args["direction"]]
                        movers.append((p.units[uid], d))
            core = p.core
            if core is not None and core.migration is not None:
                direction, progress = core.migration
                progress += 1
                if progress >= CORE_MIGRATION_TICKS:
                    core.migration = None  # 尝试真实移动；失败即归零
                    movers.append((core, DIRECTIONS[direction]))
                else:
                    core.migration = (direction, progress)
        self._resolve_movement_graph(actives, movers, events)

    def _resolve_movement_graph(self, actives, movers, events):
        if not movers:
            return
        finals = {}
        for p in actives:
            if p.core is not None:
                finals[p.core] = p.core.pos
            for u in p.units.values():
                finals[u] = u.pos
        intents = {}
        for obj, delta in movers:
            x, y = obj.pos
            intents[obj] = (x + delta[0], y + delta[1])

        failed = {}  # obj -> reason（移动失败原因，供策略按 reason 分类处理）
        settled = set()  # 已成功移动（finals 已更新到 intents）
        # 反复扫描：等待者可被后续轮次解决（链式）；全部等待时做环检测
        while True:
            progressed = False
            for obj in sorted(intents, key=lambda o: o.uid):
                if obj in failed or obj in settled:
                    continue
                dest = intents[obj]
                if not self._passable(obj, dest):
                    failed[obj] = "MOVE_BLOCKED_TERRAIN"
                    progressed = True
                    continue
                occupants = [o for o in finals if finals[o] == dest and o is not obj]
                leaving = [o for o in occupants if o in intents and o not in failed]
                if leaving:
                    continue  # 等它们先移动（链式）
                stayers = [o for o in occupants if o not in intents or o in failed]
                # 不同玩家的对象不能同格终局
                if any(o.owner != obj.owner for o in stayers):
                    failed[obj] = "MOVE_DESTINATION_OCCUPIED"
                    progressed = True
                    continue
                comps = [
                    o
                    for o in intents
                    if o not in failed and o not in settled and o is not obj and intents[o] == dest
                ]
                # 跨玩家竞争同一格 → 全部失败
                if any(o.owner != obj.owner for o in comps):
                    failed[obj] = "MOVE_CONTESTED"
                    for o in comps:
                        failed[o] = "MOVE_CONTESTED"
                    progressed = True
                    continue
                slots = CELL_CAPACITY - len(stayers)
                if slots <= 0:
                    failed[obj] = "CELL_UNIT_LIMIT"
                    progressed = True
                    continue
                candidates = sorted([*comps, obj], key=lambda o: o.uid)
                winners = candidates[:slots]
                if obj not in winners:
                    failed[obj] = "CELL_UNIT_LIMIT"
                else:
                    finals[obj] = dest
                    settled.add(obj)
                    for o in candidates[slots:]:
                        failed[o] = "CELL_UNIT_LIMIT"
                progressed = True
            if progressed:
                continue
            # 无进展：剩余未决者全部在“等待” → 检测依赖环
            pending = [o for o in intents if o not in failed and o not in settled]
            if not pending:
                break
            handled = False
            for obj in list(pending):
                cycle = self._find_cycle(obj, intents, finals, pending)
                if cycle is None:
                    continue
                if self._cycle_ok(cycle):
                    for o in cycle:
                        finals[o] = intents[o]
                        settled.add(o)
                else:
                    for o in cycle:
                        failed[o] = "MOVE_SWAP_BLOCKED"
                handled = True
            if not handled:
                break  # 无环（孤立等待者，理论上不会出现）

        # 收尾：仍未移动者失败（链式依赖未成功 → DEPENDENCY），然后一轮传播阻塞
        for obj in list(intents):
            if obj not in failed and obj not in settled:
                failed[obj] = "MOVE_DEPENDENCY_FAILED"
        for obj in sorted(intents, key=lambda o: o.uid):
            if obj in failed or obj in settled:
                continue
            dest = intents[obj]
            if not self._passable(obj, dest):
                failed[obj] = "MOVE_BLOCKED_TERRAIN"
                continue
            occupants = [o for o in finals if finals[o] == dest and o is not obj]
            if any(o.owner != obj.owner for o in occupants):
                failed[obj] = "MOVE_DESTINATION_OCCUPIED"
                continue
            comps = [
                o
                for o in intents
                if o not in failed and o not in settled and o is not obj and intents[o] == dest
            ]
            if any(o.owner != obj.owner for o in comps):
                failed[obj] = "MOVE_CONTESTED"
                continue
            slots = CELL_CAPACITY - len(occupants)
            if slots <= 0:
                failed[obj] = "CELL_UNIT_LIMIT"
                continue
            candidates = sorted([*comps, obj], key=lambda o: o.uid)
            if obj in candidates[:slots]:
                finals[obj] = dest
                settled.add(obj)

        for obj, pos in finals.items():
            if pos != obj.pos:
                events.append({"type": "MOVED", "obj_id": obj.uid, "to": pos})
                obj.pos = pos
        # 有移动意图但最终未移动 → 阻塞反馈（供策略感知真实障碍/冲突，
        # 带官方 reason_code：TERRAIN/CONTESTED/OCCUPIED/DEPENDENCY/CELL_UNIT_LIMIT/SWAP）
        for obj in sorted(intents, key=lambda o: o.uid):
            if finals[obj] == obj.pos:
                dx = intents[obj][0] - obj.pos[0]
                dy = intents[obj][1] - obj.pos[1]
                for name, (ndx, ndy) in DIRECTIONS.items():
                    if (ndx, ndy) == (dx, dy):
                        events.append(
                            {
                                "type": "MOVE_BLOCKED",
                                "obj_id": obj.uid,
                                "direction": name,
                                "reason": failed.get(obj, "MOVE_DEPENDENCY_FAILED"),
                            }
                        )
                        break

    def _find_cycle(self, start, intents, finals, pending):
        """沿依赖链（obj 的 dest 内仍未决的占据者）找环；返回环成员列表或 None。"""
        seen = {}
        chain = []
        cur = start
        while cur is not None:
            if cur in seen:
                idx = seen[cur]
                cycle = chain[idx:]
                return cycle if len(cycle) >= 2 else None
            seen[cur] = len(chain)
            chain.append(cur)
            dest = intents[cur]
            deps = [o for o in finals if finals[o] == dest and o in pending and o is not cur]
            cur = deps[0] if deps else None
        return None

    @staticmethod
    def _cycle_ok(cycle):
        """2 环仅同玩家可交换；3+ 环（卡迪纳网格上即 4 格环）可成功。"""
        if len(cycle) >= 3:
            return True
        a, b = cycle
        return a.owner == b.owner

    # =====================================================================
    # Step 5: 验证新 START_MOVE
    # =====================================================================
    def _step5_start_move(self, actives, plans, events):
        for p in actives:
            plan = plans.get(p.player_id)
            if not plan or not plan["core"]:
                continue
            atype, args = plan["core"]
            core = p.core
            if core is None:
                continue
            if atype == "START_MOVE" and core.migration is None:
                if args["direction"] not in DIRECTIONS:
                    continue
                core.migration = (args["direction"], 1)
            elif atype == "CANCEL_MOVE" and core.migration is not None:
                # 取消迁移并清零进度
                core.migration = None
                events.append({"type": "CORE_MOVE_CANCELLED", "player": p.player_id})

    # =====================================================================
    # Step 6: Beacon 拾取 / 放下
    # =====================================================================
    def _step6_beacon(self, actives, plans, events):
        pickupers = []  # (obj, player)
        droppers = []
        for p in actives:
            plan = plans.get(p.player_id)
            if not plan:
                continue
            core = p.core
            if plan["core"]:
                atype = plan["core"][0]
                if atype == "PICKUP_BEACON" and core is not None and core.migration is None:
                    pickupers.append((core, p))
                elif atype == "DROP_BEACON" and core is not None and core.migration is None:
                    droppers.append((core, p))
            for uid, (atype, _args) in plan["units"].items():
                u = p.units.get(uid)
                if u is None:
                    continue
                if atype == "PICKUP_BEACON":
                    pickupers.append((u, p))
                elif atype == "DROP_BEACON":
                    droppers.append((u, p))

        for obj, p in droppers:
            if obj.carries_beacon:
                obj.carries_beacon = False
                self._drop_beacon_at(obj.pos)
                self._beacon_dropped_this_tick = True
                events.append(
                    {
                        "type": "BEACON_DROPPED",
                        "obj_id": obj.uid,
                        "player": p.player_id,
                        "at": obj.pos,
                    }
                )

        # 掉落当 Tick 不可再被拾取（官方冷却：阻止同 Tick 接力传递）
        if self._beacon[0] == "ground" and not self._beacon_dropped_this_tick:
            _, gx, gy = self._beacon
            eligible = [o for (o, _p) in pickupers if o.pos == (gx, gy) and not o.carries_beacon]
            if eligible:
                winner = min(eligible, key=lambda o: o.uid)
                winner.carries_beacon = True
                self._beacon = ("carried", winner.uid)
                events.append(
                    {"type": "BEACON_PICKED_UP", "obj_id": winner.uid, "player": winner.owner}
                )

    # =====================================================================
    # Step 7: Worker 采集 / 存放
    # =====================================================================
    def _step7_worker_actions(self, actives, plans, events):
        # HARVEST：同格同源竞争，最低 uid 优先
        by_cell = {}
        for p in actives:
            plan = plans.get(p.player_id)
            if not plan:
                continue
            for uid, (atype, _args) in plan["units"].items():
                u = p.units.get(uid)
                if u is None or u.utype != "WORKER" or atype != "HARVEST":
                    continue
                if u.cargo > 0:
                    continue  # 只允许空货采集
                x, y = u.pos
                has_cargo = (x, y) in self.cargo
                has_natural = (x, y) in self.world.resources
                if not has_cargo and not has_natural:
                    events.append(
                        {"type": "HARVEST_FAILED", "unit": u.uid, "reason": "NO_RESOURCE"}
                    )
                    continue
                by_cell.setdefault((x, y), []).append((u, has_cargo, has_natural))
        for (x, y), cands in sorted(by_cell.items()):
            cands.sort(key=lambda c: c[0].uid)
            succeeded = False  # 同源竞争：只有最低 uid 的 Worker 成功
            for u, has_cargo, has_natural in cands:
                if succeeded:
                    events.append(
                        {"type": "HARVEST_FAILED", "unit": u.uid, "reason": "RESOURCE_DEPLETED"}
                    )
                    continue
                owner_has_beacon = self._player_has_beacon(u.owner)
                if has_cargo and (x, y) in self.cargo:
                    amt = self.cargo[(x, y)]
                    gain = min(2 if owner_has_beacon else 1, amt)
                    u.cargo = gain
                    if amt == gain:
                        del self.cargo[(x, y)]
                    else:
                        self.cargo[(x, y)] = amt - gain
                    succeeded = True
                    events.append(
                        {
                            "type": "HARVESTED",
                            "unit": u.uid,
                            "from": "cargo",
                            "amount": gain,
                            "at": (x, y),
                        }
                    )
                elif has_natural and (x, y) in self.world.resources:
                    self.world.consume_resource(x, y)
                    gain = 2 if owner_has_beacon else 1
                    u.cargo = gain
                    p = self._player_of(u.owner)
                    if p is not None:
                        p.stats["harvested"] += gain
                    succeeded = True
                    events.append(
                        {
                            "type": "HARVESTED",
                            "unit": u.uid,
                            "from": "natural",
                            "amount": gain,
                            "at": (x, y),
                        }
                    )
                else:
                    events.append(
                        {"type": "HARVEST_FAILED", "unit": u.uid, "reason": "RESOURCE_DEPLETED"}
                    )

        # DEPOSIT
        for p in actives:
            plan = plans.get(p.player_id)
            if not plan:
                continue
            core = p.core
            if core is None:
                continue
            for uid, (atype, _args) in plan["units"].items():
                u = p.units.get(uid)
                if u is None or u.utype != "WORKER" or atype != "DEPOSIT":
                    continue
                if u.pos != core.pos:
                    continue
                if core.migration is not None:
                    events.append(
                        {"type": "DEPOSIT_FAILED", "unit": u.uid, "reason": "CORE_MIGRATING"}
                    )
                    continue
                cap = storage_capacity(p.population)
                room = cap - core.resources
                if room <= 0:
                    events.append(
                        {"type": "DEPOSIT_FAILED", "unit": u.uid, "reason": "CORE_RESOURCE_FULL"}
                    )
                    continue
                moved = min(u.cargo, room)
                u.cargo -= moved
                core.resources += moved
                p.stats["deposited"] = p.stats.get("deposited", 0) + moved
                events.append({"type": "DEPOSITED", "unit": u.uid, "amount": moved})

    def _player_has_beacon(self, player_id):
        if self._beacon[0] != "carried":
            return False
        return self._uid_owner.get(self._beacon[1]) == player_id

    def _player_of(self, player_id):
        return self._players.get(player_id)

    # =====================================================================
    # Step 8+9: 战斗（快照、累积、同时结算）
    # =====================================================================
    def _step8_9_combat(self, actives, plans, events):
        pos_units = {}
        for u in [u for p in actives for u in p.units.values()]:
            pos_units.setdefault(u.pos, []).append(u)
        pos_cores = {c.pos: c for c in [p.core for p in actives if p.core is not None]}

        damage = {}  # target_uid -> {attacker_uid: dmg}
        atk_records = []  # (target_uid, attacker_uid, dmg)

        for p in actives:
            plan = plans.get(p.player_id)
            if not plan:
                continue
            for uid, (atype, args) in plan["units"].items():
                u = p.units.get(uid)
                if u is None or u.just_spawned:
                    continue
                if atype == "SWEEP" and u.utype == "VANGUARD":
                    dx, dy = DIRECTIONS[args["direction"]]
                    tx, ty = u.pos[0] + dx, u.pos[1] + dy
                    for t in pos_units.get((tx, ty), []):
                        if t.owner != p.player_id:
                            self._add_damage(damage, atk_records, t.uid, u.uid, 1)
                    c = pos_cores.get((tx, ty))
                    if c is not None and c.owner != p.player_id:
                        self._add_damage(damage, atk_records, c.uid, u.uid, 1)
                elif atype == "SHOOT" and u.utype == "RANGER":
                    target_cell = tuple(args["expected_cell"])
                    x0, y0 = u.pos
                    if not is_shot_line(x0, y0, target_cell[0], target_cell[1], RANGER_RANGE):
                        events.append(
                            {"type": "SHOT_MISSED", "unit": u.uid, "reason": "NOT_ALIGNED"}
                        )
                        continue
                    blocked = False
                    for cx, cy in shot_intermediate_cells(x0, y0, target_cell[0], target_cell[1]):
                        if self.world.is_obstacle(cx, cy):
                            blocked = True
                            break
                    if blocked:
                        events.append({"type": "SHOT_MISSED", "unit": u.uid, "reason": "OBSTACLE"})
                        continue
                    hostiles = [t for t in pos_units.get(target_cell, []) if t.owner != p.player_id]
                    c = pos_cores.get(target_cell)
                    if c is not None and c.owner != p.player_id:
                        hostiles.append(c)
                    if not hostiles:
                        events.append({"type": "SHOT_MISSED", "unit": u.uid, "reason": "EMPTY"})
                        continue
                    target = min(hostiles, key=lambda t: (t.hp, t.uid))
                    self._add_damage(damage, atk_records, target.uid, u.uid, 1)

        # 同时应用伤害
        dead_units = set()
        dead_cores = []
        for target_uid, attackers in damage.items():
            total = sum(attackers.values())
            owner = self._owner_of(actives, target_uid)
            if owner is None:
                continue
            t = (
                owner.core
                if owner.core is not None and owner.core.uid == target_uid
                else owner.units.get(target_uid)
            )
            if t is None:
                continue
            for atk_uid, dmg in attackers.items():
                atk_owner = self._owner_of(actives, atk_uid)
                if atk_owner is not None:
                    atk_owner.stats["damage_dealt"] += dmg
            if hasattr(t, "shield"):
                absorbed = min(t.shield, total)
                t.shield -= absorbed
                total -= absorbed
            t.hp -= total
            events.append(
                {
                    "type": "CORE_DAMAGED" if hasattr(t, "shield") else "UNIT_DAMAGED",
                    "target_id": t.uid,
                    "player": owner.player_id,
                    "damage": total,
                }
            )
            if t.hp <= 0:
                if hasattr(t, "shield"):
                    dead_cores.append(owner)
                else:
                    dead_units.add(t)

        # 移除死亡单位
        for u in dead_units:
            p = self._owner_of(actives, u.uid)
            if p is not None:
                self._remove_unit(p, u, events, "COMBAT_KILLED")
        # 移除被毁 Core：舰队全部移除
        destroyed_ids = {p.player_id for p in dead_cores}
        for p in dead_cores:
            events.append({"type": "CORE_DESTROYED", "player": p.player_id, "reason": "ATTACK"})
            core = p.core
            if core.carries_beacon:
                core.carries_beacon = False
                self._drop_beacon_at(core.pos)
            for u in list(p.units.values()):
                if u.carries_beacon:
                    u.carries_beacon = False
                    self._drop_beacon_at(u.pos)
                if u.cargo > 0:
                    self._drop_cargo(u)
                p.stats["units_lost"] += 1  # 舰队随 Core 摧毁计入损失
                del p.units[u.uid]
            p.respawn_count += 1
        # 库存转移（受害者按 player_id 顺序）
        for p in sorted(dead_cores, key=lambda p: p.player_id):
            victim = p
            attackers = {}
            for target_uid, atk_uid, _dmg in atk_records:
                if target_uid == victim.core.uid:
                    ao = self._owner_of(actives, atk_uid)
                    if ao is not None:
                        attackers[ao] = attackers.get(ao, 0) + _dmg
            if attackers:
                winner = max(attackers, key=lambda ao: (attackers[ao], -ao.player_id))
                if winner.core is None or winner.player_id in destroyed_ids:
                    victim.stats["resources_lost"] += victim.core.resources
                    events.append(
                        {
                            "type": "CORE_RESOURCES_DESTROYED",
                            "victim": victim.player_id,
                            "amount": victim.core.resources,
                        }
                    )
                else:
                    cap = storage_capacity(winner.population)
                    room = max(0, cap - winner.core.resources)
                    avail = victim.core.resources
                    moved = min(room, avail)
                    winner.core.resources += moved
                    victim.stats["resources_lost"] += avail
                    events.append(
                        {
                            "type": "CORE_RESOURCES_CAPTURED",
                            "winner": winner.player_id,
                            "amount": moved,
                            "available": avail,
                            "destroyed": avail - moved,
                            "capacity": cap,
                        }
                    )
            victim.core = None
        return dead_cores

    @staticmethod
    def _add_damage(damage, atk_records, target_uid, attacker_uid, dmg):
        inner = damage.setdefault(target_uid, {})
        inner[attacker_uid] = inner.get(attacker_uid, 0) + dmg
        atk_records.append((target_uid, attacker_uid, dmg))

    def _owner_of(self, actives, uid):
        for p in actives:
            if uid in p.units or (p.core is not None and p.core.uid == uid):
                return p
        return None

    # =====================================================================
    # Step 10: Core SELF_DESTRUCT（战斗后幸存者）
    # =====================================================================
    def _step10_core_self_destruct(self, actives, plans, dead_cores, events):
        dead_ids = {p.player_id for p in dead_cores}
        for p in actives:
            if p.player_id in dead_ids or p.core is None:
                continue
            plan = plans.get(p.player_id)
            if not plan or not plan["core"] or plan["core"][0] != "SELF_DESTRUCT":
                continue
            core = p.core
            # Core self-destruction removes the whole fleet, just like combat
            # destruction.  Keeping these entries after ``core = None`` would
            # create ghost units that survive forever and block respawn.
            for u in p.units.values():
                if u.carries_beacon:
                    u.carries_beacon = False
                    self._drop_beacon_at(u.pos)
                if u.cargo > 0:
                    self._drop_cargo(u)
            if core.carries_beacon:
                core.carries_beacon = False
                self._drop_beacon_at(core.pos)
            p.respawn_count += 1
            p.stats["units_lost"] += p.population
            events.append(
                {"type": "CORE_DESTROYED", "player": p.player_id, "reason": "SELF_DESTRUCT"}
            )
            p.units.clear()
            p.core = None
            # v0.14 respawn is immediate whenever a legal placement exists.
            dead_cores.append(p)

    # =====================================================================
    # Step 11: Unit HEAL（战斗后，UUID 顺序）
    # =====================================================================
    def _step11_unit_heal(self, actives, plans, events):
        for p in actives:
            core = p.core
            if core is None or core.migration is not None:
                continue
            plan = plans.get(p.player_id)
            if not plan:
                continue
            healers = [
                u
                for uid, (atype, _a) in plan["units"].items()
                if atype == "HEAL" and (u := p.units.get(uid)) is not None and u.pos == core.pos
            ]
            healers.sort(key=lambda u: u.uid)
            for u in healers:
                max_hp = UNIT_STATS[u.utype]["hp"]
                while u.hp < max_hp and core.resources > 0:
                    u.hp += 1
                    core.resources -= 1
                    p.stats["heal_cost"] += 1
                    events.append({"type": "UNIT_HEALED", "unit": u.uid})

    # =====================================================================
    # Step 12: Core 动作（HEAL / REPAIR_SHIELD / SPAWN）
    # =====================================================================
    def _step12_core_actions(self, actives, plans, events):
        for p in sorted(actives, key=lambda p: p.player_id):
            core = p.core
            if core is None or core.migration is not None:
                continue
            plan = plans.get(p.player_id)
            if not plan or not plan["core"]:
                continue
            atype, args = plan["core"]
            if atype == "HEAL":
                while core.hp < CORE_HP and core.resources > 0:
                    core.hp += 1
                    core.resources -= 1
                    p.stats["heal_cost"] += 1
            elif atype == "REPAIR_SHIELD":
                cap = (
                    CORE_SHIELD_CAP_BEACON if self._player_has_beacon(p.player_id) else CORE_SHIELD
                )
                if core.shield < cap and core.resources >= 1:
                    core.shield += 1
                    core.resources -= 1
                    p.stats["repair_cost"] += 1
            elif atype == "SPAWN":
                utype = args["unit_type"]
                # 动态单位价格（rules v0.14）：N = 当前存活单位数
                # （同 Tick 自毁/战斗死亡已先结算）
                cost = unit_cost(UNIT_STATS[utype]["cost"], p.population)
                if core.resources < cost:
                    events.append(
                        {
                            "type": "SPAWN_FAILED",
                            "player": p.player_id,
                            "reason": "INSUFFICIENT_RESOURCES",
                            "required": cost,
                        }
                    )
                    continue
                same = [u for u in p.units.values() if u.pos == core.pos]
                if len(same) >= 1:
                    events.append(
                        {"type": "SPAWN_FAILED", "player": p.player_id, "reason": "CELL_UNIT_LIMIT"}
                    )
                    continue
                u = Unit(p.player_id, utype, core.pos)
                p.units[u.uid] = u
                core.resources -= cost
                p.stats["spawn_cost"] += cost
                events.append(
                    {
                        "type": "UNIT_SPAWNED",
                        "player": p.player_id,
                        "unit": u.uid,
                        "utype": utype,
                        "cost": cost,
                    }
                )

    # =====================================================================
    # Step 13: 重生
    # =====================================================================
    def _step13_respawn(self, players, dead_cores, events, tick):
        dead_ids = {p.player_id for p in dead_cores}
        candidates = [
            p
            for p in players.values()
            if p.core is None and (p.player_id in dead_ids or p.respawning)
        ]
        for p in sorted(candidates, key=lambda p: p.player_id):
            if p.core is not None:
                continue
            pos = self._find_spawn(players, p.player_id)
            if pos is None:
                p.respawning = True
                events.append({"type": "RESPAWN_FAILED", "player": p.player_id})
                continue
            p.respawning = False
            core = Core(p.player_id, pos)
            p.core = core
            w = Unit(p.player_id, "WORKER", pos)
            p.units[w.uid] = w
            events.append({"type": "CORE_RESPAWNED", "player": p.player_id, "at": pos})

    def _find_spawn(self, players, me, center=(0, 0), fixed=False):
        """在距离最近活 Core 20-30 曼哈顿的环带内找合法位置（优先实体少）。

        center: 出生基准点（无其他玩家时使用；默认 [0,0]，线上复现场景
        传远离原点的坐标）。
        fixed: True 时强制以 center 为基准（忽略其他玩家位置）——
        用于预发育档案：老玩家固定在各自出生中心，不会被"距最近
        活 Core 20-30 环"拉回同一区域（否则全员挤成一团）。
        """
        others = [p.core for p in players.values() if p.core is not None and p.player_id != me]
        if fixed:
            base = center
        else:
            base = min(others, key=lambda c: self._mdist(c.pos, center)).pos if others else center
        candidates = set()
        for d in range(20, 31):
            for x in range(-d, d + 1):
                candidates.add((base[0] + x, base[1] + (d - abs(x))))
                candidates.add((base[0] + x, base[1] - (d - abs(x))))
        best = None
        best_score = None
        for cand in candidates:
            if not self._valid_spawn_cell(players, me, cand):
                continue
            # 优先实体少 + 地形开阔（5×5 窗口障碍少），避免出生在死角
            score = (
                self._nearby_entities(players, cand, 5),
                self._openness(cand),
                cand[0],
                cand[1],
            )
            if best_score is None or score < best_score:
                best_score = score
                best = cand
        return best

    def _openness(self, cand):
        """cand 周围 5×5 窗口内的障碍格数（少 = 开阔）。"""
        x, y = cand
        n = 0
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                if self.world.is_obstacle(x + dx, y + dy):
                    n += 1
        return n

    def _valid_spawn_cell(self, players, me, cand):
        x, y = cand
        if not self.world.in_bounds(x, y):
            return False
        if self.world.terrain_kind(x, y) != EMPTY:
            return False
        n = 0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (
                self.world.in_bounds(x + dx, y + dy)
                and self.world.terrain_kind(x + dx, y + dy) != OBSTACLE
            ):
                n += 1
        if n < 2:
            return False
        for p in players.values():
            if p.core is not None and p.player_id != me and self._mdist(p.core.pos, cand) < 20:
                return False
        for p in players.values():
            if p.core is not None and p.core.pos == cand:
                return False
            for u in p.units.values():
                if u.pos == cand:
                    return False
        return True

    def _nearby_entities(self, players, pos, radius):
        n = 0
        for p in players.values():
            if p.core is not None and self._mdist(p.core.pos, pos) <= radius:
                n += 1
            for u in p.units.values():
                if self._mdist(u.pos, pos) <= radius:
                    n += 1
        return n

    # =====================================================================
    # 工具
    # =====================================================================
    def _remove_unit(self, p, u, events, reason):
        """移除单位：掉落 cargo 与 Beacon。"""
        if u.cargo > 0:
            self._drop_cargo(u)
        if u.carries_beacon:
            u.carries_beacon = False
            self._drop_beacon_at(u.pos)
        del p.units[u.uid]
        p.stats["units_lost"] += 1
        events.append(
            {"type": "UNIT_REMOVED", "unit": u.uid, "reason": reason, "player": p.player_id}
        )

    def _drop_cargo(self, u):
        x, y = u.pos
        self.cargo[(x, y)] = self.cargo.get((x, y), 0) + u.cargo
        u.cargo = 0

    @staticmethod
    def _mdist(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _passable(self, obj, dest):
        x, y = dest
        kind = self.world.terrain_kind(x, y)
        if kind == OBSTACLE:
            return False
        if isinstance(obj, Core):
            # 迁移中的 Core 只能进 EMPTY：资源点/货堆格不可入
            if (x, y) in self.cargo:
                return False
            return kind == EMPTY
        return kind in (EMPTY, RESOURCE)

    def _occupied_by_core(self, players, x, y):
        return any(p.core is not None and p.core.pos == (x, y) for p in players.values())
