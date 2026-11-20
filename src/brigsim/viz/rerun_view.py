"""M2 写实 3D 可视化（rerun）：高程地形 + 气象粒子 + GLB 战车 + 战术要素。

借鉴 VBS4 / Steel Beasts / VR-Vantage / OneSAF AAR 的设计：
- 主视图为斜视 3D 战场（写实地形配色、暗色背景），右侧时间序列与交战记录；
- 红/蓝遵循 NATO/MIL-STD 习惯色；单位按编制层级文字标注；
- 射界扇面、目标信标、毁伤烟柱、雨雾粒子叠加战场态势。

输入仍是统一事件日志（snapshot 含 x/y/heading/alive/damaged）。
"""

from __future__ import annotations

import functools
import math
from pathlib import Path

import numpy as np
import pandas as pd
import rerun as rr
import rerun.blueprint as rrb

from ..data.schema import Scenario
from ..kernel.terrain import HeightField
from .models import ensure_model, ground_offset

# C7-i：可视化统一使用太行山真实 DEM（SRTM 30m，AWS Terrarium）。
# 需求方要求"所有工况的 3D 显示都用和太行山一样的真实地形"。只在渲染层替换，
# 不改仿真输入（四率数据/报告权威数字保持不变）；LOS 判定也用这张可见地形，
# 保证"所见即所得"（画的山体遮挡 = 判定的山体遮挡）。
VISUAL_REAL_DEM = True
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REAL_DEM_TIF = _PROJECT_ROOT / "scenarios" / "terrain" / "dem_real_taihang.tif"
_REAL_PASS_TIF = _PROJECT_ROOT / "scenarios" / "terrain" / "pass_real_taihang.tif"


@functools.lru_cache(maxsize=1)
def _real_dem_field(size_m: tuple[float, float], grid_n: int) -> HeightField | None:
    """加载太行山真实 DEM 为 HeightField（进程内缓存，只读一次盘）。"""
    if not _REAL_DEM_TIF.is_file():
        return None
    try:
        return HeightField.from_geotiff(
            str(_REAL_DEM_TIF),
            size=size_m,
            grid_n=grid_n,
            water_level=780.0,
            passability_path=str(_REAL_PASS_TIF) if _REAL_PASS_TIF.is_file() else None,
        )
    except Exception:
        return None


def _visual_terrain(terrain: HeightField) -> HeightField:
    """C7-i：把可视化地形替换为太行山真实 DEM（保持 size/grid_n 与原地形一致）。"""
    if not VISUAL_REAL_DEM:
        return terrain
    real = _real_dem_field(tuple(terrain.size), int(terrain.n))
    return real if real is not None else terrain

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

# C7-b：8 地形族差异化色表（让不同 preset 在 3D 里一眼可辨）
_TERRAIN_PALETTES: dict[str, dict[str, tuple[int, int, int]]] = {
    "canyon":      {"water": (60, 75, 95),   "sand": (165, 140, 105), "grass": (80, 100, 55),
                    "forest": (42, 60, 35),  "rock":  (95, 80, 65),   "snow": (210, 215, 210)},
    "plain":       {"water": (50, 100, 140), "sand": (210, 195, 145), "grass": (115, 140, 70),
                    "forest": (72, 100, 55),  "rock":  (130, 120, 105), "snow": (235, 235, 230)},
    "rolling":     {"water": (42, 88, 128),  "sand": (200, 185, 138), "grass": (102, 132, 66),
                    "forest": (65, 92, 50),   "rock":  (118, 106, 88),  "snow": (228, 230, 228)},
    "mountain":    {"water": (30, 60, 95),   "sand": (155, 140, 110), "grass": (68, 92, 52),
                    "forest": (38, 56, 32),  "rock":  (85, 72, 58),   "snow": (240, 242, 240)},
    "paddy":       {"water": (28, 72, 65),   "sand": (180, 175, 120), "grass": (82, 118, 55),
                    "forest": (52, 78, 40),   "rock":  (115, 108, 90),  "snow": (225, 228, 225)},
    "urban":       {"water": (55, 85, 115),  "sand": (170, 165, 155), "grass": (82, 105, 68),
                    "forest": (50, 72, 42),   "rock":  (100, 98, 96),   "snow": (220, 222, 220)},
    "desert":      {"water": (70, 90, 100),  "sand": (220, 190, 120), "grass": (125, 120, 55),
                    "forest": (90, 78, 38),   "rock":  (145, 120, 80),  "snow": (240, 235, 220)},
    "snowfield":   {"water": (35, 65, 90),   "sand": (200, 200, 195), "grass": (120, 145, 135),
                    "forest": (80, 110, 100), "rock":  (135, 135, 130), "snow": (245, 248, 248)},
    None:          {"water": C_WATER, "sand": C_SAND, "grass": C_GRASS,  # 默认（geotiff 或无 preset）
                    "forest": C_FOREST, "rock": C_ROCK, "snow": C_SNOW},
}


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
# C7-j：空中平台真实模型独立于地面 use_models 开关——回放 rrd 为控体积地面用
# 轻量盒体（use_models=False），但无人机/巡飞弹/武直始终用真实 GLB（3 型共 ~14KB，
# 体积可忽略），满足"用真实装备而非圆球替代"。设 False 可退回圆球。
AIR_USE_REAL_MODELS = True
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

