"""写实比例装备模型：用 trimesh 程序化生成 GLB（米制，车体朝向 +X）。

第一轮采用硬表面程序化建模（炮塔/炮管/履带/负重轮齐全），保证可复现、
无外部下载依赖、License 干净（trimesh MIT）。所有部件合并为单一网格并
写入顶点色（glTF COLOR_0），最大程度兼容各渲染器（含 rerun Asset3D）。
M3 可用实测/扫描 GLB 直接替换 assets/models 下同名文件。
"""

from __future__ import annotations

import functools
import math
from pathlib import Path

import numpy as np
import trimesh

ASSET_DIR = Path(__file__).resolve().parents[3] / "assets" / "models"
# C7-l：Web 回放专用的低面数地面装备。原始 tank/ifv/atgm_team GLB 分别
# 483KB/3.0MB/1.7MB，逐单位嵌入会让旅级 rrd 膨胀到 162~268MB，超出
# Rerun Web Viewer 的 WASM 内存预算。这里用同一套程序化构建器生成
# 低面数版本（几 KB/辆），仍是真实 3D 装备形体而非盒体/点球。
LITE_DIR = ASSET_DIR / "lite"
USE_LITE_GROUND_MODELS = True
LITE_TYPES = ("tank", "ifv", "atgm_team", "recon_veh")

OLIVE = (77, 84, 46)  # 军绿
OLIVE_LIGHT = (87, 94, 54)
TAN = (115, 102, 71)  # 沙褐
DARK = (22, 22, 20)  # 履带/炮管/橡胶


def _paint(mesh: trimesh.Trimesh, rgb) -> trimesh.Trimesh:
    rgba = np.tile(np.array([rgb[0], rgb[1], rgb[2], 255], dtype=np.uint8), (len(mesh.vertices), 1))
    mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=rgba)
    return mesh


def _box(extents, center, color) -> trimesh.Trimesh:
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(center)
    return _paint(m, color)


def _cyl(radius, length, center, axis, color, sections: int = 16) -> trimesh.Trimesh:
    m = trimesh.creation.cylinder(radius=radius, height=length, sections=sections)
    # 圆柱默认轴向 +Z，转到目标轴
    if axis == "x":
        m.apply_transform(trimesh.transformations.rotation_matrix(1.5707963, [0, 1, 0]))
    elif axis == "y":
        m.apply_transform(trimesh.transformations.rotation_matrix(1.5707963, [1, 0, 0]))
    m.apply_translation(center)
    return _paint(m, color)


def _tank() -> list[trimesh.Trimesh]:
    p = [_box((6.2, 3.2, 0.9), (0, 0, 1.15), OLIVE)]
    # 前上倾斜装甲
    glacis = _box((1.1, 3.0, 0.25), (2.7, 0, 1.55), OLIVE)
    glacis.apply_transform(trimesh.transformations.rotation_matrix(0.55, [0, 1, 0]))
    p.append(glacis)
    # 履带 + 负重轮
    for y in (-1.55, 1.55):
        p.append(_box((6.6, 0.55, 0.85), (0, y, 0.78), DARK))
        for x in (-2.5, -1.25, 0.0, 1.25, 2.5):
            p.append(_cyl(0.42, 0.5, (x, y, 0.42), "y", DARK, sections=12))
    # 炮塔 + 炮盾 + 炮管
    p.append(_cyl(1.25, 0.85, (0.2, 0, 2.0), "z", OLIVE, sections=20))
    p.append(_box((0.7, 1.1, 0.5), (1.25, 0, 2.05), OLIVE))
    p.append(_cyl(0.07, 4.4, (3.0, 0, 2.1), "x", DARK, sections=10))
    return p


