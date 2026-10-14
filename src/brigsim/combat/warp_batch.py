"""M4 交战内核：env 维批量复制 + HashGrid 邻域查询。

把 R 次复制的状态按 env*n+i 平铺到同一组 wp.array，一条 launch 同时推进
全部环境；提前结束的环境由 active[env] 冻结。每环境可用独立想定
（灵敏度 per-env 参数列，切片 3）。

关键约定：
- 每环境随机流 wp.rand_init(seed0[env], tick*n+i)，感知段按候选身份 j
  单独播种（SENSE_KEY_OFF + (tick*n+i)*n+j）——与 WarpKernel 单环境
  的播种公式完全相同，且与候选遍历顺序无关；同种子下批量 env 与逐个
  单环境的轨迹逐位一致（测试据此做精确相等断言，而非仅分布对拍）；
- 目标搜索用单个 wp.HashGrid（切片 4）：各环境坐标沿 x 平移 env*stride
  做空间隔离；网格每 tick 重建两次——sense 前一次（机动前位置）、
  engage 前一次（单位每 tick 走一个步长，不重建会漏掉新进入射程的
  目标）；sense（半径=探测距）与 engage（半径=武器射程）走网格邻域
  查询，等距取小编号消歧；蓝方追击转向保留稠密扫描——C2 共享航迹
  无距离上界（目标只在某友邻探测距内，开火单位自身可在任意远），
  半径查询无法覆盖；
- 保障（SimPy）仍在 CPU：每环境一个 SustainmentModel，状态列是批量 host
  大数组的切片视图，每 tick 一次整批 H2D/D2H（不为每环境单独传输）；
- 批量模式不产出逐射事件日志（回放用单环境 WarpKernel），只收最终指标。
"""

from __future__ import annotations

import numpy as np
import warp as wp

from ..data.schema import Scenario
from ..experiment.metrics import per_run_metrics
from ..kernel.state import SimState
from .warp_kernel import SENSE_KEY_OFF, _move_factor, resolve_device

RED = 0


@wp.kernel
def k_b_gridpoints(
    pts: wp.array(dtype=wp.vec3),
    x: wp.array(dtype=wp.float32),
    y: wp.array(dtype=wp.float32),
    n: int,
    stride: float,
):
    """HashGrid 建网点：各环境坐标沿 x 平移 env*stride 做环境间隔离。"""
    g = wp.tid()
    env = g / n
    pts[g] = wp.vec3(x[g] + float(env) * stride, y[g], 0.0)


@wp.func
def _b_nearest_dense(
    env: int,
    i: int,
    n: int,
    tick: int,
    base: int,
    x: wp.array(dtype=wp.float32),
    y: wp.array(dtype=wp.float32),
    alive: wp.array(dtype=wp.int32),
    side: wp.array(dtype=wp.int32),
    tracks: wp.array(dtype=wp.int32),
) -> int:
    """稠密全扫描共享航迹最近敌方（蓝方追击转向用）。

    C2 航迹全方共享：目标出生时只在某友邻探测距内，开火单位自身可距
    目标任意远，故无距离上界、不能套半径查询。仅蓝方调用且无随机数，
    规模 O(蓝*n)，保留稠密。
    """
    si = side[base + i]
    xi = x[base + i]
    yi = y[base + i]
    best = int(-1)
    best_d = float(1.0e30)
    for k in range(base, base + n):
        j = k - base
        if side[k] == si or alive[k] != 1:
            continue
        if tracks[(env * 2 + si) * n + j] > tick:
            dx = x[k] - xi
            dy = y[k] - yi
            d = wp.sqrt(dx * dx + dy * dy)
            if d < best_d:
                best_d = d
                best = j
    return best


