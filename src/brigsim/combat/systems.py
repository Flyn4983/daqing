"""C7-a0：合成兵种战斗支援系统（Mesa 内核侧的"体系对抗"层）。

普通直瞄交火仍由 PlatformAgent 完成；本模块实现普通智能体无法表达的
合成兵种机制，全部作用于 SimState 列存（Warp 侧后续按同样语义移植）：

1. 间瞄炮兵状态机：展开(行军→战斗)→共享航迹选目标→呼叫延迟→面杀伤弹着；
   弹药基数有限；发射即可能被反炮兵雷达定位。
2. 超视距导弹（BLOS）：必须有侦察/无人机/雷达来源的目标指引（目标指示链）。
3. 空中层：侦察无人机（开视野/校射）、巡飞弹（高价值节点精确猎杀）、
   武装直升机（对装甲多发射击）；按"起飞-进入-战位-返航"时序推进。
4. 防空：防空导弹/高炮在作用半径内拦截空中目标；有对空雷达引导时拦截包线完整，
   无引导或雷达被干扰时显著降级。
5. 电磁：雷达间歇开机（辐射被反辐射猎杀）、雷达干扰致盲、
   通信干扰断链（共享航迹失效、无人机失控）。
6. C2 光环：指挥车覆盖区内己方感知/毁伤概率加成。
7. 烟幕/障碍/假目标：烟幕遮蔽光学不遮雷达；障碍区须工兵破障；
   未被识破的假目标消耗精确弹药。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import weather

RED, BLUE = 0, 1

# BLOS（红箭-10 一类）接受的目标指示来源
DESIGNATION_SOURCES = {"recon", "radar_air", "radar_cb", "uav"}

# 巡飞弹高价值目标选择优先级（被反辐射定位的开机雷达在选择时再插队）
LOITER_PRIORITY = [
    "radar_air", "radar_cb", "c2", "artillery", "missile",
    "air_defense", "ew", "support", "engineer", "recon", "assault",
]

# 防空拦截基础概率：按防空系统射程档（远=导弹 / 近=高炮）× 空中目标类型
_AD_PK = {
    "long": {"helo": 0.85, "loitering": 0.55, "uav": 0.35},
    "short": {"helo": 0.35, "loitering": 0.75, "uav": 0.80},
}
_AD_GUIDED_SCALE = 0.40   # 无对空雷达引导时的拦截率乘子
_AD_JAMMED_SCALE = 0.50   # 火控雷达被干扰时
_RECOG_RANGE = 600.0      # 侦察平台抵近识破假目标的距离
SMOKE_LAY_RANGE = 1500.0  # 发烟车距屏烟点的最大远程施放距离（米）


@dataclass
class AirAsset:
    """一个空中架次的运行时状态。"""

    aid: str
    side: int
    kind: str
    launch_tick: int
    waypoints: list[tuple[float, float]]
    alt: float
    loiter_total: int
    seek: float
    weapon_pk: float
    shots_left: int
    sensor_range: float
    sensor_kind: str
    label: str
    ammo_class: str = "air_missile"  # C7-a1：部位权重表弹药类别
    state: str = "pending"      # inbound/station/loitering/exiting/killed/expended
    x: float = 0.0
    y: float = 0.0
    wp_idx: int = 0
    loiter_left: int = 0
    extension: int = 0          # 巡飞弹未发现目标时的延长搜索次数
    exit_left: int = 0
    spawn_x: float = 0.0

    @property
    def active(self) -> bool:
        return self.state in ("inbound", "station", "loitering", "exiting")


@dataclass
class FireMission:
    """一次已发射、等待弹着的火力任务。"""

    shooter: int
    side: int
    tx: float
    ty: float
    impact_tick: int
    splash: float
    pk: float
    weapon: str
    target_idx: int | None = None  # BLOS 锁定单位 idx；间瞄为 None（打点目标）


@dataclass
class _Obstacle:
    kind: str
    cx: float
    cy: float
    hx: float
    hy: float
    breach_ticks: int
    progress: int = 0
    breached: bool = False

    def contains(self, x: float, y: float) -> bool:
        return abs(x - self.cx) <= self.hx and abs(y - self.cy) <= self.hy


@dataclass
class _Smoke:
    vehicle_idx: int
    x: float
    y: float
    radius: float
    start: int
    duration: int
    activated_at: int | None = None  # 实际到位激活 tick（起烟时刻起持续 duration）


class BattleSystems:
    """每 tick 由 MesaKernel 在各作战阶段间调用。"""

    def __init__(self, kernel) -> None:
        self.k = kernel
        self.s = kernel.state
        self.sc = kernel.scenario
        self.rng = kernel.rng
        n = self.s.n
        env = self.sc.environment
        self.factors = weather.sensor_factors(env)
        self.ground_move = weather.ground_move_factor(env)
        self.air_grounded = weather.air_grounded(env)

        # 本 tick 体系乘子（pre_combat 刷新）
        self.det_mult = np.ones(n)
        self.pk_mult = np.ones(n)
        self.jammed_radar = np.zeros(n, dtype=bool)
        self.comms_denied = np.zeros(n, dtype=bool)
        self.guided = {RED: False, BLUE: False}
        self.active_smoke: list[_Smoke] = []

        self.obstacles = [
            _Obstacle(o.kind, o.center[0], o.center[1], o.size[0] / 2, o.size[1] / 2,
                      o.breach_ticks)
            for o in self.sc.battlefield.obstacles
        ]
        id_to_idx = {str(uid): i for i, uid in enumerate(self.s.unit_ids)}
        self.smoke_orders = [
            _Smoke(
                vehicle_idx=id_to_idx.get(o.unit_id, -1),
                x=o.at[0], y=o.at[1], radius=o.radius_m,
                start=o.start_tick, duration=o.duration_ticks,
            )
            for o in self.sc.battlefield.smoke_orders
        ]

        self.air_pending: list[AirAsset] = [
            AirAsset(
                aid=f"{p.side[:1]}{p.kind[:2]}{i:02d}",
                side=RED if p.side == "red" else BLUE,
                kind=p.kind,
                launch_tick=p.launch_tick,
                waypoints=list(p.waypoints),
                alt=p.altitude_m,
                loiter_total=p.loiter_ticks,
                seek=p.seek_radius_m,
                weapon_pk=p.weapon_pk,
                shots_left=p.weapon_count,
                sensor_range=p.sensor_range_m,
                sensor_kind=p.sensor_kind,
                label=p.label or p.kind,
            )
            for i, p in enumerate(self.sc.air_packages)
        ]
        self.air: list[AirAsset] = []
        self.missions: list[FireMission] = []

        # 可观测统计（测试/大屏用）
        self.intercepts: list[dict] = []
        self.air_kills: list[dict] = []

    # ------------------------------------------------------------------
    # 阶段 0：体系态势刷新（在 ground sense 之前）
    # ------------------------------------------------------------------

    def pre_combat(self) -> None:
        s, t = self.s, self.s.tick
        # 阵地展开倒计时（生存状态下）
        work = (s.emplace_timer > 0) & s.alive
        s.emplace_timer[work] -= 1
        # 雷达间歇开机
        radars = np.isin(s.role, ["radar_air", "radar_cb"]) & s.alive
        self.s.radar_on[:] = False
        self.s.radar_on[radars] = self.rng.random(int(radars.sum())) < s.emission_duty[radars]

        self.det_mult[:] = 1.0
        self.pk_mult[:] = 1.0
        self.jammed_radar[:] = False
        self.comms_denied[:] = False

        ew_idx = np.where((s.role == "ew") & s.alive)[0]
        c2_idx = np.where((s.role == "c2") & s.alive)[0]
        radar_idx = np.where(np.isin(s.role, ["radar_air", "radar_cb"]) & s.alive)[0]

        # 电磁压制：电抗车对作用半径内的敌雷达/敌地面单位生效
        for j in ew_idx:
            side = int(s.side[j])
            enemy = 1 - side
            hit = self._units_in_radius(j, enemy, s.coverage[j])
            rolls = self.rng.random(len(hit)) < s.jam_strength[j]
            for u, ok in zip(hit, rolls):
                if not ok:
                    continue
                mode = int(s.jam_mode[j])
                # 雷达类干扰同时致盲警戒/反炮兵雷达与防空火控雷达
                if mode in (0, 2) and s.role[u] in (
                    "radar_air", "radar_cb", "air_defense",
                ):
                    self.jammed_radar[u] = True
                if mode in (1, 2):
                    self.comms_denied[u] = True

        # 通信被压制的 C2 节点本 tick 不提供光环
        c2_alive = [j for j in c2_idx if not self.comms_denied[j]]
        for j in c2_alive:
            side = int(s.side[j])
            bonus = float(s.aura_bonus[j])
            if bonus <= 0:
                continue
            for u in self._units_in_radius(j, side, s.coverage[j]):
                if not self.comms_denied[u]:
                    self.det_mult[u] *= 1.0 + bonus
                    self.pk_mult[u] *= 1.0 + bonus

        # 对空引导：本 tick 有未被致盲的对空雷达 → 该方防空有引导
        self.guided = {RED: False, BLUE: False}
        for j in radar_idx:
            if s.role[j] == "radar_air" and s.radar_on[j] and not self.jammed_radar[j]:
                self.guided[int(s.side[j])] = True

        # 烟幕：start_tick 为最早许可时刻；发烟车在屏烟点后方
        # SMOKE_LAY_RANGE 内即可远程发烟（不用开到烟幕中心），
        # 从起烟 tick 起持续 duration 个 tick
        self.active_smoke = []
        for z in self.smoke_orders:
            v = z.vehicle_idx
            if v < 0 or not s.alive[v] or t < z.start:
                continue
            if z.activated_at is None:
                if np.hypot(s.x[v] - z.x, s.y[v] - z.y) > SMOKE_LAY_RANGE:
                    continue
                z.activated_at = t
                self.k.log.effect(t, "smoke", int(s.side[v]), z.x, z.y,
                                  f"r={z.radius:.0f}")
            if t < z.activated_at + z.duration:
                self.active_smoke.append(z)

    def _units_in_radius(self, j: int, side: int, radius: float) -> np.ndarray:
        s = self.s
        idx = np.where(s.alive & (s.side == side))[0]
        d = np.hypot(s.x[idx] - s.x[j], s.y[idx] - s.y[j])
        return idx[d <= radius]

    def in_smoke(self, x: float, y: float) -> bool:
        return any((x - z.x) ** 2 + (y - z.y) ** 2 <= z.radius ** 2 for z in self.active_smoke)

    # ------------------------------------------------------------------
    # 共享航迹（带来源与假目标识别标记）
    # ------------------------------------------------------------------

    def add_track(self, side: int, target: int, ttl: int, source: str,
                  link_denied: bool = False) -> None:
        """登记/刷新一条共享航迹。

        link_denied=True 表示侦察单位自身处于敌通信干扰区内：只能本 tick 自用，
        航迹不进入数据链（exp=当前 tick，下一拍即过期）。
        """
        cur = self.k.tracks[side].get(int(target))
        info = {
            "exp": self.s.tick + (0 if link_denied else int(ttl)),
            "src": source,
            "recog": bool(cur["recog"]) if cur else False,
        }
        self.k.tracks[side][int(target)] = info

    def comms_denied_side_pos(self, side: int, x: float, y: float) -> bool:
        """该方在 (x,y) 处的空中/地面数据链是否被敌通信干扰压制。"""
        s = self.s
        for j in np.where((s.role == "ew") & s.alive & (s.side != side))[0]:
            if int(s.jam_mode[j]) in (1, 2) and s.jam_strength[j] > 0:
                if np.hypot(s.x[j] - x, s.y[j] - y) <= s.coverage[j]:
                    return True
        return False

    def mark_recog(self, side: int, target: int) -> None:
        cur = self.k.tracks[side].get(int(target))
        if cur is not None:
            cur["recog"] = True

    # ------------------------------------------------------------------
    # 阶段：空中层 + 防空 + 间瞄（在 ground move/直瞄之后调用）
    # ------------------------------------------------------------------

    def step_fires(self) -> None:
        self._spawn_air()
        self._update_air()
        self._air_intercept()
        self._launch_indirect()
        self._resolve_impacts()
        self._air_snapshot()

    # ---- 空中架次生命周期 ------------------------------------------------

    def _spawn_air(self) -> None:
        t = self.s.tick
        ready = [a for a in self.air_pending if a.launch_tick <= t]
        for a in ready:
            self.air_pending.remove(a)
            if self.air_grounded:
                a.launch_tick = t + 1  # 气象不允许起飞：顺延一个 tick
                self.air_pending.append(a)
                continue
            a.state = "inbound"
            a.wp_idx = 0
            a.spawn_x = -200.0 if a.side == RED else self.k.bound[0] + 200.0
            a.x, a.y = a.spawn_x, a.waypoints[0][1]
            self.air.append(a)

    def _move_toward(self, a: AirAsset, tx: float, ty: float, speed: float) -> bool:
        d = np.hypot(tx - a.x, ty - a.y)
        if d <= speed:
            a.x, a.y = tx, ty
            return True
        a.x += speed * (tx - a.x) / d
        a.y += speed * (ty - a.y) / d
        return False

    def _update_air(self) -> None:
        for a in self.air:
            if not a.active:
                continue
            if a.state == "inbound":
                speed = 900.0 if a.kind == "helo" else 550.0
                tgt = a.waypoints[a.wp_idx]
                if self._move_toward(a, tgt[0], tgt[1], speed):
                    a.wp_idx += 1
                    if a.wp_idx >= len(a.waypoints):
                        a.loiter_left = a.loiter_total
                        a.state = "station" if a.kind in ("uav", "helo") else "loitering"
            elif a.state in ("station", "loitering"):
                station = a.waypoints[-1]
                # 战位小幅盘旋
                a.x = station[0] + self.rng.uniform(-250, 250)
                a.y = station[1] + self.rng.uniform(-250, 250)
                if a.kind == "uav":
                    self._uav_scan(a)
                    a.loiter_left -= 1
                    if a.loiter_left <= 0:
                        a.state = "exiting"
                        a.exit_left = 2
                elif a.kind == "helo":
                    if a.shots_left > 0:
                        self._helo_strike(a)
                    else:
                        a.state = "exiting"
                        a.exit_left = 2
                else:  # loitering：巡弋期结束时猎杀
                    a.loiter_left -= 1
                    if a.loiter_left <= 0:
                        hit = self._loiter_strike(a)
                        if hit:
                            a.state = "exiting"
                            a.exit_left = 1
                        elif a.extension < 2:
                            a.extension += 1
                            a.loiter_left = 3
                        else:
                            a.state = "expended"  # 未找到目标，自毁/耗尽
            elif a.state == "exiting":
                self._move_toward(a, a.spawn_x, a.y, 900.0)
                a.exit_left -= 1
                if a.exit_left <= 0:
                    a.state = "expended"
        self.air = [a for a in self.air if a.state not in ("killed", "expended")]

    def _enemies_near(self, side: int, x: float, y: float, radius: float) -> np.ndarray:
        s = self.s
        idx = np.where(s.alive & (s.side != side))[0]
        d = np.hypot(s.x[idx] - x, s.y[idx] - y)
        return idx[d <= radius]

    def _uav_scan(self, a: AirAsset) -> None:
        s = self.s
        cands = self._enemies_near(a.side, a.x, a.y, a.sensor_range)
        if cands.size == 0:
            return
        f = self.factors[a.sensor_kind if a.sensor_kind in self.factors else "fused"]
        d = np.hypot(s.x[cands] - a.x, s.y[cands] - a.y)
        probs = 0.9 * f * (1.0 - 0.4 * d / a.sensor_range)
        probs[s.dug_in[cands]] *= 0.7
        found = self.rng.random(cands.size) < probs
        for t in cands[found]:
            self._share(a.side, int(t), 4, "uav")
        # 通信干扰区里失控概率
        if self.comms_denied_side_pos(a.side, a.x, a.y) and self.rng.random() < 0.35:
            self._kill_air(a, "通信干扰失控坠落", ew=True)

    def _helo_strike(self, a: AirAsset) -> None:
        s = self.s
        cands = self._enemies_near(a.side, a.x, a.y, a.seek)
        cands = cands[np.isin(s.role[cands], ["assault", "artillery", "support"])]
        if cands.size == 0:
            return
        d = np.hypot(s.x[cands] - a.x, s.y[cands] - a.y)
        t = int(cands[int(np.argmin(d))])
        f = self.factors["optical"]
        if self.rng.random() < a.weapon_pk * f:
            self._resolve(t, a.weapon_pk * f, a, "helo")
        a.shots_left -= 1

    def _loiter_strike(self, a: AirAsset) -> bool:
        s = self.s
        cands = self._enemies_near(a.side, a.x, a.y, a.seek)
        # 已被侦察识破的假目标不再诱骗巡飞弹
        trk = self.k.tracks[a.side]
        cands = np.array(
            [i for i in cands
             if not (s.decoy[i] and trk.get(int(i), {}).get("recog"))],
            dtype=int,
        )
        if cands.size == 0:
            return False
        # 开机雷达最优先（反辐射猎杀）；静默雷达无法被反辐射导引，价值排在
        # 突击车辆之后；假目标永远留到最后（被识破的假目标不会进入候选）
        def value(i: int) -> tuple:
            role_i = str(s.role[i])
            emitting = bool(s.radar_on[i]) and role_i in ("radar_air", "radar_cb")
            if emitting:
                return (0, 0, 0)
            if s.decoy[i]:
                return (2, 0, 0)
            if role_i in ("radar_air", "radar_cb"):
                rank = LOITER_PRIORITY.index("assault") + 1
            else:
                try:
                    rank = LOITER_PRIORITY.index(role_i)
                except ValueError:
                    rank = len(LOITER_PRIORITY)
            return (1, 0, rank)

        t = int(min(cands, key=value))
        self.k.log.effect(s.tick, "loiter_strike", a.side, s.x[t], s.y[t], a.aid)
        self._resolve(t, a.weapon_pk, a, "loitering")
        return True

    def _resolve(self, target: int, pk_eff: float, a: AirAsset, weapon: str) -> None:
        s = self.s
        killed, hit = self.k.damage_model.resolve_hit(
            a.aid, int(target), pk_eff, a.ammo_class, weapon, always_log=False,
        )
        if not hit:
            return  # resolve_hit 命中时已记 engagement，此处只补空中击杀统计
        self.air_kills.append({"tick": s.tick, "asset": a.aid, "target": str(s.unit_ids[target]),
                              "killed": killed})

    # ---- 防空拦截 ----------------------------------------------------------

    def _air_intercept(self) -> None:
        s = self.s
        ad_idx = np.where((s.role == "air_defense") & s.alive & (s.cooldown <= 0))[0]
        for j in ad_idx:
            if s.ammo[j] == 0:
                continue
            side = int(s.side[j])
            in_range = [
                a for a in self.air
                if a.active and a.side != side
                and np.hypot(a.x - s.x[j], a.y - s.y[j]) <= s.coverage[j]
            ]
            if not in_range:
                continue
            a = in_range[0]
            band = "long" if s.coverage[j] >= 4000 else "short"
            p = _AD_PK[band][a.kind]
            if not self.guided[int(s.side[j])]:
                p *= _AD_GUIDED_SCALE
            if self.jammed_radar[j]:
                p *= _AD_JAMMED_SCALE
            s.cooldown[j] = s.reload_ticks[j]
            if s.ammo[j] > 0:
                s.ammo[j] -= 1
            if self.rng.random() < p:
                self._kill_air(a, f"{s.unit_ids[j]} 拦截", ad=True)
                self.intercepts.append({"tick": s.tick, "unit": str(s.unit_ids[j]),
                                       "asset": a.aid})
                self.k.log.effect(s.tick, "intercept", int(s.side[j]), a.x, a.y, a.aid)

    def _kill_air(self, a: AirAsset, reason: str, *, ad: bool = False, ew: bool = False) -> None:
        a.state = "killed"
        self.k.log.effect(self.s.tick, "air_kill", 1 - a.side, a.x, a.y, reason)

    # ---- 间瞄炮兵 / BLOS ---------------------------------------------------

    def _launch_indirect(self) -> None:
        s = self.s
        shooters = np.where(
            s.alive
            & np.isin(s.fire_mode, ["indirect", "blos"])
            & (s.cooldown <= 0)
            & (s.ammo != 0)
            & (s.emplace_timer <= 0)
        )[0]
        for i in shooters:
            side = int(s.side[i])
            # 候选航迹：未被识破的假目标保留（假目标正是为了诱骗炮兵/导弹）
            cands = [
                j for j, info in self.k.tracks[side].items()
                if s.alive[j] and not info["recog"]
                and np.hypot(s.x[j] - s.x[i], s.y[j] - s.y[i]) <= s.weapon_range[i]
            ]
            if not cands:
                continue
            mode = str(s.fire_mode[i])
            if mode == "blos":
                designated = [j for j in cands
                              if self.k.tracks[side][j]["src"] in DESIGNATION_SOURCES]
                if not designated:
                    continue  # 超视距导弹无目标指示不发射
                t = self._nearest(i, designated)
                tx, ty, target_idx = float(s.x[t]), float(s.y[t]), int(t)
                weapon = "blos"
            else:
                t = self._nearest(i, cands)
                tx, ty, target_idx = float(s.x[t]), float(s.y[t]), None
                weapon = "indirect"
            delay = max(1, int(s.fire_delay[i]))
            self.missions.append(FireMission(
                shooter=int(i), side=side, tx=tx, ty=ty,
                impact_tick=s.tick + delay, splash=float(s.splash[i]),
                pk=float(s.pk[i]), weapon=weapon, target_idx=target_idx,
            ))
            s.cooldown[i] = s.reload_ticks[i]
            if s.ammo[i] > 0:
                s.ammo[i] -= 1
            self.k.log.effect(s.tick, "fire_launch", side, float(s.x[i]), float(s.y[i]),
                              str(s.unit_ids[i]))
            # 反炮兵雷达：捕获发射阵位
            self._counter_battery(int(i), side, float(s.x[i]), float(s.y[i]))

    def _nearest(self, i: int, cands: list[int]) -> int:
        s = self.s
        d = np.hypot(s.x[cands] - s.x[i], s.y[cands] - s.y[i])
        return int(cands[int(np.argmin(d))])

    def _counter_battery(self, shooter: int, fired_side: int, x: float, y: float) -> None:
        s = self.s
        enemy = 1 - fired_side
        for j in np.where((s.role == "radar_cb") & s.alive & (s.side == enemy))[0]:
            if not s.radar_on[j] or self.jammed_radar[j]:
                continue
            if np.hypot(s.x[j] - x, s.y[j] - y) <= s.coverage[j]:
                self._share(enemy, shooter, 4, "radar_cb")

    def _share(self, side: int, target: int, ttl: int, src: str) -> None:
        info = self.k.tracks[side].get(int(target))
        self.k.tracks[side][int(target)] = {
            "exp": self.s.tick + ttl,
            "src": src,
            "recog": bool(info["recog"]) if info else False,
        }

    def _resolve_impacts(self) -> None:
        s = self.s
        due = [m for m in self.missions if m.impact_tick <= s.tick]
        self.missions = [m for m in self.missions if m.impact_tick > s.tick]
        for m in due:
            if not s.alive[m.shooter]:
                continue
            if m.target_idx is not None:
                # BLOS：锁定单位当前位置（导弹追踪），目标死亡则脱靶
                t = m.target_idx
                if not s.alive[t]:
                    self.k.log.effect(s.tick, "fire_impact", m.side, m.tx, m.ty, "miss")
                    continue
                self._impact_roll(m, np.array([t]), m.tx, m.ty)
            else:
                victims = np.where(s.alive & (s.side != m.side))[0]
                d = np.hypot(s.x[victims] - m.tx, s.y[victims] - m.ty)
                self._impact_roll(m, victims[d <= m.splash], m.tx, m.ty)

    def _impact_roll(self, m: FireMission, victims: np.ndarray, x: float, y: float) -> None:
        s = self.s
        self.k.log.effect(s.tick, "fire_impact", m.side, x, y,
                          f"r={m.splash:.0f},n={victims.size}")
        for t in victims:
            pk = m.pk * (0.5 if s.dug_in[t] else 1.0)
            ammo_class = "atgm" if m.weapon == "blos" else "indirect"
            self.k.damage_model.resolve_hit(
                str(s.unit_ids[m.shooter]), int(t), pk, ammo_class, m.weapon,
                always_log=False,
            )

    def _air_snapshot(self) -> None:
        for a in self.air:
            self.k.log.air_row(self.s.tick, a)

    # ------------------------------------------------------------------
    # 障碍与工兵
    # ------------------------------------------------------------------

    def obstacle_at(self, x: float, y: float) -> _Obstacle | None:
        for o in self.obstacles:
            if not o.breached and o.contains(x, y):
                return o
        return None

    def obstacle_move_factor(self, x: float, y: float) -> float | None:
        o = self.obstacle_at(x, y)
        if o is None:
            return None
        return {"minefield": 0.2, "dragons_teeth": 0.15, "anti_tank_ditch": 0.02}[o.kind]

    def mine_damage(self, i: int, x: float, y: float) -> None:
        """移动后结算：未开辟雷场内的触雷概率损伤（分级抽样统一走 DamageModel）。"""
        o = self.obstacle_at(x, y)
        if o is None or o.kind != "minefield":
            return
        self.k.damage_model.resolve_mine(i)

    def breach_target(self, i: int) -> tuple[float, float] | None:
        """工兵当前要开辟的障碍：取未破障且在行进前方最近者的近侧边沿。"""
        s = self.s
        todo = [o for o in self.obstacles if not o.breached and o.cx > s.x[i] - 400.0]
        if not todo:
            return None
        o = min(todo, key=lambda z: np.hypot(z.cx - s.x[i], z.cy - s.y[i]))
        return (o.cx - o.hx, min(max(s.y[i], o.cy - o.hy), o.cy + o.hy))

    def progress_breach(self) -> None:
        """工兵在作业距离内累计破障进度（每个工兵每 tick 计 1）。"""
        s = self.s
        eng = np.where((s.role == "engineer") & s.alive)[0]
        for o in self.obstacles:
            if o.breached:
                continue
            workers = [
                i for i in eng
                if np.hypot(s.x[i] - (o.cx - o.hx), s.y[i] - o.cy) <= 400.0
            ]
            if workers:
                o.progress += len(workers)
                if o.progress >= o.breach_ticks:
                    o.breached = True
                    self.k.log.effect(s.tick, "breach", int(s.side[workers[0]]),
                                      o.cx, o.cy, o.kind)