def _ifv() -> list[trimesh.Trimesh]:
    p = [_box((6.4, 2.9, 0.85), (0, 0, 1.05), OLIVE_LIGHT)]
    for y in (-1.35, 1.35):  # 8x8 轮式
        for x in (-2.3, -0.78, 0.78, 2.3):
            p.append(_cyl(0.46, 0.42, (x, y, 0.46), "y", DARK, sections=12))
    p.append(_cyl(0.62, 0.55, (0.1, 0, 1.78), "z", OLIVE_LIGHT, sections=18))
    p.append(_cyl(0.045, 2.6, (1.55, 0, 1.85), "x", DARK, sections=8))
    return p


def _atgm_team() -> list[trimesh.Trimesh]:
    p = [_box((1.4, 1.1, 0.5), (0, 0, 0.5), TAN)]
    p.append(_cyl(0.13, 1.9, (0.35, 0, 1.05), "x", DARK, sections=10))
    for dy, dx in ((0.5, 0.45), (-0.5, 0.45), (0.0, -0.5)):  # 三脚架
        leg = _cyl(0.035, 1.15, (dx * 0.6, dy * 0.6, 0.45), "x", DARK, sections=8)
        leg.apply_transform(trimesh.transformations.rotation_matrix(dx * 0.6, [0, 1, 0]))
        leg.apply_transform(trimesh.transformations.rotation_matrix(dy * 0.6, [1, 0, 0]))
        p.append(leg)
    return p


# ---- C7-a0 合成兵种粗形体（M3 起可用 assets/models 同名 GLB 直接替换）----


def _wheeled_hull(length=6.6, width=2.9, color=OLIVE_LIGHT) -> list[trimesh.Trimesh]:
    """8x8/6x6 轮式底盘：车体 + 四对负重轮。"""
    p = [_box((length, width, 0.9), (0, 0, 1.05), color)]
    for y in (-width / 2 + 0.12, width / 2 - 0.12):
        for x in (-length / 2 + 1.1, -length / 6, length / 6, length / 2 - 1.1):
            p.append(_cyl(0.46, 0.4, (x, y, 0.46), "y", DARK, sections=12))
    return p


def _track_hull(length=6.4, width=3.0, color=OLIVE) -> list[trimesh.Trimesh]:
    """履带底盘：车体 + 双侧履带。"""
    p = [_box((length, width - 0.7, 0.9), (0, 0, 1.1), color)]
    for y in (-width / 2 + 0.25, width / 2 - 0.25):
        p.append(_box((length + 0.3, 0.5, 0.8), (0, y, 0.75), DARK))
    return p


def _spg(barrel_len=3.4, barrel_r=0.09) -> list[trimesh.Trimesh]:
    """自行火炮：履带底盘 + 大仰角炮管（迫击炮/榴弹炮共用，长度区分）。"""
    p = _track_hull(color=OLIVE)
    p.append(_box((2.6, 2.2, 0.7), (-0.3, 0, 1.95), OLIVE))  # 炮塔舱
    barrel = _cyl(barrel_r, barrel_len, (0.9, 0, 2.6), "x", DARK, sections=10)
    barrel.apply_transform(trimesh.transformations.rotation_matrix(0.6, [0, 1, 0]))
    p.append(barrel)
    return p


def _mrl() -> list[trimesh.Trimesh]:
    """自行火箭炮：轮式底盘 + 仰角多管定向器束。"""
    p = _wheeled_hull(color=OLIVE_LIGHT)
    pack = _box((2.8, 1.8, 0.7), (0.4, 0, 2.0), OLIVE)
    pack.apply_transform(trimesh.transformations.rotation_matrix(0.35, [0, 1, 0]))
    p.append(pack)
    for y in (-0.55, 0.0, 0.55):
        for z in (1.85, 2.15):
            tube = _cyl(0.06, 3.0, (0.9, y, z), "x", DARK, sections=8)
            tube.apply_transform(trimesh.transformations.rotation_matrix(0.35, [0, 1, 0]))
            p.append(tube)
    return p


