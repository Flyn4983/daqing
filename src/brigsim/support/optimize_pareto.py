"""成本约束下的 Pareto 方案选优（只读派生层，不改内核/权威数字）。

对应 P2-5 的叙事闭环：录入的三维成本权重（战损/时效/成本）过去只填进方案文字，
没有真正用于"给定成本约束→反推最优保障参数"。本模块把权重落到pareto.json 的
74 个已寻优方案上做可解释的加权评分与硬约束筛选，让这条反问题闭环在界面上可演示。

铁律（与项目红线一致）：
- **只读** pareto.json，绝不写回；不重跑仿真/优化，不触碰 experiment/surrogate。
- 评分维度全部取 pareto 方案已有的归一化指标，min-max 归一到 [0,1]，越优分越高。
- 权重由 mission_input 归一到 1；cost_ceiling 与方案 cost 同量纲（0-1 归一化成本），
  **budget_limit（万元）因量纲不同不直接参与筛选**，仅在结论中如实提示需人工换算。
- 结论是"在既有 Pareto 前沿内按偏好排序"，不是新的权威结论，不改 74/knee/0.165 等数字。

评分公式（三维，均为"越高越好"的归一分）：
    s_战损 = (max(attrition_red) - a) / (max - min)        战损越低越好
    s_时效 = (r - min(readiness_red)) / (max - min)         完好率越高越好
    s_成本 = (max(cost) - c) / (max - min)                  成本越低越好
    score  = w_战损*s_战损 + w_时效*s_时效 + w_成本*s_成本
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
_DATA = _REPO / "src" / "brigsim" / "agent" / "web" / "data"

# 评分维度 → (pareto 字段, 方向)。direction='min' 表示越小越好。
DIMENSIONS: dict[str, dict] = {
    "attrition_weight": {"field": "attrition_red", "direction": "min",
                         "label": "战损", "better": "红方战损率越低越好"},
    "time_weight": {"field": "readiness_red", "direction": "max",
                    "label": "时效", "better": "红方完好率越高越好（抢修/补给时效的综合体现）"},
    "cost_weight": {"field": "cost", "direction": "min",
                    "label": "成本", "better": "归一化保障成本越低越好"},
}

# 用于对照的锚点方案标签（来自 pareto.tags）
_ANCHORS = {"leanest": "资源最省", "knee": "折中推荐(权威knee)", "strongest": "保障最强"}


def _pareto_path(scenario: str | None = None) -> Path:
    """读全局 pareto.json（74方案权威）；scenario 指定时优先读该工况同名文件。"""
    if scenario:
        p = _DATA / "scenarios" / scenario / "pareto.json"
        if p.exists():
            return p
    return _DATA / "pareto.json"


def _minmax(values: list[float]) -> tuple[float, float]:
    return (min(values), max(values)) if values else (0.0, 1.0)


def _norm(v: float, lo: float, hi: float, direction: str) -> float:
    if hi - lo < 1e-12:
        return 1.0
    x = (v - lo) / (hi - lo)
    return 1.0 - x if direction == "min" else x


def recommend(weights: dict[str, float] | None = None, *, cost_ceiling: float | None = None,
              scenario: str | None = None, top_n: int = 5) -> dict:
    """按三维权重对 74 方案评分排序，返回 Top-N 与锚点对照。

    Args:
        weights: attrition_weight/time_weight/cost_weight（未归一会在此归一；缺省等权）。
        cost_ceiling: 归一化成本上限 [0,1]，与方案 cost 同量纲；None/<=0 表示不设限。
        scenario: 工况名，指定时读该工况 pareto.json，否则读全局权威 pareto.json。
        top_n: 返回前 N 个方案。

    Returns:
        dict：ranked(Top-N，含 score/三维分/7维决策/9指标)、anchors(锚点对照)、
              n_candidates/n_filtered/n_plans、effective_weights、cost_ceiling、
              formula 说明、budget_limit 量纲提示、source(读自哪个文件)。
    """
    w = dict(weights or {})
    raw = {k: max(0.0, float(w.get(k, 1.0 / 3))) for k in DIMENSIONS}
    wsum = sum(raw.values())
    eff = {k: (round(v / wsum, 4) if wsum > 0 else round(1.0 / 3, 4)) for k, v in raw.items()}

    path = _pareto_path(scenario)
    doc = json.loads(path.read_text(encoding="utf-8"))
    plans = doc.get("plans") or []
    if not plans:
        return {"error": "pareto 方案集为空", "source": str(path.relative_to(_REPO))}

    # 各维度 min-max 区间（基于全部候选，过滤前计算，保证分数可比）
    bounds: dict[str, tuple[float, float]] = {}
    for key, meta in DIMENSIONS.items():
        vals = [float(p.get(meta["field"], 0)) for p in plans]
        bounds[key] = _minmax(vals)

    # 成本硬约束（同量纲 0-1）
    ce = None
    if cost_ceiling is not None:
        try:
            ce = float(cost_ceiling)
        except (TypeError, ValueError):
            ce = None
        if ce is not None and (ce <= 0 or ce > 1):
            ce = None  # 0=不设限；>1 非法 → 忽略

    candidates = [p for p in plans if ce is None or float(p.get("cost", 1)) <= ce + 1e-12]

    scored: list[dict] = []
    for p in candidates:
        parts = {}
        for key, meta in DIMENSIONS.items():
            v = float(p.get(meta["field"], 0))
            lo, hi = bounds[key]
            parts[key] = round(_norm(v, lo, hi, meta["direction"]), 4)
        score = sum(eff[k] * parts[k] for k in DIMENSIONS)
        row = dict(p)
        row["_score"] = round(score, 4)
        row["_scores"] = parts
        scored.append(row)

    scored.sort(key=lambda r: (-r["_score"], float(r.get("cost", 1))))
    top = scored[:max(1, min(int(top_n), len(scored)))]

    # 锚点对照：从原始 plans 中按 rank_tag/tags 找到 knee/baseline 等，计算其在该权重下的得分
    tags = doc.get("tags") or {}
    anchors: dict[str, dict] = {}
    for tag, label in _ANCHORS.items():
        idx = tags.get(tag)
        if idx is None or not (0 <= int(idx) < len(plans)):
            continue
        p = plans[int(idx)]
        parts = {}
        for key, meta in DIMENSIONS.items():
            lo, hi = bounds[key]
            parts[key] = round(_norm(float(p.get(meta["field"], 0)), lo, hi, meta["direction"]), 4)
        anchors[tag] = {
            "label": label,
            "index": int(idx),
            "score": round(sum(eff[k] * parts[k] for k in DIMENSIONS), 4),
            "cost": p.get("cost"),
            "attrition_red": p.get("attrition_red"),
            "readiness_red": p.get("readiness_red"),
        }
    # 基线对照
    base = doc.get("baseline") or {}
    if base:
        parts = {}
        for key, meta in DIMENSIONS.items():
            lo, hi = bounds[key]
            parts[key] = round(_norm(float(base.get(meta["field"], 0)), lo, hi, meta["direction"]), 4)
        anchors["baseline"] = {
            "label": "现行基线", "index": None,
            "score": round(sum(eff[k] * parts[k] for k in DIMENSIONS), 4),
            "cost": base.get("cost"), "attrition_red": base.get("attrition_red"),
            "readiness_red": base.get("readiness_red"),
        }

    return {
        "ranked": top,
        "anchors": anchors,
        "n_plans": len(plans),
        "n_candidates": len(candidates),
        "n_filtered": len(plans) - len(candidates),
        "effective_weights": eff,
        "cost_ceiling": ce,
        "dimension_meta": DIMENSIONS,
        "formula": "score = Σ w_i · s_i；三维 s 均为 min-max 归一到[0,1]的'越优越高'分（战损/成本反向、时效正向）",
        "budget_limit_note": "budget_limit(万元)与方案归一化成本(0-1)量纲不同，不直接参与筛选；如需按金额约束须先人工换算为归一化成本上限(cost_ceiling)",
        "source": str(path.relative_to(_REPO)).replace("\\", "/"),
        "note": "结论为既有 Pareto 前沿(74方案)内按录入偏好的加权排序，非新权威结论；不改任何权威数字，不重跑仿真。",
    }
