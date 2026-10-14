"""M4 交战内核（切片 1）：NVIDIA Warp 数据并行内核，单环境版。

与 MesaKernel 的对应关系（语义对拍见 tests/test_m4_warp_kernel.py）：
- SimState 的 numpy 列仍是 host 端镜像与指标/日志口径；真值每 tick 在
  wp.array 上演算，tick 边界与 host 同步一次（不过细粒度 D2H）；
- sense -> 机动 -> 交战 三个 @wp.kernel，目标搜索首版用稠密 O(n²) 扫描
  （n≤200 时每 tick 仅几万对，批量 env 维上 GPU 后再换 wp.HashGrid）；
- 共享航迹 tracks[side*n+j] 用 atomic_max 写失效时刻（Mesa 为按索引顺序
  last-writer，语义略有差异：并行取更晚失效，统计等价）；
- 同 tick 交战为并行语义：tick 初存活的射手都会开火，可能出现双方互杀
  （Mesa 顺序激活时先被杀者不开火）——并行 ABM 的标准取舍，指标分布对拍；
- 保障（SimPy）留在 CPU：tick 初 advance 后把 damaged/under_repair 推上
  设备，tick 末把新战损拉回并登记抢修通道；后续批量 env 维时再向量化。

随机数：wp.rand_init(base_seed, tick*n+i+段偏移)，每线程独立流，
与 Mesa 的全局 numpy 流不同源，故一致性验证按跨复制分布而非逐轨迹。
"""

from __future__ import annotations

import numpy as np
import warp as wp

from ..data.schema import Scenario
from ..kernel.base import Kernel
from ..sustainment.repair import SustainmentModel

RED, BLUE = 0, 1

_warp_inited = False

# 感知段每 (tick, 射手 i, 候选 j) 随机流的高位偏移：与交战段 (tick*n+n+i)
# 错开键空间。键最大约 2^28 + duration*n^2 < 2^31。
# 按候选身份播种后，候选遍历顺序（稠密 j 升序 / HashGrid 任意序）不影响判定。
SENSE_KEY_OFF = 1 << 28


# ---------- 设备端地形：双线性 + 坡度/水面/通行性（与 HeightField.move_factor 同式） ----------


@wp.func
def _grid_at(grid: wp.array(dtype=wp.float32), gn: int, sx: float, sy: float,
             gx: float, gy: float) -> float:
    fx = wp.clamp(gx / sx, 0.0, 1.0) * float(gn - 1)
    fy = wp.clamp(gy / sy, 0.0, 1.0) * float(gn - 1)
    i0 = int(wp.floor(fx))
    j0 = int(wp.floor(fy))
    i1 = i0 + 1
    j1 = j0 + 1
    if i1 >= gn:
        i1 = gn - 1
    if j1 >= gn:
        j1 = gn - 1
    tx = fx - float(i0)
    ty = fy - float(j0)
    v00 = grid[j0 * gn + i0]
    v10 = grid[j0 * gn + i1]
    v01 = grid[j1 * gn + i0]
    v11 = grid[j1 * gn + i1]
    return (
        (1.0 - tx) * (1.0 - ty) * v00
        + tx * (1.0 - ty) * v10
        + (1.0 - tx) * ty * v01
        + tx * ty * v11
    )


@wp.func
def _move_factor(
    h: wp.array(dtype=wp.float32),
    p: wp.array(dtype=wp.float32),
    gn: int,
    sx: float,
    sy: float,
    water: float,
    gx: float,
    gy: float,
) -> float:
    cell = sx / float(gn)  # 注意：与 numpy 版一致，分母是 n 而非 n-1
    hx = (_grid_at(h, gn, sx, sy, gx + cell, gy) - _grid_at(h, gn, sx, sy, gx - cell, gy)) / (
        2.0 * cell
    )
    hy = (_grid_at(h, gn, sx, sy, gx, gy + cell) - _grid_at(h, gn, sx, sy, gx, gy - cell)) / (
        2.0 * cell
    )
    slope = wp.sqrt(hx * hx + hy * hy)
    factor = wp.clamp(1.0 - 1.6 * slope, 0.25, 1.0)
    if _grid_at(h, gn, sx, sy, gx, gy) < water:
        factor = factor * 0.4
    factor = factor * _grid_at(p, gn, sx, sy, gx, gy)
    return wp.clamp(factor, 0.05, 1.0)


