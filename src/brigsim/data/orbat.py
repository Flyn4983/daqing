"""ORBAT 编制展开：成建制模板（UnitTemplate）按队形生成显式单位列表。

队形（heading 约定与 UnitSpec 一致：0=北(+y)，顺时针；anchor 为队形几何中心）：
- line   横队：沿垂直 heading 方向一字展开
- column 纵队：沿 heading 方向梯次配置
- wedge  前三角：1 辆尖兵 + 后续成对向两翼展开（接敌冲击队形）

营/旅级想定只需写连级模板，不再手摆几十上百个单位。
"""

from __future__ import annotations

import math

from .schema import UnitSpec, UnitTemplate


def _local_offsets(
    count: int, layout: str, along: float, lateral: float
) -> list[tuple[float, float]]:
    """返回队形内各单位的 (纵向 along, 横向 lateral) 局部坐标（未居中）。"""
    if layout == "line":
        return [(0.0, (i - (count - 1) / 2.0) * lateral) for i in range(count)]
    if layout == "column":
        return [((i - (count - 1) / 2.0) * along, 0.0) for i in range(count)]
    # wedge：0 号尖兵居前，其余按行成对向左右展开
    pts = [(0.0, 0.0)]
    for i in range(1, count):
        row = (i + 1) // 2
        side = 1.0 if i % 2 == 1 else -1.0  # 奇数左、偶数右
        pts.append((row * along, side * row * lateral))
    return pts


def instantiate(tpl: UnitTemplate) -> list[UnitSpec]:
    h = math.radians(tpl.heading_deg)
    forward = (math.sin(h), math.cos(h))  # heading 方向
    left = (-math.cos(h), math.sin(h))  # 左侧横向
    local = _local_offsets(tpl.count, tpl.layout, tpl.spacing_along, tpl.spacing_lateral)
    mean_a = sum(a for a, _ in local) / len(local)
    mean_b = sum(b for _, b in local) / len(local)

    units = []
    for i, (a, b) in enumerate(local, start=1):
        da, db = a - mean_a, b - mean_b
        units.append(
            UnitSpec(
                unit_id=f"{tpl.id_prefix}{i:02d}",
                type=tpl.type,
                pos=(
                    tpl.anchor[0] + da * forward[0] + db * left[0],
                    tpl.anchor[1] + da * forward[1] + db * left[1],
                ),
                org=f"{tpl.org} {i:02d}",
                heading_deg=tpl.heading_deg,
                dug_in=tpl.dug_in,
                decoy=tpl.decoy,
                initial_worn=(i <= tpl.worn_count),  # C7-a1：前 k 个单位带病参战
            )
        )
    return units


def expand_templates(
    templates: dict[str, list[UnitTemplate]], known_types: set[str]
) -> dict[str, list[UnitSpec]]:
    out: dict[str, list[UnitSpec]] = {}
    for side, tpls in templates.items():
        units: list[UnitSpec] = []
        seen: set[str] = set()
        for tpl in tpls:
            if tpl.type not in known_types:
                raise ValueError(f"模板 {tpl.org} 引用了未定义的平台类型: {tpl.type}")
            if tpl.id_prefix in seen:
                raise ValueError(f"{side} 方模板 id_prefix 重复: {tpl.id_prefix}")
            seen.add(tpl.id_prefix)
            units.extend(instantiate(tpl))
        out[side] = units
    return out
