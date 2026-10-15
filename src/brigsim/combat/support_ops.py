"""C7-a2：保障实体机动系统（抢救/器材补给/弹药补给车的物理业务链）。

仅对 SustainmentSpec.support_echelon.depot 声明的方别生效（原型只有红方）；
未声明方别（蓝方/旧想定）step() 直接返回，沿用 a1 时间抽象，保证零漂移。

四类业务（每 tick 在地面机动段之后推进）：
1. 装甲抢救车（arv）：空闲时认领距自己最近的待拖 M 级受损单位 → 驶向受损点
   挂钩 → 以折减车速拖回营中继抢修点；途中 arv 被毁而受损车幸存则重新排队，
   受损车战毁则释放 arv；
2. 器材补给车（supply_veh）：旅器材库按 wave_interval_ticks 节拍产出波次，
   空车出库→营抢修点交割（deliver_parts 分入携行/运行档并唤醒缺货请领）→
   返库；途中被击毁本波器材损失（mark_wave_lost），下一波次照常派出；
3. 弹药补给车（ammo_veh）：同节拍库→站往返，抵站后向分发半径内部队补弹
   （间瞄/导弹/防空，补至初始基数），统计补弹发数；
4. 自行后送单位进站检测与待拖/待进站/拖救途中死亡清理。

DES 同步约定：所有唤醒 SustainmentModel 的动作都经其 _schedule_at_next_tick
落在下一 tick（repair.py 内部实现），本系统只读写 SimState 列存与调接口，
不倒拨 SimPy 时钟。
"""

from __future__ import annotations

import numpy as np

RED, BLUE = 0, 1
_SIDE_NAMES = {RED: "red", BLUE: "blue"}

_HOOK_RADIUS_M = 200.0      # 抵受损点此距离内挂钩
_STATION_RADIUS_M = 250.0   # 抵营抢修点此距离视为到站点
_TOW_GAP_M = 50.0           # 被拖车辆在抢救车后方的跟随间距（可视化牵引线）
_AMMO_ROLES = ("artillery", "missile", "air_defense")


