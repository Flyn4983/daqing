"""M1 交战内核：Mesa 多智能体 + NumPy 列存状态，sense -> C2 -> 机动 -> 交战 四阶段。

设计要点：
- SimState 的 numpy 列是唯一状态真相（M4 可直接搬到 Warp）；PlatformAgent 只是
  持有列索引的薄行为壳，不自己存位置/血量；
- 感知按方共享航迹（C2）：任一单位发现的目标在 track_ttl 内可被同方任何人射击；
  航迹记录来源兵种（src），超视距导弹只接受侦察/无人机/雷达来源的目标指示；
- C7-a0：合成兵种机制（间瞄炮兵、空中层、防空、雷达电磁、C2 光环、烟幕、障碍）
  统一在 BattleSystems（combat/systems.py）中，PlatformAgent 只保留地面机动与直瞄；
- 每 tick 先把 SimPy 保障进程推进到本时刻，再执行作战阶段，tick 边界对齐；
- 阶段内按单位索引固定顺序激活，随机性全部来自 Kernel.rng，保证可复现。
"""

from __future__ import annotations

import mesa
import numpy as np

from ..kernel.base import Kernel
from ..sustainment.repair import SustainmentModel
from .damage import DISP_ONSITE, GRADE_M, DamageModel
from .support_ops import SupportOps
from .systems import _RECOG_RANGE, BattleSystems

RED, BLUE = 0, 1

# 占领阵位、不参与地面冲击的兵种（冲击/侦察/工兵/发烟车之外全部固守）
_HOLD_ROLES = {
    "artillery", "missile", "air_defense", "radar_air", "radar_cb",
    "ew", "c2",
}
_DIRECT_MOVERS = {"assault", "recon", "engineer"}
_SMOKE_BLOCK_KINDS = {"optical", "ir"}  # 烟幕只遮光电，不遮雷达