@wp.func
def _nearest_tracked(
    i: int,
    n: int,
    tick: int,
    x: wp.array(dtype=wp.float32),
    y: wp.array(dtype=wp.float32),
    alive: wp.array(dtype=wp.int32),
    side: wp.array(dtype=wp.int32),
    tracks: wp.array(dtype=wp.int32),
) -> int:
    """本方共享航迹中最近的存活敌方索引，无则 -1。"""
    si = side[i]
    xi = x[i]
    yi = y[i]
    best = int(-1)
    best_d = float(1.0e30)
    for j in range(0, n):
        if side[j] == si or alive[j] != 1:
            continue
        if tracks[si * n + j] > tick:
            dx = x[j] - xi
            dy = y[j] - yi
            d = wp.sqrt(dx * dx + dy * dy)
            if d < best_d:
                best_d = d
                best = j
    return best


# ---------- 每 tick 内核序列 ----------


@wp.kernel
def k_cooldown(cooldown: wp.array(dtype=wp.int32), alive: wp.array(dtype=wp.int32)):
    i = wp.tid()
    if alive[i] == 1 and cooldown[i] > 0:
        cooldown[i] = cooldown[i] - 1


@wp.kernel
def k_sense(
    x: wp.array(dtype=wp.float32),
    y: wp.array(dtype=wp.float32),
    alive: wp.array(dtype=wp.int32),
    side: wp.array(dtype=wp.int32),
    sensor: wp.array(dtype=wp.float32),
    detect: wp.array(dtype=wp.float32),
    ttl: wp.array(dtype=wp.int32),
    tracks: wp.array(dtype=wp.int32),
    tick: int,
    vis: float,
    n: int,
    seed: int,
):
    i = wp.tid()
    if alive[i] != 1:
        return
    si = side[i]
    reach = sensor[i] * vis
    xi = x[i]
    yi = y[i]
    for j in range(0, n):
        if side[j] == si or alive[j] != 1:
            continue
        dx = x[j] - xi
        dy = y[j] - yi
        d = wp.sqrt(dx * dx + dy * dy)
        if d > reach:
            continue
        # 每候选独立流（键含 j）：与批量 HashGrid 的任意遍历顺序逐位等价
        cstate = wp.rand_init(seed, SENSE_KEY_OFF + (tick * n + i) * n + j)
        r = wp.randf(cstate)
        prob = detect[i] * (1.0 - d / reach)
        if r < prob:
            wp.atomic_max(tracks, si * n + j, tick + ttl[i])


@wp.kernel
def k_clear(buf: wp.array(dtype=wp.int32)):
    i = wp.tid()
    buf[i] = 0


