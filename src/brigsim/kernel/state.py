"""仿真状态：ECS 风格的 NumPy 结构化列存。

刻意不把状态散落在 Python 对象里——同一列存布局未来可直接搬到
NVIDIA Warp 的 wp.array，实现批量复制的 GPU 加速（M4），上层代码不变。
"""

from __future__ import annotations

import numpy as np

from ..data.schema import Scenario


class SimState:
    def __init__(self, scenario: Scenario) -> None:
        units, sides, types = [], [], []
        for side_id, side_name in enumerate(("red", "blue")):
            for u in scenario.forces.get(side_name, []):
                units.append(u)
                sides.append(side_id)
                types.append(u.type)

        self.n = len(units)
        self.unit_ids = np.array([u.unit_id for u in units], dtype=object)
        self.side = np.asarray(sides, dtype=np.int8)  # 0=red 1=blue
        self.type_names = np.asarray(types, dtype=object)
        self.x = np.asarray([u.pos[0] for u in units], dtype=np.float64)
        self.y = np.asarray([u.pos[1] for u in units], dtype=np.float64)
        self.alive = np.ones(self.n, dtype=bool)
        # 受损（未毁）：影响完好率，M1 起由保障模型修复
        self.damaged = np.zeros(self.n, dtype=bool)
        # 在修（抢修队列中/维修作业中）：机动与交战暂停
        self.under_repair = np.zeros(self.n, dtype=bool)
        # 射击冷却计数（>0 时本 tick 不能射击），每 tick 递减
        self.cooldown = np.zeros(self.n, dtype=np.int16)
        # 朝向（弧度，数学坐标系，0=+x 向东）；由机动更新，射界/模型朝向使用
        self.heading = np.asarray(
            [np.deg2rad(90.0 - u.heading_deg) for u in units], dtype=np.float64
        )
        self.labels = np.asarray([u.org or u.unit_id for u in units], dtype=object)

        specs = [scenario.platform_types[t] for t in types]
        # 方别不对称乘子（既设防御等），缺省方 = 1.0；装备基准值双方共用
        from ..data.schema import SideModifiers

        mods = [
            scenario.side_modifiers.get(("red", "blue")[int(s)], SideModifiers())
            for s in self.side
        ]
        self.sensor_range = np.asarray([s.sensor_range_m for s in specs], dtype=np.float64)
        self.weapon_range = np.asarray([s.weapon_range_m for s in specs], dtype=np.float64)
        self.pk = np.clip(
            np.asarray([s.pk * m.pk_scale for s, m in zip(specs, mods)]), 0.0, 1.0
        )
        self.speed = np.asarray(
            [s.speed_m_per_tick * m.speed_scale for s, m in zip(specs, mods)],
            dtype=np.float64,
        )
        self.reload_ticks = np.asarray([s.reload_ticks for s in specs], dtype=np.int16)
        self.detect_prob = np.clip(
            np.asarray([s.detect_prob * m.detect_scale for s, m in zip(specs, mods)]),
            0.0, 1.0,
        )
        self.track_ttl = np.asarray([s.track_ttl_ticks for s in specs], dtype=np.int16)
        self.mtbf = np.asarray([s.mtbf_ticks for s in specs], dtype=np.float64)
        self.mttr = np.asarray([s.mttr_ticks for s in specs], dtype=np.float64)

        self.tick = 0
        # 当前任务阶段（全局标量；M3 多阶段任务，内核推进时更新阶段与目标点）
        self.phase = 0
        self.objective = np.asarray(scenario.mission.phase_list[0].objective, dtype=np.float64)
