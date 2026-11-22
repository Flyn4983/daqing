"""真实业务约束录入层 —— 把报告"三源约束"变成 GUI 可设定、可校核的输入。

背景
----
报告《创新点重构_模型公式数据.md》§创新点③把可行域写成三源约束：
  - 战损/物理：不可修不分配、超本级能力须后送
  - 指挥权威：上级时限 t_i ≤ T_dead、重点装备 a_i(T) ≥ a_min；期望四率为软约束
  - 资源现实：人力/备件/吞吐 ≤ 可用量；成本/风险预算为软约束

此前软件只把三源约束做成"展示性映射"（research.js 自述：软件将资源/编制来源落到
成本权重与档位边界），GUI 无法录入修理车上限、人员上限、人员修理能力、抢修时限、
上级命令等真实约束。本模块补齐这一层。

红线（用户 2026-11-22 确认）
---------------------------
- 约束只在**方案层与选优层**生效：录入 → 校验 → 可行性校核 → Pareto 硬筛选 →
  方案槽位与 docx。
- **不注入 NSGA-II 寻优内核**：不改 FEATURES/代理/74 方案/knee/四率权威数字。
- 硬约束违反 → 阻断保存与方案生成；软约束违反 → 告警并写入方案提示，不阻断。
- 本模块只做纯函数与本地 JSON 落盘，不依赖仿真内核，ARM 上毫秒级。
"""

from __future__ import annotations

import time
from typing import Any

# ---- 约束规格：前端表单与后端校验的单一数据源 ----
# source: resource=资源现实源 / command=指挥权威源 / physical=战损物理源 / soft=软约束
# type: int / float / enum / text
# knob: 该约束在 Pareto 选优中收窄的 7 维旋钮（None=不收窄旋钮）
CONSTRAINT_SPEC: list[dict] = [
    # ---- 资源现实源（硬）：上级可提供的资源上限 ----
    {"key": "repair_veh_cap", "source": "resource", "hard": True, "type": "int",
     "name": "装甲抢修车可用上限", "unit": "台", "default": 8, "min": 1, "max": 30,
     "knob": "red_repair_teams",
     "hint": "上级实际可配属的抢修车总数；录入的抢修车数不得超过此上限"},
    {"key": "arv_cap", "source": "resource", "hard": True, "type": "int",
     "name": "装甲抢救车可用上限", "unit": "台", "default": 6, "min": 1, "max": 20,
     "knob": "red_arv",
     "hint": "上级实际可配属的抢救车总数；录入数不得超过此上限"},
    {"key": "supply_veh_cap", "source": "resource", "hard": True, "type": "int",
     "name": "器材补给车可用上限", "unit": "台", "default": 4, "min": 1, "max": 12,
     "knob": "red_supply_veh",
     "hint": "上级实际可配属的器材补给车总数；录入数不得超过此上限"},
    {"key": "personnel_cap", "source": "resource", "hard": True, "type": "int",
     "name": "维修人员上限", "unit": "人", "default": 120, "min": 1, "max": 2000,
     "knob": None,
     "hint": "在编维修人员总数；每抢修组按 personnel_per_team 折算占用"},
    {"key": "personnel_per_team", "source": "resource", "hard": True, "type": "int",
     "name": "单组修理人员编制", "unit": "人/组", "default": 8, "min": 1, "max": 40,
     "knob": None,
     "hint": "一个抢修组需占用的人员数，用于校核人员上限"},
    {"key": "repair_rate_per_team", "source": "resource", "hard": True, "type": "float",
     "name": "单组修理能力", "unit": "台/小时", "default": 3.0, "min": 0.1, "max": 20.0,
     "knob": None,
     "hint": "一个抢修组每小时可完成的修复台数，用于抢修时限可行性校核"},

    # ---- 指挥权威源（硬）：上级命令给出的时限与规则 ----
    {"key": "repair_deadline_h", "source": "command", "hard": True, "type": "float",
     "name": "抢修时限", "unit": "小时", "default": 8.0, "min": 0.5, "max": 72.0,
     "knob": None,
     "hint": "上级要求的可修战损全部修复完成的时限；0.5~72 小时"},
    {"key": "evac_deadline_h", "source": "command", "hard": True, "type": "float",
     "name": "后送时限", "unit": "小时", "default": 12.0, "min": 1.0, "max": 120.0,
     "knob": None,
     "hint": "上级要求的后送到位时限；此值直接填入方案后送章节（非 LLM 编造）"},
    {"key": "key_equipment_min_ready", "source": "command", "hard": True, "type": "float",
     "name": "重点装备完好率下限", "unit": "%", "default": 0, "min": 0, "max": 100,
     "knob": None,
     "hint": "0=不设限；设置后重点装备完好率低于该值即判定不可行"},
    {"key": "superior_order", "source": "command", "hard": True, "type": "text",
     "name": "上级命令（原文/要点）", "unit": "", "default": "", "max_len": 2000,
     "knob": None,
     "hint": "上级赋予的保障任务、时限与禁止事项，原文录入；LLM 不得改写数字"},
    {"key": "priority_rule", "source": "command", "hard": True, "type": "text",
     "name": "抢修优先规则", "unit": "", "default": "", "max_len": 1000,
     "knob": None,
     "hint": "如'先主战装备后保障装备、先机动受损后火力受损'等，原文录入"},

    # ---- 战损物理源（硬）：不可修判定规则 ----
    {"key": "max_repair_grade", "source": "physical", "hard": True, "type": "enum",
     "name": "本级最高可修等级", "unit": "", "default": "F", "options": ["M", "F", "K"],
     "knob": None,
     "hint": "M=仅机动受损 / F=机动+火力受损 / K=含战毁（K 通常不可修，选 K 需说明依据）"},

    # ---- 软约束：违反仅告警 ----
    {"key": "risk_budget", "source": "soft", "hard": False, "type": "float",
     "name": "风险预算上限", "unit": "0-1", "default": 1.0, "min": 0.0, "max": 1.0,
     "knob": None,
     "hint": "1.0=不设限；越低表示越厌恶风险，仅作方案提示不阻断"},
]

