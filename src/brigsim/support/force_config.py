"""对战双方实力配置：GUI 录入 → Scenario 编成/方别乘子注入（不改内核）。

设计要点（对齐分析 §功能改造范围 A 层）：
- **红蓝对称**：每方都可录入地面装备 26 类数量、空中架次 3 类数量、
  方别乘子（pk / detect / speed）、带病装备与工事比例。
- **零漂移**：未提交 forces（或为空）时 `inject()` 原样返回 scenario，
  既有权威数字（R²=0.705、四率等）完全不变。
- **不改内核**：只在 api._deduce_worker 加载想定后、apply_features 前，
  用 `scenario.model_copy(update=...)` 覆盖 forces/air_packages/side_modifiers。
  缩放沿用 design._resize_entity_group 相同的"裁尾/克隆顺延"约定。
- 数量语义：给定类型录入数 = 该方该类型的**目标总数**；想定中不存在该类型时
  不凭空新增（避免无锚点几何），并在 warnings 中说明。
- 乘子语义：pk/detect/speed 直接覆盖 SideModifiers（apply_features 的
  pk_scale 之后在其上累乘 red_pk_scale/blue_pk_scale 情景因子，二者叠加符合约定）。
"""

from __future__ import annotations

from typing import Any

from .catalog import CATALOG

_SIDES = ("red", "blue")
_AIR_KINDS = ("uav", "loitering", "helo")
_GROUND_KEYS = {e["key"] for e in CATALOG["equipment"] if e["key"] not in _AIR_KINDS}
_MULTIPLIERS = ("pk_scale", "detect_scale", "speed_scale")


def _as_int(v: Any, name: str, lo: int = 0, hi: int = 2000) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是整数，收到 {v!r}")
    if not (lo <= n <= hi):
        raise ValueError(f"{name} 必须在 [{lo},{hi}] 之间，收到 {n}")
    return n


def _as_float(v: Any, name: str, lo: float = 0.1, hi: float = 5.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是数字，收到 {v!r}")
    if not (lo <= f <= hi):
        raise ValueError(f"{name} 必须在 [{lo},{hi}] 之间，收到 {f}")
    return f


def validate(forces: Any) -> tuple[dict, list[str]]:
    """校验双方实力录入，返回 (规范化 forces, 警告列表)。forces 为空返回 {}。"""
    warnings: list[str] = []
    if not forces:
        return {}, warnings
    if not isinstance(forces, dict):
        raise ValueError("forces 必须是对象")
    out: dict = {}
    for side in _SIDES:
        raw = forces.get(side) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"forces.{side} 必须是对象")
        side_out: dict = {"equipment": {}, "air": {}, "modifiers": {},
                          "worn_ratio": 0.0, "dug_ratio": 0.0}
        for key, val in (raw.get("equipment") or {}).items():
            k = str(key)
            # 允许想定专属平台类型（如 medium_brigade 的 wheel_assault），
            # 不只限于标准 26 类目录；只要类型标识合法即可，注入时按想定实际编成匹配。
            if not k.replace("_", "").isalnum():
                warnings.append(f"{side}.equipment.{k} 类型标识不合法，已忽略")
                continue
            if k in _AIR_KINDS:
                warnings.append(f"{side}.equipment.{k} 属空中架次，请填入 air 分组")
                continue
            side_out["equipment"][k] = _as_int(val, f"forces.{side}.equipment.{k}")
        for kind, val in (raw.get("air") or {}).items():
            k = str(kind)
            if k not in _AIR_KINDS:
                warnings.append(f"{side}.air.{k} 不是空中架次类型，已忽略")
                continue
            side_out["air"][k] = _as_int(val, f"forces.{side}.air.{k}")
        for name in _MULTIPLIERS:
            f = raw.get("modifiers", {}).get(name, 1.0)
            side_out["modifiers"][name] = _as_float(f, f"forces.{side}.modifiers.{name}")
        side_out["worn_ratio"] = _as_float(raw.get("worn_ratio", 0.0),
                                            f"forces.{side}.worn_ratio", 0.0, 1.0)
        side_out["dug_ratio"] = _as_float(raw.get("dug_ratio", 0.0),
                                           f"forces.{side}.dug_ratio", 0.0, 1.0)
        out[side] = side_out
    return out, warnings