@wp.func
def _b_nearest_grid(
    grid: wp.uint64,
    env: int,
    i: int,
    n: int,
    tick: int,
    base: int,
    x: wp.array(dtype=wp.float32),
    y: wp.array(dtype=wp.float32),
    alive: wp.array(dtype=wp.int32),
    side: wp.array(dtype=wp.int32),
    tracks: wp.array(dtype=wp.int32),
    stride: float,
    radius: float,
) -> int:
    """网格查询半径内的共享航迹最近敌方；返回 env 内局部索引，无则 -1。

    交战段只关心射程内目标：全局最近者在射程外时开火必然不发生，
    故半径=武器射程的查询与稠密全扫描语义等价。等距取较小索引消歧。
    """
    g = base + i
    si = side[g]
    qx = x[g] + float(env) * stride
    query = wp.hash_grid_query(grid, wp.vec3(qx, y[g], 0.0), radius)
    best = int(-1)
    best_d = float(1.0e30)
    for k in query:
        if k / n != env:
            continue  # 哈希碰撞/环绕落入的其他环境点
        j = k - base
        if side[k] == si or alive[k] != 1:
            continue
        if tracks[(env * 2 + si) * n + j] > tick:
            dx = x[k] - x[g]
            dy = y[k] - y[g]
            d = wp.sqrt(dx * dx + dy * dy)
            if d <= radius and (d < best_d or (d == best_d and j < best)):
                best_d = d
                best = j
    return best


@wp.kernel
def k_b_cooldown(
    cd: wp.array(dtype=wp.int32), alive: wp.array(dtype=wp.int32),
    active: wp.array(dtype=wp.int32), n: int,
):
    g = wp.tid()
    env = g / n
    if active[env] == 0:
        return
    if alive[g] == 1 and cd[g] > 0:
        cd[g] = cd[g] - 1


@wp.kernel
def k_b_sense(
    x: wp.array(dtype=wp.float32),
    y: wp.array(dtype=wp.float32),
    alive: wp.array(dtype=wp.int32),
    side: wp.array(dtype=wp.int32),
    sensor: wp.array(dtype=wp.float32),
    detect: wp.array(dtype=wp.float32),
    ttl: wp.array(dtype=wp.int32),
    tracks: wp.array(dtype=wp.int32),
    active: wp.array(dtype=wp.int32),
    grid: wp.uint64,
    tick: int,
    vis: wp.array(dtype=wp.float32),
    n: int,
    stride: float,
    seed0: wp.array(dtype=wp.int32),
):
    g = wp.tid()
    env = g / n
    i = g - env * n
    if active[env] == 0 or alive[g] != 1:
        return
    si = side[g]
    reach = sensor[g] * vis[env]
    qx = x[g] + float(env) * stride
    query = wp.hash_grid_query(grid, wp.vec3(qx, y[g], 0.0), reach)
    for k in query:
        if k / n != env:
            continue
        j = k - env * n
        if side[k] == si or alive[k] != 1:
            continue
        dx = x[k] - x[g]
        dy = y[k] - y[g]
        d = wp.sqrt(dx * dx + dy * dy)
        if d > reach:
            continue
        # 每候选独立流（键含 j），网格遍历顺序不影响判定
        cstate = wp.rand_init(seed0[env], SENSE_KEY_OFF + (tick * n + i) * n + j)
        r = wp.randf(cstate)
        prob = detect[g] * (1.0 - d / reach)
        if r < prob:
            wp.atomic_max(tracks, (env * 2 + si) * n + j, tick + ttl[g])


@wp.kernel
def k_b_clear(buf: wp.array(dtype=wp.int32)):
    buf[wp.tid()] = 0