@wp.kernel
def k_move(
    x: wp.array(dtype=wp.float32),
    y: wp.array(dtype=wp.float32),
    heading: wp.array(dtype=wp.float32),
    alive: wp.array(dtype=wp.int32),
    damaged: wp.array(dtype=wp.int32),
    under_repair: wp.array(dtype=wp.int32),
    side: wp.array(dtype=wp.int32),
    speed: wp.array(dtype=wp.float32),
    weapon: wp.array(dtype=wp.float32),
    tracks: wp.array(dtype=wp.int32),
    heights: wp.array(dtype=wp.float32),
    passab: wp.array(dtype=wp.float32),
    tick: int,
    vis: float,
    n: int,
    gn: int,
    sx: float,
    sy: float,
    bx: float,
    by: float,
    water: float,
    ox: float,
    oy: float,
):
    i = wp.tid()
    if alive[i] != 1 or under_repair[i] == 1:
        return
    tgt = _nearest_tracked(i, n, tick, x, y, alive, side, tracks)
    if side[i] == RED:
        # 红方无论是否接触都向当前阶段目标点冲击（接触时身管指向目标的逻辑
        # 在 Mesa 版里随后被 _step_toward 覆盖，最终朝向即目标点方向）
        dx = ox - x[i]
        dy = oy - y[i]
        d = wp.sqrt(dx * dx + dy * dy)
        if d > 0.0:
            heading[i] = wp.atan2(dy, dx)
            f = _move_factor(heights, passab, gn, sx, sy, water, x[i], y[i])
            if damaged[i] == 1:
                f = f * 0.5
            step = wp.min(speed[i] * f, d)
            x[i] = wp.clamp(x[i] + step * dx / d, 0.0, bx)
            y[i] = wp.clamp(y[i] + step * dy / d, 0.0, by)
    else:
        if tgt < 0:
            return  # 蓝方未发现目标固守
        dx = x[tgt] - x[i]
        dy = y[tgt] - y[i]
        d = wp.sqrt(dx * dx + dy * dy)
        heading[i] = wp.atan2(dy, dx)
        if d > weapon[i] * vis:
            f = _move_factor(heights, passab, gn, sx, sy, water, x[i], y[i])
            step = wp.min(speed[i] * f, d)
            x[i] = wp.clamp(x[i] + step * dx / d, 0.0, bx)
            y[i] = wp.clamp(y[i] + step * dy / d, 0.0, by)


@wp.kernel
def k_engage(
    x: wp.array(dtype=wp.float32),
    y: wp.array(dtype=wp.float32),
    alive: wp.array(dtype=wp.int32),
    under_repair: wp.array(dtype=wp.int32),
    side: wp.array(dtype=wp.int32),
    cooldown: wp.array(dtype=wp.int32),
    pk: wp.array(dtype=wp.float32),
    weapon: wp.array(dtype=wp.float32),
    reload: wp.array(dtype=wp.int32),
    tracks: wp.array(dtype=wp.int32),
    fired: wp.array(dtype=wp.int32),
    target: wp.array(dtype=wp.int32),
    outcome: wp.array(dtype=wp.int32),  # 0=未击发/脱靶 1=受损 2=击毁
    want_kill: wp.array(dtype=wp.int32),
    want_dmg: wp.array(dtype=wp.int32),
    tick: int,
    vis: float,
    n: int,
    seed: int,
):
    i = wp.tid()
    target[i] = -1
    if alive[i] != 1 or under_repair[i] == 1 or cooldown[i] > 0:
        return
    tgt = _nearest_tracked(i, n, tick, x, y, alive, side, tracks)
    if tgt < 0:
        return
    dx = x[tgt] - x[i]
    dy = y[tgt] - y[i]
    d = wp.sqrt(dx * dx + dy * dy)
    if d > weapon[i] * vis:
        return
    state = wp.rand_init(seed, tick * n + n + i)  # 与 sense 段错开偏移
    r = wp.randf(state)
    fired[i] = 1
    target[i] = tgt
    if r < pk[i]:
        outcome[i] = 2
        wp.atomic_max(want_kill, tgt, 1)
    elif r < pk[i] + 0.25:
        outcome[i] = 1
        wp.atomic_max(want_dmg, tgt, 1)
    cooldown[i] = reload[i]


@wp.kernel
def k_apply(
    alive: wp.array(dtype=wp.int32),
    damaged: wp.array(dtype=wp.int32),
    under_repair: wp.array(dtype=wp.int32),
    want_kill: wp.array(dtype=wp.int32),
    want_dmg: wp.array(dtype=wp.int32),
):
    j = wp.tid()
    if want_kill[j] == 1:
        alive[j] = 0
        damaged[j] = 0
        under_repair[j] = 0
    elif want_dmg[j] == 1:
        damaged[j] = 1