def _missile_veh() -> list[trimesh.Trimesh]:
    """导弹发射车：8x8 底盘 + 倾斜四联发射箱。"""
    p = _wheeled_hull(color=OLIVE_LIGHT)
    box = _box((3.2, 2.0, 0.9), (0.3, 0, 1.95), TAN)
    box.apply_transform(trimesh.transformations.rotation_matrix(0.3, [0, 1, 0]))
    p.append(box)
    return p


def _spaa() -> list[trimesh.Trimesh]:
    """自行高炮：履带底盘 + 双联高炮 + 顶部搜索雷达板。"""
    p = _track_hull(color=OLIVE)
    p.append(_cyl(0.9, 0.6, (0, 0, 1.95), "z", OLIVE, sections=16))
    for y in (-0.18, 0.18):
        p.append(_cyl(0.05, 3.2, (1.6, y, 2.2), "x", DARK, sections=8))
    dish = _box((0.9, 0.12, 0.5), (-0.7, 0, 2.55), DARK)
    dish.apply_transform(trimesh.transformations.rotation_matrix(0.25, [1, 0, 0]))
    p.append(dish)
    return p


def _radar_veh(dish: bool = False) -> list[trimesh.Trimesh]:
    """雷达车：8x8 底盘 + 可升降平板天线（dish=True 改用碟形反炮兵雷达）。"""
    p = _wheeled_hull(color=OLIVE_LIGHT)
    p.append(_box((2.6, 2.4, 0.25), (-0.3, 0, 2.9), TAN))  # 平板天线
    p.append(_box((0.12, 0.12, 1.3), (-0.3, 0, 2.15), DARK))  # 升降杆
    if dish:
        p[-2] = _cyl(1.0, 0.18, (-0.3, 0, 2.9), "y", TAN, sections=18)
    return p


def _ew_veh() -> list[trimesh.Trimesh]:
    """电抗车：8x8 底盘 + 设备舱 + 鞭天线群与小型干扰碟。"""
    p = _wheeled_hull(color=OLIVE_LIGHT)
    p.append(_box((3.4, 2.5, 0.8), (-0.2, 0, 1.9), OLIVE))
    for x in (-1.3, -0.5, 0.3, 1.1):
        p.append(_cyl(0.03, 1.8, (x, 1.0, 2.9), "z", DARK, sections=6))
    p.append(_cyl(0.45, 0.12, (0.6, -0.9, 2.6), "z", TAN, sections=14))
    return p


def _c2_veh() -> list[trimesh.Trimesh]:
    """指挥通信车：8x8 高通信舱 + 多根天线。"""
    p = _wheeled_hull(color=OLIVE_LIGHT)
    p.append(_box((4.0, 2.6, 1.1), (-0.4, 0, 1.95), OLIVE))
    for x, y in ((-1.6, 0.9), (-0.6, -1.0), (0.8, 1.0), (1.5, -0.8)):
        p.append(_cyl(0.03, 2.2, (x, y, 3.3), "z", DARK, sections=6))
    return p


def _eng_veh() -> list[trimesh.Trimesh]:
    """综合扫雷破障车：履带底盘 + 前部 V 形扫雷犁 + 后部起重吊臂。"""
    p = _track_hull(color=OLIVE)
    plow = _box((0.4, 3.4, 0.7), (3.3, 0, 0.7), DARK)
    plow.apply_transform(trimesh.transformations.rotation_matrix(0.35, [0, 1, 0]))
    p.append(plow)
    boom = _cyl(0.08, 2.8, (-2.2, 0, 2.4), "x", DARK, sections=8)
    boom.apply_transform(trimesh.transformations.rotation_matrix(-0.7, [0, 1, 0]))
    p.append(boom)
    return p