@wp.kernel
def k_b_move(
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
    active: wp.array(dtype=wp.int32),
    oxs: wp.array(dtype=wp.float32),
    oys: wp.array(dtype=wp.float32),
    tick: int,
    vis: wp.array(dtype=wp.float32),
    n: int,
    gn: int,
    sx: float,
    sy: float,
    bx: float,
    by: float,
    water: float,
):
    g = wp.tid()
    env = g / n
    i = g - env * n
    if active[env] == 0 or alive[g] != 1 or under_repair[g] == 1:
        return
    base = env * n
    if side[g] == RED:
        dx = oxs[env] - x[g]
        dy = oys[env] - y[g]
        d = wp.sqrt(dx * dx + dy * dy)
        if d > 0.0:
            heading[g] = wp.atan2(dy, dx)
            f = _move_factor(heights, passab, gn, sx, sy, water, x[g], y[g])
            if damaged[g] == 1:
                f = f * 0.5
            step = wp.min(speed[g] * f, d)
            x[g] = wp.clamp(x[g] + step * dx / d, 0.0, bx)
            y[g] = wp.clamp(y[g] + step * dy / d, 0.0, by)
    else:
        # 追击转向需要全局最近航迹（C2 共享下无距离上界），保留稠密
        tgt = _b_nearest_dense(
            env, i, n, tick, base, x, y, alive, side, tracks
        )
        if tgt < 0:
            return
        k = base + tgt
        dx = x[k] - x[g]
        dy = y[k] - y[g]
        d = wp.sqrt(dx * dx + dy * dy)
        heading[g] = wp.atan2(dy, dx)
        if d > weapon[g] * vis[env]:
            f = _move_factor(heights, passab, gn, sx, sy, water, x[g], y[g])
            step = wp.min(speed[g] * f, d)
            x[g] = wp.clamp(x[g] + step * dx / d, 0.0, bx)
            y[g] = wp.clamp(y[g] + step * dy / d, 0.0, by)


@wp.kernel
def k_b_engage(
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
    want_kill: wp.array(dtype=wp.int32),
    want_dmg: wp.array(dtype=wp.int32),
    active: wp.array(dtype=wp.int32),
    tick: int,
    vis: wp.array(dtype=wp.float32),
    n: int,
    seed0: wp.array(dtype=wp.int32),
    grid: wp.uint64,
    stride: float,
):
    g = wp.tid()
    env = g / n
    i = g - env * n
    if (
        active[env] == 0
        or alive[g] != 1
        or under_repair[g] == 1
        or cooldown[g] > 0
    ):
        return
    base = env * n
    # 只需射程内最近航迹：全局最近在射程外则不可能开火，网格半径=射程
    wrange = weapon[g] * vis[env]
    tgt = _b_nearest_grid(
        grid, env, i, n, tick, base, x, y, alive, side, tracks, stride, wrange
    )
    if tgt < 0:
        return
    k = base + tgt
    dx = x[k] - x[g]
    dy = y[k] - y[g]
    d = wp.sqrt(dx * dx + dy * dy)
    if d > weapon[g] * vis[env]:
        return
    state = wp.rand_init(seed0[env], tick * n + n + i)
    r = wp.randf(state)
    if r < pk[g]:
        wp.atomic_max(want_kill, k, 1)
    elif r < pk[g] + 0.25:
        wp.atomic_max(want_dmg, k, 1)
    cooldown[g] = reload[g]


@wp.kernel
def k_b_apply(
    alive: wp.array(dtype=wp.int32),
    damaged: wp.array(dtype=wp.int32),
    under_repair: wp.array(dtype=wp.int32),
    want_kill: wp.array(dtype=wp.int32),
    want_dmg: wp.array(dtype=wp.int32),
    active: wp.array(dtype=wp.int32),
    n: int,
):
    g = wp.tid()
    env = g / n
    if active[env] == 0:
        return
    if want_kill[g] == 1:
        alive[g] = 0
        damaged[g] = 0
        under_repair[g] = 0
    elif want_dmg[g] == 1:
        damaged[g] = 1