def resolve_device(name: str | None) -> wp.Device:
    """None=优先 CUDA，不可用时回退 CPU。"""
    global _warp_inited
    if not _warp_inited:
        wp.init()
        _warp_inited = True
    if name is not None:
        return wp.get_device(name)
    try:
        return wp.get_device("cuda:0")
    except Exception:
        return wp.get_device("cpu")


class WarpKernel(Kernel):
    """一次想定一次运行；状态在 wp.array 上，SimState 为 host 镜像。"""

    name = "warp"

    def __init__(self, scenario: Scenario, seed: int = 0, device: str | None = None) -> None:
        Kernel.__init__(self, scenario, seed)
        s = self.state
        self.dev = resolve_device(device)
        self.vis = float(scenario.environment.visibility_factor)
        self.bound = scenario.terrain.size_m
        self.seed = int(seed)
        self.sustain = SustainmentModel(scenario, s, self.rng)

        # 多阶段任务（host 端推进，objective 变化随发射参数下发）
        self.phases = scenario.mission.phase_list
        self.phase_idx = 0
        s.objective = np.asarray(self.phases[0].objective, dtype=np.float64)
        self._accomplished = False

        d = self.dev

        np_dtype = {wp.float32: np.float32, wp.int32: np.int32}

        def arr(col, dtype):
            return wp.array(np.asarray(col, dtype=np_dtype[dtype]), dtype=dtype, device=d)

        # 装备状态列
        self.a_x = arr(s.x, wp.float32)
        self.a_y = arr(s.y, wp.float32)
        self.a_heading = arr(s.heading, wp.float32)
        self.a_alive = arr(s.alive, wp.int32)
        self.a_damaged = arr(s.damaged, wp.int32)
        self.a_repair = arr(s.under_repair, wp.int32)
        self.a_cd = arr(s.cooldown, wp.int32)
        self.a_side = arr(s.side, wp.int32)
        # 装备性能列（按单位展开，想定内不变）
        self.a_sensor = arr(s.sensor_range, wp.float32)
        self.a_weapon = arr(s.weapon_range, wp.float32)
        self.a_pk = arr(s.pk, wp.float32)
        self.a_speed = arr(s.speed, wp.float32)
        self.a_reload = arr(s.reload_ticks, wp.int32)
        self.a_detect = arr(s.detect_prob, wp.float32)
        self.a_ttl = arr(s.track_ttl, wp.int32)
        # 共享航迹失效时刻（0=无航迹），长度 2n：前 n 红方航迹、后 n 蓝方
        self.a_tracks = wp.zeros(2 * s.n, dtype=wp.int32, device=d)
        # 交战结果缓冲
        self.a_fired = wp.zeros(s.n, dtype=wp.int32, device=d)
        self.a_target = wp.zeros(s.n, dtype=wp.int32, device=d)
        self.a_outcome = wp.zeros(s.n, dtype=wp.int32, device=d)
        self.a_kill = wp.zeros(s.n, dtype=wp.int32, device=d)
        self.a_dmg = wp.zeros(s.n, dtype=wp.int32, device=d)

        # 地形（行=y 列=x，展平为 1D）
        t = self.terrain
        self.gn = t.n
        self.a_h = arr(t.heights.reshape(-1), wp.float32)
        self.a_p = arr(t.passability.reshape(-1), wp.float32)

    # ---- tick 编排 ----

    def step(self) -> None:
        s = self.state
        n = s.n
        s.tick += 1

        # 1) 保障在 CPU 推进（故障/修复事件），结果推上设备
        self.sustain.advance(s.tick)
        self.a_damaged.assign(np.asarray(s.damaged, dtype=np.int32))
        self.a_repair.assign(np.asarray(s.under_repair, dtype=np.int32))

        common = dict(device=self.dev)
        # 2) 冷却递减
        wp.launch(k_cooldown, dim=n, inputs=[self.a_cd, self.a_alive], **common)
        # 3) 感知（共享航迹）
        wp.launch(
            k_sense,
            dim=n,
            inputs=[
                self.a_x, self.a_y, self.a_alive, self.a_side, self.a_sensor,
                self.a_detect, self.a_ttl, self.a_tracks, s.tick, self.vis, n, self.seed,
            ],
            **common,
        )
        # 4) 清空交战缓冲 -> 机动 -> 交战 -> 应用战损
        wp.launch(k_clear, dim=n, inputs=[self.a_fired], **common)
        wp.launch(k_clear, dim=n, inputs=[self.a_outcome], **common)
        wp.launch(k_clear, dim=n, inputs=[self.a_kill], **common)
        wp.launch(k_clear, dim=n, inputs=[self.a_dmg], **common)
        ox, oy = (float(v) for v in s.objective)
        wp.launch(
            k_move,
            dim=n,
            inputs=[
                self.a_x, self.a_y, self.a_heading, self.a_alive, self.a_damaged,
                self.a_repair, self.a_side, self.a_speed, self.a_weapon, self.a_tracks,
                self.a_h, self.a_p, s.tick, self.vis, n, self.gn,
                float(self.terrain.size[0]), float(self.terrain.size[1]),
                float(self.bound[0]), float(self.bound[1]),
                float(self.terrain.water_level), ox, oy,
            ],
            **common,
        )
        wp.launch(
            k_engage,
            dim=n,
            inputs=[
                self.a_x, self.a_y, self.a_alive, self.a_repair, self.a_side, self.a_cd,
                self.a_pk, self.a_weapon, self.a_reload, self.a_tracks,
                self.a_fired, self.a_target, self.a_outcome, self.a_kill, self.a_dmg,
                s.tick, self.vis, n, self.seed,
            ],
            **common,
        )
        wp.launch(
            k_apply,
            dim=n,
            inputs=[self.a_alive, self.a_damaged, self.a_repair, self.a_kill, self.a_dmg],
            **common,
        )
        wp.synchronize_device(self.dev)

        # 5) 设备 -> host 镜像
        s.x = self.a_x.numpy().astype(np.float64)
        s.y = self.a_y.numpy().astype(np.float64)
        s.heading = self.a_heading.numpy().astype(np.float64)
        s.alive = self.a_alive.numpy().astype(bool)
        s.damaged = self.a_damaged.numpy().astype(bool)

        # 6) 交战日志 + 新战损登记抢修通道（host SimPy）
        fired = self.a_fired.numpy()
        target = self.a_target.numpy()
        outcome = self.a_outcome.numpy()
        for i in np.flatnonzero(fired):
            t = int(target[i])
            self.log.engagement(
                s.tick,
                str(s.unit_ids[i]),
                str(s.unit_ids[t]),
                bool(outcome[i] == 2),
                bool(outcome[i] == 1),
            )
        for j in np.flatnonzero(self.a_dmg.numpy()):
            if s.alive[j]:
                self.sustain.combat_damage(int(j))

        # 7) 阶段推进与快照
        self._update_phases()
        self.log.snapshot(s.tick, s)

    # ---- 任务阶段（与 MesaKernel 同逻辑，numpy 镜像上执行） ----

    @property
    def finished(self) -> bool:
        if super().finished:
            return True
        if self._accomplished:
            return True
        return not bool((self.state.alive & (self.state.side == RED)).any())

    def _red_min_dist(self, point) -> float:
        s = self.state
        red_alive = s.alive & (s.side == RED)
        if not red_alive.any():
            return float("inf")
        return float(np.hypot(s.x[red_alive] - point[0], s.y[red_alive] - point[1]).min())

    def _update_phases(self) -> None:
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
