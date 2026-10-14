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
    device: str | None = "cuda:0",
    batch_size: int = 512,
) -> pd.DataFrame:
    """对 plans 的每行用 reps 次蒙特卡洛仿真复验，返回对比长表。

    列：plan（rank_tag 或方案序号）、metric、surrogate（代理预测）、
    simulation（仿真均值）、abs_error、sim_std（跨复制标准差）。
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
