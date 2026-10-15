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
        # C7-a1：初始带病装备 MTBF 折减（worn_mtbf_scale）
        worn_scale = scenario.sustainment.worn_mtbf_scale
        self.mtbf = np.asarray(
            [
                s.mtbf_ticks * (worn_scale if u.initial_worn else 1.0)
                for s, u in zip(specs, units)
            ],
            dtype=np.float64,
        )
        self.mttr = np.asarray([s.mttr_ticks for s in specs], dtype=np.float64)

        # ---- C7-a0：合成兵种列（默认值保持旧想定行为不变）----
        self.role = np.asarray([s.role for s in specs], dtype=object)
        self.fire_mode = np.asarray([s.fire_mode for s in specs], dtype=object)
        self.sensor_kind = np.asarray([s.sensor_kind for s in specs], dtype=object)
        # 弹药基数：-1=不限制；间瞄/导弹/防空随射击消耗
        self.ammo = np.asarray(
            [-1 if s.ammo_basic_load is None else s.ammo_basic_load for s in specs],
            dtype=np.int32,
        )
        self.splash = np.asarray([s.splash_radius_m for s in specs], dtype=np.float64)
        self.fire_delay = np.asarray([s.call_fire_delay_ticks for s in specs], dtype=np.int16)
        # 阵地展开：emplace_timer 每 tick -1，归零后可射击（0=无需展开或已就绪）
        self.emplace_need = np.asarray([s.emplace_ticks for s in specs], dtype=np.int16)
        self.emplace_timer = np.asarray([s.emplace_ticks for s in specs], dtype=np.int16)
        self.coverage = np.asarray([s.coverage_radius_m for s in specs], dtype=np.float64)
        self.jam_strength = np.asarray([s.jam_strength for s in specs], dtype=np.float64)
        self.aura_bonus = np.asarray([s.aura_bonus for s in specs], dtype=np.float64)
        self.emission_duty = np.asarray([s.emission_duty for s in specs], dtype=np.float64)
        self.radar_on = np.zeros(self.n, dtype=bool)  # 本 tick 是否辐射（间歇开机）
        jam_code = {"radar": 0, "comms": 1, "both": 2}
        self.jam_mode = np.asarray([jam_code[s.jam_mode] for s in specs], dtype=np.int8)
        self.dug_in = np.asarray(
            [u.dug_in for u in units], dtype=bool
        )
        self.decoy = np.asarray([u.decoy for u in units], dtype=bool)

        # ---- C7-a1：战损分级 / 分部件 / 处置方式（旧想定恒为 0/空）----
        # damage_grade：0 无 / 1=M 机动受损 / 2=F 火力受损 / 3=K 战毁
        self.damage_grade = np.zeros(self.n, dtype=np.int8)
        self.part_id = np.asarray([""] * self.n, dtype=object)  # 受损部件名
        # disposition：0 无 / 1 现地处置(onsite) / 2 拖救(tow) / 3 自行后送(drive_back) /
        #              4 后送(rear) / 5 报废(writeoff, K 级)
        self.disposition = np.zeros(self.n, dtype=np.int8)
        # 弹药类别（resolve_hit 的部件毁伤分布偏移键）
        self.ammo_class = np.asarray([s.ammo_class for s in specs], dtype=object)

        self.tick = 0
        # 当前任务阶段（全局标量；M3 多阶段任务，内核推进时更新阶段与目标点）
        self.phase = 0
        self.objective = np.asarray(scenario.mission.phase_list[0].objective, dtype=np.float64)
