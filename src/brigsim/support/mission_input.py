"""任务资源录入模型 —— 图形界面录入内容的唯一输入契约。

落盘 `outputs/support/mission_<id>.json`；通过 `to_run_config()` 映射为
现有 `/api/run-config` 的参数（7 旋钮 + 任务参数），从而复用现有推演链路，
**不需要修改仿真内核**。油料/日常业务/通用资源只用于方案层，不进内核。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .catalog import CATALOG
from . import force_config

_STORE = Path(__file__).resolve().parents[3] / "outputs" / "support" / "missions"
_STORE.mkdir(parents=True, exist_ok=True)

_EQUIP_KEYS = {e["key"] for e in CATALOG["equipment"]}
_AMMO_CLASSES = {a["class"] for a in CATALOG["ammo"]}
_FUEL_GRADES = {f["grade"] for f in CATALOG["fuel"]}
_SV_KEYS = {v["key"] for v in CATALOG["support_vehicles"]}
_RES_KEYS = {r["key"] for r in CATALOG["resources"]}
_ECHELONS = {"carry", "operating", "depot"}
_MISSION_TYPES = set(CATALOG["mission_types"])
_SCENARIOS = set(CATALOG["scenarios"])
_DAILY_KEYS = {d["key"] for d in CATALOG["daily_ops"]}


def _as_int(v: Any, name: str, lo: int = 0, hi: int = 10_000_000) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是整数，收到 {v!r}")
    if not (lo <= n <= hi):
        raise ValueError(f"{name} 必须在 [{lo},{hi}] 之间，收到 {n}")
    return n


def validate(payload: dict) -> tuple[dict, list[str]]:
    """校验并返回 (规范化后的输入, 警告列表)。抛 ValueError 表示致命错误。"""
    if not isinstance(payload, dict):
        raise ValueError("输入必须是 JSON 对象")
    p = dict(payload)
    p["task_id"] = str(p.get("task_id") or f"TASK-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}")
    p["task_name"] = str(p.get("task_name") or "合成旅装备保障任务")
    mt = str(p.get("mission_type", "进攻"))
    if mt not in _MISSION_TYPES:
        raise ValueError(f"mission_type 必须是 {sorted(_MISSION_TYPES)}")
    p["mission_type"] = mt

    p["base_scenario"] = str(p.get("base_scenario") or "medium_brigade")
    if p["base_scenario"] not in _SCENARIOS:
        raise ValueError(f"base_scenario 必须是 {sorted(_SCENARIOS)}")
    p["duration_ticks"] = _as_int(p.get("duration_ticks", 96), "duration_ticks", 10, 400)
    p["n_runs"] = _as_int(p.get("n_runs", 12), "n_runs", 1, 60)
    p["seed"] = _as_int(p.get("seed", 20261013), "seed", 0, 2**31 - 1)

    warnings: list[str] = []

    # 装备编成
    eq = p.get("equipment") or []
    if not isinstance(eq, list) or not eq:
        raise ValueError("equipment 至少要录入 1 类装备")
    norm_eq = []
    for i, e in enumerate(eq):
        if not isinstance(e, dict):
            raise ValueError(f"equipment[{i}] 必须是对象")
        key = str(e.get("key", ""))
        if key not in _EQUIP_KEYS:
            raise ValueError(f"equipment[{i}].key 未知：{key}")
        norm_eq.append({
            "key": key,
            "name": e.get("name") or next((x["name"] for x in CATALOG["equipment"] if x["key"] == key), key),
            "category": e.get("category") or next((x["category"] for x in CATALOG["equipment"] if x["key"] == key), "assault"),
            "count": _as_int(e.get("count", 0), f"equipment[{i}].count", 0),
            "org": str(e.get("org") or "旅直属"),
            "initial_worn": _as_int(e.get("initial_worn", 0), f"equipment[{i}].initial_worn", 0),
            "ammo_basic_load": _as_int(e.get("ammo_basic_load", 0), f"equipment[{i}].ammo_basic_load", 0),
        })
    if sum(e["count"] for e in norm_eq) == 0:
        raise ValueError("equipment 总数量必须 > 0")
    p["equipment"] = norm_eq

    # 弹药
    norm_ammo = []
    for i, a in enumerate(p.get("ammo") or []):
        cls = str(a.get("class", ""))
        if cls not in _AMMO_CLASSES:
            raise ValueError(f"ammo[{i}].class 未知：{cls}")
        norm_ammo.append({
            "class": cls, "name": a.get("name") or cls,
            "basic_load": _as_int(a.get("basic_load", 0), f"ammo[{i}].basic_load", 0),
            "reserve": _as_int(a.get("reserve", 0), f"ammo[{i}].reserve", 0),
            "unit": str(a.get("unit") or "发"),
        })
    p["ammo"] = norm_ammo

    # 油料（方案层）
    norm_fuel = []
    for i, f in enumerate(p.get("fuel") or []):
        grade = str(f.get("grade", ""))
        if grade not in _FUEL_GRADES:
            raise ValueError(f"fuel[{i}].grade 未知：{grade}")
        norm_fuel.append({
            "grade": grade,
            "stock_l": _as_int(f.get("stock_l", 0), f"fuel[{i}].stock_l", 0),
            "per_vehicle_load_l": _as_int(f.get("per_vehicle_load_l", 0), f"fuel[{i}].per_vehicle_load_l", 0),
            "resupply_l": _as_int(f.get("resupply_l", 0), f"fuel[{i}].resupply_l", 0),
            "unit": str(f.get("unit") or "升"),
        })
    p["fuel"] = norm_fuel
    if not norm_fuel:
        warnings.append("未录入油料：方案中的油料保障章节将以占位符显示")

    # 保障车辆
    norm_sv = []
    for i, v in enumerate(p.get("support_vehicles") or []):
        key = str(v.get("key", ""))
        if key not in _SV_KEYS:
            raise ValueError(f"support_vehicles[{i}].key 未知：{key}")
        norm_sv.append({
            "key": key, "name": v.get("name") or key,
            "count": _as_int(v.get("count", 0), f"support_vehicles[{i}].count", 0),
            "org": str(v.get("org") or "保障群"),
        })
    p["support_vehicles"] = norm_sv

    # 其他资源（三级器材 + 通用消耗品）
    norm_res = []
    for i, r in enumerate(p.get("resources") or []):
        key = str(r.get("key", ""))
        if key not in _RES_KEYS:
            raise ValueError(f"resources[{i}].key 未知：{key}")
        qty = r.get("qty") or {}
        if not isinstance(qty, dict):
            raise ValueError(f"resources[{i}].qty 必须是对象")
        norm_res.append({
            "key": key, "name": r.get("name") or key,
            "qty": {e: _as_int(qty.get(e, 0), f"resources[{i}].qty.{e}", 0) for e in _ECHELONS},
        })
    p["resources"] = norm_res

    # 日常业务要素
    daily = p.get("daily_ops") or {}
    if not isinstance(daily, dict):
        raise ValueError("daily_ops 必须是对象")
    for k in daily:
        if k not in _DAILY_KEYS:
            warnings.append(f"日常业务要素 {k} 不在标准目录中，已保留原文")
    p["daily_ops"] = {k: str(v) for k, v in daily.items()}

    # 对战双方实力配置（红蓝对称；缺省=零漂移）
    forces, fwarn = force_config.validate(p.get("forces"))
    p["forces"] = forces
    warnings.extend(fwarn)

    # P0-6：保障目标阈值（方案 §二 保障任务与决心；不进内核，仅方案层）
    targets = p.get("targets") or {}
    if not isinstance(targets, dict):
        raise ValueError("targets 必须是对象")
    def pct_or(v, default: str) -> str:
        s = str(v if v not in (None, "") else default).strip()
        if not s.endswith("%"):
            s += "%"
        return s
    p["targets"] = {
        "readiness": pct_or(targets.get("readiness"), "90"),
        "success": pct_or(targets.get("success"), "85"),
        "repair_timeliness": pct_or(targets.get("repair_timeliness"), "80"),
    }

    # P2-5：成本权重与上限（仅方案层/对比展示，不映射到内核 7 旋钮）
    from .catalog import COST_WEIGHT_SPEC
    cw = p.get("cost_weights") or {}
    if not isinstance(cw, dict):
        raise ValueError("cost_weights 必须是对象")
    norm_cw: dict[str, float | int] = {}
    for spec in COST_WEIGHT_SPEC:
        key = spec["key"]
        raw = cw.get(key, spec["default"])
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"cost_weights.{key} 必须是数值，收到 {raw!r}")
        val = max(float(spec["min"]), min(float(spec["max"]), val))
        norm_cw[key] = int(val) if spec["max"] >= 1_000_000 else round(val, 3)
    # 三项相对权重之和归一到 1（仅展示用，避免权重乱填导致评估失真）
    wsum = norm_cw["attrition_weight"] + norm_cw["time_weight"] + norm_cw["cost_weight"]
    if wsum > 0:
        for key in ("attrition_weight", "time_weight", "cost_weight"):
            norm_cw[key] = round(norm_cw[key] / wsum, 3)
    p["cost_weights"] = norm_cw

    # 控制参数
    p["objective"] = [
        float((p.get("objective") or [9000, 5000])[0]),
        float((p.get("objective") or [9000, 5000])[1]),
    ]
    p["success_radius_m"] = _as_int(p.get("success_radius_m", 600), "success_radius_m", 50, 5000)
    return p, warnings


def _knob_value(sv: list[dict], key: str, default: int) -> int:
    for v in sv:
        if v["key"] == key:
            return v["count"]
    return default


def _level_from_qty(res: list[dict], key: str, hi: int) -> int:
    """把器材数量映射为档级 0..hi（0=精简，hi=充裕）。"""
    for r in res:
        if r["key"] == key:
            total = sum(r["qty"].values())
            return max(0, min(hi, total // 8))
    return 2 if hi == 4 else 2


def to_run_config(p: dict) -> dict:
    """把录入内容映射为现有 /api/run-config 的参数（复用现有推演，不改内核）。"""
    sv = p.get("support_vehicles") or []
    res = p.get("resources") or []
    repair = _knob_value(sv, "repair_veh", 6)
    arv = _knob_value(sv, "arv", 3)
    supply = _knob_value(sv, "supply_veh", 2)
    carry = _level_from_qty(res, "mobility", 4)
    operating = _level_from_qty(res, "powerpack", 3)
    wave_qty = 2
    for r in res:
        if r["key"] == "weapon" and r["qty"].get("depot", 0):
            wave_qty = max(1, min(4, r["qty"]["depot"] // 10 or 2))
    return {
        "scenario": p["base_scenario"],
        "seed": p["seed"],
        "max_ticks": p["duration_ticks"],
        "duration_ticks": p["duration_ticks"],
        "objective_x": p["objective"][0],
        "objective_y": p["objective"][1],
        "success_radius_m": p["success_radius_m"],
        "red_repair_teams": max(2, min(10, repair or 6)),
        "red_arv": max(2, min(6, arv or 3)),
        "red_supply_veh": max(1, min(3, supply or 2)),
        "carry_level": carry,
        "operating_level": operating,
        "wave_interval_ticks": 20,
        "wave_qty": wave_qty,
        "mtbf_scale": 1.0,
        "red_pk_scale": 1.0,
        "blue_pk_scale": 1.0,
    }


def save(p: dict) -> str:
    _STORE.mkdir(parents=True, exist_ok=True)
    mid = p.get("mission_id") or uuid.uuid4().hex[:10]
    p["mission_id"] = mid
    p["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (_STORE / f"mission_{mid}.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return mid


def load(mid: str) -> dict | None:
    f = _STORE / f"mission_{mid}.json"
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def latest() -> dict | None:
    files = sorted(_STORE.glob("mission_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    return json.loads(files[0].read_text(encoding="utf-8")) if files else None


def summary(p: dict) -> dict:
    """输入摘要（前端第 1 节点状态卡 + 方案 roster 槽位）。"""
    by_cat: dict[str, int] = {}
    for e in p.get("equipment") or []:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + int(e["count"])
    cat_names = {
        "assault": "突击", "artillery": "炮兵", "missile": "导弹", "air_defense": "防空",
        "radar_air": "对空雷达", "radar_cb": "反炮兵雷达", "recon": "侦察",
        "ew": "电抗", "c2": "指挥", "engineer": "工兵", "support": "保障",
    }
    total = sum(by_cat.values())
    roster_by_category = "、".join(f"{cat_names.get(k, k)}{v}" for k, v in by_cat.items())
    fuel = p.get("fuel") or []
    fs = force_config.summary(p.get("forces") or {})
    red_s = fs["sides"]["red"]
    blue_s = fs["sides"]["blue"]
    return {
        "forces_configured": fs["configured"],
        "red_total": red_s["ground_total"] or total,
        "blue_total": blue_s["ground_total"],
        "red_air": red_s["air_total"],
        "blue_air": blue_s["air_total"],
        "red_pk_scale": red_s["pk_scale"],
        "blue_pk_scale": blue_s["pk_scale"],
        "force_balance": (f"红{red_s['ground_total'] or '—'}:蓝{blue_s['ground_total'] or '—'}"
                          if fs["configured"] else "按基础想定"),
        "task_id": p["task_id"], "task_name": p["task_name"],
        "roster_total": total, "roster_by_category": roster_by_category,
        "readiness_level": (p.get("daily_ops") or {}).get("readiness_level", "二级战备"),
        "duty_roster": (p.get("daily_ops") or {}).get("duty_roster", ""),
        "training_plan": (p.get("daily_ops") or {}).get("training_plan", ""),
        "supply_request_approval": (p.get("daily_ops") or {}).get("supply_request_approval", ""),
        "maintenance_plan": (p.get("daily_ops") or {}).get("maintenance_plan", ""),
        "fuel_consumption_l": sum(f["stock_l"] for f in fuel),
        "fuel_stock_l": sum(f["stock_l"] for f in fuel),
        "fuel_resupply_l": sum(f["resupply_l"] for f in fuel),
        "tanker_count": _knob_value(p.get("support_vehicles") or [], "tanker", 0),
        "carry_stock": sum(r["qty"]["carry"] for r in p.get("resources") or []),
        "operating_stock": sum(r["qty"]["operating"] for r in p.get("resources") or []),
        "depot_stock": sum(r["qty"]["depot"] for r in p.get("resources") or []),
        "ammo_by_class": "、".join(f"{a['name']}基数{a['basic_load']}储备{a['reserve']}{a['unit']}"
                                for a in (p.get("ammo") or [])) or "未录入",
        "ammo_wave_qty": sum(a["basic_load"] for a in (p.get("ammo") or [])),
        "wave_interval_min": 20 * 0.5,
        "wave_qty": 2,
        "dist_radius_m": 3000,
        "repair_teams": _knob_value(p.get("support_vehicles") or [], "repair_veh", 6),
        "arv_count": _knob_value(p.get("support_vehicles") or [], "arv", 3),
        "supply_veh": _knob_value(p.get("support_vehicles") or [], "supply_veh", 2),
    }