def has_content(forces: dict) -> bool:
    """是否真正录入了实力（任一数量/乘子偏离默认即视为已配置）。"""
    if not forces:
        return False
    for side in _SIDES:
        s = forces.get(side) or {}
        if any(v for v in (s.get("equipment") or {}).values()):
            return True
        if any(v for v in (s.get("air") or {}).values()):
            return True
        mods = s.get("modifiers") or {}
        if any(abs(float(mods.get(k, 1.0)) - 1.0) > 1e-9 for k in _MULTIPLIERS):
            return True
        if float(s.get("worn_ratio", 0)) > 0 or float(s.get("dug_ratio", 0)) > 0:
            return True
    return False


# ---------------- 默认编成读取（前端"载入想定默认"） ----------------

def defaults(scenario_name: str) -> dict:
    """从想定读取双方当前编成数量/空中架次/乘子，作为 GUI 初值。"""
    from ..data.provider import YamlFileProvider

    sc = YamlFileProvider().load_scenario(scenario_name)
    # 平台类型显示名：标准目录 + 想定自带的 platform_types（含 wheel_* 等专属类型）
    names = {e["key"]: e["name"] for e in CATALOG["equipment"]}
    for key, spec in sc.platform_types.items():
        names.setdefault(key, str(getattr(spec, "name", "") or key))
    out: dict = {"scenario": scenario_name, "sides": {}, "equipment_names": names}
    for side in _SIDES:
        eq: dict[str, int] = {}
        for u in sc.forces.get(side, []):
            if u.type not in _AIR_KINDS:
                eq[u.type] = eq.get(u.type, 0) + 1
        air: dict[str, int] = {}
        for ap in sc.air_packages:
            if ap.side == side:
                air[ap.kind] = air.get(ap.kind, 0) + 1
        mods = sc.side_modifiers.get(side)
        out["sides"][side] = {
            "equipment": eq, "air": air,
            "modifiers": {
                "pk_scale": float(getattr(mods, "pk_scale", 1.0)),
                "detect_scale": float(getattr(mods, "detect_scale", 1.0)),
                "speed_scale": float(getattr(mods, "speed_scale", 1.0)),
            },
            "total_ground": sum(eq.values()), "total_air": sum(air.values()),
        }
    return out


# ---------------- 注入：按类型/架次缩放到目标数量 ----------------

def _resize_type_group(units: list, ptype: str, target: int) -> list:
    """把某方某平台类型的全部单位缩放到 target 个，返回新列表。"""
    idx = [i for i, u in enumerate(units) if getattr(u, "type", "") == ptype]
    if not idx:
        return units
    group = [units[i] for i in idx]
    if len(group) == target:
        return units
    order = sorted(idx, key=lambda i: units[i].unit_id)
    ordered = [units[i] for i in order]
    keep = ordered[:target]
    extra: list = []
    if target > len(ordered):
        ys = sorted(float(u.pos[1]) for u in ordered)
        gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 1]
        step = abs(sum(gaps) / len(gaps)) if gaps else 700.0
        template = ordered[-1]
        for k in range(len(ordered), target):
            uid = f"{template.unit_id}x{k + 1:02d}"
            pos = (float(template.pos[0]), float(ys[-1]) + step * (k - len(ordered) + 1))
            extra.append(template.model_copy(update={"unit_id": uid, "pos": pos}))
    new_group = keep + extra
    group_ids = {u.unit_id for u in ordered}
    out: list = []
    inserted = False
    for u in units:
        if u.unit_id in group_ids:
            if not inserted:
                out.extend(new_group)
                inserted = True
        else:
            out.append(u)
    return out


def _resize_air(packages: list, side: str, kind: str, target: int) -> list:
    """把某方某类空中架次缩放到 target 个。"""
    idx = [i for i, p in enumerate(packages) if p.side == side and p.kind == kind]
    if not idx:
        return packages
    group = [packages[i] for i in idx]
    if len(group) == target:
        return packages
    keep = group[:target]
    extra: list = []
    if target > len(group):
        template = group[-1]
        base_tick = int(template.launch_tick)
        for k in range(len(group), target):
            extra.append(template.model_copy(update={
                "launch_tick": base_tick + 1 + k,
                "label": f"{template.label}·补{k + 1}",
            }))
    new_group = keep + extra
    group_ids = {id(p) for p in group}
    out: list = []
    inserted = False
    for p in packages:
        if id(p) in group_ids:
            if not inserted:
                out.extend(new_group)
                inserted = True
        else:
            out.append(p)
    return out