# C7-a2 保障实体图层配色
DEPOT_C = (232, 188, 86)      # 旅器材库（金）
TOW_LINE_C = (96, 168, 250)   # 拖救牵引线（亮蓝）
SUPPLY_C = (140, 220, 160)    # 器材波次/拖抵（薄荷绿）
AMMO_C = (255, 205, 90)       # 补弹（琥珀金）
SUPPORT_LOST_C = (235, 80, 72)  # 前送损失（红）


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
    # C7-i：可视化地形统一为太行山真实 DEM（仅渲染与 LOS 判定用，仿真数据不变）
    terrain = _visual_terrain(terrain)

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
    # C7-d：渲染垂直夸张——物理高程不变，几何/单位/贴地要素统一缩放，
    # 平原地形（plain 总起伏 44m）也有可感知的三维地貌
    z_exag = auto_z_exag(terrain)
    geo = terrain.scaled(z_exag)

    _log_terrain(terrain, scenario.environment.illumination, z_exag)
    _log_map_frame(geo, scenario.environment.illumination)
    _log_passability(geo)
    _log_static_units(info, terrain, use_models)
    _log_objective(scenario, geo)
    _log_repair_stations(scenario, geo)
    _log_depots(scenario, geo)
    _log_obstacles(scenario, geo)
    _log_decoys(scenario, geo)
    _log_weather_header(scenario)

    # C7-a0：空中航迹与战场效果点（None 或空表时对应图层静默）
    air_trails: dict[str, list] = {}
    air_done: set[str] = set()
    air_model_logged: set[str] = set()  # C7-j：每架次 Asset3D 只静态 log 一次
    # 屏烟：想定中的半径/持续时间 + 效果帧给出的实际起烟 tick
    smoke_specs = {
        (round(z.at[0]), round(z.at[1])): (float(z.radius_m), int(z.duration_ticks))
        for z in scenario.battlefield.smoke_orders
    }
    smoke_start: dict[tuple, int] = {}
    effects = effects if effects is not None and not effects.empty else None
    air = air if air is not None and not air.empty else None
    # C7-j：空中模型 Asset3D 必须在首次 set_time 之前以 static 发出，否则 rerun
    # 会把 static 数据绑到当前 tick，回放跳帧后模型消失（只剩标签）。地面单位的
    # _log_static_units 同样在循环前调用，遵循同一约定。
    if air is not None and AIR_USE_REAL_MODELS:
        for aid, kind in air[["asset_id", "kind"]].drop_duplicates().itertuples(False, name=None):
            key = f"{aid}:{kind}"
            try:
                rr.log(f"world/air/{aid}/model",
                       rr.Asset3D(path=str(ensure_model(str(kind)))), static=True)
                air_model_logged.add(key)
            except Exception:
                pass

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
        zs = geo.height_at(xs, ys)  # C7-d：单位 z 随渲染夸张同步缩放，贴合成像地面
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
        _log_fans(cur, zs, info, outline=embed, geo=geo, los_terrain=terrain)
        _log_coverages(tick, cur, zs, info, thin=embed, geo=geo, los_terrain=terrain)
        _log_smoke(tick, cur, zs)
        _log_weather(tick, scenario, geo)
        _log_engagements(tick, engagements, pos_table, geo, info)
        _log_tow_lines(cur, info, geo)
        _log_effects(tick, effects, geo, smoke_specs, smoke_start)
        if air is not None:
            _log_air(tick, air, air_trails, air_done, use_models=AIR_USE_REAL_MODELS,
                     model_logged=air_model_logged,
                     ground_max=float(np.asarray(geo.heights).max()))
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


# 地表细节纹理资产（size=1024, seed=11 的确定性产物，与场景无关）：
# 纯 Python opensimplex 生成一次需 ~92s 且全程持 GIL，会饿死服务进程内所有线程
# （live 推演 worker / HTTP handler 全被拖住 → 预览"假锁"）——故预生成为 .npy 资产。
_TEXTURE_ASSET = Path(__file__).resolve().parent / "assets" / "ground_texture.npy"
_TEXTURE_CACHE: dict[tuple[int, int], np.ndarray] = {}


def auto_z_exag(t: HeightField, target_span: float = 240.0, hi: float = 4.0) -> float:
    """渲染垂直夸张系数：按地形实际起伏量自动放大（C7-d）。

    物理高程不变（内核机动/单位站位仍用真实高程），仅渲染网格与单位 z 乘该
    系数。平原预设总起伏仅 44m/10km（0.4%），真实比例下永远是"一张黄纸"；
    target_span 为期望视觉起伏量（米），起伏已达目标则返回 1.0（如 DEM 388m）。
    plain→4.0×、rolling→2.5×、canyon→1.26×、DEM→1.0×。
    """
    span = float(np.max(t.heights) - np.min(t.heights))
    if not np.isfinite(span) or span < 1.0:
        return 1.0
    return float(min(hi, max(1.0, target_span / span)))


def _ground_texture_generate(size: int, seed: int) -> np.ndarray:
    """自然色地表细节纹理（暖灰基底 + 泥地斑驳 + 碎石颗粒）。

    与顶点色相乘后呈土地质感而非塑料纯色；粗细两级 FBM 噪声同源资产化。
    """
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
    # 暖灰基底（微偏黄褐），粗噪再叠一点绿棕斑驳，接近真实地表航片质感
    warm = np.array([199.0, 195.0, 181.0])
    mottle = np.stack([coarse * 0.9 - fine * 0.3, coarse * 0.6 + fine * 0.5, -coarse * 0.4], axis=-1)
    rgb = warm[None, None, :] * shade[..., None] + mottle * 6.0
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _ground_texture(size: int = 1024, seed: int = 11) -> np.ndarray:
    """地表细节纹理：优先读预生成资产（毫秒级），缺失时按原逻辑生成并缓存。

    纹理是固定资产（默认 size/seed 与场景无关），rerun 只读取不修改，可安全共享同一数组。
    """
    key = (size, seed)
    hit = _TEXTURE_CACHE.get(key)
    if hit is not None:
        return hit
    tex = None
    if key == (1024, 11) and _TEXTURE_ASSET.is_file():
        try:
            tex = np.load(_TEXTURE_ASSET)
        except Exception:
            tex = None
    if tex is None:
        tex = _ground_texture_generate(size, seed)
    # C7-f：贴图亮度补偿——albedo 纹理与顶点色相乘（均值 191/255≈0.75，整体压暗 25%），
    # 统一再乘 ~1.13 把均值提到 ≈215，抵消相乘损耗；clip 防溢出
    tex = np.clip(tex.astype(np.float32) * (215.0 / 191.0), 0, 255).astype(np.uint8)
    _TEXTURE_CACHE[key] = tex
    return tex


_SUN = {
    # illumination -> (方位角°, 高度角°, 光照强度)
    "day":   (315.0, 46.0, 1.00),
    "dusk":  (282.0, 13.0, 0.92),
    "night": (340.0, 7.0, 0.55),
}


def _sun_direction(illumination: str) -> tuple[float, float, float, float]:
    """想定昼/昏/夜 → 太阳方向单位向量 + 光照强度（山影明暗用）。"""
    az, alt, inten = _SUN.get(illumination, _SUN["day"])
    a, e = math.radians(az), math.radians(alt)
    return (math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e), inten)