SPEC_BY_KEY: dict[str, dict] = {c["key"]: c for c in CONSTRAINT_SPEC}
SOURCE_NAMES: dict[str, str] = {
    "resource": "资源现实源", "command": "指挥权威源",
    "physical": "战损物理源", "soft": "软约束",
}


def defaults() -> dict[str, Any]:
    """返回全部约束的默认字典（缺省零漂移：默认不阻断任何既有流程）。"""
    return {c["key"]: c["default"] for c in CONSTRAINT_SPEC}


def validate(payload: Any) -> tuple[dict[str, Any], list[str]]:
    """规范化并校验约束录入。

    硬约束取值范围非法 → 抛 ValueError（阻断保存）；软约束越界 → 钳制并告警。
    返回 (规范化约束, 告警列表)。
    """
    raw = payload if isinstance(payload, dict) else {}
    norm: dict[str, Any] = {}
    warnings: list[str] = []
    for c in CONSTRAINT_SPEC:
        key = c["key"]
        val = raw.get(key, c["default"])
        ctype = c["type"]
        try:
            if ctype == "int":
                v = int(val)
                if not (c["min"] <= v <= c["max"]):
                    raise ValueError(f"{c['name']}必须在[{c['min']},{c['max']}]，收到 {v}")
            elif ctype == "float":
                v = float(val)
                if not (c["min"] <= v <= c["max"]):
                    raise ValueError(f"{c['name']}必须在[{c['min']},{c['max']}]，收到 {v}")
                v = round(v, 3)
            elif ctype == "enum":
                v = str(val)
                if v not in c["options"]:
                    raise ValueError(f"{c['name']}必须是 {c['options']} 之一，收到 {v}")
            elif ctype == "text":
                v = str(val or "").strip()
                if len(v) > c.get("max_len", 2000):
                    v = v[: c.get("max_len", 2000)]
                    warnings.append(f"{c['name']}超过长度上限已截断")
            else:
                v = val
        except (TypeError, ValueError) as exc:
            if c["hard"]:
                raise ValueError(f"硬约束录入错误：{exc}") from exc
            warnings.append(f"软约束 {c['name']} 录入非法，已用默认值：{exc}")
            v = c["default"]
        norm[key] = v

    # 逻辑一致性（硬）：每组人员不能超过总人员上限；单组能力须为正
    if norm["personnel_per_team"] > norm["personnel_cap"]:
        raise ValueError("单组修理人员编制不得超过维修人员上限")
    return norm, warnings


