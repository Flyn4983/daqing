"""Pareto 保障方案回真实仿真复验（防止优化器钻代理模型误差的空子）。

代理模型再准也有误差，多目标搜索可能系统性地偏向代理"虚高"区域。
因此对 Pareto 前沿的代表方案（knee 推荐 + 两端）用蒙特卡洛仿真重新评估，
逐指标对比代理预测与仿真均值，作为方案可交付前的可信度闸门。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.schema import Scenario
from ..surrogate.dataset import evaluate_points
from ..surrogate.design import TARGETS
from .problem import DECISION_VARS, ScenarioContext, build_feature_matrix


def validate_plans(
    scenario: Scenario,
    plans: pd.DataFrame,
    ctx: ScenarioContext | None = None,
    reps: int = 16,
    base_seed: int = 20261014,
    device: str | None = None,
    batch_size: int = 512,
    n_workers: int = 10,
) -> pd.DataFrame:
    """对 plans 的每行用 reps 次蒙特卡洛仿真复验，返回对比长表。

    列：plan（rank_tag 或方案序号）、metric、surrogate（代理预测）、
    simulation（仿真均值）、abs_error、sim_std（跨复制标准差）。
    C7-a3 起合成兵种想定默认 Mesa 多进程（device=None, n_workers=10）。
    """
    ctx = ctx or ScenarioContext.baseline()
    X = build_feature_matrix(plans[DECISION_VARS].to_numpy(float), ctx).to_numpy(float)
    means, stds, _ = evaluate_points(
        scenario,
        X,
        reps=reps,
        base_seed=base_seed,
        device=device,
        batch_size=batch_size,
        n_workers=n_workers,
    )
    rows = []
    for i, (_, plan) in enumerate(plans.iterrows()):
        tag = plan.get("rank_tag") or f"pareto_{i}"
        for j, metric in enumerate(TARGETS):
            rows.append(
                {
                    "plan": tag,
                    "metric": metric,
                    "surrogate": float(plan[metric]) if metric in plan else np.nan,
                    "simulation": float(means[i, j]),
                    "abs_error": (
                        abs(float(plan[metric]) - float(means[i, j]))
                        if metric in plan
                        else np.nan
                    ),
                    "sim_std": float(stds[i, j]),
                }
            )
    return pd.DataFrame(rows)


def representative_plans(pareto: pd.DataFrame) -> pd.DataFrame:
    """取 knee/leanest/strongest 三个代表方案（标签缺失则按成本分位补点）。"""
    tags = pareto[pareto["rank_tag"] != ""]
    if len(tags) >= 2:
        return tags.reset_index(drop=True)
    idx = [int(pareto["cost"].idxmin()), len(pareto) // 2, int(pareto["cost"].idxmax())]
    return pareto.iloc[sorted(set(idx))].reset_index(drop=True)


def sim_guarded_knee(
    scenario: Scenario,
    pareto: pd.DataFrame,
    ctx: ScenarioContext | None = None,
    *,
    reps: int = 48,
    base_seed: int = 20261014,
    device: str | None = None,
    batch_size: int = 512,
    n_workers: int = 10,
    guard_success: float = 0.65,
    surr_min: float = 0.70,
    max_candidates: int = 8,
    baseline_row: pd.Series | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """仿真守卫下重选 knee：代理筛前沿，蒙特卡洛仿真裁决。

    代理在低成本角点存在系统性虚高（优化器专钻模型误差空子），纯乌托邦距离
    选出的 knee 真实成功率可能远低于预测。做法：在代理预测 success≥surr_min
    的前沿方案中按成本铺开候选（并强制纳入原 knee/leanest/strongest），统一
    回真实仿真跑 reps 次，取**仿真成功率≥guard_success 的最低成本方案**作为
    新 knee；无一过闸则退化为仿真成功率最高者（info["passed"]=False，调用方
    应告警）。这正体现平台方法论：优化出方案、仿真来验证。

    baseline_row 可选（含 DECISION_VARS 与 TARGETS 代理值），一并复验并以
    plan="baseline" 进入长表，但不写入 Pareto 表、不参与 knee 遴选。

    返回 (重打 rank_tag 的 pareto, 复验长表, info)。
    """
    ctx = ctx or ScenarioContext.baseline()
    work = pareto.reset_index(drop=True).copy()

    def _tag_idx(tag: str) -> int | None:
        hit = work.index[work["rank_tag"] == tag].tolist()
        return hit[0] if hit else None

    knee_i, lean_i, strong_i = (
        _tag_idx("knee"), _tag_idx("leanest"), _tag_idx("strongest"),
    )

    eligible = work.index[work["success"] >= surr_min].to_numpy()
    if len(eligible) > max_candidates:
        pos = np.linspace(0, len(eligible) - 1, max_candidates).round().astype(int)
        chosen = set(eligible[np.unique(pos)].tolist())
    else:
        chosen = set(eligible.tolist())
    for i in (knee_i, lean_i, strong_i):
        if i is not None:
            chosen.add(i)
    cand_idx = sorted(chosen)

    cand = work.loc[cand_idx].copy()
    if baseline_row is not None:
        base_vals = {"rank_tag": "baseline"}
        for c in cand.columns:
            if c == "rank_tag":
                continue
            base_vals[c] = (
                float(baseline_row[c])
                if c in baseline_row.index and pd.notna(baseline_row[c])
                else np.nan
            )
        base_df = pd.DataFrame([base_vals]).reindex(columns=cand.columns)
        plans_eval = pd.concat([cand, base_df], ignore_index=True)
    else:
        plans_eval = cand.reset_index(drop=True)

    X = build_feature_matrix(
        plans_eval[DECISION_VARS].to_numpy(float), ctx
    ).to_numpy(float)
    means, stds, _ = evaluate_points(
        scenario, X, reps=reps, base_seed=base_seed,
        device=device, batch_size=batch_size, n_workers=n_workers,
    )
    j_succ = TARGETS.index("success")
    sim_success = means[:, j_succ]

    n_cand = len(cand)
    pass_mask = sim_success[:n_cand] >= guard_success
    if pass_mask.any():
        order = np.argsort(cand["cost"].to_numpy())
        new_knee_pos = order[pass_mask[order]][0]
        new_knee_i = cand_idx[int(new_knee_pos)]
        passed = True
    else:
        new_knee_i = cand_idx[int(np.argmax(sim_success[:n_cand]))]
        passed = False

    work.loc[work["rank_tag"] == "knee", "rank_tag"] = ""
    work.loc[new_knee_i, "rank_tag"] = "knee"

    tag_set = {"knee", "leanest", "strongest"}
    tagged = {
        i: t for i, t in zip(work.index, work["rank_tag"]) if t in tag_set
    }
    rows = []
    for i, (_, plan) in enumerate(plans_eval.iterrows()):
        if i < n_cand:
            orig_i = cand_idx[i]
            tag = tagged.get(orig_i, f"pareto_{orig_i}")
        else:
            tag = "baseline"
        for j, metric in enumerate(TARGETS):
            surr = float(plan[metric]) if metric in plan and pd.notna(plan[metric]) else np.nan
            rows.append(
                {
                    "plan": tag,
                    "metric": metric,
                    "surrogate": surr,
                    "simulation": float(means[i, j]),
                    "abs_error": abs(surr - float(means[i, j])) if np.isfinite(surr) else np.nan,
                    "sim_std": float(stds[i, j]),
                }
            )
    val_df = pd.DataFrame(rows)
    info = {
        "passed": passed,
        "guard_success": guard_success,
        "knee_sim_success": float(sim_success[cand_idx.index(new_knee_i)]),
        "n_candidates": n_cand,
        "reps": reps,
    }
    return work, val_df, info
