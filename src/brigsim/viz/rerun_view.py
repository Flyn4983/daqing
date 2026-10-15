"""M2 写实 3D 可视化（rerun）：高程地形 + 气象粒子 + GLB 战车 + 战术要素。

借鉴 VBS4 / Steel Beasts / VR-Vantage / OneSAF AAR 的设计：
- 主视图为斜视 3D 战场（写实地形配色、暗色背景），右侧时间序列与交战记录；
- 红/蓝遵循 NATO/MIL-STD 习惯色；单位按编制层级文字标注；
- 射界扇面、目标信标、毁伤烟柱、雨雾粒子叠加战场态势。

输入仍是统一事件日志（snapshot 含 x/y/heading/alive/damaged）。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import rerun as rr
import rerun.blueprint as rrb

from ..data.schema import Scenario
from ..kernel.terrain import HeightField
from .models import ensure_model, ground_offset

SIDE_COLOR = {0: (214, 69, 55), 1: (64, 132, 240)}
SIDE_COLOR_A = {0: (214, 69, 55, 22), 1: (64, 132, 240, 22)}
DAMAGED_C = (239, 170, 23)
OBJECTIVE_C = (47, 220, 120)
SHOT_MISS = (150, 150, 150)

# 写实地形配色（低到高 + 坡度岩石）
C_WATER = (36, 78, 116)
C_SAND = (196, 180, 132)
C_GRASS = (96, 124, 62)
C_FOREST = (58, 88, 46)
C_ROCK = (120, 108, 92)
C_SNOW = (228, 230, 228)


REPAIR_C = (168, 85, 247)  # 在修单位图元色（紫）
WRECK_C = (72, 72, 80)  # 战损残骸图元色（深灰）

# C7-a1 战损分级状态色：M 失动深蓝 / F 火力受损沿用琥珀 / K 战毁棕黑残骸 /
# 重损后送深红，肉眼一屏可分
M_KILL_C = (50, 110, 210)
WRECK_K_C = (96, 46, 34)
REAR_C = (180, 60, 60)

# C7-a0 合成兵种图层配色
RADAR_C = (90, 210, 230)    # 雷达探测包络（青）
EW_C = (214, 90, 230)       # 电磁干扰区（品红）
AURA_C = (90, 230, 150)     # C2 指挥光环（绿）
DECOY_C = (235, 195, 70)    # 假目标（琥珀黄）
SMOKE_BAND_C = (208, 212, 218, 46)  # 屏烟带（浅灰半透）

WEATHER_NAME = {
    "clear": "晴朗", "overcast": "多云", "rain": "降雨", "heavy_rain": "暴雨",
    "snow": "降雪", "fog": "雾", "heavy_fog": "浓雾", "sand": "沙尘", "haze": "霾",
}
ILLUM_NAME = {"day": "昼间", "dusk": "黄昏", "night": "夜间"}
AIR_KIND_LABEL = {"uav": "无人机", "loitering": "巡飞弹", "helo": "武直"}
AIR_DONE_STATES = {"killed", "expended", "exiting"}
OBSTACLE_STYLE = {
    "minefield": ((200, 84, 60), "雷场"),
    "dragons_teeth": ((239, 170, 23), "三角锥"),
    "anti_tank_ditch": ((120, 120, 132), "反坦克壕"),
}
IMPACT_C = (240, 130, 40)
LAUNCH_C = (248, 222, 110)
INTERCEPT_C = (110, 226, 240)
AIRSTRIKE_C = (238, 78, 62)
BREACH_C = (120, 232, 160)


def visualize(
    snapshots: pd.DataFrame,
    engagements: pd.DataFrame,
    *,
    scenario: Scenario,
    terrain: HeightField,
    rrd_path: str | Path | None = None,
    spawn: bool = False,
    eye: tuple[float, float, float] | None = None,
    look: tuple[float, float, float] | None = None,
    supply: pd.DataFrame | None = None,
    air: pd.DataFrame | None = None,
    effects: pd.DataFrame | None = None,
    app_id: str = "brigsim",
    use_models: bool = True,
    tick_stride: int = 1,
    embed: bool = False,
) -> None:
    """录制 3D 回放。

    use_models=False 时不嵌 GLB（C6 大屏轻量 rrd）：单位用盒体图元，
    颜色区分 正常（方别色）/受损（琥珀）/在修（紫）/战损残骸（深灰）。
    tick_stride>1 时动态帧隔 tick 记录（rerun latest-at 自动保持，体积减半），
    静态要素照常、最后一帧始终记录。
    embed=True 时写入大屏 iframe 专用极简蓝图（3D 视口独占，面板全收起）；
    默认 False 保持 ``brigsim replay`` 桌面蓝图（侧栏时序/交战日志 + 时间轴）。
    """
    rr.init(app_id, spawn=spawn)
    if rrd_path is not None:
        rr.save(str(rrd_path))
    rr.send_blueprint(
        _make_embed_blueprint(eye, look) if embed else _make_blueprint(eye, look)
    )

    info = _unit_info(scenario)
    decoy_ids = {
        u.unit_id
        for side_name in ("red", "blue")
        for u in scenario.forces.get(side_name, [])
        if u.decoy
    }
    _log_terrain(terrain)
    _log_passability(terrain)
    _log_static_units(info, terrain, use_models)
    _log_objective(scenario, terrain)
    _log_repair_stations(scenario, terrain)
    _log_obstacles(scenario, terrain)
    _log_decoys(scenario, terrain)
    _log_weather_header(scenario)

    # C7-a0：空中航迹与战场效果点（None 或空表时对应图层静默）
    air_trails: dict[str, list] = {}
    air_done: set[str] = set()
    # 屏烟：想定中的半径/持续时间 + 效果帧给出的实际起烟 tick
    smoke_specs = {
        (round(z.at[0]), round(z.at[1])): (float(z.radius_m), int(z.duration_ticks))
        for z in scenario.battlefield.smoke_orders
    }
    smoke_start: dict[tuple, int] = {}
    effects = effects if effects is not None and not effects.empty else None
    air = air if air is not None and not air.empty else None

    pos_table = {
        (int(t), uid): (float(x), float(y))
        for t, uid, x, y in snapshots[["tick", "unit_id", "x", "y"]].itertuples(False)
    }
    # 各型装备的接地高度（按实际 GLB 包围盒，支持同名资产无缝替换）
    z_off = (
        {typ: ground_offset(typ) for typ in {v[0] for v in info.values()}}
        if use_models
        else {}
    )
    model_state: dict[str, str] = {}
    phases = scenario.mission.phase_list
    shown_phase = -1
    all_ticks = sorted(int(t) for t in snapshots["tick"].unique())
    last_tick = all_ticks[-1] if all_ticks else 0

    for tick, grp in snapshots.groupby("tick", sort=True):
        tick = int(tick)
        if tick_stride > 1 and tick != last_tick and tick % tick_stride != 0:
            continue
        rr.set_time("tick", sequence=tick)
        rr.set_time("sim_clock", duration=float(tick * scenario.mission.dt_seconds))

        if "phase" in grp.columns:
            ph_idx = int(grp["phase"].iloc[0])
            if ph_idx != shown_phase and ph_idx < len(phases):
                rr.log(
                    "feed/phase",
                    rr.TextLog(
                        f"T+{tick:03d} 进入阶段 {ph_idx + 1}/{len(phases)}：{phases[ph_idx].name}",
                        level="WARN" if ph_idx < len(phases) - 1 else "CRITICAL",
                        color=(230, 170, 60) if ph_idx < len(phases) - 1 else (47, 220, 120),
                    ),
                )
                shown_phase = ph_idx

        cur = grp.set_index("unit_id")
        xs = cur["x"].to_numpy()
        ys = cur["y"].to_numpy()
        zs = terrain.height_at(xs, ys)
        alive_idx, dead_idx = [], []
        if use_models:
            for k, uid in enumerate(cur.index):
                ent = f"world/units/{uid}"
                alive, damaged = bool(cur.at[uid, "alive"]), bool(cur.at[uid, "damaged"])
                rr.log(
                    ent,
                    rr.Transform3D(
                        # 按模型实际包围盒接地
                        translation=[
                            float(xs[k]),
                            float(ys[k]),
                            float(zs[k]) + z_off[info[uid][0]],
                        ],
                        rotation=rr.RotationAxisAngle(
                            axis=[0, 0, 1], radians=float(cur.at[uid, "heading"])
                        ),
                    ),
                )
                has_g = "damage_grade" in cur.columns and "disposition" in cur.columns
                if not alive:
                    # C7-a1：K 战毁（grade=3）棕黑，与普通伤亡深灰区分
                    state = "wreck_k" if (
                        has_g and int(cur.at[uid, "damage_grade"]) == 3
                    ) else "dead"
                elif damaged and has_g:
                    if int(cur.at[uid, "disposition"]) == 4:
                        state = "rear"       # 重损后送（深红）
                    elif int(cur.at[uid, "damage_grade"]) == 1:
                        state = "m_kill"     # M 失动（深蓝）
                    else:
                        state = "damaged"    # F 火力受损（琥珀）
                else:
                    state = "damaged" if damaged else "ok"
                if model_state.get(uid) != state:
                    _tint_model(ent, info[uid][0], state)
                    model_state[uid] = state
                (dead_idx if not alive else alive_idx).append(k)
        else:
            for k, uid in enumerate(cur.index):
                alive = bool(cur.at[uid, "alive"])
                (dead_idx if not alive else alive_idx).append(k)
            _log_units_primitives(cur, zs, info)

        _log_markers(cur=cur, zs=zs, info=info, decoy_ids=decoy_ids)
        _log_fans(cur, zs, info, outline=embed)
        _log_coverages(tick, cur, zs, info, thin=embed)
        _log_smoke(tick, cur, zs)
        _log_weather(tick, scenario, terrain)
        _log_engagements(tick, engagements, pos_table, terrain, info)
        _log_effects(tick, effects, terrain, smoke_specs, smoke_start)
        if air is not None:
            _log_air(tick, air, air_trails, air_done)
        _log_metrics(tick, cur)
        if supply is not None and not supply.empty:
            _log_supply(tick, supply)
        if tick == 0 or tick % 10 == 0:
            _log_tally(tick, cur)

    rr.log("feed/engagements", rr.TextLog("—— 推演结束 ——", level="INFO"))


# ---------- 静态要素 ----------


def _unit_info(scenario: Scenario) -> dict[str, tuple[str, int, str, float, str, float]]:
    types = scenario.platform_types
    out: dict[str, tuple[str, int, str, float, str, float]] = {}
    for side_id, side_name in ((0, "red"), (1, "blue")):
        for u in scenario.forces.get(side_name, []):
            t = types[u.type]
            out[u.unit_id] = (
                u.type, side_id, u.org or u.unit_id,
                t.weapon_range_m, t.role, t.coverage_radius_m,
            )
    return out


def _ground_texture(size: int = 1024, seed: int = 11) -> np.ndarray:
    """中性灰度地表细节纹理（泥地斑驳+碎石颗粒），与顶点色相乘保留高程分层配色。"""
    from opensimplex import OpenSimplex

    coarse = np.zeros((size, size))
    lin = np.linspace(0.0, 26.0, size, endpoint=False)
    amp, freq, total = 1.0, 1.0, 0.0
    noise = OpenSimplex(seed=seed)
    for _ in range(4):
        coarse += amp * np.asarray(noise.noise2array(lin * freq, lin * freq))
        total += amp
        amp *= 0.5
        freq *= 2.3
    coarse /= total
    fine_noise = OpenSimplex(seed=seed + 1)
    flin = np.linspace(0.0, 130.0, size, endpoint=False)
    fine = np.asarray(fine_noise.noise2array(flin, flin))
    shade = (1.0 + coarse * 0.24 + fine * 0.07).clip(0.55, 1.5)
    return np.clip(np.full((size, size, 3), 188.0) * shade[..., None], 0, 255).astype(np.uint8)


def _log_terrain(t: HeightField) -> None:
    # 视觉网格 4 倍细分并采样 height_at：保证渲染三角面与内核双线性高程一致，
    # 否则 156m 粗网格的三角面与插值面能差数米，单位会被埋入地下
    n = (t.n - 1) * 4 + 1
    dx_world = t.size[0] / (n - 1)
    xs = np.linspace(0, t.size[0], n)
    ys = np.linspace(0, t.size[1], n)
    gx, gy = np.meshgrid(xs, ys)
    heights = t.height_at(gx.ravel(), gy.ravel()).reshape(n, n)
    verts = np.column_stack([gx.ravel(), gy.ravel(), heights.ravel()])

    # 坡度（中心差分）
    dhd_y, dhd_x = np.gradient(heights, dx_world, dx_world)
    slope = np.hypot(dhd_x, dhd_y).ravel()
    h = heights.ravel()
    colors = np.zeros((n * n, 4), dtype=np.uint8)
    colors[:] = C_GRASS + (255,)
    colors[(h > 40) & (h < 110)] = C_FOREST + (255,)
    colors[h >= 110] = C_ROCK + (255,)
    colors[h >= 185] = C_SNOW + (255,)
    colors[(h < t.water_level + 4) & (h >= t.water_level)] = C_SAND + (255,)
    colors[h < t.water_level] = C_WATER + (255,)
    rocky = (slope > 0.35) & (h >= t.water_level)
    colors[rocky] = C_ROCK + (255,)

    # 顶点法线：让方向光在坡面上产生明暗，地形不再是塑料平面
    normals = np.zeros((n * n, 3))
    normals[:, 0] = -dhd_x.ravel()
    normals[:, 1] = -dhd_y.ravel()
    normals[:, 2] = 1.0
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)

    # 归一化 UV（0..1，纹理覆盖全战场，约 10m/texel）
    uvs = np.column_stack([gx.ravel() / t.size[0], gy.ravel() / t.size[1]])

    ii, jj = np.meshgrid(np.arange(n - 1), np.arange(n - 1))
    a = (jj * n + ii).ravel()
    idx = np.stack(
        [
            np.column_stack([a, a + 1, a + n + 1]),
            np.column_stack([a, a + n + 1, a + n]),
        ],
        axis=1,
    ).reshape(-1, 3)
    rr.log(
        "world/terrain",
        rr.Mesh3D(
            vertex_positions=verts,
            triangle_indices=idx,
            vertex_colors=colors,
            vertex_normals=normals,
            vertex_texcoords=uvs,
            albedo_texture=_ground_texture(),
        ),
        static=True,
    )
    # 半透明水面
    qx, qy = np.meshgrid([0, t.size[0]], [0, t.size[1]])
    water = np.column_stack([qx.ravel(), qy.ravel(), np.full(4, t.water_level)])
    rr.log(
        "world/water",
        rr.Mesh3D(
            vertex_positions=water,
            triangle_indices=[[0, 1, 3], [0, 3, 2]],
            vertex_colors=[(30, 70, 110, 120)] * 4,
        ),
        static=True,
    )


def _pass_quads(t: HeightField, mask: np.ndarray, color, deck: bool):
    """把 mask 选中的通行性栅格节点构造成贴地四边形贴片。

    deck=True 时 z 抬到水面以上（桥梁板面），否则贴地形面 0.6m（道路）。
    """
    cell = t.size[0] / (t.n - 1)
    half = cell / 2
    js, is_ = np.where(mask)
    if is_.size == 0:
        return None
    cx, cy = is_ * cell, js * cell
    ground = t.heights[js, is_]
    cz = np.maximum(ground, t.water_level) + 0.8 if deck else ground + 0.6
    offs = [(-half, -half), (half, -half), (half, half), (-half, half)]
    verts = np.stack([np.column_stack([cx + dx, cy + dy, cz]) for dx, dy in offs], axis=1).reshape(
        -1, 3
    )
    base = (np.arange(is_.size) * 4)[:, None, None]
    tris = (base + np.array([[0, 1, 2], [0, 2, 3]])[None, :, :]).reshape(-1, 3)
    colors = np.tile(np.array(color, dtype=np.uint8), (verts.shape[0], 1))
    return rr.Mesh3D(
        vertex_positions=verts,
        triangle_indices=tris,
        vertex_colors=colors,
    )


def _dilate4(m: np.ndarray) -> np.ndarray:
    """4 邻域一像素膨胀（边界 np.roll 回绕，道路/桥均不在边界，可接受）。"""
    return m | np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)


def _log_passability(t: HeightField) -> None:
    """通行性叠加层：只显式标绘道路/桥梁（p>0.95；普通地表 0.6/0.9 不上色，
    否则整个地形都会被贴片覆盖）。水面上的为桥梁（灰褐），旱地为公路（土黄）。"""
    if np.allclose(t.passability, 1.0):
        return  # 无通行性栅格（默认全 1），不画
    p, h = t.passability, t.heights
    wet = h < t.water_level + 1.0
    # 道路在旱地中唯一 p 接近 1；桥梁经降采样后峰值约 0.8（河面中位约 0.28），
    # 用相对河面的高值阈值识别。细线降采样后会断，做 1 格膨胀连成线
    road_hi = p > 0.95
    bridge_hi = wet & (p > 0.45)
    roads = _dilate4(road_hi) & ~wet
    bridges = _dilate4(bridge_hi) & wet
    mesh = _pass_quads(t, roads, (205, 180, 110, 210), deck=False)
    if mesh is not None:
        rr.log("world/passability/roads", mesh, static=True)
    mesh = _pass_quads(t, bridges, (140, 126, 104, 235), deck=True)
    if mesh is not None:
        rr.log("world/passability/bridges", mesh, static=True)


def _log_static_units(info, terrain: HeightField, use_models: bool = True) -> None:
    if not use_models:
        return
    for uid, (typ, *_rest) in info.items():
        glb = ensure_model(typ)
        rr.log(f"world/units/{uid}/model", rr.Asset3D(path=str(glb)), static=True)


def _log_units_primitives(cur: pd.DataFrame, zs: np.ndarray, info) -> None:
    """C6 轻量图元单位：盒体 + 状态色。

    C7-a1 优先级（肉眼可辨 M/F/K/后送）：在修紫 > 重损后送深红 >
    M 失动深蓝 > F 火力受损琥珀 > 正常方别色；死亡残骸深灰，
    K 战毁（grade=3）棕黑。旧数据无 grade 列时回退旧三态。
    """
    has_grade = "damage_grade" in cur.columns and "disposition" in cur.columns
    alive_centers: list = []
    alive_rot: list = []
    alive_colors: list = []
    wreck_centers: list = []
    wreck_rot: list = []
    wreck_colors: list = []
    for k, uid in enumerate(cur.index):
        x, y, z = float(cur.at[uid, "x"]), float(cur.at[uid, "y"]), float(zs[k])
        hz = float(cur.at[uid, "heading"]) * 0.5
        quat = [0.0, 0.0, math.sin(hz), math.cos(hz)]  # xyzw，绕 z 轴
        if not bool(cur.at[uid, "alive"]):
            wreck_centers.append([x, y, z + 1.0])
            wreck_rot.append(quat)
            wreck_colors.append(
                WRECK_K_C if has_grade and int(cur.at[uid, "damage_grade"]) == 3
                else WRECK_C
            )
            continue
        side = info[uid][1]
        if bool(cur.at[uid, "under_repair"]):
            color = REPAIR_C
        elif bool(cur.at[uid, "damaged"]):
            if has_grade and int(cur.at[uid, "disposition"]) == 4:
                color = REAR_C
            elif has_grade and int(cur.at[uid, "damage_grade"]) == 1:
                color = M_KILL_C
            else:
                color = DAMAGED_C
        else:
            color = SIDE_COLOR[side]
        alive_centers.append([x, y, z + 2.5])
        alive_rot.append(quat)
        alive_colors.append(color)

    if alive_centers:
        rr.log(
            "world/units/primitives",
            rr.Boxes3D(
                centers=alive_centers,
                sizes=[12.0, 7.0, 5.0],
                rotations=np.asarray(alive_rot),
                colors=alive_colors,
            ),
        )
    else:
        rr.log("world/units/primitives", rr.Clear(recursive=False))
    if wreck_centers:
        rr.log(
            "world/units/wrecks",
            rr.Boxes3D(
                centers=wreck_centers,
                sizes=[10.0, 5.0, 2.0],
                rotations=np.asarray(wreck_rot),
                colors=wreck_colors,
            ),
        )
    else:
        rr.log("world/units/wrecks", rr.Clear(recursive=False))


def _tint_model(ent: str, typ: str, state: str) -> None:
    glb = ensure_model(typ)
    if state == "ok":
        # 正常态沿用 static 原色资源，无需重发
        return
    tints = {
        # C7-a1：M 失动深蓝 / 重损后送深红 / K 战毁棕黑（F 沿用 amber 受损色）
        "m_kill": (50, 110, 210),
        "rear": (180, 60, 60),
        "wreck_k": (96, 46, 34),
        "damaged": (170, 148, 108),
        "dead": (42, 42, 44),
    }
    rr.log(
        f"{ent}/model",
        rr.Asset3D(
            path=str(glb),
            albedo_factor=rr.encodings.Rgba32(
                (tints[state][0] << 24) | (tints[state][1] << 16)
                | (tints[state][2] << 8) | 255
            ),
        ),
    )


def _log_objective(scenario: Scenario, terrain: HeightField) -> None:
    """逐阶段绘制目标点：末段绿色（最终目标），中间阶段琥珀色。"""
    phases = scenario.mission.phase_list
    th = np.linspace(0, 2 * np.pi, 48)
    for k, ph in enumerate(phases):
        final = k == len(phases) - 1
        color = OBJECTIVE_C if final else (230, 170, 60)
        ox, oy = ph.objective
        z = float(terrain.height_at(ox, oy))
        base = f"world/objective/phase{k}"
        pole = np.asarray([[ox, oy, z], [ox, oy, z + 260]])
        rr.log(f"{base}/pole", rr.LineStrips3D([pole], colors=[color], radii=6), static=True)
        rr.log(
            f"{base}/beacon",
            rr.Points3D(
                [[ox, oy, z + 270]],
                radii=160 if final else 110,
                colors=[color],
                labels=[f"阶段{k + 1} {ph.name}"],
            ),
            static=True,
        )
        ring = np.column_stack(
            [
                ox + ph.success_radius_m * np.cos(th),
                oy + ph.success_radius_m * np.sin(th),
                np.full_like(th, z + 2),
            ]
        )
        rr.log(f"{base}/ring", rr.LineStrips3D([ring], colors=[color], radii=4), static=True)


def _log_repair_stations(scenario: Scenario, terrain: HeightField) -> None:
    """各方修理所：方别色立柱 + 信标 + 300m 地域环（在修单位后送目标）。"""
    stations = scenario.sustainment.repair_station
    if not stations:
        return
    th = np.linspace(0, 2 * np.pi, 48)
    labels = {0: "红方旅修理所", 1: "蓝方旅修理所"}
    for side in (0, 1):
        key = "red" if side == 0 else "blue"
        if key not in stations:
            continue
        color = SIDE_COLOR[side]
        ox, oy = (float(v) for v in stations[key])
        z = float(terrain.height_at(ox, oy))
        base = f"world/station/{key}"
        pole = np.asarray([[ox, oy, z], [ox, oy, z + 200]])
        rr.log(f"{base}/pole", rr.LineStrips3D([pole], colors=[color], radii=6), static=True)
        rr.log(
            f"{base}/beacon",
            rr.Points3D([[ox, oy, z + 210]], radii=90, colors=[color], labels=[labels[side]]),
            static=True,
        )
        ring = np.column_stack(
            [ox + 300 * np.cos(th), oy + 300 * np.sin(th), np.full_like(th, z + 2)]
        )
        rr.log(f"{base}/ring", rr.LineStrips3D([ring], colors=[color], radii=4), static=True)


def _log_obstacles(scenario: Scenario, terrain: HeightField) -> None:
    """障碍地域（蓝方防御体系）：矩形边界 + 稀疏内部标识点 + 中心标签。"""
    th = np.linspace(0, 2 * np.pi, 24)
    for k, ob in enumerate(scenario.battlefield.obstacles):
        color, label = OBSTACLE_STYLE[ob.kind]
        cx, cy = (float(v) for v in ob.center)
        sx, sy = (float(v) for v in ob.size)
        z = float(terrain.height_at(cx, cy)) + 2.0
        x0, x1, y0, y1 = cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2
        rect = [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z], [x0, y0, z]]
        base = f"world/obstacles/ob{k}"
        rr.log(f"{base}/edge", rr.LineStrips3D([rect], colors=[color], radii=5), static=True)
        # 内部稀疏标识点：雷场红点、三角锥琥珀点、反坦克壕暗点
        gx, gy = np.meshgrid(np.linspace(x0, x1, 5), np.linspace(y0, y1, 5))
        pts = np.column_stack([gx.ravel(), gy.ravel(), np.full(25, z + 3)])
        rr.log(
            f"{base}/dots",
            rr.Points3D(pts, radii=14, colors=[color], labels=[label] + [""] * 24),
            static=True,
        )
        # 雷场补一圈警示环
        if ob.kind == "minefield":
            ring = np.column_stack(
                [cx + min(sx, sy) / 2 * np.cos(th),
                 cy + min(sx, sy) / 2 * np.sin(th), np.full_like(th, z)]
            )
            rr.log(f"{base}/warn", rr.LineStrips3D([ring], colors=[color], radii=3), static=True)


def _log_decoys(scenario: Scenario, terrain: HeightField) -> None:
    """假目标：初始阵位地面菱形虚影框 + 立柱标签，提示可被识别过滤的诱饵。"""
    k = 0
    for side_name in ("red", "blue"):
        for u in scenario.forces.get(side_name, []):
            if not u.decoy:
                continue
            x, y = (float(v) for v in u.pos)
            z = float(terrain.height_at(x, y)) + 2.0
            d = 26.0
            diamond = [[x, y + d, z], [x + d, y, z], [x, y - d, z],
                       [x - d, y, z], [x, y + d, z]]
            base = f"world/decoys/d{k}"
            rr.log(
                f"{base}/ring",
                rr.LineStrips3D([diamond], colors=[DECOY_C], radii=4),
                static=True,
            )
            rr.log(
                f"{base}/tag",
                rr.Points3D([[x, y, z + 60]], radii=40, colors=[DECOY_C],
                            labels=["假目标（可识别）"]),
                static=True,
            )
            k += 1


# ---------- 每 tick 要素 ----------


def _log_markers(*, cur, zs, info, decoy_ids: set[str] | None = None) -> None:
    """单位标签：小型菱形标图 + 竖引线悬于车顶（借鉴 VBS/VR-Vantage 标签引脚）。"""
    tag_h = 72.0
    decoy_ids = decoy_ids or set()
    for side in (0, 1):
        ids = [uid for uid in cur.index if info[uid][1] == side and bool(cur.at[uid, "alive"])]
        if not ids:
            continue
        pts, labels, leaders = [], [], []
        for uid in ids:
            k = cur.index.get_loc(uid)
            x, y, z = float(cur.at[uid, "x"]), float(cur.at[uid, "y"]), float(zs[k])
            pts.append([x, y, z + tag_h])
            labels.append(info[uid][2] + ("〔假〕" if uid in decoy_ids else ""))
            leaders.append([[x, y, z + 3.0], [x, y, z + tag_h - 6.0]])
        color = SIDE_COLOR[side]
        rr.log(
            f"world/markers/{'red' if side == 0 else 'blue'}",
            rr.Points3D(pts, radii=6, colors=[color], labels=labels),
        )
        rr.log(
            f"world/leaders/{'red' if side == 0 else 'blue'}",
            rr.LineStrips3D(leaders, colors=[color], radii=1.5),
        )


def _log_fans(cur, zs, info, *, outline: bool = False) -> None:
    """存活单位射界扇面（半角粗取 ±10°）。

    桌面版用半透明三角网格填充；C6 大屏嵌入版（outline=True）改用细线轮廓
    （两条射界边 + 弧），避免几十个半透明扇面叠加成刺眼尖刺、喧宾夺主。
    """
    half = np.deg2rad(10)
    arc = np.linspace(-half, half, 11)

    if outline:
        strips, strip_colors = [], []
        for k, uid in enumerate(cur.index):
            if not bool(cur.at[uid, "alive"]):
                continue
            side = info[uid][1]
            rng_ = info[uid][3]
            h = float(cur.at[uid, "heading"])
            ox, oy, oz = float(cur.at[uid, "x"]), float(cur.at[uid, "y"]), float(zs[k]) + 2
            arc_pts = [
                [ox + rng_ * np.cos(h + a), oy + rng_ * np.sin(h + a), oz]
                for a in arc
            ]
            c = SIDE_COLOR[side] + (150,)
            strips += [[arc_pts[0], [ox, oy, oz]], arc_pts,
                       [arc_pts[-1], [ox, oy, oz]]]
            strip_colors += [c, c, c]
        if strips:
            rr.log(
                "world/fans",
                rr.LineStrips3D(strips, colors=strip_colors, radii=4.0),
            )
        else:
            rr.log("world/fans", rr.Clear(recursive=False))
        return

    verts, colors = [], []
    for k, uid in enumerate(cur.index):
        if not bool(cur.at[uid, "alive"]):
            continue
        side = info[uid][1]
        rng_ = info[uid][3]
        h = float(cur.at[uid, "heading"])
        ox, oy, oz = float(cur.at[uid, "x"]), float(cur.at[uid, "y"]), float(zs[k]) + 2
        verts.append([ox, oy, oz])
        for a in arc:
            verts.append([ox + rng_ * np.cos(h + a), oy + rng_ * np.sin(h + a), oz])
        c = SIDE_COLOR_A[side]
        colors += [c] * (len(arc) + 1)
    if not verts:
        rr.log("world/fans", rr.Clear(recursive=False))
        return
    # 每个扇面独立三角形索引
    indices, seg = [], 12
    for base in range(0, len(verts), seg):
        for j in range(seg - 2):
            indices.append([base, base + j + 1, base + j + 2])
    rr.log(
        "world/fans",
        rr.Mesh3D(
            vertex_positions=np.asarray(verts, dtype=np.float64),
            triangle_indices=np.asarray(indices),
            vertex_colors=np.asarray(colors, dtype=np.uint8),
        ),
    )


def _ring_pts(cx, cy, z, radius, n=48):
    th = np.linspace(0, 2 * np.pi, n)
    return [[cx + radius * np.cos(a), cy + radius * np.sin(a), z] for a in th]


def _log_coverages(tick: int, cur, zs, info, *, thin: bool = False) -> None:
    """体系作用包络（每 tick 跟随单位）：雷达探测圈+扫描线、防空杀伤圈、
    电抗压制区、C2 指挥光环。全部细线图元，控制 rrd 体积。"""
    rings: list[list] = []
    ring_colors: list = []
    sweeps: list[list] = []
    sweep_colors: list = []
    line_r = 2.5 if thin else 3.5
    for k, uid in enumerate(cur.index):
        if not bool(cur.at[uid, "alive"]):
            continue
        role = info[uid][4]
        cov = info[uid][5]
        if cov <= 0:
            continue
        ox, oy, oz = float(cur.at[uid, "x"]), float(cur.at[uid, "y"]), float(zs[k]) + 3
        if role in ("radar_air", "radar_cb"):
            color = RADAR_C
            rings.append(_ring_pts(ox, oy, oz, cov, 56))
            ring_colors.append(color)
            # 旋转扫描线（相位按 uid 散列，多部雷达不同步）
            phase = (hash(uid) % 360) * math.pi / 180.0
            a = tick * 0.32 + phase
            sweeps.append([[ox, oy, oz],
                           [ox + cov * math.cos(a), oy + cov * math.sin(a), oz]])
            sweep_colors.append(color)
        elif role == "air_defense":
            rings.append(_ring_pts(ox, oy, oz, cov, 48))
            ring_colors.append(SIDE_COLOR[info[uid][1]])
        elif role == "ew":
            rings.append(_ring_pts(ox, oy, oz, cov, 64))
            ring_colors.append(EW_C)
        elif role == "c2":
            rings.append(_ring_pts(ox, oy, oz, cov, 48))
            ring_colors.append(AURA_C)
    if rings:
        rr.log("world/coverages/rings",
               rr.LineStrips3D(rings, colors=ring_colors, radii=line_r))
    else:
        rr.log("world/coverages/rings", rr.Clear(recursive=False))
    if sweeps:
        rr.log("world/coverages/sweeps",
               rr.LineStrips3D(sweeps, colors=sweep_colors, radii=line_r * 1.4))
    else:
        rr.log("world/coverages/sweeps", rr.Clear(recursive=False))


def _instanced_spheres(centers, radii, colors, *, z_scale=1.0, subdiv=2):
    """批量实例化低精度球 Mesh3D（Points3D/Ellipsoids3D 在 rerun 0.39 上半透明发黑，
    Mesh3D 的 vertex_colors alpha 混合正常，烟雾/雾用它）。"""
    import trimesh

    base = trimesh.creation.icosphere(subdivisions=subdiv)
    v0, f0 = np.asarray(base.vertices), np.asarray(base.faces)
    nv = v0.shape[0]
    verts, faces, cols = [], [], []
    radii = np.broadcast_to(np.asarray(radii, dtype=float), len(centers))
    for c, r, col in zip(np.asarray(centers), radii, colors):
        off = sum(len(x) for x in verts)
        vv = v0 * np.asarray([r, r, r * z_scale]) + np.asarray(c)
        verts.append(vv)
        faces.append(f0 + off)
        cols.append(np.tile(np.asarray(col, dtype=np.uint8), (nv, 1)))
    return rr.Mesh3D(
        vertex_positions=np.concatenate(verts),
        triangle_indices=np.concatenate(faces),
        vertex_colors=np.concatenate(cols),
    )


def _log_smoke(tick: int, cur, zs) -> None:
    """毁伤烟柱：半透明浅灰球团串，随高度扩散变大变淡（Mesh3D 实例）。"""
    centers, colors, radii = [], [], []
    rng = np.random.default_rng(1000 + tick)
    for k, uid in enumerate(cur.index):
        alive, damaged = bool(cur.at[uid, "alive"]), bool(cur.at[uid, "damaged"])
        if alive and not damaged:
            continue
        killed = not alive
        n_col = 6 if killed else 3
        top = 220 if killed else 120
        for j in range(n_col):
            rise = (tick * 7 + j * (top / n_col)) % top
            spread = 6 + rise * (0.34 if killed else 0.18)
            centers.append(
                [
                    float(cur.at[uid, "x"]) + rng.normal(0, spread),
                    float(cur.at[uid, "y"]) + rng.normal(0, spread),
                    float(zs[k]) + 8 + rise,
                ]
            )
            if killed:
                a = int(92 - rise / top * 48)
                g = int(150 + rise / top * 55)  # 顶部烟团更白
                colors.append((g, g, min(g + 6, 230), a))
                radii.append(20 + rise * 0.22)
            else:
                colors.append((120, 108, 92, 70))
                radii.append(13 + rise * 0.15)
    if centers:
        rr.log("world/smoke", _instanced_spheres(centers, radii, colors))
    else:
        rr.log("world/smoke", rr.Clear(recursive=False))


def _log_weather_header(scenario: Scenario) -> None:
    env = scenario.environment
    name = WEATHER_NAME[env.weather]
    illum = ILLUM_NAME[env.illumination]
    if env.wind_speed_mps >= 0.5:
        wind = f"  风速 {env.wind_speed_mps:.0f}m/s 风向 {env.wind_dir_deg:.0f}°"
    else:
        wind = "  无风"
    rr.log(
        "feed/weather",
        rr.TextLog(f"气象：{name}（{illum}）{wind}", level="INFO"),
        static=True,
    )


def _ground_haze(tick, terrain, n_fog, color, *, r_lo=160, r_hi=300, h=28):
    """贴地漂移雾/霾/沙尘扁球层（Mesh3D 半透明实例）。"""
    rng = np.random.default_rng(7)
    fx = rng.uniform(0, terrain.size[0], n_fog)
    fy = rng.uniform(0, terrain.size[1], n_fog)
    fr = rng.uniform(r_lo, r_hi, n_fog)
    drift = (tick * 6) % 300
    centers = np.column_stack([(fx + drift) % terrain.size[0], fy, np.zeros(n_fog)])
    gz = terrain.height_at(centers[:, 0], centers[:, 1])
    centers[:, 2] = gz + h
    return _instanced_spheres(centers, fr, [color] * n_fog, z_scale=0.14, subdiv=1)


def _log_weather(tick: int, scenario: Scenario, terrain: HeightField) -> None:
    w = scenario.environment.weather
    n_pts = 620 if w == "heavy_rain" else 380
    rng = np.random.default_rng(7)
    px = rng.uniform(0, terrain.size[0], n_pts)
    py = rng.uniform(0, terrain.size[1], n_pts)
    if w in ("rain", "heavy_rain"):
        # Points3D 不支持半透明，用不透明淡蓝小点表现雨幕；暴雨更密更急
        speed = 160 if w == "heavy_rain" else 110
        z = 250 + ((rng.uniform(0, 900, n_pts) - tick * speed) % 900)
        color = (120, 150, 200) if w == "heavy_rain" else (150, 180, 220)
        rr.log(
            "world/weather/precip",
            rr.Points3D(np.column_stack([px, py, z]), radii=8, colors=[color]),
        )
    elif w == "snow":
        z = 150 + ((rng.uniform(0, 700, n_pts) - tick * 30) % 700)
        rr.log(
            "world/weather/precip",
            rr.Points3D(np.column_stack([px, py, z]), radii=12, colors=[(235, 240, 245)]),
        )
    elif w in ("fog", "heavy_fog"):
        # 贴地扁球雾团，缓慢漂移；多球重叠 alpha 会叠加，取低值
        n_fog = 220 if w == "heavy_fog" else 130
        alpha = 22 if w == "heavy_fog" else 16
        rr.log(
            "world/weather/precip",
            _ground_haze(tick, terrain, n_fog, (202, 207, 214, alpha)),
        )
    elif w == "sand":
        rr.log(
            "world/weather/precip",
            _ground_haze(tick, terrain, 150, (196, 162, 104, 26), r_lo=200, r_hi=380, h=46),
        )
    elif w == "haze":
        rr.log(
            "world/weather/precip",
            _ground_haze(tick, terrain, 100, (200, 200, 196, 18), r_lo=260, r_hi=460, h=60),
        )
    else:
        rr.log("world/weather/precip", rr.Clear(recursive=False))


def _log_engagements(tick, engagements, pos_table, terrain, info) -> None:
    if engagements.empty:
        return
    ev = engagements[engagements["tick"] == tick]
    origins, vectors, colors = [], [], []
    for _, e in ev.iterrows():
        a = pos_table.get((tick, e["shooter"]))
        b = pos_table.get((tick, e["target"]))
        if a is None or b is None:
            continue
        za = float(terrain.height_at(a[0], a[1])) + 4
        origins.append([a[0], a[1], za])
        vectors.append([b[0] - a[0], b[1] - a[1], 0.0])
        colors.append((232, 70, 58) if e["killed"] else (DAMAGED_C if e["damaged"] else SHOT_MISS))
        if e["killed"]:
            txt = f"T+{tick:03d}  {info[e['shooter']][2]} → {info[e['target']][2]} 【击毁】"
            rr.log("feed/engagements", rr.TextLog(txt, level="CRITICAL", color=(232, 70, 58)))
        elif e["damaged"]:
            rr.log(
                "feed/engagements",
                rr.TextLog(
                    f"T+{tick:03d}  {info[e['shooter']][2]} → {info[e['target']][2]} 致损",
                    level="WARN",
                    color=DAMAGED_C,
                ),
            )
    if origins:
        rr.log(
            "world/engagements",
            rr.Arrows3D(origins=origins, vectors=vectors, colors=colors, radii=18),
        )


def _log_air(
    tick: int,
    air: pd.DataFrame,
    trails: dict[str, list],
    done: set[str],
) -> None:
    """空中架次：按真实高度分层的航点 + 尾迹；被拦截/耗尽/返航后清除。"""
    new = air[air["tick"] == tick]
    for _, r in new.iterrows():
        aid = str(r["asset_id"])
        trails.setdefault(aid, []).append([float(r["x"]), float(r["y"]), float(r["alt"])])
        if str(r["state"]) in AIR_DONE_STATES:
            done.add(aid)
    for aid, pts in trails.items():
        base = f"world/air/{aid}"
        if aid in done:
            rr.log(f"{base}/trail", rr.Clear(recursive=False))
            rr.log(f"{base}/mark", rr.Clear(recursive=False))
            continue
        side = 0
        kind = "uav"
        # 尾迹/航点属性取该架次最近一行
        rows = air[(air["asset_id"] == aid) & (air["tick"] <= tick)]
        if not rows.empty:
            last = rows.iloc[-1]
            side, kind = int(last["side"]), str(last["kind"])
        color = SIDE_COLOR[side]
        rr.log(f"{base}/trail", rr.LineStrips3D([pts], colors=[color], radii=2.5))
        x, y, z = pts[-1]
        rr.log(
            f"{base}/mark",
            rr.Points3D(
                [[x, y, z]], radii=120, colors=[color],
                labels=[AIR_KIND_LABEL.get(kind, kind)],
            ),
        )


def _smoke_band_arrays(tick: int, cx: float, cy: float, radius: float, terrain: HeightField):
    """一道屏烟的几何数组（确定性分布的贴地扁球带，缓慢翻涌不逐帧跳变）。"""
    import trimesh

    seed = (int(cx) * 73856093) ^ (int(cy) * 19349663)
    rng = np.random.default_rng(abs(seed) % (2**32))
    n = max(7, int((radius / 230.0) ** 2 * 2.2))
    rr_ = rng.uniform(0, radius * 0.92, n)
    aa = rng.uniform(0, 2 * np.pi, n)
    jitter_x = 46.0 * math.sin(tick * 0.55 + seed % 10)
    jitter_y = 46.0 * math.cos(tick * 0.47 + seed % 10)
    centers = np.column_stack([
        cx + rr_ * np.cos(aa) + jitter_x,
        cy + rr_ * np.sin(aa) + jitter_y,
        np.zeros(n),
    ])
    centers[:, 2] = terrain.height_at(centers[:, 0], centers[:, 1]) + 24
    radii = rng.uniform(radius * 0.26, radius * 0.42, n)
    base = trimesh.creation.icosphere(subdivisions=1)
    v0, f0 = np.asarray(base.vertices), np.asarray(base.faces)
    verts, faces, cols = [], [], []
    for c, r in zip(centers, radii):
        off = sum(len(x) for x in verts)
        verts.append(v0 * np.asarray([r, r, r * 0.06]) + c)
        faces.append(f0 + off)
        cols.append(np.tile(np.asarray(SMOKE_BAND_C, dtype=np.uint8), (len(v0), 1)))
    return np.concatenate(verts), np.concatenate(faces), np.concatenate(cols)


def _log_effects(
    tick: int,
    effects: pd.DataFrame | None,
    terrain: HeightField,
    smoke_specs: dict[tuple, tuple[float, int]],
    smoke_start: dict[tuple, int],
) -> None:
    """战场效果点（每 tick 瞬时，次 tick 清除）：
    炮弹出膛闪光、弹着爆炸、防空拦截闪光、空中击杀、巡飞弹命中、破障标记；
    另有持续型屏烟带（按起烟 tick + 想定持续时间窗绘制）。"""
    # ---- 持续：屏烟带（起烟事件可能落在隔帧上，按 <=tick 累积登记）----
    if effects is not None:
        for _, r in effects[(effects["kind"] == "smoke") & (effects["tick"] <= tick)].iterrows():
            key = (round(float(r["x"])), round(float(r["y"])))
            smoke_start.setdefault(key, int(r["tick"]))
    band_buf = []
    for key, t0 in smoke_start.items():
        radius, duration = smoke_specs.get(key, (500.0, 6))
        if t0 <= tick < t0 + duration:
            band_buf.append(
                _smoke_band_arrays(tick, float(key[0]), float(key[1]), radius, terrain)
            )
    if band_buf:
        vv, ff, cc = [], [], []
        off = 0
        for v, f, c in band_buf:
            vv.append(v)
            ff.append(f + off)
            cc.append(c)
            off += len(v)
        rr.log(
            "world/effects/smoke_bands",
            rr.Mesh3D(
                vertex_positions=np.concatenate(vv),
                triangle_indices=np.concatenate(ff),
                vertex_colors=np.concatenate(cc),
            ),
        )
    else:
        rr.log("world/effects/smoke_bands", rr.Clear(recursive=False))

    if effects is None:
        return
    ev = effects[effects["tick"] == tick]
    launch, impact, intercept, strike, breach = [], [], [], [], []
    feed = []
    for _, r in ev.iterrows():
        x, y = float(r["x"]), float(r["y"])
        z = float(terrain.height_at(x, y))
        kind = str(r["kind"])
        if kind == "fire_launch":
            launch.append([x, y, z + 6])
        elif kind == "fire_impact":
            impact.append([x, y, z + 10])
        elif kind == "intercept":
            intercept.append([x, y, z + 8])
            feed.append(("防空拦截！", INTERCEPT_C))
        elif kind == "air_kill":
            intercept.append([x, y, z + 8])
            feed.append((f"空中目标被击落（{r['detail']}）", AIRSTRIKE_C))
        elif kind == "loiter_strike":
            strike.append([x, y, z + 10])
            feed.append(("巡飞弹命中雷达/节点", AIRSTRIKE_C))
        elif kind == "breach":
            breach.append([x, y, z + 8])
            feed.append(("工兵开辟通路", BREACH_C))
    buckets = (
        ("launch", launch, LAUNCH_C, 90),
        ("impact", impact, IMPACT_C, 150),
        ("intercept", intercept, INTERCEPT_C, 130),
        ("strike", strike, AIRSTRIKE_C, 170),
        ("breach", breach, BREACH_C, 100),
    )
    for name, pts, color, radius in buckets:
        if pts:
            rr.log(f"world/effects/{name}",
                   rr.Points3D(pts, radii=radius, colors=[color]))
        else:
            rr.log(f"world/effects/{name}", rr.Clear(recursive=False))
    for txt, color in feed:
        rr.log("feed/effects", rr.TextLog(f"T+{tick:03d} {txt}", level="WARN", color=color))


def _log_metrics(tick: int, cur: pd.DataFrame) -> None:
    for side, name in ((0, "red"), (1, "blue")):
        m = cur["side"] == side
        if not m.any():
            continue
        alive = cur.loc[m, "alive"]
        ready = alive & (~cur.loc[m, "damaged"])
        rr.log(f"metrics/{name}/attrition", rr.Scalars(float(1 - alive.mean())))
        rr.log(f"metrics/{name}/readiness", rr.Scalars(float(ready.mean())))


def _log_supply(tick: int, supply: pd.DataFrame) -> None:
    """备件库存与在修数时间曲线（每 tick 每方一条摘要）。"""
    row = supply[supply["tick"] == tick]
    for _, r in row.iterrows():
        name = "red" if int(r["side"]) == 0 else "blue"
        rr.log(f"supply/{name}/spares", rr.Scalars(float(r["spares"])))
        rr.log(f"supply/{name}/in_repair", rr.Scalars(float(r["in_repair"])))


def _log_tally(tick: int, cur: pd.DataFrame) -> None:
    r = cur[cur["side"] == 0]
    b = cur[cur["side"] == 1]
    r_alive, r_ready = int(r["alive"].sum()), int((r["alive"] & ~r["damaged"]).sum())
    b_alive = int(b["alive"].sum())
    rr.log(
        "feed/tally",
        rr.TextLog(
            f"T+{tick:03d} 红方 存活 {r_alive}/{len(r)}  完好 {r_ready}"
            f" ｜ 蓝方 存活 {b_alive}/{len(b)}",
            level="INFO",
            color=(200, 210, 220),
        ),
    )


# ---------- 蓝图 ----------


def _make_blueprint(
    eye: tuple[float, float, float] | None = None,
    look: tuple[float, float, float] | None = None,
) -> rrb.Blueprint:
    scene = _scene_view(
        eye or (3600.0, -5200.0, 6800.0),
        look or (5200.0, 5000.0, 120.0),
    )
    side_panel = rrb.Tabs(
        rrb.TimeSeriesView(name="红方 战损/完好", origin="/metrics/red"),
        rrb.TimeSeriesView(name="蓝方 战损/完好", origin="/metrics/blue"),
        rrb.TimeSeriesView(name="备件库存/在修", origin="/supply"),
        rrb.TextLogView(name="交战统计", origin="/feed"),
        active_tab=3,
    )
    return rrb.Blueprint(
        rrb.Horizontal(scene, side_panel, column_shares=[3.1, 1.05]),
        rrb.BlueprintPanel(expanded=False),
        rrb.SelectionPanel(expanded=False),
        rrb.TimePanel(expanded=True),
    )


def _make_embed_blueprint(
    eye: tuple[float, float, float] | None = None,
    look: tuple[float, float, float] | None = None,
) -> rrb.Blueprint:
    """C6 大屏 iframe 嵌入用：3D 视口独占，侧栏/蓝图/选择/时间轴全部收起。

    时间轴面板虽收起，但通过蓝图 play_state=playing + loop_mode=all 让回放
    加载即自动循环播放（录屏无需手动点播放）；timeline 锚定序列轴 tick。
    """
    scene = _scene_view(
        eye or (2400.0, -7600.0, 9200.0),
        look or (5200.0, 4600.0, 160.0),
    )
    return rrb.Blueprint(
        scene,
        rrb.BlueprintPanel(expanded=False),
        rrb.SelectionPanel(expanded=False),
        rrb.TimePanel(
            expanded=False,
            timeline="tick",
            play_state="playing",
            loop_mode="all",
            fps=12.0,
        ),
    )


def _scene_view(
    eye: tuple[float, float, float], look: tuple[float, float, float]
) -> rrb.Spatial3DView:
    return rrb.Spatial3DView(
        name="战场态势",
        origin="/world",
        background=(15, 17, 22),
        line_grid=rrb.LineGrid3D(spacing=1000.0, color=(60, 66, 78)),
        eye_controls=rrb.EyeControls3D(
            position=list(eye),
            look_target=list(look),
            eye_up=[0.0, 0.0, 1.0],
        ),
    )