def _truck() -> list[trimesh.Trimesh]:
    """通用保障卡车：6x6 底盘 + 车头 + 货厢（发烟/抢救/抢修/补给/弹药车共用）。"""
    p = [_box((6.8, 2.6, 0.7), (0, 0, 0.95), OLIVE_LIGHT)]
    for y in (-1.18, 1.18):
        for x in (-2.3, -0.2, 2.2):
            p.append(_cyl(0.44, 0.4, (x, y, 0.44), "y", DARK, sections=12))
    p.append(_box((1.6, 2.4, 1.1), (2.2, 0, 1.7), OLIVE))   # 车头
    p.append(_box((3.6, 2.5, 1.2), (-1.1, 0, 1.75), TAN))   # 货厢
    return p


# ---- C7-j 空中平台：无人机 / 巡飞弹 / 武装直升机（程序化，机头朝 +X）----
# 此前空中单位用 Points3D 圆球代替，用户反馈"能否用真实装备"。复用同一套
# trimesh → GLB 管线，三类空中目标各建可辨外形：固定翼无人机（大展弦比平直翼）
# / 巡飞弹（小三角翼 + 筒身）/ 武直（短粗机身 + 十字主旋翼 + 尾梁尾桨）。

AIR_GRAY = (128, 134, 138)   # 无人机浅灰
AIR_TAN = (120, 108, 78)     # 巡飞弹沙褐
AIR_OLIVE = (72, 82, 58)     # 武直军绿


def _uav() -> list[trimesh.Trimesh]:
    """中高空固定翼察打无人机（翼龙/彩虹级）：细长机身 + 大展弦比平直翼
    + H 尾翼 + 腹下吊舱，机头 +X。"""
    p = [_cyl(0.45, 8.5, (0, 0, 0), "x", AIR_GRAY, sections=14)]  # 机身
    nose = _cyl(0.45, 1.6, (4.6, 0, 0), "x", AIR_GRAY, sections=14)  # 机头整流罩
    p.append(nose)
    # 主翼：大展弦比薄板（翼展 ~14m，弦长 ~1.6m）
    wing = _box((1.6, 14.0, 0.18), (-0.4, 0, 0.1), AIR_GRAY)
    p.append(wing)
    # H 形尾翼：水平尾 + 双垂尾
    p.append(_box((1.1, 4.6, 0.14), (-3.7, 0, 0.05), AIR_GRAY))
    for y in (-2.2, 2.2):
        fin = _box((1.0, 0.14, 1.5), (-3.8, y, 0.75), AIR_GRAY)
        p.append(fin)
    # 腹下光电/武器吊舱
    p.append(_cyl(0.28, 1.4, (0.6, 0, -0.6), "x", DARK, sections=10))
    return p


def _loitering() -> list[trimesh.Trimesh]:
    """巡飞弹（弹簧刀级）：小型三角翼 + 筒式机身 + 头部传感器，机头 +X。"""
    p = [_cyl(0.16, 1.5, (0, 0, 0), "x", AIR_TAN, sections=10)]
    p.append(_cyl(0.17, 0.35, (0.85, 0, 0), "x", DARK, sections=10))  # 导引头
    # 三角翼：用 box 近似后掠翼（左右各一片）
    for sign in (-1, 1):
        w = _box((1.1, 0.9, 0.06), (-0.25, sign * 0.55, 0.05), AIR_TAN)
        w.apply_transform(
            trimesh.transformations.rotation_matrix(sign * 0.5, [1, 0, 0])
        )
        p.append(w)
    # 尾部弹翼
    p.append(_box((0.35, 1.1, 0.05), (-0.65, 0, 0.05), AIR_TAN))
    return p


