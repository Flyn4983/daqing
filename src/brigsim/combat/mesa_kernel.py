"""M1 交战内核：Mesa 多智能体 + NumPy 列存状态，sense -> C2 -> 机动 -> 交战 四阶段。

设计要点：
- SimState 的 numpy 列是唯一状态真相（M4 可直接搬到 Warp）；PlatformAgent 只是
  持有列索引的薄行为壳，不自己存位置/血量；
- 感知按方共享航迹（C2）：任一单位发现的目标在 track_ttl 内可被同方任何人射击；
- 每 tick 先把 SimPy 保障进程推进到本时刻，再执行作战阶段，tick 边界对齐；
- 阶段内按单位索引固定顺序激活，随机性全部来自 Kernel.rng，保证可复现。
"""

from __future__ import annotations

import mesa
import numpy as np

from ..kernel.base import Kernel
from ..sustainment.repair import SustainmentModel

RED, BLUE = 0, 1


class PlatformAgent(mesa.Agent):
    """一个作战平台（坦克/步战/反坦克组…）；状态全部经由 idx 读写 SimState 列。"""

    def __init__(self, model: "MesaKernel", idx: int) -> None:
        super().__init__(model)
        self.idx = idx

    def sense(self) -> None:
        """侦察：对有效射程内的敌方存活单位按距离衰减概率抽样发现，写入本方共享航迹。"""
        m, s = self.model, self.model.state
        i = self.idx
        if not s.alive[i]:
            return
        enemies = m.enemies[int(s.side[i])]
        cand = enemies[s.alive[enemies]]
        if cand.size == 0:
            return
        reach = s.sensor_range[i] * m.vis
        d = np.hypot(s.x[cand] - s.x[i], s.y[cand] - s.y[i])
        cand = cand[d <= reach]
        if cand.size == 0:
            return
        d = d[d <= reach]
        probs = s.detect_prob[i] * (1.0 - d / reach)  # 近界满概率，远界线性降到 0
        found = self.model.rng.random(cand.size) < probs
        for j in cand[found]:
            m.tracks[int(s.side[i])][int(j)] = s.tick + int(s.track_ttl[i])

    def move(self) -> None:
        """机动决策（粗版教条）：红方冲击目标点，蓝方前出接敌或固守。

        在修单位后送：配置修理所时向本方修理所机动（脱离接触），
        未配置则原地不动（旧行为）。
        """
        m, s = self.model, self.model.state
        i = self.idx
        if not s.alive[i]:
            return
        if s.under_repair[i]:
            stn = m.stations.get(int(s.side[i]))
            if stn is None:
                return
            self._step_toward(stn[0], stn[1])
            return

        contact = m.nearest_tracked(i)
        if s.side[i] == RED:
            tx, ty = s.objective[0], s.objective[1]
            if contact is not None and contact[1] <= s.weapon_range[i] * m.vis:
                # 接触敌方且在射界内：朝向目标（身管/车体指向），继续向目标点冲击
                s.heading[i] = np.arctan2(s.y[contact[0]] - s.y[i], s.x[contact[0]] - s.x[i])
                self._step_toward(tx, ty)
            else:
                self._step_toward(tx, ty)
        else:
            if contact is None:
                return  # 未发现目标：固守阵地
            j, d = contact
            s.heading[i] = np.arctan2(s.y[j] - s.y[i], s.x[j] - s.x[i])
            if d > s.weapon_range[i] * m.vis:
                self._step_toward(s.x[j], s.y[j])
            # 已在射界内：固守射击

    def engage(self) -> None:
        """射击：冷却归零、在修不能打；选择航迹中射界内最近敌方，pk 判定毁伤。"""
        m, s = self.model, self.model.state
        i = self.idx
        if (not s.alive[i]) or s.under_repair[i] or s.cooldown[i] > 0:
            return
        contact = m.nearest_tracked(i)
        if contact is None or contact[1] > s.weapon_range[i] * m.vis:
            return
        t = contact[0]
        roll = self.model.rng.random()
        if roll < s.pk[i]:
            s.alive[t] = False
            s.damaged[t] = False
            killed, damaged = True, False
        elif roll < s.pk[i] + 0.25:
            m.sustain.combat_damage(t)
            killed, damaged = False, True
        else:
            killed, damaged = False, False
        s.cooldown[i] = s.reload_ticks[i]
        m.log.engagement(s.tick, str(s.unit_ids[i]), str(s.unit_ids[t]), killed, damaged)

    def _step_toward(self, tx: float, ty: float) -> None:
        s = self.model.state
        i = self.idx
        dx, dy = tx - s.x[i], ty - s.y[i]
        d = np.hypot(dx, dy)
        if d <= 0:
            return
        s.heading[i] = np.arctan2(dy, dx)
        factor = float(self.model.terrain.move_factor(s.x[i], s.y[i]))
        if s.damaged[i]:
            factor *= 0.5  # 受损单位机动降级
        step = min(float(s.speed[i]) * factor, d)
        s.x[i] = min(max(s.x[i] + step * dx / d, 0.0), self.model.bound[0])
        s.y[i] = min(max(s.y[i] + step * dy / d, 0.0), self.model.bound[1])


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
        self.vis = scenario.environment.visibility_factor
        self.bound = scenario.terrain.size_m
        # 每方的敌方索引（固定）
        self.enemies = {
            RED: np.where(s.side != RED)[0],
            BLUE: np.where(s.side != BLUE)[0],
        }
        # 共享航迹：side -> {敌方 idx: 失效 tick}
        self.tracks = {RED: {}, BLUE: {}}
        self.sustain = SustainmentModel(scenario, s, self.rng)
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
        # 3-5) 作战四阶段（侦察共享航迹 -> 决策机动 -> 交战）
        self.agents.do("sense")
        self._purge_tracks()
        self.agents.do("move")
        self.agents.do("engage")
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
                j: exp for j, exp in self.tracks[side].items() if exp > now and s.alive[j]
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
        """红方任一存活单位进入当前阶段目标半径即切换；进占末段目标 = 任务成功。"""
        s = self.state
        last = len(self.phases) - 1
        while self.phase_idx < last:
            ph = self.phases[self.phase_idx]
            if self._red_min_dist(ph.objective) > ph.success_radius_m:
                break
            self.phase_idx += 1
            s.objective = np.asarray(self.phases[self.phase_idx].objective, dtype=np.float64)
        s.phase = self.phase_idx
        ph = self.phases[self.phase_idx]
        if self.phase_idx == last and self._red_min_dist(ph.objective) <= ph.success_radius_m:
            self._accomplished = True

    def mission_success(self) -> bool:
        return self._accomplished