def effective_bounds(constraints: dict[str, Any] | None) -> dict[str, int]:
    """把资源上限派生为 7 旋钮的有效上界（供 Pareto 硬筛选，不写回内核）。

    仅收窄上限，不抬升；FEATURES 的下界/其余旋钮保持权威定义。
    """
    from ..surrogate.design import FEATURES

    if not isinstance(constraints, dict):
        return {}
    base = {name: hi for name, _lo, hi, _is_int in FEATURES}
    out: dict[str, int] = {}
    for c in CONSTRAINT_SPEC:
        knob = c.get("knob")
        key = c["key"]
        if not knob or key not in constraints:
            continue
        try:
            cap = int(constraints[key])
        except (TypeError, ValueError):
            continue
        out[knob] = max(0, min(int(base.get(knob, cap)), cap))
    return out


def check_feasibility(
    mission: dict[str, Any],
    constraints: dict[str, Any] | None,
    *,
    loss: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """逐条校核录入资源/时限是否满足约束。

    Args:
        mission: 任务录入（support_vehicles / daily_ops / target 等）
        constraints: 约束录入；None 表示未录入 → 用默认值（不阻断）
        loss: 可选战损清单，用于抢修时限与重点装备完好率校核

    Returns:
        {overall, items:[{key,name,source,hard,status,actual,limit,unit,message}],
         n_fail, n_warn, blocking:[key...]}
    """
    c = {**defaults(), **(constraints or {})}
    mission = mission or {}
    sv = {v.get("key"): int(v.get("count", 0) or 0)
          for v in (mission.get("support_vehicles") or []) if isinstance(v, dict)}
    items: list[dict[str, Any]] = []

    def add(key: str, status: str, actual: Any, limit: Any, message: str) -> None:
        spec = SPEC_BY_KEY[key]
        items.append({
            "key": key, "name": spec["name"], "source": spec["source"],
            "source_name": SOURCE_NAMES[spec["source"]], "hard": spec["hard"],
            "status": status, "actual": actual, "limit": limit,
            "unit": spec["unit"], "message": message,
        })

    # ---- 资源现实源：录入车辆数 vs 上级可用上限 ----
    for key, sv_key in (("repair_veh_cap", "repair_veh"),
                        ("arv_cap", "arv"), ("supply_veh_cap", "supply_veh")):
        actual = sv.get(sv_key, 0)
        cap = int(c[key])
        ok = actual <= cap
        add(key, "pass" if ok else "fail", actual, cap,
            f"录入 {actual} 台 ≤ 可用上限 {cap} 台" if ok
            else f"录入 {actual} 台超过可用上限 {cap} 台，请压减或申请上级增配")

    # 人员上限：抢修组数 × 单组人员 ≤ 总人员
    teams = sv.get("repair_veh", 0)
    need_p = teams * int(c["personnel_per_team"])
    ok = need_p <= int(c["personnel_cap"])
    add("personnel_cap", "pass" if ok else "fail", need_p, int(c["personnel_cap"]),
        f"{teams} 组 × {c['personnel_per_team']} 人/组 = {need_p} 人 ≤ 上限 {c['personnel_cap']} 人" if ok
        else f"抢修需 {need_p} 人，超过在编上限 {c['personnel_cap']} 人，请压减抢修组或增员")

    # 抢修时限：可修需求 vs (组数 × 单组能力 × 时限)。有清单才算，无清单记为待校核
    summary = (loss or {}).get("summary") or {}
    by_grade = summary.get("by_grade") or {}
    repairable = int(by_grade.get("M", 0)) + int(by_grade.get("F", 0))
    if loss and summary:
        capacity = teams * float(c["repair_rate_per_team"]) * float(c["repair_deadline_h"])
        ok = capacity + 1e-9 >= repairable
        add("repair_deadline_h", "pass" if ok else "fail",
            round(capacity, 2), repairable,
            f"时限内可修 {capacity:.1f} 台 ≥ 待修 {repairable} 台" if ok
            else f"时限内最多修 {capacity:.1f} 台 < 待修 {repairable} 台，需增组/提能力/放宽时限或组织后送")
    else:
        add("repair_deadline_h", "pass", "待推演后有清单校核", c["repair_deadline_h"],
            "尚无战损清单，抢修时限待推演后校核（不阻断录入）")

    # 上级时限类文本：录入即视为已接受（非 LLM 生成）
    for key in ("evac_deadline_h", "superior_order", "priority_rule", "max_repair_grade"):
        spec = SPEC_BY_KEY[key]
        val = c[key]
        filled = bool(str(val).strip()) if spec["type"] == "text" else True
        add(key, "pass" if filled else "warn", val if filled else "未录入", "—",
            f"已录入{spec['name']}" if filled else f"{spec['name']}未录入，方案将以占位符显示")

    # 重点装备完好率下限：有清单才有实测值
    m_min = float(c["key_equipment_min_ready"])
    if m_min > 0 and loss:
        rates = loss.get("rates") or {}
        actual = float(rates.get("readiness", 0)) * 100
        ok = actual >= m_min
        add("key_equipment_min_ready", "pass" if ok else "fail", round(actual, 2), m_min,
            f"完好率 {actual:.1f}% ≥ 下限 {m_min:.1f}%" if ok
            else f"完好率 {actual:.1f}% < 上级下限 {m_min:.1f}%，请调整保障方案")
    else:
        add("key_equipment_min_ready", "pass", m_min if m_min else "未设限", m_min,
            "未设下限" if not m_min else "无清单数据，完好率下限待推演后校核")

    # 软约束：风险预算（仅告警）
    rb = float(c["risk_budget"])
    add("risk_budget", "warn" if rb < 1.0 else "pass", rb, 1.0,
        f"风险预算 {rb:.2f}（软约束，仅写入方案提示）" if rb < 1.0 else "未设风险限制")

    n_fail = sum(1 for x in items if x["status"] == "fail")
    n_warn = sum(1 for x in items if x["status"] == "warn")
    return {
        "overall": "fail" if n_fail else ("warn" if n_warn else "pass"),
        "items": items,
        "n_fail": n_fail, "n_warn": n_warn,
        "blocking": [x["key"] for x in items if x["status"] == "fail"],
        "constraints": c,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "硬约束来自上级命令与资源现实，违反即阻断；软约束仅告警。校核不修改仿真内核与权威数字。",
    }


def constraint_slots(constraints: dict[str, Any] | None, check: dict[str, Any] | None) -> dict[str, str]:
    """派生方案模板槽位值（全部确定性，非 LLM 生成）。"""
    c = {**defaults(), **(constraints or {})}
    fails = (check or {}).get("blocking") or []
    chk = (check or {}).get("overall", "pass")
    status_cn = {"pass": "满足", "warn": "有提示", "fail": "不满足"}.get(chk, "未校核")
    return {
        "constraint_summary": (
            f"抢修车上限 {c['repair_veh_cap']} 台、抢救车上限 {c['arv_cap']} 台、"
            f"器材车上限 {c['supply_veh_cap']} 台；维修人员上限 {c['personnel_cap']} 人"
            f"（单组 {c['personnel_per_team']} 人）；单组修理能力 {c['repair_rate_per_team']} 台/小时"
        ),
        "constraint_deadline": (
            f"抢修时限 {c['repair_deadline_h']} 小时、后送时限 {c['evac_deadline_h']} 小时；"
            f"本级最高可修等级 {c['max_repair_grade']} 级"
        ),
        "constraint_order": (str(c["superior_order"]).strip() or "（上级命令未录入，待补充）"),
        "constraint_priority": (str(c["priority_rule"]).strip() or "（抢修优先规则未录入，待补充）"),
        "constraint_feasibility": (
            f"{status_cn}：硬约束 {len(fails)} 项不满足"
            + ("（" + "、".join(SPEC_BY_KEY[k]["name"] for k in fails if k in SPEC_BY_KEY) + "）" if fails else "")
            + "；软约束提示 " + str((check or {}).get("n_warn", 0)) + " 项"
        ),
    }
