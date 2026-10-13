"""M2 写实 3D 可视化（rerun）：高程地形 + 气象粒子 + GLB 战车 + 战术要素。

借鉴 VBS4 / Steel Beasts / VR-Vantage / OneSAF AAR 的设计：
- 主视图为斜视 3D 战场（写实地形配色、暗色背景），右侧时间序列与交战记录；
- 红/蓝遵循 NATO/MIL-STD 习惯色；单位按编制层级文字标注；
- 射界扇面、目标信标、毁伤烟柱、雨雾粒子叠加战场态势。

输入仍是统一事件日志（snapshot 含 x/y/heading/alive/damaged）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import rerun as rr
import rerun.blueprint as rrb

from ..data.schema import Scenario
from ..kernel.terrain import HeightField
from .models import ensure_model

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
    app_id: str = "brigsim",
) -> None:
    rr.init(app_id, spawn=spawn)
    if rrd_path is not None:
        rr.save(str(rrd_path))
    rr.send_blueprint(_make_blueprint(eye, look))

    info = _unit_info(scenario)
    _log_terrain(terrain)
    _log_static_units(info, terrain)
    _log_objective(scenario, terrain)
    _log_weather_header(scenario)

    pos_table = {
        (int(t), uid): (float(x), float(y))
        for t, uid, x, y in snapshots[["tick", "unit_id", "x", "y"]].itertuples(False)
    }
    model_state: dict[str, str] = {}

    for tick, grp in snapshots.groupby("tick", sort=True):
        tick = int(tick)
        rr.set_time("tick", sequence=tick)
        rr.set_time("sim_clock", duration=float(tick * scenario.mission.dt_seconds))

        cur = grp.set_index("unit_id")
        xs = cur["x"].to_numpy()
        ys = cur["y"].to_numpy()
        zs = terrain.height_at(xs, ys)
        alive_idx, dead_idx = [], []
        for k, uid in enumerate(cur.index):
            ent = f"world/units/{uid}"
            alive, damaged = bool(cur.at[uid, "alive"]), bool(cur.at[uid, "damaged"])
            rr.log(
                ent,
                rr.Transform3D(
                    # 模型履带最低点在本地 z=-0.5，抬高 0.5 使其恰好接地
                    translation=[float(xs[k]), float(ys[k]), float(zs[k]) + 0.5],
                    rotation=rr.RotationAxisAngle(
                        axis=[0, 0, 1], radians=float(cur.at[uid, "heading"])
                    ),
                ),
            )
            state = "dead" if not alive else ("damaged" if damaged else "ok")
            if model_state.get(uid) != state:
                _tint_model(ent, info[uid][0], state)
                model_state[uid] = state
            (dead_idx if not alive else alive_idx).append(k)

        _log_markers(cur=cur, zs=zs, info=info)
        _log_fans(cur, zs, info)
        _log_smoke(tick, cur, zs)
        _log_weather(tick, scenario, terrain)
        _log_engagements(tick, engagements, pos_table, terrain, info)
        _log_metrics(tick, cur)
        if tick == 0 or tick % 10 == 0:
            _log_tally(tick, cur)

    rr.log("feed/engagements", rr.TextLog("—— 推演结束 ——", level="INFO"))


# ---------- 静态要素 ----------


def _unit_info(scenario: Scenario) -> dict[str, tuple[str, int, str, float]]:
    types = scenario.platform_types
    out: dict[str, tuple[str, int, str, float]] = {}
    for side_id, side_name in ((0, "red"), (1, "blue")):
        for u in scenario.forces.get(side_name, []):
            out[u.unit_id] = (u.type, side_id, u.org or u.unit_id, types[u.type].weapon_range_m)
    return out


def _log_terrain(t: HeightField) -> None:
    # 视觉网格 4 倍细分并采样 height_at：保证渲染三角面与内核双线性高程一致，
    # 否则 156m 粗网格的三角面与插值面能差数米，单位会被埋入地下
    n = (t.n - 1) * 4 + 1
    xs = np.linspace(0, t.size[0], n)
    ys = np.linspace(0, t.size[1], n)
    gx, gy = np.meshgrid(xs, ys)
    heights = t.height_at(gx.ravel(), gy.ravel()).reshape(n, n)
    verts = np.column_stack([gx.ravel(), gy.ravel(), heights.ravel()])

    # 坡度（中心差分）
    dy, dx = np.gradient(heights, t.size[0] / (n - 1), t.size[1] / (n - 1))
    slope = np.hypot(dx, dy).ravel()
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
        rr.Mesh3D(vertex_positions=verts, triangle_indices=idx, vertex_colors=colors),
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


def _log_static_units(info, terrain: HeightField) -> None:
    for uid, (typ, *_rest) in info.items():
        glb = ensure_model(typ)
        rr.log(f"world/units/{uid}/model", rr.Asset3D(path=str(glb)), static=True)


def _tint_model(ent: str, typ: str, state: str) -> None:
    glb = ensure_model(typ)
    if state == "ok":
        # 正常态沿用 static 原色资源，无需重发
        return
    elif state == "damaged":
        rr.log(
            f"{ent}/model",
            rr.Asset3D(
                path=str(glb),
                albedo_factor=rr.encodings.Rgba32((170 << 24) | (148 << 16) | (108 << 8) | 255),
            ),
        )
    else:  # dead
        rr.log(
            f"{ent}/model",
            rr.Asset3D(
                path=str(glb),
                albedo_factor=rr.encodings.Rgba32((42 << 24) | (42 << 16) | (44 << 8) | 255),
            ),
        )


def _log_objective(scenario: Scenario, terrain: HeightField) -> None:
    ox, oy = scenario.mission.objective
    z = float(terrain.height_at(ox, oy))
    pole = np.asarray([[ox, oy, z], [ox, oy, z + 260]])
    rr.log(
        "world/objective/pole", rr.LineStrips3D([pole], colors=[OBJECTIVE_C], radii=6), static=True
    )
    rr.log(
        "world/objective/beacon",
        rr.Points3D([[ox, oy, z + 270]], radii=160, colors=[OBJECTIVE_C], labels=["任务目标"]),
        static=True,
    )
    # 目标区半径圈
    th = np.linspace(0, 2 * np.pi, 48)
    ring = np.column_stack(
        [
            ox + scenario.mission.success_radius_m * np.cos(th),
            oy + scenario.mission.success_radius_m * np.sin(th),
            np.full_like(th, z + 2),
        ]
    )
    rr.log(
        "world/objective/ring", rr.LineStrips3D([ring], colors=[OBJECTIVE_C], radii=4), static=True
    )


# ---------- 每 tick 要素 ----------


def _log_markers(*, cur, zs, info) -> None:
    """单位标签：小型菱形标图 + 竖引线悬于车顶（借鉴 VBS/VR-Vantage 标签引脚）。"""
    tag_h = 72.0
    for side in (0, 1):
        ids = [uid for uid in cur.index if info[uid][1] == side and bool(cur.at[uid, "alive"])]
        if not ids:
            continue
        pts, labels, leaders = [], [], []
        for uid in ids:
            k = cur.index.get_loc(uid)
            x, y, z = float(cur.at[uid, "x"]), float(cur.at[uid, "y"]), float(zs[k])
            pts.append([x, y, z + tag_h])
            labels.append(info[uid][2])
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


def _log_fans(cur, zs, info) -> None:
    """存活单位射界扇面（半透明三角网格，扇形半角随当前武器，粗取 ±10°）。"""
    verts, colors = [], []
    half = np.deg2rad(10)
    arc = np.linspace(-half, half, 11)
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
    name = {"clear": "晴朗", "rain": "降雨", "fog": "雾", "snow": "降雪"}[
        scenario.environment.weather
    ]
    rr.log(
        "feed/weather",
        rr.TextLog(
            f"气象：{name}  能见度系数 {scenario.environment.visibility_factor}", level="INFO"
        ),
        static=True,
    )


def _log_weather(tick: int, scenario: Scenario, terrain: HeightField) -> None:
    w, n_pts = scenario.environment.weather, 380
    rng = np.random.default_rng(7)
    px = rng.uniform(0, terrain.size[0], n_pts)
    py = rng.uniform(0, terrain.size[1], n_pts)
    if w == "rain":
        # Points3D 不支持半透明，用不透明淡蓝小点表现雨幕
        z = 250 + ((rng.uniform(0, 900, n_pts) - tick * 110) % 900)
        rr.log(
            "world/weather/precip",
            rr.Points3D(np.column_stack([px, py, z]), radii=8, colors=[(150, 180, 220)]),
        )
    elif w == "snow":
        z = 150 + ((rng.uniform(0, 700, n_pts) - tick * 30) % 700)
        rr.log(
            "world/weather/precip",
            rr.Points3D(np.column_stack([px, py, z]), radii=12, colors=[(235, 240, 245)]),
        )
    elif w == "fog":
        # 贴地扁球雾团（Mesh3D 半透明），稀疏分布、缓慢漂移；多球重叠 alpha 会叠加，取低值
        n_fog = 130
        fx = rng.uniform(0, terrain.size[0], n_fog)
        fy = rng.uniform(0, terrain.size[1], n_fog)
        fr = rng.uniform(160, 300, n_fog)
        drift = (tick * 6) % 300
        centers = np.column_stack([(fx + drift) % terrain.size[0], fy, np.zeros(n_fog)])
        heights = terrain.height_at(centers[:, 0], centers[:, 1])
        centers[:, 2] = heights + 28
        colors = [(202, 207, 214, 16)] * n_fog
        rr.log(
            "world/weather/precip", _instanced_spheres(centers, fr, colors, z_scale=0.14, subdiv=1)
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


def _log_metrics(tick: int, cur: pd.DataFrame) -> None:
    for side, name in ((0, "red"), (1, "blue")):
        m = cur["side"] == side
        if not m.any():
            continue
        alive = cur.loc[m, "alive"]
        ready = alive & (~cur.loc[m, "damaged"])
        rr.log(f"metrics/{name}/attrition", rr.Scalars(float(1 - alive.mean())))
        rr.log(f"metrics/{name}/readiness", rr.Scalars(float(ready.mean())))


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
    scene = rrb.Spatial3DView(
        name="战场态势",
        origin="/world",
        background=(15, 17, 22),
        line_grid=rrb.LineGrid3D(spacing=1000.0, color=(60, 66, 78)),
        eye_controls=rrb.EyeControls3D(
            position=list(eye) if eye else [3600.0, -5200.0, 6800.0],
            look_target=list(look) if look else [5200.0, 5000.0, 120.0],
            eye_up=[0.0, 0.0, 1.0],
        ),
    )
    side_panel = rrb.Tabs(
        rrb.TimeSeriesView(name="红方 战损/完好", origin="/metrics/red"),
        rrb.TimeSeriesView(name="蓝方 战损/完好", origin="/metrics/blue"),
        rrb.TextLogView(name="交战统计", origin="/feed"),
        active_tab=2,
    )
    return rrb.Blueprint(
        rrb.Horizontal(scene, side_panel, column_shares=[3.1, 1.05]),
        rrb.BlueprintPanel(expanded=False),
        rrb.SelectionPanel(expanded=False),
        rrb.TimePanel(expanded=True),
    )
