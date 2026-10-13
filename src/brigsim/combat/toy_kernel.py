"""M0 粗内核：机动 -> 侦察 -> 射击 -> 毁伤 的最小闭环（纯 NumPy，非保真模型）。

规则刻意从简，只为跑通链路、供可视化先行使用；细节在 M1/M3 逐轮打磨。
"""

from __future__ import annotations

import numpy as np

from ..kernel.base import Kernel

RED, BLUE = 0, 1


class ToyNumpyKernel(Kernel):
    name = "toy_numpy"

    def step(self) -> None:
        s = self.state
        s.tick += 1
        alive = s.alive

        # 1) 机动：红方冲向任务目标点；蓝方在武器射程外朝最近红方前压，否则固守
        if alive.any():
            red_alive = alive & (s.side == RED)
            blue_alive = alive & (s.side == BLUE)
            if red_alive.any():
                self._move_toward(red_alive, s.objective[0], s.objective[1], aggro=False)
            if red_alive.any() and blue_alive.any():
                # 每个蓝方找最近红方
                rx, ry = s.x[red_alive], s.y[red_alive]
                idx_red = np.where(red_alive)[0]
                for j in np.where(blue_alive)[0]:
                    k = idx_red[np.argmin((rx - s.x[j]) ** 2 + (ry - s.y[j]) ** 2)]
                    d = np.hypot(s.x[k] - s.x[j], s.y[k] - s.y[j])
                    if d > s.weapon_range[j]:
                        self._move_one_toward(j, s.x[k], s.y[k])

        # 2) 侦察+射击+毁伤：每个存活单位对射程内最近敌方单位射击一次
        for i in np.where(alive)[0]:
            enemy = alive & (s.side != s.side[i])
            if not enemy.any():
                continue
            idx_enemy = np.where(enemy)[0]
            d = np.hypot(s.x[idx_enemy] - s.x[i], s.y[idx_enemy] - s.y[i])
            reach = np.minimum(s.sensor_range[i], s.weapon_range[i])
            if d.min() > reach:
                continue
            t = idx_enemy[np.argmin(d)]
            self._resolve_shot(i, t)

        self.log.snapshot(s.tick, s)

    def _move_toward(self, mask, tx: float, ty: float, aggro: bool) -> None:
        for i in np.where(mask)[0]:
            self._move_one_toward(i, tx, ty)

    def _move_one_toward(self, i: int, tx: float, ty: float) -> None:
        s = self.state
        d = np.hypot(tx - s.x[i], ty - s.y[i])
        step = min(s.speed[i], d)
        if d > 0:
            s.x[i] += step * (tx - s.x[i]) / d
            s.y[i] += step * (ty - s.y[i]) / d

    def _resolve_shot(self, i: int, t: int) -> None:
        s, rng = self.state, self.rng
        roll = rng.random()
        killed = roll < s.pk[i]
        # 粗处理：未击毁但有一定概率造成受损（M1 改为命中概率×毁伤等级抽样）
        damaged = (not killed) and roll < s.pk[i] + 0.25
        if killed:
            s.alive[t] = False
            s.damaged[t] = False
        elif damaged:
            s.damaged[t] = True
        self.log.engagement(
            s.tick, str(s.unit_ids[i]), str(s.unit_ids[t]), killed, damaged and not killed
        )

    # ---- 任务判据 ----
    def mission_success(self) -> bool:
        s = self.scenario.mission
        red_alive = self.state.alive & (self.state.side == RED)
        if not red_alive.any():
            return False
        d = np.hypot(
            self.state.x[red_alive] - s.objective[0],
            self.state.y[red_alive] - s.objective[1],
        )
        return bool(d.min() <= s.success_radius_m)