def _log_terrain(t: HeightField, illumination: str = "day", z_exag: float = 1.0) -> None:
    """写实地形网格（C7-d：几何垂直夸张 + 颜色按真实高程分带 + 通行性晕染）。

    z_exag 为渲染垂直夸张系数（物理高程不变）：网格/法线/水面按缩放高程构建，
    颜色分带（水/沙/草/林/岩/雪）与坡度岩面判定仍用真实高程——视觉上是一座
    更高的青山，而不是把 44m 丘陵涂成雪山。
    """
    # 视觉网格 4 倍细分并采样 height_at：保证渲染三角面与内核双线性高程一致，
    # 否则 156m 粗网格的三角面与插值面能差数米，单位会被埋入地下
    n = (t.n - 1) * 4 + 1
    dx_world = t.size[0] / (n - 1)
    xs = np.linspace(0, t.size[0], n)
    ys = np.linspace(0, t.size[1], n)
    gx, gy = np.meshgrid(xs, ys)
    h = t.height_at(gx.ravel(), gy.ravel()).reshape(n, n)  # 真实高程（颜色/坡度）
    heights = h * z_exag  # 渲染高程（几何）
    verts = np.column_stack([gx.ravel(), gy.ravel(), heights.ravel()])

    # C7-b：按 preset_family 取差异化色表（默认 None → geotiff/无 preset 用原始标准色）
    # C7-f：提亮 ×1.5——顶点色最终被 山影(0.78~1.12) × 暖灰贴图(均值≈0.75) 双重相乘，
    # 原暗调色板（canyon 林地 (42,60,35)）乘完只剩 ≈(22,31,18)，整屏近黑（用户反馈
    # "回放模式整个 3D 界面太暗"）。调色板本身保持色相不变，只提亮度。
    pal = {
        k: tuple(min(255, int(round(v * 1.5))) for v in c)
        for k, c in _TERRAIN_PALETTES.get(t.preset_family, _TERRAIN_PALETTES[None]).items()
    }
    c_water, c_sand, c_grass = pal["water"], pal["sand"], pal["grass"]
    c_forest, c_rock, c_snow = pal["forest"], pal["rock"], pal["snow"]

    # 坡度（中心差分，真实高程）——岩面判定不受垂直夸张影响
    dhd_y, dhd_x = np.gradient(h, dx_world, dx_world)
    slope = np.hypot(dhd_x, dhd_y).ravel()
    hr = h.ravel()
    colors = np.zeros((n * n, 4), dtype=np.uint8)
    colors[:] = c_grass + (255,)
    colors[(hr > 40) & (hr < 110)] = c_forest + (255,)
    colors[hr >= 110] = c_rock + (255,)
    colors[hr >= 185] = c_snow + (255,)
    colors[(hr < t.water_level + 4) & (hr >= t.water_level)] = c_sand + (255,)
    colors[hr < t.water_level] = c_water + (255,)
    rocky = (slope > 0.35) & (hr >= t.water_level)
    colors[rocky] = c_rock + (255,)

    # 低草区随高程向干黄渐变（25m→50m，C7-d 收敛到 15%：此前 28% 叠加暖色贴图
    # 后整屏发黄，用户反馈"很多黄色的色块"）
    low_grass = (hr >= t.water_level + 4) & (hr < 40.0)
    blend = np.clip((hr - 25.0) / 25.0, 0.0, 1.0)
    dry_rgb = (
        np.array(c_grass, float) * (1.0 - 0.15 * blend[:, None])
        + np.array(c_sand, float) * (0.15 * blend[:, None])
    )
    colors[low_grass, :3] = dry_rgb[low_grass].astype(np.uint8)

    # C7-d：通行性淡色晕染（仅 FBM 预设）——预设的 p=1.0 是"默认开阔地"而非道路，
    # 不能按 DEM 逻辑铺路网；难行区（河沟/绝壁/水田 <0.45）冷灰蓝、松软区
    # （0.45-0.75）轻蒙，让通行性差异可感知又不喧宾夺主
    if t.preset_family is not None and not np.allclose(t.passability, 1.0):
        ci = np.minimum(np.arange(n) // 4, t.n - 1)
        p_f = t.passability[np.ix_(ci, ci)].ravel()
        c_arr = colors[:, :3].astype(np.float32)
        obst = np.array([104, 112, 124], dtype=np.float32)
        hard = p_f < 0.45
        soft = (p_f >= 0.45) & (p_f < 0.75)
        c_arr[hard] = c_arr[hard] * 0.62 + obst * 0.38
        c_arr[soft] = c_arr[soft] * 0.82 + obst * 0.18
        colors[:, :3] = np.clip(c_arr, 0, 255).astype(np.uint8)

    # 顶点法线：按缩放高程求梯度——夸张后坡面更陡，山影明暗对比更强、立体感更足
    dz_y, dz_x = np.gradient(heights, dx_world, dx_world)
    normals = np.zeros((n * n, 3))
    normals[:, 0] = -dz_x.ravel()
    normals[:, 1] = -dz_y.ravel()
    normals[:, 2] = 1.0
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)

    # 山影明暗（hillshade，制图学标准地貌表达）：太阳方向 Lambert 项乘顶点色，
    # 山脊/沟谷/陡坡产生连续明暗层次；昼夜用不同太阳方位/强度
    sx, sy, sz, inten = _sun_direction(illumination)
    lambert = np.clip(normals @ np.array([sx, sy, sz]), 0.0, 1.0)
    # C7-f：0.66+0.40L → 0.78+0.34L（日间范围 [0.78,1.12]）——原下限 0.66 让背光坡面
    # 过暗（叠贴图后近黑），保留明暗对比但整体抬亮
    shade = (1.0 - inten) + inten * (0.78 + 0.34 * lambert)
    colors[:, :3] = np.clip(colors[:, :3].astype(np.float32) * shade[:, None], 0, 255).astype(np.uint8)

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
    # 半透明水面（随垂直夸张同步缩放，谷地水面不会与地形脱节）
    qx, qy = np.meshgrid([0, t.size[0]], [0, t.size[1]])
    water = np.column_stack([qx.ravel(), qy.ravel(), np.full(4, t.water_level * z_exag)])
    rr.log(
        "world/water",
        rr.Mesh3D(
            vertex_positions=water,
            triangle_indices=[[0, 1, 3], [0, 3, 2]],
            vertex_colors=[(30, 70, 110, 120)] * 4,
        ),
        static=True,
    )