class SupportOps:
    """旅器材三档储备的地面实体机动层（持内核引用，无自有随机源）。"""

    def __init__(self, kernel) -> None:
        self.k = kernel
        self.s = kernel.state
        self.sustain = kernel.sustain
        self.sides: set[int] = set(self.sustain.ech_sides)
        self.active = bool(self.sides)
        if not self.active:
            return
        ech = self.sustain.echelon
        s = self.s
        # 保障实体索引（启动时固定，alive 每 tick 动态判断）
        self.arvs: dict[int, list[int]] = {side: [] for side in self.sides}
        self.supply_vehs: dict[int, list[int]] = {side: [] for side in self.sides}
        self.ammo_vehs: dict[int, list[int]] = {side: [] for side in self.sides}
        for i in range(s.n):
            side = int(s.side[i])
            if side not in self.sides:
                continue
            t = str(s.type_names[i])
            if t == "arv":
                self.arvs[side].append(i)
            elif t == "supply_veh":
                self.supply_vehs[side].append(i)
            elif t == "ammo_veh":
                self.ammo_vehs[side].append(i)
        # 旅器材库（后留档接入点）
        self.depots = {
            side: tuple(float(v) for v in ech.depot[_SIDE_NAMES[side]])
            for side in self.sides
        }
        # 拖救任务：arv_idx -> 受损单位 idx；outbound=赶赴受损点，towing=挂钩回拖
        self.arv_outbound: dict[int, int] = {}
        self.arv_towing: dict[int, int] = {}
        # 器材波次：veh_idx -> [phase("out"/"back"), cargo {part: qty}]
        self.sup_tasks: dict[int, list] = {}
        # 弹药车往返：veh_idx -> phase
        self.ammo_tasks: dict[int, str] = {}
        # 下一波次到期 tick（库按节拍产出，车不空闲时波次排队等候）
        self.next_wave = {side: int(ech.wave_interval_ticks) for side in self.sides}
        self.next_ammo = {side: int(ech.wave_interval_ticks) for side in self.sides}
        # 弹药初始基数（补弹上限）
        self.ammo_max = np.asarray(s.ammo).copy()

    # ---- 每 tick 入口（内核机动段之后调用）----

    def step(self) -> None:
        if not self.active:
            return
        self._cleanup_dead()
        self._step_recovery()
        self._step_station_arrivals()
        self._step_supply_waves()
        self._step_ammo_runs()

    # ---- 1. 抢救车拖救链 ----

    def _step_recovery(self) -> None:
        s, sus = self.s, self.sustain
        for side in self.sides:
            station = self.k.stations.get(side)
            if station is None:
                continue
            # 空闲 arv 认领最近待拖单位（一辆受损车只派一辆 arv）
            busy = set(self.arv_outbound) | set(self.arv_towing)
            free = [a for a in self.arvs[side] if s.alive[a] and a not in busy]
            if free:
                # 已在赶赴途中（outbound）的目标仍留在 pending_tow（挂钩时才移除），
                # 必须排除，避免第二辆 arv 对同一受损车重复认领
                claimed = set(self.arv_outbound.values())
                assigned: set[int] = set()
                for a in free:
                    pending = [
                        i for i in sus.pending_tow[side]
                        if s.alive[i] and i not in assigned and i not in claimed
                    ]
                    if not pending:
                        break
                    d = np.hypot(
                        s.x[pending] - s.x[a], s.y[pending] - s.y[a]
                    )
                    tgt = int(pending[int(np.argmin(d))])
                    self.arv_outbound[a] = tgt
                    assigned.add(tgt)
            # 赶赴受损点 → 挂钩
            for a in list(self.arv_outbound):
                tgt = self.arv_outbound[a]
                if not s.alive[a] or not s.alive[tgt]:
                    continue  # _cleanup_dead 已处理
                if self._move_to(a, s.x[tgt], s.y[tgt]) <= _HOOK_RADIUS_M:
                    sus.hook_tow(tgt)
                    self._effect("tow_hook", side, s.x[tgt], s.y[tgt],
                                 f"抢救车挂钩 {s.unit_ids[tgt]}（M级失动）")
                    self.arv_towing[a] = tgt
                    del self.arv_outbound[a]
            # 挂钩拖回营抢修点 → 卸车唤醒修复进程
            for a in list(self.arv_towing):
                tgt = self.arv_towing[a]
                if not s.alive[a] or not s.alive[tgt]:
                    continue
                d = self._move_to(
                    a, station[0], station[1],
                    speed_scale=self.sustain.echelon.tow_speed_scale,
                )
                # 被拖单位跟随抢救车（沿其朝向后方 _TOW_GAP_M）
                h = float(s.heading[a])
                s.x[tgt] = float(np.clip(s.x[a] - _TOW_GAP_M * np.cos(h),
                                         0.0, self.k.bound[0]))
                s.y[tgt] = float(np.clip(s.y[a] - _TOW_GAP_M * np.sin(h),
                                         0.0, self.k.bound[1]))
                s.heading[tgt] = h
                if d <= _STATION_RADIUS_M:
                    sus.complete_tow(tgt)
                    self._effect("tow_done", side, station[0], station[1],
                                 f"{s.unit_ids[tgt]} 拖抵营抢修点")
                    del self.arv_towing[a]

    def _step_station_arrivals(self) -> None:
        """F/重度受损单位自驶进站检测。"""
        s, sus = self.s, self.sustain
        for side in self.sides:
            station = self.k.stations.get(side)
            if station is None:
                continue
            for i in list(sus.awaiting_station[side]):
                if not s.alive[i]:
                    continue
                d = np.hypot(s.x[i] - station[0], s.y[i] - station[1])
                if d <= _STATION_RADIUS_M:
                    sus.complete_station(i)

    # ---- 2. 器材前送波次 ----

    def _step_supply_waves(self) -> None:
        s, sus, ech = self.s, self.sustain, self.sustain.echelon
        tick = int(s.tick)
        for side in self.sides:
            station = self.k.stations.get(side)
            if station is None:
                continue
            depot = self.depots[side]
            # 波次到期且有空闲补给车 → 派车（无车则波次等候，不丢弃）
            busy = set(self.sup_tasks)
            if tick >= self.next_wave[side]:
                free = [v for v in self.supply_vehs[side]
                        if s.alive[v] and v not in busy]
                if free:
                    v = free[0]
                    cargo = {pid: int(ech.wave_qty)
                             for pid in self.sustain.part_catalog}
                    self.sup_tasks[v] = ["out", cargo]
                    self.next_wave[side] = tick + int(ech.wave_interval_ticks)
            for v in list(self.sup_tasks):
                if not s.alive[v]:
                    continue  # _cleanup_dead 已处理
                phase, cargo = self.sup_tasks[v]
                if phase == "out":
                    if self._move_to(v, station[0], station[1]) <= _STATION_RADIUS_M:
                        sus.deliver_parts(side, cargo)
                        n = sum(cargo.values())
                        self._effect("supply_wave", side, station[0], station[1],
                                     f"器材补给车抵站，前送器材 {n} 件入两档储备")
                        self.sup_tasks[v][0] = "back"
                else:
                    if self._move_to(v, depot[0], depot[1]) <= _STATION_RADIUS_M:
                        del self.sup_tasks[v]

    # ---- 3. 弹药补给车往返补弹 ----

    def _step_ammo_runs(self) -> None:
        s, ech, tick = self.s, self.sustain.echelon, int(self.s.tick)
        for side in self.sides:
            station = self.k.stations.get(side)
            if station is None:
                continue
            depot = self.depots[side]
            busy = set(self.ammo_tasks)
            if tick >= self.next_ammo[side]:
                free = [v for v in self.ammo_vehs[side]
                        if s.alive[v] and v not in busy]
                if free:
                    self.ammo_tasks[free[0]] = "out"
                    self.next_ammo[side] = tick + int(ech.wave_interval_ticks)
            for v in list(self.ammo_tasks):
                if not s.alive[v]:
                    continue
                if self.ammo_tasks[v] == "out":
                    if self._move_to(v, station[0], station[1]) <= _STATION_RADIUS_M:
                        n_units, rounds = self._distribute_ammo(side, station)
                        if rounds > 0:
                            self.sustain.ammo_delivered[side] += rounds
                            self._effect(
                                "ammo_deliver", side, station[0], station[1],
                                f"弹药补给车抵站，为 {n_units} 个火力单位补弹 {rounds} 发",
                            )
                        self.ammo_tasks[v] = "back"
                else:
                    if self._move_to(v, depot[0], depot[1]) <= _STATION_RADIUS_M:
                        del self.ammo_tasks[v]

    def _distribute_ammo(self, side: int, station) -> tuple[int, int]:
        """给营抢修点分发半径内的间瞄/导弹/防空单位补弹至初始基数。"""
        s, ech = self.s, self.sustain.echelon
        roles = np.asarray(s.role, dtype=object)
        mask = (
            s.alive & (s.side == side) & (s.ammo >= 0)
            & np.isin(roles, list(_AMMO_ROLES))
        )
        idx = np.where(mask)[0]
        if idx.size == 0:
            return 0, 0
        d = np.hypot(s.x[idx] - station[0], s.y[idx] - station[1])
        idx = idx[d <= ech.ammo_dist_radius_m]
        rounds = 0
        for i in idx:
            add = int(self.ammo_max[i]) - int(s.ammo[i])
            if add > 0:
                capped = min(add, int(ech.ammo_wave_qty))
                s.ammo[i] += capped
                rounds += capped
        return int(idx.size), int(rounds)

    # ---- 4. 死亡清理 ----

    def _cleanup_dead(self) -> None:
        s, sus = self.s, self.sustain
        # 抢救车：出库途中被毁（受损车仍在待拖队列，自然等下一辆）；
        # 赶赴目标途中目标战毁（撤销任务并终止其修复等待）；
        # 拖救途中被毁：受损车幸存→重新排队，受损车同毁→终止等待
        for a in list(self.arv_outbound):
            tgt = self.arv_outbound[a]
            if not s.alive[a]:
                del self.arv_outbound[a]
            elif not s.alive[tgt]:
                sus.fail_recovery(tgt)
                del self.arv_outbound[a]
        for a in list(self.arv_towing):
            tgt = self.arv_towing[a]
            if not s.alive[a]:
                if s.alive[tgt]:
                    sus.requeue_tow(tgt)
                else:
                    sus.fail_recovery(tgt)
                del self.arv_towing[a]
            elif not s.alive[tgt]:
                sus.fail_recovery(tgt)
                del self.arv_towing[a]
        # 器材车：前送途中被毁本波器材损失
        for v in list(self.sup_tasks):
            if not s.alive[v]:
                phase, _cargo = self.sup_tasks[v]
                side = int(s.side[v])
                if phase == "out":
                    sus.mark_wave_lost(side)
                    self._effect("supply_lost", side, s.x[v], s.y[v],
                                 "器材补给车前送途中被击毁，本波器材损失")
                del self.sup_tasks[v]
        # 弹药车：任务作废（弹药伴随车辆损失，不计入补弹统计）
        for v in list(self.ammo_tasks):
            if not s.alive[v]:
                del self.ammo_tasks[v]
        # 待拖/待进站等待中的单位战毁
        for side in self.sides:
            for i in list(sus.pending_tow[side]):
                if not s.alive[i]:
                    sus.fail_recovery(i)
            for i in list(sus.awaiting_station[side]):
                if not s.alive[i]:
                    sus.fail_station(i)

    # ---- 机动原语 ----

    def _move_to(self, i: int, tx: float, ty: float, *,
                 speed_scale: float = 1.0) -> float:
        """单位 i 朝 (tx,ty) 移动一个 tick，返回移动后到目标的距离（米）。

        车速经地形通行/体系减速/障碍区折减（保障车队规避障碍），位置不越界。
        """
        k, s, sysm = self.k, self.s, self.k.sys
        dx, dy = tx - s.x[i], ty - s.y[i]
        d = float(np.hypot(dx, dy))
        if d <= 0.0:
            return 0.0
        s.heading[i] = np.arctan2(dy, dx)
        factor = float(k.terrain.move_factor(s.x[i], s.y[i]))
        factor *= float(sysm.ground_move)
        obs = sysm.obstacle_move_factor(float(s.x[i]), float(s.y[i]))
        if obs is not None:
            factor *= obs
        step = min(float(s.speed[i]) * factor * speed_scale, d)
        s.x[i] = float(np.clip(s.x[i] + step * dx / d, 0.0, k.bound[0]))
        s.y[i] = float(np.clip(s.y[i] + step * dy / d, 0.0, k.bound[1]))
        return float(np.hypot(tx - s.x[i], ty - s.y[i]))

    def _effect(self, kind: str, side: int, x: float, y: float, detail: str) -> None:
        self.k.log.effect(int(self.s.tick), kind, int(side), float(x), float(y), detail)
