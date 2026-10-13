"""写实比例装备模型：用 trimesh 程序化生成 GLB（米制，车体朝向 +X）。

第一轮采用硬表面程序化建模（炮塔/炮管/履带/负重轮齐全），保证可复现、
无外部下载依赖、License 干净（trimesh MIT）。所有部件合并为单一网格并
写入顶点色（glTF COLOR_0），最大程度兼容各渲染器（含 rerun Asset3D）。
M3 可用实测/扫描 GLB 直接替换 assets/models 下同名文件。
"""

from __future__ import annotations

import functools
from pathlib import Path

import numpy as np
import trimesh

ASSET_DIR = Path(__file__).resolve().parents[3] / "assets" / "models"

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


_BUILDERS = {"tank": _tank, "ifv": _ifv, "atgm_team": _atgm_team}


def ensure_model(type_name: str) -> Path:
    """返回类型对应 GLB 路径，缺失则合并部件为单网格生成并缓存。"""
    path = ASSET_DIR / f"{type_name}.glb"
    if path.exists():
        return path
    builder = _BUILDERS.get(type_name)
    if builder is None:
        raise KeyError(f"未注册的装备模型类型: {type_name}")
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
