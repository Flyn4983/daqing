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

        specs = [scenario.platform_types[t] for t in types]
        self.sensor_range = np.asarray([s.sensor_range_m for s in specs], dtype=np.float64)
        self.weapon_range = np.asarray([s.weapon_range_m for s in specs], dtype=np.float64)
        self.pk = np.asarray([s.pk for s in specs], dtype=np.float64)
        self.speed = np.asarray([s.speed_m_per_tick for s in specs], dtype=np.float64)

        self.tick = 0
        self.objective = np.asarray(scenario.mission.objective, dtype=np.float64)