def _log_map_frame(t: HeightField, illumination: str = "day") -> None:
    """地图要素（贴地 static）：坐标网格 + 指北针 + 比例尺。

    - 网格步长自适应：单方向网格线控制在 ~10 条以内（100m~2km 七档候选）；
    - 每 5 格一条主线（稍亮稍粗），其余次线；
    - 折线沿地形采样高度 +0.6m，不穿地；
    - 指北针/比例尺放战场东北/东南角上空，颜色随昼夜明暗调整。
    """
    W, H = float(t.size[0]), float(t.size[1])
    step = 2000.0
    for cand in (100.0, 200.0, 250.0, 500.0, 1000.0, 2000.0):
        if W / cand <= 10 and H / cand <= 10:
            step = cand
            break
    night = illumination == "night"
    c_minor = (96, 102, 116) if night else (192, 196, 202)
    c_major = (128, 136, 150) if night else (224, 227, 232)
    c_mark = (150, 156, 168) if night else (240, 240, 234)

    n_s = 65
    ys = np.linspace(0.0, H, n_s)
    xs = np.linspace(0.0, W, n_s)
    strips: list[np.ndarray] = []
    majors: list[np.ndarray] = []
    x, k = 0.0, 0
    while x <= W + 1e-6:
        zz = t.height_at(np.full(n_s, x), ys) + 0.6
        line = np.column_stack([np.full(n_s, x), ys, zz])
        strips.append(line)
        if k % 5 == 0:
            majors.append(line)
        x += step
        k += 1
    y, k = 0.0, 0
    while y <= H + 1e-6:
        zz = t.height_at(xs, np.full(n_s, y)) + 0.6
        line = np.column_stack([xs, np.full(n_s, y), zz])
        strips.append(line)
        if k % 5 == 0:
            majors.append(line)
        y += step
        k += 1

    # 指北针（战场中部贴地大箭头，指向地图北 +y）。
    # 注意：实验证实画面边缘/四角区域在默认相机位姿下受视锥/裁剪影响不可见，
    # 中部为各相机位姿下稳定可见区，故地图要素置于中部而非传统四角。
    nx, ny = W * 0.52, H * 0.54
    tail_len = 1600.0
    nz = float(t.height_at(nx, ny))
    tail_z = float(t.height_at(nx, ny - tail_len))
    rr.log(
        "world/map/north",
        rr.Arrows3D(
            vectors=[[0.0, tail_len, nz - tail_z]],
            origins=[[nx, ny - tail_len, tail_z + 6.0]],
            colors=[c_mark],
            radii=100.0,
        ),
        static=True,
    )
    rr.log(
        "world/map/north_label",
        rr.Points3D(
            [[nx, ny + 220.0, nz + 150.0]],
            labels=["N"],
            radii=0.01,
            colors=[c_mark],
        ),
        static=True,
    )
    # 比例尺（指北针左下贴地：2 格长主线和两端刻度帽 + 米数标签；
    # z 取地形/水面较高者 +8m，避免谷地比例尺没入半透明水面下不可见）
    sx0, sy0 = W * 0.40, H * 0.42
    bar = step * 2
    sz = max(float(t.height_at(sx0 + bar / 2.0, sy0)), t.water_level) + 8.0
    cap = 200.0
    rr.log(
        "world/map/scale",
        rr.LineStrips3D(
            [
                # 主线（3 点）
                np.column_stack([
                    [sx0, sx0 + bar / 2.0, sx0 + bar],
                    [sy0, sy0, sy0],
                    [sz + 6.0, sz + 6.0, sz + 6.0],
                ]),
                # 两端刻度帽（4 点折线：下-上-下-上）
                np.column_stack([
                    [sx0, sx0, sx0 + bar, sx0 + bar],
                    [sy0 - cap, sy0 + cap, sy0 - cap, sy0 + cap],
                    [sz + 6.0, sz + 6.0, sz + 6.0, sz + 6.0],
                ]),
            ],
            colors=[c_mark],
            radii=80.0,
        ),
        static=True,
    )
    rr.log(
        "world/map/scale_label",
        rr.Points3D(
            [[sx0 + bar / 2.0, sy0 + 240.0, sz + 100.0]],
            labels=[f"{int(bar)} m"],
            radii=0.01,
            colors=[c_mark],
        ),
        static=True,
    )
    rr.log("world/map/grid", rr.LineStrips3D(strips, colors=c_minor, radii=0.7), static=True)
    rr.log("world/map/grid_major", rr.LineStrips3D(majors, colors=c_major, radii=1.6), static=True)
    # HUD 文字版地图信息（3D 要素之外的兜底可读性）
    rr.log(
        "feed/map",
        rr.TextLog(
            f"地图：坐标网格 {int(step)}m（主线每 5 格） · 指北 N=+y · 比例尺 {int(step * 2)}m",
            level="INFO",
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
    """通行性叠加层：只显式标绘 DEM 道路/桥梁（geotiff 专用）。

    C7-d 修复：原按 p>0.95 绝对阈值识别"公路"，但 FBM 预设的 p=1.0 语义是
    "默认开阔地"而非道路——plain/rolling 预设 84%~93% 的格子都是 1.0，导致
    大屏上大半张地图被土黄贴片盖住（用户报"很多黄色的色块"）。
    现在：geotiff（preset_family=None，DEM 通行性栅格真实编码路网）才画
    道路(土黄)/桥梁(灰褐)；预设的通行性差异改由 _log_terrain 的冷灰蓝
    淡色晕染表达（河沟/绝壁更灰蓝、开阔地不着色）。
    """
    if t.preset_family is not None:
        return  # FBM 预设无路网语义，不上贴片（晕染在 _log_terrain）
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
    # C7-d：兼容最小列集（live 预览只构造 x/y/heading/alive）——缺省列按正常态补全
    for col, default in (("damaged", False), ("under_repair", False)):
        if col not in cur.columns:
            cur = cur.copy()
            cur[col] = default
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
    # C7-a2：声明器材三档的方别，修理所即营中继抢修点（运行档存放地）
    ech = scenario.sustainment.support_echelon
    depot_sides = set(ech.depot) if ech is not None else set()
    labels = {
        0: "红方营中继抢修点" if "red" in depot_sides else "红方旅修理所",
        1: "蓝方营中继抢修点" if "blue" in depot_sides else "蓝方旅修理所",
    }
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


def _log_depots(scenario: Scenario, terrain: HeightField) -> None:
    """C7-a2：旅器材库（后留档接入点）：金色立柱 + 方形库地域环 + 标签。"""
    ech = scenario.sustainment.support_echelon
    if ech is None:
        return
    side_name = {0: "red", 1: "blue"}
    for side in (0, 1):
        key = side_name[side]
        if key not in ech.depot:
            continue
        ox, oy = (float(v) for v in ech.depot[key])
        z = float(terrain.height_at(ox, oy))
        base = f"world/depot/{key}"
        pole = np.asarray([[ox, oy, z], [ox, oy, z + 230]])
        rr.log(f"{base}/pole",
               rr.LineStrips3D([pole], colors=[DEPOT_C], radii=7), static=True)
        rr.log(
            f"{base}/beacon",
            rr.Points3D([[ox, oy, z + 240]], radii=100, colors=[DEPOT_C],
                       labels=[f"{'红' if side == 0 else '蓝'}方旅器材库（后留档·波次前送）"]),
            static=True,
        )
        h = 320.0  # 方形库地域环，与修理所圆环区分
        ring = np.asarray([
            [ox - h, oy - h, z + 2], [ox + h, oy - h, z + 2],
            [ox + h, oy + h, z + 2], [ox - h, oy + h, z + 2],
            [ox - h, oy - h, z + 2],
        ])
        rr.log(f"{base}/ring",
               rr.LineStrips3D([ring], colors=[DEPOT_C], radii=4), static=True)


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


def _log_fans(cur, zs, info, *, outline: bool = False, geo=None, los_terrain=None) -> None:
    """存活单位射界扇面（半角粗取 ±10°）。

    桌面版用半透明三角网格填充；C6 大屏嵌入版（outline=True）改用低透明度
    填充扇面 + 亮弧线轮廓（C7-d：纯细线轮廓用户看不见）。
    C7-e：geo 传入时弧点逐点贴地（垂直夸张后扁平弧会被地形埋没）。

    C7-h：los_terrain 传入（真实未缩放地形）时，逐弧点做通视判定：
    被山体遮挡的射界方向不再画填充/亮弧——与内核 mesa_kernel.has_los 的
    直瞄射击口径一致（此前渲染画满圆扇面、内核却因地形遮挡打不到，画面与
    行为脱节）。遮挡三角形跳过，可见扇区照常高亮。
    """
    half = np.deg2rad(10)
    arc = np.linspace(-half, half, 11)

    def _visible(ox, oy, ax, ay):
        """弧点是否通视（无 los_terrain 时恒可见，保持旧行为）。"""
        if los_terrain is None:
            return True
        try:
            return bool(los_terrain.has_los(ox, oy, float(ax), float(ay), 3.0))
        except Exception:
            return True

    if outline:
        # C7-d：用户反馈大屏里"装备作用范围不可见"——由纯细线轮廓改为
        # 低透明度填充扇面（alpha 36）+ 亮弧线轮廓（alpha 150）：
        # 既清晰表达各装备作用范围，又避免高透明尖刺喧宾夺主
        fill_v: list = []
        fill_c: list = []
        strips, strip_colors = [], []
        _vis_masks: list = []  # C7-h：每个扇面的逐弧点可见掩码
        seg = len(arc) + 1
        for k, uid in enumerate(cur.index):
            if not bool(cur.at[uid, "alive"]):
                continue
            side = info[uid][1]
            rng_ = info[uid][3]
            h = float(cur.at[uid, "heading"])
            ox, oy = float(cur.at[uid, "x"]), float(cur.at[uid, "y"])
            oz = float(zs[k]) + 2
            ax = ox + rng_ * np.cos(h + arc)
            ay = oy + rng_ * np.sin(h + arc)
            if geo is not None:
                # C7-e：贴地扇面（缩放地形逐点采样 + 抬升，防被夸张后的地形埋没）
                az = np.asarray(geo.height_at(ax, ay), dtype=float) + 4.0
            else:
                az = np.full(len(arc), oz)
            arc_pts = [
                [float(a_), float(b_), float(c_)]
                for a_, b_, c_ in zip(ax, ay, az)
            ]
            # C7-h：逐弧点通视——被山体遮挡的方向不画填充/亮弧
            vis = [_visible(ox, oy, a_, b_) for a_, b_ in zip(ax, ay)]
            fill_v.append([ox, oy, oz])
            fill_v += arc_pts
            fill_c += [SIDE_COLOR[side] + (36,)] * seg
            c_edge = SIDE_COLOR[side] + (150,)
            # 只在弧点可见处画亮弧线（连续可见段成条）
            run = []
            for idx, ok in enumerate(vis):
                if ok:
                    run.append(arc_pts[idx])
                else:
                    if len(run) >= 2:
                        strips.append(run); strip_colors.append(c_edge)
                    run = []
            if len(run) >= 2:
                strips.append(run); strip_colors.append(c_edge)
            # 两侧边界线：仅当端点可见
            if vis[0]:
                strips.append([arc_pts[0], [ox, oy, oz]]); strip_colors.append(c_edge)
            if vis[-1]:
                strips.append([arc_pts[-1], [ox, oy, oz]]); strip_colors.append(c_edge)
            # 记录每个扇面的可见掩码，供三角形裁剪
            _vis_masks.append(vis)
        indices = []
        if fill_v:
            seg_i = 0
            for base in range(0, len(fill_v), seg):
                mask = _vis_masks[seg_i] if seg_i < len(_vis_masks) else [True] * seg
                seg_i += 1
                for j in range(seg - 2):
                    # 三角形 (base, base+j+1, base+j+2) 中 base+j+1 / base+j+2
                    # 分别是弧点 j / j+1（base 是原点）；mask 索引即弧点序 j/j+1。
                    # 两端点均可见才画（任一端被地形遮挡即视为该方向被遮蔽）
                    if j < len(mask) and j + 1 < len(mask) and mask[j] and mask[j + 1]:
                        indices.append([base, base + j + 1, base + j + 2])
        # C7-j（live 峡谷场景 "Mesh has no triangle indices" 根因）：扇面被地形
        # 全部遮挡时 indices=[]，若照常 log 一个 (0,3) 空索引 Mesh3D，rerun viewer
        # 会报 "Failed to load mesh /world/fans: Mesh has no triangle indices"。
        # 可见三角形为 0 时改发 Clear（亮弧线 strips 不受影响，仍按可见段绘制）。
        if fill_v and indices:
            rr.log(
                "world/fans",
                rr.Mesh3D(
                    vertex_positions=np.asarray(fill_v, dtype=np.float64),
                    triangle_indices=np.asarray(indices, dtype=np.int32).reshape((-1, 3)),
                    vertex_colors=np.asarray(fill_c, dtype=np.uint8),
                ),
            )
        else:
            rr.log("world/fans", rr.Clear(recursive=False))
        if strips:
            rr.log(
                "world/fans_edge",
                rr.LineStrips3D(strips, colors=strip_colors, radii=5.5),
            )
        else:
            rr.log("world/fans_edge", rr.Clear(recursive=False))
        return

    verts, colors = [], []
    _vis_masks: list = []  # C7-h：逐弧点可见掩码
    for k, uid in enumerate(cur.index):
        if not bool(cur.at[uid, "alive"]):
            continue
        side = info[uid][1]
        rng_ = info[uid][3]
        h = float(cur.at[uid, "heading"])
        ox, oy = float(cur.at[uid, "x"]), float(cur.at[uid, "y"])
        oz = float(zs[k]) + 2
        verts.append([ox, oy, oz])
        ax = ox + rng_ * np.cos(h + arc)
        ay = oy + rng_ * np.sin(h + arc)
        if geo is not None:
            az = np.asarray(geo.height_at(ax, ay), dtype=float) + 4.0
        else:
            az = np.full(len(arc), oz)
        for a_, b_, c_ in zip(ax, ay, az):
            verts.append([float(a_), float(b_), float(c_)])
        c = SIDE_COLOR_A[side]
        colors += [c] * (len(arc) + 1)
        _vis_masks.append([_visible(ox, oy, a_, b_) for a_, b_ in zip(ax, ay)])
    if not verts:
        rr.log("world/fans", rr.Clear(recursive=False))
        return
    # 每个扇面独立三角形索引（C7-h：跳过被地形遮挡的方向）
    indices, seg = [], 12
    for si, base in enumerate(range(0, len(verts), seg)):
        mask = _vis_masks[si] if si < len(_vis_masks) else [True] * seg
        for j in range(seg - 2):
            if j < len(mask) and j + 1 < len(mask) and mask[j] and mask[j + 1]:
                indices.append([base, base + j + 1, base + j + 2])
    # C7-j：同样防御——所有扇面被地形遮挡时 indices=[]，改发 Clear 而非空 Mesh3D，
    # 避免 viewer 报 "Mesh has no triangle indices"（C7-i 仅规范了 dtype/shape，
    # 但 (0,3) 空数组仍会被 viewer 判为无索引 Mesh）。
    if indices:
        rr.log(
            "world/fans",
            rr.Mesh3D(
                vertex_positions=np.asarray(verts, dtype=np.float64),
                triangle_indices=np.asarray(indices, dtype=np.int32).reshape((-1, 3)),
                vertex_colors=np.asarray(colors, dtype=np.uint8),
            ),
        )
    else:
        rr.log("world/fans", rr.Clear(recursive=False))


def _ring_pts(cx, cy, z, radius, n=48):
    th = np.linspace(0, 2 * np.pi, n)
    return [[cx + radius * np.cos(a), cy + radius * np.sin(a), z] for a in th]


def _ring_pts_tf(cx, cy, radius, geo, lift, n=48):
    """贴地圆环：每点按（缩放后）地形高程采样 + 固定抬升。

    C7-e：用户反馈"作用范围圈被地形遮挡、颜色发灰不明显"——根因是圆环为
    平面 z=中心地形高+3，而渲染垂直夸张（plain 4×）后圈内地形起伏可达
    上百年（44m×4≈176m），扁平圈被山丘埋没。改为逐点贴地 + 抬升可见。
    """
    th = np.linspace(0, 2 * np.pi, n)
    px = cx + radius * np.cos(th)
    py = cy + radius * np.sin(th)
    pz = np.asarray(geo.height_at(px, py), dtype=float) + lift
    return [[float(a), float(b), float(c)] for a, b, c in zip(px, py, pz)]


def _log_coverages(tick: int, cur, zs, info, *, thin: bool = False, geo=None,
                   los_terrain=None) -> None:
    """体系作用包络（每 tick 跟随单位）：雷达探测圈+扫描线、防空杀伤圈、
    电抗压制区、C2 指挥光环。geo 传入时圆环/扫描线逐点贴地（C7-e）。

    C7-h：los_terrain 传入（真实未缩放地形）时，圆环逐点做通视判定：
    被山体遮挡的方向不打亮该段——与内核直瞄/雷达口径一致，避免"雷达圈画满
    全向但实际探测被地形遮蔽"的脱节。
    """
    rings: list[list] = []
    ring_colors: list = []
    sweeps: list[list] = []
    sweep_colors: list = []
    line_r = 3.5 if thin else 5.0
    lift = 8.0

    def _visible(ox, oy, px, py):
        if los_terrain is None:
            return True
        try:
            return bool(los_terrain.has_los(ox, oy, float(px), float(py), 3.0))
        except Exception:
            return True

    def _visible_arcs(cx, cy, base_z, radius, n):
        """返回可见圆弧段列表（每段是点列，被地形遮挡处断开）。"""
        if geo is not None:
            pts = _ring_pts_tf(cx, cy, radius, geo, lift, n)
        else:
            pts = _ring_pts(cx, cy, base_z, radius, n)
        mask = [_visible(cx, cy, p[0], p[1]) for p in pts]
        arcs, run = [], []
        for p, ok in zip(pts, mask):
            if ok:
                run.append(p)
            else:
                if len(run) >= 2:
                    arcs.append(run)
                run = []
        if len(run) >= 2:
            arcs.append(run)
        # 闭合：首尾均可见时把末段与首段接上
        if mask[0] and mask[-1] and arcs:
            arcs[-1] = arcs[-1] + arcs[0][:1]
        return arcs

    def _emit(cx, cy, base_z, radius, n, color):
        for arc in _visible_arcs(cx, cy, base_z, radius, n):
            rings.append(arc)
            ring_colors.append(color)

    for k, uid in enumerate(cur.index):
        if not bool(cur.at[uid, "alive"]):
            continue
        role = info[uid][4]
        cov = info[uid][5]
        if cov <= 0:
            continue
        ox, oy, oz = float(cur.at[uid, "x"]), float(cur.at[uid, "y"]), float(zs[k]) + lift
        if role in ("radar_air", "radar_cb"):
            color = RADAR_C
            _emit(ox, oy, oz, cov, 56, color)
            # 旋转扫描线（相位按 uid 散列）；终点被地形遮挡时该方向不打扫描线
            phase = (hash(uid) % 360) * math.pi / 180.0
            a = tick * 0.32 + phase
            ex, ey = ox + cov * math.cos(a), oy + cov * math.sin(a)
            if _visible(ox, oy, ex, ey):
                ez = (float(geo.height_at(ex, ey)) + lift) if geo is not None else oz
                sweeps.append([[ox, oy, oz], [ex, ey, ez]])
                sweep_colors.append(color)
        elif role == "air_defense":
            _emit(ox, oy, oz, cov, 48, SIDE_COLOR[info[uid][1]])
        elif role == "ew":
            _emit(ox, oy, oz, cov, 64, EW_C)
        elif role == "c2":
            _emit(ox, oy, oz, cov, 48, AURA_C)
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


def _instanced_spheres(centers, radii, colors, *, z_scale=1.0, subdiv=1):
    """批量实例化低精度球 Mesh3D（Points3D/Ellipsoids3D 在 rerun 0.39 上半透明发黑，
    Mesh3D 的 vertex_colors alpha 混合正常，烟雾/雾用它）。

    C7-i：subdiv 默认 2→1（icosphere 162→42 顶点/320→80 面，减负 75%）。
    rerun web viewer 单 entity 有内存预算上限，超预算会报 "exceeds memory
    budget / try native viewer" 且丢弃数据引发 "invalid index"；烟雾是半透明
    球团，subdiv=1 视觉几乎无差异，但显著减小 rrd 体积与 GPU 缓冲。
    """
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
    if not verts:  # C7-i：空实例→规范空 Mesh，避免 np.concatenate([]) 抛异常
        return rr.Mesh3D(
            vertex_positions=np.zeros((0, 3), dtype=np.float64),
            triangle_indices=np.zeros((0, 3), dtype=np.int32),
            vertex_colors=np.zeros((0, 4), dtype=np.uint8),
        )
    return rr.Mesh3D(
        vertex_positions=np.concatenate(verts),
        triangle_indices=np.asarray(np.concatenate(faces), dtype=np.int32).reshape((-1, 3)),
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
    vis = f"  能见度 {env.visibility_factor:.0%}"
    rr.log(
        "feed/weather",
        rr.TextLog(f"气象：{name}（{illum}）{wind}{vis}", level="INFO"),
        static=True,
    )


def _ground_haze(tick, terrain, n_fog, color, *, r_lo, r_hi, h):
    """贴地漂移雾/霾/沙尘椭球层（Ellipsoids3D 实例，官方轻量组件）。

    半径/数量/透明度按"恰当可辨"收敛：能看清战场轮廓，不糊成一片。
    """
    rng = np.random.default_rng(7)
    fx = rng.uniform(0, terrain.size[0], n_fog)
    fy = rng.uniform(0, terrain.size[1], n_fog)
    fr = rng.uniform(r_lo, r_hi, n_fog)
    drift = (tick * 6) % 300
    cx = (fx + drift) % terrain.size[0]
    gz = terrain.height_at(cx, fy)
    centers = np.column_stack([cx, fy, gz + h])
    return rr.Ellipsoids3D(
        centers=centers,
        radii=np.column_stack([fr, fr, fr * 0.16]),
        colors=[color] * n_fog,
    )


def _log_weather(tick: int, scenario: Scenario, terrain: HeightField) -> None:
    env = scenario.environment
    w = env.weather
    night = env.illumination == "night"
    dim = 0.5 if night else 1.0

    def _dim(c):
        vals = [int(round(v * dim)) for v in c[:3]]
        if len(c) > 3:
            vals.append(c[3])
        return tuple(vals)

    # 风矢（3 支横排箭头，仅有效风速时显示）
    if env.wind_speed_mps >= 0.5:
        wd = math.radians(env.wind_dir_deg)
        vx, vy = math.sin(wd), math.cos(wd)
        W, H = terrain.size
        origins = [[W * 0.08, H * f, 0.0] for f in (0.22, 0.5, 0.78)]
        for o in origins:
            o[2] = float(terrain.height_at(o[0], o[1])) + 8.0
        rr.log(
            "world/weather/wind",
            rr.Arrows3D(
                vectors=[[vx * 320.0, vy * 320.0, 0.0]] * 3,
                origins=origins,
                colors=[_dim((196, 203, 214))],
                radii=14.0,
            ),
        )

    rng = np.random.default_rng(7)
    if w in ("rain", "heavy_rain"):
        # 斜细雨线（LineStrips3D，长 ~18m 随风斜吹）：比不透明大点更接近真实雨幕。
        # C7-d：用户反馈"气象没有明显渲染"——密度/线宽上调约 40%，仍保持细雨质感
        n_pts = 340 if w == "heavy_rain" else 260
        speed = 160 if w == "heavy_rain" else 110
        L = 18.0
        px = rng.uniform(0, terrain.size[0], n_pts)
        py = rng.uniform(0, terrain.size[1], n_pts)
        z = 260 + ((rng.uniform(0, 900, n_pts) - tick * speed) % 900)
        wd = math.radians(env.wind_dir_deg)
        dx, dy = math.sin(wd) * L * 0.25, math.cos(wd) * L * 0.25
        strips = [
            np.array([[px[i], py[i], z[i]], [px[i] + dx, py[i] + dy, z[i] - L]])
            for i in range(n_pts)
        ]
        rr.log(
            "world/weather/precip",
            rr.LineStrips3D(strips, colors=[_dim((105, 138, 188))], radii=2.0),
        )
    elif w == "snow":
        n_pts = 260
        px = rng.uniform(0, terrain.size[0], n_pts)
        py = rng.uniform(0, terrain.size[1], n_pts)
        z = 150 + ((rng.uniform(0, 700, n_pts) - tick * 30) % 700)
        rr.log(
            "world/weather/precip",
            rr.Points3D(np.column_stack([px, py, z]), radii=5.5, colors=[_dim((238, 242, 246))]),
        )
    elif w in ("fog", "heavy_fog"):
        # 贴地扁球雾团，缓慢漂移；多球重叠 alpha 会叠加，取低值。
        # C7-d：数量/半径/透明度上调——大团雾贴地漂移才看得出"雾"，
        # 此前 60 球 alpha 11 在 WASM viewer 里几乎不可见
        n_fog = 120 if w == "heavy_fog" else 85
        alpha = 26 if w == "heavy_fog" else 20
        rr.log(
            "world/weather/precip",
            _ground_haze(tick, terrain, n_fog, _dim((205, 210, 216)) + (alpha,), r_lo=260, r_hi=520, h=26),
        )
    elif w == "sand":
        rr.log(
            "world/weather/precip",
            _ground_haze(tick, terrain, 85, _dim((196, 162, 104)) + (22,), r_lo=280, r_hi=500, h=38),
        )
    elif w == "haze":
        rr.log(
            "world/weather/precip",
            _ground_haze(tick, terrain, 70, _dim((200, 200, 196)) + (18,), r_lo=300, r_hi=520, h=55),
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


# C7-j：空中模型米制外形（机身 8~15m）在 10km 战场视野里太小，需放大才能辨认；
# 全景相机下地形布满扇面/标签，需足够大才醒目。50× 后武直旋翼 ~750m（最小 rrd
# 实验验证此量级清晰可见），仍远小于战场 10km 尺度。
AIR_MODEL_SCALE = 50.0
# 飞行高度兜底：C7-i 统一太行山真实 DEM（最高 ~2024m）后，原仿真想定的
# alt=900/1500m 低于山峰，飞机被埋进山体。渲染时抬到地形最高点之上该余量。
AIR_GROUND_CLEARANCE = 400.0


def _log_air(
    tick: int,
    air: pd.DataFrame,
    trails: dict[str, list],
    done: set[str],
    *,
    use_models: bool = False,
    model_logged: set[str] | None = None,
    ground_max: float | None = None,
) -> None:
    """空中架次：按真实高度分层的航点 + 尾迹；被拦截/耗尽/返航后清除。

    C7-j：use_models=True 时用程序化 GLB（无人机/巡飞弹/武直）+ Transform3D
    渲染真实外形，按轨迹差分推算航向；False 时回退 Points3D 圆球（轻量）。
    ground_max：渲染地形最高高程，空高度不足时抬高避免飞机埋山（C7-i 真实 DEM）。
    """
    new = air[air["tick"] == tick]
    floor = (ground_max + AIR_GROUND_CLEARANCE) if ground_max is not None else None
    for _, r in new.iterrows():
        aid = str(r["asset_id"])
        alt = float(r["alt"])
        if floor is not None:
            alt = max(alt, floor)
        trails.setdefault(aid, []).append([float(r["x"]), float(r["y"]), alt])
        if str(r["state"]) in AIR_DONE_STATES:
            done.add(aid)
    if model_logged is None:
        model_logged = set()
    for aid, pts in trails.items():
        base = f"world/air/{aid}"
        if aid in done:
            rr.log(f"{base}/trail", rr.Clear(recursive=False))
            rr.log(f"{base}/mark", rr.Clear(recursive=False))
            rr.log(f"{base}/model", rr.Clear(recursive=False))
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
        if use_models:
            # 型号 GLB 静态资源（同一架次只发一次；换型时 kind 变化亦重发）
            asset_key = f"{aid}:{kind}"
            if asset_key not in model_logged:
                try:
                    glb = ensure_model(kind)
                    rr.log(f"{base}/model", rr.Asset3D(path=str(glb)), static=True)
                    model_logged.add(asset_key)
                except Exception:
                    pass  # 模型生成失败则退化为仅有尾迹，不阻断推演
            # 该 tick 的位姿：轨迹末两点差分算航向（机头 +X 模型绕 Z 旋转）
            if len(pts) >= 2:
                dx, dy = pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]
                heading = math.atan2(dy, dx) if (dx or dy) else 0.0
            else:
                heading = 0.0
            if asset_key in model_logged:
                rr.log(
                    base,
                    rr.Transform3D(
                        translation=[x, y, z],
                        rotation=rr.RotationAxisAngle(axis=[0, 0, 1], radians=heading),
                        scale=AIR_MODEL_SCALE,
                    ),
                )
            # 型号文字标签：Points3D 极小锚点仅承载中文 label（rerun 0.39 无 Text3D）
            rr.log(
                f"{base}/label",
                rr.Points3D(
                    [[x, y, z + 45]], radii=[8], colors=[color],
                    labels=[AIR_KIND_LABEL.get(kind, kind)],
                ),
            )
        else:
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


def _log_tow_lines(cur: pd.DataFrame, info, terrain: HeightField) -> None:
    """C7-a2：抢救车挂钩拖救时的牵引线（由快照就近配对推导：
    存活 arv 与 100m 内 M 级在修受损单位相连；挂钩间距 50m，阈值 100m）。"""
    alive = cur["alive"]
    is_arv = np.asarray([info[uid][0] == "arv" for uid in cur.index])
    towed = alive & cur["damaged"] & cur["under_repair"] & (cur["damage_grade"] == 1)
    arv_rows = cur[alive & is_arv]
    tgt_rows = cur[towed]
    segs, used = [], set()
    for _aid, ar in arv_rows.iterrows():
        if tgt_rows.empty:
            break
        d = np.hypot(
            tgt_rows["x"].to_numpy() - float(ar["x"]),
            tgt_rows["y"].to_numpy() - float(ar["y"]),
        )
        k = int(np.argmin(d))
        tuid = tgt_rows.index[k]
        if float(d[k]) > 100.0 or tuid in used:
            continue
        tr = tgt_rows.iloc[k]
        za = float(terrain.height_at(float(ar["x"]), float(ar["y"])))
        zt = float(terrain.height_at(float(tr["x"]), float(tr["y"])))
        segs.append([[float(ar["x"]), float(ar["y"]), za + 18],
                     [float(tr["x"]), float(tr["y"]), zt + 18]])
        used.add(tuid)
    if segs:
        rr.log(
            "world/support/tow_lines",
            rr.LineStrips3D(segs, colors=[TOW_LINE_C] * len(segs), radii=5),
        )
    else:
        rr.log("world/support/tow_lines", rr.Clear(recursive=False))


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
                triangle_indices=np.asarray(np.concatenate(ff), dtype=np.int32).reshape((-1, 3)),
                vertex_colors=np.concatenate(cc),
            ),
        )
    else:
        rr.log("world/effects/smoke_bands", rr.Clear(recursive=False))

    if effects is None:
        return
    ev = effects[effects["tick"] == tick]
    launch, impact, intercept, strike, breach = [], [], [], [], []
    tow_pts, supply_pts, lost_pts, ammo_pts = [], [], [], []
    feed = []
    for _, r in ev.iterrows():
        x, y = float(r["x"]), float(r["y"])
        z = float(terrain.height_at(x, y))
        kind = str(r["kind"])
        detail = str(r["detail"])
        if kind == "fire_launch":
            launch.append([x, y, z + 6])
        elif kind == "fire_impact":
            impact.append([x, y, z + 10])
        elif kind == "intercept":
            intercept.append([x, y, z + 8])
            feed.append(("防空拦截！", INTERCEPT_C, "WARN"))
        elif kind == "air_kill":
            intercept.append([x, y, z + 8])
            feed.append((f"空中目标被击落（{detail}）", AIRSTRIKE_C, "WARN"))
        elif kind == "loiter_strike":
            strike.append([x, y, z + 10])
            feed.append(("巡飞弹命中雷达/节点", AIRSTRIKE_C, "WARN"))
        elif kind == "breach":
            breach.append([x, y, z + 8])
            feed.append(("工兵开辟通路", BREACH_C, "WARN"))
        # ---- C7-a2：保障业务事件 ----
        elif kind == "tow_hook":
            tow_pts.append([x, y, z + 12])
            feed.append((detail or "抢救车挂钩拖救", TOW_LINE_C, "WARN"))
        elif kind == "tow_done":
            tow_pts.append([x, y, z + 12])
            feed.append((detail or "受损装备拖抵营抢修点", SUPPLY_C, "WARN"))
        elif kind == "supply_wave":
            supply_pts.append([x, y, z + 12])
            feed.append((detail or "器材前送波次抵站", SUPPLY_C, "WARN"))
        elif kind == "supply_lost":
            lost_pts.append([x, y, z + 14])
            feed.append((detail or "补给车前送途中损失", SUPPORT_LOST_C, "CRITICAL"))
        elif kind == "ammo_deliver":
            ammo_pts.append([x, y, z + 12])
            feed.append((detail or "弹药补给车抵站补弹", AMMO_C, "WARN"))
    buckets = (
        ("launch", launch, LAUNCH_C, 90),
        ("impact", impact, IMPACT_C, 150),
        ("intercept", intercept, INTERCEPT_C, 130),
        ("strike", strike, AIRSTRIKE_C, 170),
        ("breach", breach, BREACH_C, 100),
        ("tow", tow_pts, TOW_LINE_C, 110),
        ("supply", supply_pts, SUPPLY_C, 120),
        ("supply_lost", lost_pts, SUPPORT_LOST_C, 150),
        ("ammo", ammo_pts, AMMO_C, 120),
    )
    for name, pts, color, radius in buckets:
        if pts:
            rr.log(f"world/effects/{name}",
                   rr.Points3D(pts, radii=radius, colors=[color]))
        else:
            rr.log(f"world/effects/{name}", rr.Clear(recursive=False))
    for txt, color, level in feed:
        rr.log("feed/effects",
               rr.TextLog(f"T+{tick:03d} {txt}", level=level, color=color))


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
        # C7-d：相机由 z=9200 高俯视降到 z=6200 斜视（与桌面版 6800 接近）——
        # 高俯视下垂直夸张的地形仍像平面地图，斜视才看得出三维地貌
        eye or (3800.0, -4800.0, 6200.0),
        look or (5000.0, 5000.0, 200.0),
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