class WarpBatchKernel:
    """同一想定 E 个复制在设备上锁步进推进；host 端仅保留指标所需镜像。"""

    def __init__(
        self,
        scenarios: Scenario | list[Scenario],
        seeds: list[int],
        device: str | None = None,
        verbose: bool = False,
    ) -> None:
        """scenarios 可为单个想定（E 个相同复制）或每环境一个想定
        （灵敏度设计点；要求各想定编制规模 n 与地形一致，参数/能见度可不同）。"""
        if isinstance(scenarios, Scenario):
            scenarios = [scenarios] * len(seeds)
        else:
            scenarios = list(scenarios)
        self.scs = scenarios
        self.seeds = list(seeds)
        self.E = len(seeds)
        assert len(self.scs) == self.E, "想定数必须与种子数一致"
        self.dev = resolve_device(device)
        self.verbose = verbose

        # host 镜像：每环境一个 SimState（供 SimPy 与最终指标），其关键列
        # 指向批量大数组的切片视图，零拷贝
        self.states = [SimState(sc) for sc in self.scs]
        ref = self.states[0]
        self.n = ref.n
        if any(st.n != self.n for st in self.states):
            raise ValueError("批量内核要求各想定编制规模 n 一致")
        E, n = self.E, self.n
        self.h_x = np.zeros(E * n, dtype=np.float32)
        self.h_y = np.zeros(E * n, dtype=np.float32)
        self.h_alive = np.ones(E * n, dtype=np.int32)
        self.h_damaged = np.zeros(E * n, dtype=np.int32)
        self.h_repair = np.zeros(E * n, dtype=np.int32)
        for e, st in enumerate(self.states):
            sl = slice(e * n, (e + 1) * n)
            self.h_x[sl] = st.x
            self.h_y[sl] = st.y
            st.x = self.h_x[sl].view()
            st.y = self.h_y[sl].view()
            st.alive = self.h_alive[sl].view().astype(bool)  # astype 会复制
            st.damaged = np.zeros(n, dtype=bool)
            st.under_repair = np.zeros(n, dtype=bool)
        # 注意：astype(bool) 得到独立副本，sustain 在该副本上改，
        # 每 tick 由 _sync_to_device/_sync_from_device 与 i32 大数组交换

        self._sustain = []
        from ..sustainment.repair import SustainmentModel

        for sc, st in zip(self.scs, self.states):
            rng = np.random.default_rng(self.seeds[len(self._sustain)])
            self._sustain.append(SustainmentModel(sc, st, rng))

        d = self.dev

        def col(name, dtype):
            # 每环境参数可不同（灵敏度 per-env 参数列），按 env 顺序拼接
            stacked = np.concatenate(
                [np.asarray(getattr(st, name), dtype=dtype) for st in self.states]
            )
            return wp.array(
                stacked,
                dtype={np.float32: wp.float32, np.int32: wp.int32}[dtype],
                device=d,
            )

        self.a_x = wp.array(self.h_x, dtype=wp.float32, device=d)
        self.a_y = wp.array(self.h_y, dtype=wp.float32, device=d)
        self.a_heading = wp.array(
            np.concatenate([np.asarray(st.heading, dtype=np.float32) for st in self.states]),
            dtype=wp.float32, device=d,
        )
        self.a_alive = wp.array(self.h_alive, dtype=wp.int32, device=d)
        self.a_damaged = wp.array(self.h_damaged, dtype=wp.int32, device=d)
        self.a_repair = wp.array(self.h_repair, dtype=wp.int32, device=d)
        self.a_cd = wp.array(np.zeros(E * n, dtype=np.int32), dtype=wp.int32, device=d)
        self.a_side = col("side", np.int32)
        self.a_sensor = col("sensor_range", np.float32)
        self.a_weapon = col("weapon_range", np.float32)
        self.a_pk = col("pk", np.float32)
        self.a_speed = col("speed", np.float32)
        self.a_reload = col("reload_ticks", np.int32)
        self.a_detect = col("detect_prob", np.float32)
        self.a_ttl = col("track_ttl", np.int32)
        # 每环境能见度（灵敏度设计点可不同）
        self.h_vis = np.asarray(
            [sc.environment.visibility_factor for sc in self.scs], dtype=np.float32
        )
        self.a_vis = wp.array(self.h_vis, dtype=wp.float32, device=d)
        # 每环境随机流种子（任意偏移；CRN 时多环境可取相同种子）
        self.a_seed0 = wp.array(np.asarray(self.seeds, dtype=np.int32), dtype=wp.int32, device=d)

        self.a_tracks = wp.zeros(E * 2 * n, dtype=wp.int32, device=d)
        self.a_kill = wp.zeros(E * n, dtype=wp.int32, device=d)
        self.a_dmg = wp.zeros(E * n, dtype=wp.int32, device=d)
        self.a_active = wp.array(np.ones(E, dtype=np.int32), dtype=wp.int32, device=d)
        # 每环境阶段序列（灵敏度只改参数，阶段一致；结构上允许不同）
        self.phase_lists = [sc.mission.phase_list for sc in self.scs]
        self.phase_idx = np.zeros(E, dtype=np.int32)
        self.accomplished = np.zeros(E, dtype=bool)
        self.h_ox = np.asarray(
            [pl[0].objective[0] for pl in self.phase_lists], dtype=np.float32
        )
        self.h_oy = np.asarray(
            [pl[0].objective[1] for pl in self.phase_lists], dtype=np.float32
        )
        self.a_ox = wp.array(self.h_ox, dtype=wp.float32, device=d)
        self.a_oy = wp.array(self.h_oy, dtype=wp.float32, device=d)

        from ..kernel.terrain import HeightField

        t = HeightField.from_spec(self.scs[0].terrain)
        self.terrain = t
        self.gn = t.n
        self.a_h = wp.array(
            np.asarray(t.heights.reshape(-1), dtype=np.float32), dtype=wp.float32, device=d
        )
        self.a_p = wp.array(
            np.asarray(t.passability.reshape(-1), dtype=np.float32), dtype=wp.float32, device=d
        )
        self.bound = self.scs[0].terrain.size_m
        self.duration = self.scs[0].mission.duration_ticks
        self.tick = 0

        # 空间哈希网格（切片 4）：所有环境共用一张表，沿 x 平移隔离。
        # 单元格边长取最大探测 reach（武器射程均 ≤ 探测距）；sense 与
        # engage 的查询半径都不超过 cell，3×3 邻格 stencil 足够。
        # 蓝方追击转向用稠密扫描（C2 共享航迹无距离上界，见 _b_nearest_dense）。
        h_sensor = np.concatenate(
            [np.asarray(st.sensor_range, dtype=np.float32) for st in self.states]
        )
        vis_full = np.repeat(self.h_vis, n).astype(np.float32)
        self.cell = float((h_sensor * vis_full).max())
        # 环境间隔：地形宽 + 4 单元格（查询环至多外探约 2 cell，不跨环境）
        self.stride = float(self.bound[0]) + 4.0 * self.cell
        dim_x = int(np.ceil(E * self.stride / self.cell)) + 2
        dim_y = int(np.ceil(float(self.bound[1]) / self.cell)) + 2
        self.grid = wp.HashGrid(dim_x, dim_y, 1, device=d)
        self.a_pts = wp.zeros(E * n, dtype=wp.vec3, device=d)

    def run(self) -> list[dict]:
        while self.active_count() > 0 and self.tick < self.duration:
            self._step()
        return self._collect_metrics()

    def active_count(self) -> int:
        if self.tick == 0:
            return self.E
        return int(self._host_active.sum())

    def _step(self) -> None:
        E, n, d = self.E, self.n, self.dev
        self.tick += 1
        tick = self.tick

        # 1) 保障：每环境 SimPy 推进到 tick（仅 active 环境；冻结环境不再产生事件）
        for e, sust in enumerate(self._sustain):
            if self._host_active[e]:
                sust.advance(tick)
        self._push_repair()

        common = dict(device=d)
        wp.launch(k_b_cooldown, dim=E * n,
                  inputs=[self.a_cd, self.a_alive, self.a_active, n], **common)
        # 重建空间哈希网格（坐标平移隔离各环境）
        wp.launch(
            k_b_gridpoints, dim=E * n,
            inputs=[self.a_pts, self.a_x, self.a_y, n, float(self.stride)],
            **common,
        )
        self.grid.build(self.a_pts, float(self.cell))
        wp.launch(
            k_b_sense, dim=E * n,
            inputs=[self.a_x, self.a_y, self.a_alive, self.a_side, self.a_sensor,
                    self.a_detect, self.a_ttl, self.a_tracks, self.a_active,
                    self.grid.id, tick, self.a_vis, n, float(self.stride),
                    self.a_seed0],
            **common,
        )
        wp.launch(k_b_clear, dim=E * n, inputs=[self.a_kill], **common)
        wp.launch(k_b_clear, dim=E * n, inputs=[self.a_dmg], **common)
        wp.launch(
            k_b_move, dim=E * n,
            inputs=[self.a_x, self.a_y, self.a_heading, self.a_alive, self.a_damaged,
                    self.a_repair, self.a_side, self.a_speed, self.a_weapon, self.a_tracks,
                    self.a_h, self.a_p, self.a_active, self.a_ox, self.a_oy,
                    tick, self.a_vis, n, self.gn,
                    float(self.terrain.size[0]), float(self.terrain.size[1]),
                    float(self.bound[0]), float(self.bound[1]),
                    float(self.terrain.water_level)],
            **common,
        )
        # engage 在 move 之后：单位每 tick 可机动一个步长，必须用机动后
        # 位置重建网格，否则新进入射程的目标还留在旧格里被漏掉
        wp.launch(
            k_b_gridpoints, dim=E * n,
            inputs=[self.a_pts, self.a_x, self.a_y, n, float(self.stride)],
            **common,
        )
        self.grid.build(self.a_pts, float(self.cell))
        wp.launch(
            k_b_engage, dim=E * n,
            inputs=[self.a_x, self.a_y, self.a_alive, self.a_repair, self.a_side, self.a_cd,
                    self.a_pk, self.a_weapon, self.a_reload, self.a_tracks,
                    self.a_kill, self.a_dmg, self.a_active,
                    tick, self.a_vis, n, self.a_seed0,
                    self.grid.id, float(self.stride)],
            **common,
        )
        wp.launch(
            k_b_apply, dim=E * n,
            inputs=[self.a_alive, self.a_damaged, self.a_repair,
                    self.a_kill, self.a_dmg, self.a_active, n],
            **common,
        )
        wp.synchronize_device(d)

        # 2) 设备 -> host 批量镜像
        self.h_x = self.a_x.numpy()
        self.h_y = self.a_y.numpy()
        self.h_alive = self.a_alive.numpy()
        self.h_damaged = self.a_damaged.numpy()
        self._pull_repair()

        # 3) 新战损登记各环境 SimPy 抢修通道
        dmg_idx = np.flatnonzero(self.a_dmg.numpy())
        for g in dmg_idx:
            e, i = divmod(int(g), n)
            if self.h_alive[g] and self._host_active[e]:
                self._sustain[e].combat_damage(i)

        # 4) 阶段推进（向量化）与 active 更新
        self._update_phases()

    # ---- host 镜像/阶段 ----

    @property
    def _host_active(self) -> np.ndarray:
        if not hasattr(self, "_active_cache"):
            self._active_cache = np.ones(self.E, dtype=np.int32)
        return self._active_cache

    def _push_repair(self) -> None:
        rep = np.zeros(self.E * self.n, dtype=np.int32)
        dam = np.zeros(self.E * self.n, dtype=np.int32)
        for e, st in enumerate(self.states):
            sl = slice(e * self.n, (e + 1) * self.n)
            rep[sl] = st.under_repair
            dam[sl] = st.damaged
        self.a_repair.assign(rep)
        self.a_damaged.assign(dam)

    def _pull_repair(self) -> None:
        for e, st in enumerate(self.states):
            sl = slice(e * self.n, (e + 1) * self.n)
            st.x = self.h_x[sl]
            st.y = self.h_y[sl]
            st.alive = self.h_alive[sl].astype(bool)
            st.damaged = self.h_damaged[sl].astype(bool)
            st.tick = self.tick

    def _update_phases(self) -> None:
        E, n = self.E, self.n
        side = np.asarray(self.states[0].side)  # 各环境编制相同
        red = side == RED
        alive_b = self.h_alive.reshape(E, n).astype(bool)
        # 与单环境 WarpKernel._update_phases 一致用 float64 算距离，
        # 避免成功半径边界上 f32/f64 判定翻转导致结束 tick 不一致
        xb = self.h_x.reshape(E, n).astype(np.float64)
        yb = self.h_y.reshape(E, n).astype(np.float64)
        red_alive = alive_b & red[None, :]
        red_any = red_alive.any(axis=1)

        ox = self.h_ox.copy()
        oy = self.h_oy.copy()
        for e in range(E):
            if not self._host_active[e]:
                continue
            phases = self.phase_lists[e]
            last = len(phases) - 1
            rx = xb[e][red_alive[e]]
            ry = yb[e][red_alive[e]]
            while self.phase_idx[e] < last:
                ph = phases[int(self.phase_idx[e])]
                if rx.size == 0 or np.hypot(
                    rx - ph.objective[0], ry - ph.objective[1]
                ).min() > ph.success_radius_m:
                    break
                self.phase_idx[e] += 1
                nxt = phases[int(self.phase_idx[e])]
                ox[e], oy[e] = nxt.objective[0], nxt.objective[1]
            ph = phases[int(self.phase_idx[e])]
            if (
                rx.size
                and self.phase_idx[e] == last
                and np.hypot(rx - ph.objective[0], ry - ph.objective[1]).min()
                <= ph.success_radius_m
            ):
                self.accomplished[e] = True
            st = self.states[e]
            st.phase = int(self.phase_idx[e])
            st.objective = np.asarray(phases[int(self.phase_idx[e])].objective)
            st.alive = self.h_alive[e * n:(e + 1) * n].astype(bool)

        # 末段达成 / 红方被歼 / 到时 -> 冻结
        active = (self.tick < self.duration) & red_any & (~self.accomplished)
        self._active_cache = active.astype(np.int32)
        self.a_active.assign(self._active_cache)
        self.h_ox, self.h_oy = ox.astype(np.float32), oy.astype(np.float32)
        self.a_ox.assign(self.h_ox)
        self.a_oy.assign(self.h_oy)

    def _collect_metrics(self) -> list[dict]:
        rows = []
        for e, st in enumerate(self.states):
            st.alive = self.h_alive[e * self.n:(e + 1) * self.n].astype(bool)
            st.damaged = self.h_damaged[e * self.n:(e + 1) * self.n].astype(bool)
            rows.append(per_run_metrics(st, bool(self.accomplished[e])))
        return rows


def run_batch(
    scenarios: Scenario | list[Scenario],
    seeds: list[int],
    device: str | None = None,
    verbose: bool = False,
) -> list[dict]:
    """设备端批量运行。

    - 同一想定多次复制：传单个 Scenario + seeds 列表；
    - 灵敏度设计点批量：传与 seeds 等长的想定列表（每环境一个扰动后想定）。
    返回与 envs 同序的指标字典列表。
    """
    return WarpBatchKernel(scenarios, seeds, device=device, verbose=verbose).run()


def run_replications_batch(
    scenario: Scenario,
    base_seed: int,
    n_runs: int,
    device: str | None = None,
    batch_size: int = 128,
) -> list[dict]:
    """同一想定 n_runs 次复制分批运行（run_replications 的批量便捷封装）。"""
    rows: list[dict] = []
    for start in range(0, n_runs, batch_size):
        count = min(batch_size, n_runs - start)
        seeds = [base_seed + start + r for r in range(count)]
        rows.extend(run_batch(scenario, seeds, device=device))
    return rows