class PlatformAgent(mesa.Agent):
    """一个作战平台；状态全部经由 idx 读写 SimState 列。"""

    def __init__(self, model: "MesaKernel", idx: int) -> None:
        super().__init__(model)
        self.idx = idx

    def sense(self) -> None:
        """侦察：射程/传感器类型×气象×通视×烟幕×掩体 → 发现概率，写入本方共享航迹。"""
        m, s, sysm = self.model, self.model.state, self.model.sys
        i = self.idx
        if not s.alive[i]:
            return
        side = int(s.side[i])
        enemies = m.enemies[side]
        cand = enemies[s.alive[enemies]]
        if cand.size == 0:
            return
        kind = str(s.sensor_kind[i])
        wf = sysm.factors[kind]
        reach = s.sensor_range[i] * wf
        d = np.hypot(s.x[cand] - s.x[i], s.y[cand] - s.y[i])
        cand = cand[d <= reach]
        if cand.size == 0:
            return
        d = d[d <= reach]
        blocked_smoke = kind in _SMOKE_BLOCK_KINDS
        for k, j in enumerate(cand):
            j = int(j)
            if not m.terrain.has_los(float(s.x[i]), float(s.y[i]),
                                   float(s.x[j]), float(s.y[j])):
                continue
            if blocked_smoke and (sysm.in_smoke(float(s.x[i]), float(s.y[i]))
                                 or sysm.in_smoke(float(s.x[j]), float(s.y[j]))):
                continue
            p = float(s.detect_prob[i]) * float(sysm.det_mult[i]) * (1.0 - d[k] / reach)
            if s.dug_in[j]:
                p *= 0.7  # 既设阵地隐蔽
            if self.model.rng.random() < p:
                sysm.add_track(side, j, int(s.track_ttl[i]), str(s.role[i]),
                               link_denied=bool(sysm.comms_denied[i]))
                # 侦察平台抵近可识破假目标
                if str(s.role[i]) == "recon" and d[k] <= _RECOG_RANGE:
                    sysm.mark_recog(side, j)

    def move(self) -> None:
        """机动决策：突击力量冲击；侦察前出；工兵破障；发烟车就定位；其余固守。

        在修单位后送：配置修理所时向本方修理所机动，未配置则原地不动。
        """
        m, s, sysm = self.model, self.model.state, self.model.sys
        i = self.idx
        if not s.alive[i]:
            return
        # C7-a1：M 级（行走/动力总成）战损失动，原地待拖救；F 级走原 under_repair 后送
        if s.damaged[i] and int(s.damage_grade[i]) == GRADE_M:
            return
        role = str(s.role[i])
        if s.under_repair[i]:
            # C7-a2：实体化方的现地换件单位不后送（驻留受损位置抢修）
            if int(s.side[i]) in m.support.sides and int(s.disposition[i]) == DISP_ONSITE:
                return
            stn = m.stations.get(int(s.side[i]))
            if stn is None:
                return
            self._step_toward(stn[0], stn[1], in_obstacle_ok=False)
            return

        # 工兵：沿攻击轴跟随冲击矛头（间隔 300m），抵近障碍 1km 内短促跃进开辟
        if role == "engineer" and int(s.side[i]) == RED:
            tgt = sysm.breach_target(i)
            if tgt is not None:
                self._follow_or_dash(tgt[0], tgt[1], dash_when=1000.0,
                                     follow_gap=300.0, in_obstacle_ok=True,
                                     x_axis=True)
            return

        # 发烟车（support 中有发烟命令者）：沿本车道跟进，在屏烟点后方 1.3km
        # 发射位停车远程发烟（不进入敌火下的烟幕中心）
        if role == "support":
            for z in sysm.smoke_orders:
                if z.vehicle_idx == i:
                    lx = z.x - 1300.0 if int(s.side[i]) == RED else z.x + 1300.0
                    self._follow_or_dash(lx, z.y, dash_when=1000.0,
                                         follow_gap=200.0, in_obstacle_ok=False,
                                         x_axis=True)
                    return
            return  # 其余保障车辆 a0 固守（a2 实体化前送/拖救）

        if role in _HOLD_ROLES:
            return

        # 侦察：前出至发现敌方即停（保持接触不冲锋）
        contact = m.nearest_tracked(i)
        if role == "recon":
            if contact is not None and contact[1] <= s.sensor_range[i]:
                s.heading[i] = np.arctan2(
                    s.y[contact[0]] - s.y[i], s.x[contact[0]] - s.x[i]
                )
                return
            self._step_toward(s.objective[0], s.objective[1], in_obstacle_ok=False)
            return

        # 突击：红方持续冲击目标点；蓝方固守或短距前出接敌
        if s.side[i] == RED:
            tx, ty = s.objective[0], s.objective[1]
            if contact is not None:
                s.heading[i] = np.arctan2(
                    s.y[contact[0]] - s.y[i], s.x[contact[0]] - s.x[i]
                )
            self._step_toward(tx, ty, in_obstacle_ok=False)
        else:
            if contact is None:
                return
            j, d = contact
            s.heading[i] = np.arctan2(s.y[j] - s.y[i], s.x[j] - s.x[i])
            # 既设阵地掘壕单位固守阵位，不出阵前出接敌（保持梯次防御纵深）
            if s.dug_in[i]:
                return
            wf = sysm.factors[str(s.sensor_kind[i])]
            if d > s.weapon_range[i] * wf:
                self._step_toward(s.x[j], s.y[j], in_obstacle_ok=False)

    def engage(self) -> None:
        """直瞄射击：冷却/在修/通视/烟幕/掩体结算；间瞄与导弹由 BattleSystems 处理。"""
        m, s, sysm = self.model, self.model.state, self.model.sys
        i = self.idx
        if (
            (not s.alive[i]) or s.under_repair[i] or s.cooldown[i] > 0
            or str(s.fire_mode[i]) != "direct"
        ):
            return
        contact = m.nearest_tracked(i)
        if contact is None:
            return
        kind = str(s.sensor_kind[i])
        wf = sysm.factors[kind]
        if contact[1] > s.weapon_range[i] * wf:
            return
        t = contact[0]
        if not m.terrain.has_los(float(s.x[i]), float(s.y[i]),
                                float(s.x[t]), float(s.y[t])):
            return
        pk_eff = float(s.pk[i]) * float(sysm.pk_mult[i])
        # 烟幕：光电瞄准直瞄武器失效性大幅下降（雷达引导火力不在此路径）
        if kind in _SMOKE_BLOCK_KINDS and (
            sysm.in_smoke(float(s.x[i]), float(s.y[i]))
            or sysm.in_smoke(float(s.x[t]), float(s.y[t]))
        ):
            pk_eff *= 0.25
        if s.dug_in[t]:
            pk_eff *= 0.6
        s.cooldown[i] = s.reload_ticks[i]
        self.model.damage_model.resolve_hit(
            str(s.unit_ids[i]), int(t), pk_eff, str(s.ammo_class[i]), "direct",
        )

    def _follow_or_dash(
        self, tx: float, ty: float, *, dash_when: float, follow_gap: float = 500.0,
        in_obstacle_ok: bool, x_axis: bool = False,
    ) -> None:
        """跟随红方突击矛头，保持 follow_gap 米间隔；矛头进入 dash_when 米内后
        向目标点短促跃进（工兵/发烟车战术动作）。

        x_axis=True：按攻击轴（东向 x）判定进度，本车在自己的 y 车道跟进，
        避免南北车道的横向偏置把抵近时机拖晚。
        """
        s = self.model.state
        i = self.idx
        roles = np.asarray(s.role, dtype=object)
        am = s.alive & (s.side == RED) & (roles == "assault")
        if not am.any():
            return
        am_idx = np.where(am)[0]
        if x_axis:
            kk = int(np.argmax(s.x[am_idx]))
            lead = int(am_idx[kk])
            d_lead = float(tx - s.x[lead])
            if d_lead <= dash_when:
                self._step_toward(tx, ty, in_obstacle_ok=in_obstacle_ok)
                return
            fx, fy = float(s.x[lead]) - follow_gap, float(s.y[i])
        else:
            d = np.hypot(s.x[am_idx] - tx, s.y[am_idx] - ty)
            kk = int(np.argmin(d))
            lead, d_lead = int(am_idx[kk]), float(d[kk])
            if d_lead <= dash_when:
                self._step_toward(tx, ty, in_obstacle_ok=in_obstacle_ok)
                return
            # 跟进点：矛头位置沿"目标→矛头"方向再后撤 follow_gap
            ux, uy = (s.x[lead] - tx) / d_lead, (s.y[lead] - ty) / d_lead
            fx, fy = s.x[lead] + follow_gap * ux, s.y[lead] + follow_gap * uy
        if np.hypot(s.x[i] - fx, s.y[i] - fy) > 60.0:
            self._step_toward(fx, fy, in_obstacle_ok=False)

    def _step_toward(self, tx: float, ty: float, *, in_obstacle_ok: bool = False) -> None:
        s, sysm = self.model.state, self.model.sys
        i = self.idx
        dx, dy = tx - s.x[i], ty - s.y[i]
        d = np.hypot(dx, dy)
        if d <= 0:
            return
        s.heading[i] = np.arctan2(dy, dx)
        factor = float(self.model.terrain.move_factor(s.x[i], s.y[i]))
        factor *= sysm.ground_move
        if s.damaged[i]:
            factor *= 0.5
        obs = sysm.obstacle_move_factor(float(s.x[i]), float(s.y[i]))
        if obs is not None and not in_obstacle_ok:
            factor *= obs
        step = min(float(s.speed[i]) * factor, d)
        nx = min(max(s.x[i] + step * dx / d, 0.0), self.model.bound[0])
        ny = min(max(s.y[i] + step * dy / d, 0.0), self.model.bound[1])
        s.x[i], s.y[i] = nx, ny
        # 雷场触雷（仅冲击中的红方地面单位；工兵作业不结算）
        if int(s.side[i]) == RED and str(s.role[i]) in ("assault", "recon"):
            sysm.mine_damage(i, float(nx), float(ny))