def _apply_worn_and_dug(units: list, ratio: float, dug: float) -> list:
    """按比例把该方单位标记为带病 initial_worn / 既设工事 dug_in（确定性轮流分配）。"""
    if ratio <= 0 and dug <= 0:
        return units
    out: list = []
    n = len(units)
    n_worn = int(round(n * ratio)) if 0 < ratio <= 1 else int(ratio)
    n_dug = int(round(n * dug)) if 0 < dug <= 1 else int(dug)
    for i, u in enumerate(units):
        patch: dict = {}
        if n_worn and i < n_worn:
            patch["initial_worn"] = True
        if n_dug and i < n_dug:
            patch["dug_in"] = True
        out.append(u.model_copy(update=patch) if patch else u)
    return out


def inject(scenario, forces: dict) -> tuple[Any, dict]:
    """把双方实力注入 Scenario，返回 (新 Scenario, 注入报告)。forces 为空时零漂移。"""
    if not has_content(forces):
        return scenario, {"injected": False, "sides": {}, "notes": ["未录入双方实力，沿用基础想定编成（零漂移）"]}
    report: dict = {"injected": True, "sides": {}, "notes": []}
    forces_update: dict = {}
    air_update: list | None = None
    mods_update: dict = {}
    base_mods = dict(scenario.side_modifiers)

    for side in _SIDES:
        cfg = forces.get(side) or {}
        units = list(scenario.forces.get(side, []))
        eq = cfg.get("equipment") or {}
        applied_eq: dict[str, int] = {}
        for key, target in eq.items():
            before = sum(1 for u in units if getattr(u, "type", "") == key)
            if before == 0:
                report["notes"].append(f"{side} 基础想定无 {key} 编组，未凭空新增（需换基础想定）")
                continue
            units = _resize_type_group(units, key, int(target))
            after = sum(1 for u in units if getattr(u, "type", "") == key)
            if after != before:
                applied_eq[key] = after
        units = _apply_worn_and_dug(units, float(cfg.get("worn_ratio", 0)),
                                    float(cfg.get("dug_ratio", 0)))
        forces_update[side] = units

        # 空中架次
        air = cfg.get("air") or {}
        if air:
            air_update = list(scenario.air_packages) if air_update is None else air_update
            for kind, target in air.items():
                before = sum(1 for p in air_update if p.side == side and p.kind == kind)
                if before == 0:
                    report["notes"].append(f"{side} 基础想定无 {kind} 空中架次，未凭空新增")
                    continue
                air_update = _resize_air(air_update, side, kind, int(target))

        # 方别乘子（在既有值上覆盖）
        mods = cfg.get("modifiers") or {}
        m0 = scenario.side_modifiers.get(side)
        patch = {k: float(v) for k, v in mods.items() if k in _MULTIPLIERS}
        if patch:
            if m0 is None:
                from ..data.schema import SideModifiers
                m0 = SideModifiers()
            mods_update[side] = m0.model_copy(update=patch)

        report["sides"][side] = {
            "ground_before": sum(1 for u in scenario.forces.get(side, [])),
            "ground_after": len(units),
            "equipment_applied": applied_eq,
            "air": dict(air),
            "modifiers": mods,
        }

    update: dict = {"forces": forces_update}
    if air_update is not None:
        update["air_packages"] = air_update
    if mods_update:
        base_mods.update(mods_update)
        update["side_modifiers"] = base_mods
    return scenario.model_copy(update=update), report


def summary(forces: dict) -> dict:
    """面向前端状态卡与方案槽位的双方实力摘要。"""
    out: dict = {"configured": has_content(forces), "sides": {}}
    for side in _SIDES:
        s = forces.get(side) or {}
        eq = s.get("equipment") or {}
        air = s.get("air") or {}
        out["sides"][side] = {
            "ground_total": sum(int(v) for v in eq.values()),
            "air_total": sum(int(v) for v in air.values()),
            "pk_scale": float((s.get("modifiers") or {}).get("pk_scale", 1.0)),
            "detect_scale": float((s.get("modifiers") or {}).get("detect_scale", 1.0)),
            "speed_scale": float((s.get("modifiers") or {}).get("speed_scale", 1.0)),
            "equipment": dict(eq), "air": dict(air),
        }
    return out