def _helo() -> list[trimesh.Trimesh]:
    """武装直升机（直-10/阿帕奇级）：短粗机身 + 串列座舱 + 十字主旋翼
    + 尾梁尾桨 + 短翼挂架，机头 +X。"""
    p = [_box((9.0, 2.0, 1.8), (0, 0, 0), AIR_OLIVE)]  # 机身
    # 串列座舱（前低后高，阶梯）
    p.append(_box((2.0, 1.6, 1.0), (2.9, 0, 0.9), DARK))
    p.append(_box((1.8, 1.6, 1.0), (1.1, 0, 1.5), DARK))
    # 主旋翼桅杆 + 十字旋翼（四片细长桨叶）
    p.append(_cyl(0.18, 1.2, (-0.6, 0, 1.8), "z", DARK, sections=10))
    for ang in (0, math.pi / 2):
        blade = _box((15.0, 0.35, 0.06), (-0.6, 0, 2.4), DARK)
        blade.apply_transform(
            trimesh.transformations.rotation_matrix(ang, [0, 0, 1])
        )
        p.append(blade)
    # 尾梁 + 尾桨 + 垂尾
    p.append(_box((5.0, 0.5, 0.5), (-6.7, 0, 0.5), AIR_OLIVE))
    tail_rotor = _box((0.08, 2.6, 0.3), (-9.0, 0, 0.9), DARK)
    tail_rotor.apply_transform(
        trimesh.transformations.rotation_matrix(0.5, [1, 0, 0])
    )
    p.append(tail_rotor)
    p.append(_box((1.2, 0.12, 1.6), (-8.6, 0, 1.0), AIR_OLIVE))  # 垂尾
    # 短翼挂架 + 火箭吊舱
    for y in (-1.7, 1.7):
        p.append(_box((1.6, 1.2, 0.2), (0.5, y, -0.3), AIR_OLIVE))
        p.append(_cyl(0.22, 1.4, (0.5, y * 1.35, -0.5), "x", DARK, sections=10))
    return p


_BUILDERS = {
    "uav": _uav, "loitering": _loitering, "helo": _helo,
    "tank": _tank, "ifv": _ifv, "atgm_team": _atgm_team,
    "mortar": lambda: _spg(2.0, 0.12),
    "howitzer": lambda: _spg(3.6, 0.09),
    "rocket": _mrl,
    "hj10": _missile_veh,
    "hq17": _missile_veh,
    "pgz09": _spaa,
    "radar_air": _radar_veh,
    "radar_cb": lambda: _radar_veh(dish=True),
    "recon_veh": _ifv,
    "ew_radar": _ew_veh,
    "ew_comms": _ew_veh,
    "ew_gen": _ew_veh,
    "c2_veh": _c2_veh,
    "eng_veh": _eng_veh,
    "smoke_veh": _truck,
    "arv": _truck,
    "repair_veh": _truck,
    "supply_veh": _truck,
    "ammo_veh": _truck,
    "dummy_arty": lambda: _spg(2.0, 0.1),
}


def ensure_model(type_name: str) -> Path:
    """返回类型对应 GLB 路径，缺失则合并部件为单网格生成并缓存。"""
    if USE_LITE_GROUND_MODELS and type_name in LITE_TYPES:
        lite = LITE_DIR / f"{type_name}.glb"
        if lite.is_file():
            return lite
    path = ASSET_DIR / f"{type_name}.glb"
    if path.exists():
        return path
    builder = _BUILDERS.get(type_name)
    if builder is None:
        # 未注册类型回退通用卡车形体（多为后装保障车辆）；放入同名 GLB 即可覆盖
        builder = _truck
    path.parent.mkdir(parents=True, exist_ok=True)
    combined = trimesh.util.concatenate(builder())
    combined.export(path, file_type="glb")
    return path


@functools.lru_cache(maxsize=None)
def ground_offset(type_name: str) -> float:
    """放置实体时需抬高的高度 = 模型本地坐标最低点的绝对值（含节点变换）。

    同名资产替换后包围盒可能不同，每次按实际 GLB 计算，避免车体悬空/埋地。
    """
    path = ensure_model(type_name)
    loaded = trimesh.load(path)
    verts = loaded.to_geometry().vertices if hasattr(loaded, "to_geometry") else loaded.vertices
    return float(-verts[:, 2].min())