class MesaKernel(Kernel, mesa.Model):
    name = "mesa"

    # Mesa 3.5 的 Model 自带 scenario 属性（实验性 Scenario API），与 Kernel.scenario
    # 同名冲突；覆盖为普通读写属性，避免其 setter 回写 pydantic 对象
    @property
    def scenario(self):  # noqa: D102
        return self._scenario

    @scenario.setter
    def scenario(self, value) -> None:
        self._scenario = value

    def __init__(self, scenario, seed: int = 0) -> None:
        # 先初始化 mesa（其 __init__ 会塞一个自己的默认 Scenario），再由 Kernel 覆盖
        mesa.Model.__init__(self)
        Kernel.__init__(self, scenario, seed)
        s = self.state
        self.bound = scenario.terrain.size_m
        # 每方的敌方索引（固定）
        self.enemies = {
            RED: np.where(s.side != RED)[0],
            BLUE: np.where(s.side != BLUE)[0],
        }
        # 共享航迹：side -> {敌方 idx: {"exp", "src", "recog"}}
        self.tracks = {RED: {}, BLUE: {}}
        self.sustain = SustainmentModel(scenario, s, self.rng)
        # C7-a1：战损分级/部位抽样/处置分流模型（roll 结算统一入口）
        self.damage_model = DamageModel(self)
        # C7-a0：合成兵种战斗支援系统
        self.sys = BattleSystems(self)
        # C7-a2：抢救/器材补给/弹药补给车实体机动（无实体化方别时 step 为空操作）
        self.support = SupportOps(self)
        # 修理所位置：在修单位的后送目标；未配置（None）时在修单位原地不动
        spec_station = scenario.sustainment.repair_station
        self.stations = (
            {RED: tuple(spec_station["red"]), BLUE: tuple(spec_station["blue"])}
            if spec_station
            else {}
        )
        # 多阶段任务：从第 0 阶段开始，红方进占当前目标点后顺序切换
        self.phases = scenario.mission.phase_list
        self.phase_idx = 0
        s.objective = np.asarray(self.phases[0].objective, dtype=np.float64)
        self._accomplished = False
        for i in range(s.n):
            PlatformAgent(self, i)

    def step(self) -> None:
        s = self.state
        s.tick += 1
        # 1) 保障：截至本 tick 时刻的故障/修复先生效
        self.sustain.advance(s.tick)
        # 2) 射击冷却递减
        np.subtract(s.cooldown, 1, out=s.cooldown)
        np.maximum(s.cooldown, 0, out=s.cooldown)
        # 3) 体系态势：雷达开机/电磁压制/C2 光环/烟幕
        self.sys.pre_combat()
        # 4) 地面侦察共享航迹 -> 机动（含工兵破障）-> 直瞄交战
        self.agents.do("sense")
        self._purge_tracks()
        self.agents.do("move")
        # C7-a2：保障实体机动（拖救/前送波次/补弹），紧接地面机动段
        self.support.step()
        self.sys.progress_breach()
        self.agents.do("engage")
        # 5) 合成兵种火力：空中层/防空/间瞄炮兵/BLOS 弹着
        self.sys.step_fires()
        # 6) 任务阶段推进（红方进占当前目标点 -> 切换下一阶段 / 末段达成）
        self._update_phases()
        # 7) 本 tick 战斗受损统一进抢修通道（下 tick 起在修免疫，与 GPU 对齐）
        self.sustain.commit_combat_damage()
        self.log.snapshot(s.tick, s, self.sustain)

    def _purge_tracks(self) -> None:
        """丢弃过期与已战毁目标的航迹。"""
        now, s = self.state.tick, self.state
        for side in (RED, BLUE):
            self.tracks[side] = {
                j: info for j, info in self.tracks[side].items()
                if info["exp"] > now and s.alive[j]
            }

    def nearest_tracked(self, i: int) -> tuple[int, float] | None:
        """本方共享航迹中距 i 最近的存活敌方 (idx, 距离)。

        在修单位视为后送脱离接触，不可被选为目标。
        """
        s = self.state
        side = int(s.side[i])
        cand = [j for j in self.tracks[side] if s.alive[j] and not s.under_repair[j]]
        if not cand:
            return None
        cand = np.asarray(cand, dtype=int)
        d = np.hypot(s.x[cand] - s.x[i], s.y[cand] - s.y[i])
        k = int(np.argmin(d))
        return int(cand[k]), float(d[k])

    @property
    def finished(self) -> bool:
        """到时、末段目标达成、红方被全歼，三者之一即结束（后两者提前收束）。"""
        if super().finished:
            return True
        if self._accomplished:
            return True
        return not bool((self.state.alive & (self.state.side == RED)).any())

    def _red_min_dist(self, point) -> float:
        red_alive = self.state.alive & (self.state.side == RED)
        if not red_alive.any():
            return float("inf")
        return float(
            np.hypot(
                self.state.x[red_alive] - point[0],
                self.state.y[red_alive] - point[1],
            ).min()
        )

    def _update_phases(self) -> None:
        """红方任一存活突击/侦察单位进入当前阶段目标半径即切换。"""
        s = self.state
        movers = s.alive & (s.side == RED) & np.isin(s.role, list(_DIRECT_MOVERS))
        last = len(self.phases) - 1
        while self.phase_idx < last:
            ph = self.phases[self.phase_idx]
            if not self._red_reach(movers, ph.objective, ph.success_radius_m):
                break
            self.phase_idx += 1
            s.objective = np.asarray(self.phases[self.phase_idx].objective, dtype=np.float64)
        s.phase = self.phase_idx
        ph = self.phases[self.phase_idx]
        if self.phase_idx == last and self._red_reach(movers, ph.objective,
                                                     ph.success_radius_m):
            self._accomplished = True

    def _red_reach(self, mask: np.ndarray, point, radius: float) -> bool:
        if not mask.any():
            return False
        return bool(
            np.hypot(self.state.x[mask] - point[0], self.state.y[mask] - point[1]).min()
            <= radius
        )

    def mission_success(self) -> bool:
        return self._accomplished
