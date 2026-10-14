"""NSGA-II 求解：代理模型驱动的保障方案 Pareto 搜索 + 推荐解与基线对比。

输出一张"方案表"（每个 Pareto 非支配方案一行）：
- 4 个决策旋钮的物理值（参谋可直接执行）；
- 3 个优化目标：success / attrition_red / cost（含归一化目标，供复前沿）；
- 代理预测的全部 9 项四率+任务指标（供 C5 解读 / C6 仪表盘）；
- rank 标签：knee（最大折中推荐）与两端（最省资源/最强保障）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..surrogate.design import TARGETS
from ..surrogate.model import SurrogateModel
from .problem import (
    DECISION_VARS,
    ScenarioContext,
    baseline_decisions,
    build_feature_matrix,
    make_problem,
    map_decisions,
    resource_cost,
)

OBJECTIVES = ["success", "attrition_red", "cost"]


def optimize_support(
    model: SurrogateModel,
    duration_ticks: int,
    ctx: ScenarioContext | None = None,
    pop_size: int = 80,
    n_gen: int = 60,
    min_readiness_red: float = 0.0,
    seed: int = 20261014,
) -> pd.DataFrame:
    """运行 NSGA-II，返回 Pareto 方案表（已标注 knee/端点）。"""
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.optimize import minimize

    ctx = ctx or ScenarioContext.baseline()
    problem = make_problem(model, duration_ticks, ctx, min_readiness_red)
    algorithm = NSGA2(pop_size=pop_size)
    res = minimize(
        problem, algorithm, ("n_gen", n_gen), seed=seed, verbose=False, save_history=False
    )

    decisions = map_decisions(res.X)
    pred = model.predict(build_feature_matrix(decisions, ctx))
    cost = resource_cost(decisions, duration_ticks)

    df = pd.DataFrame(decisions, columns=DECISION_VARS)
    df[DECISION_VARS] = df[DECISION_VARS].round().astype(int)
    df["success"] = pred["success"].to_numpy()
    df["attrition_red"] = pred["attrition_red"].to_numpy()
    df["cost"] = cost
    for t in TARGETS:
        if t not in ("success", "attrition_red"):
            df[t] = pred[t].to_numpy()
    # 归一化目标副本（C6 画 3D/二维前沿用）
    df["f_success_neg"] = -df["success"]
    df["f_attrition_red"] = df["attrition_red"]
    df["f_cost"] = df["cost"]

    df["rank_tag"] = ""
    df.loc[_knee_index(res.F), "rank_tag"] = "knee"
    # 两端：成本最低（最省）、success 最高（最强保障）
    df.loc[int(df["cost"].idxmin()), "rank_tag"] = (
        "knee" if df.loc[int(df["cost"].idxmin()), "rank_tag"] == "knee" else "leanest"
    )
    best_succ = int(df["success"].idxmax())
    if df.loc[best_succ, "rank_tag"] == "":
        df.loc[best_succ, "rank_tag"] = "strongest"
    # 去掉决策完全相同的重复个体（同决策组内带 rank_tag 的行优先保留）
    df = df.sort_values(["cost", "rank_tag"], ascending=[True, False])
    df = df.drop_duplicates(subset=DECISION_VARS, keep="first")
    return df.sort_values("cost").reset_index(drop=True)


def _knee_index(F: np.ndarray) -> int:
    """归一化目标空间中离乌托邦点最近的解（最大折中/knee 推荐）。"""
    F = np.asarray(F, float)
    lo = F.min(axis=0)
    hi = F.max(axis=0)
    span = np.where(hi - lo > 1e-12, hi - lo, 1.0)
    Fn = (F - lo) / span
    return int(np.argmin(np.linalg.norm(Fn, axis=1)))


def evaluate_baseline(
    model: SurrogateModel, duration_ticks: int, ctx: ScenarioContext | None = None
) -> pd.Series:
    """v3 标定基线保障方案的代理预测（与 Pareto 方案对比用）。"""
    ctx = ctx or ScenarioContext.baseline()
    decisions = baseline_decisions()
    pred = model.predict(build_feature_matrix(decisions, ctx)).iloc[0]
    row = pd.Series(decisions[0], index=DECISION_VARS).round().astype(int)
    row["cost"] = float(resource_cost(decisions, duration_ticks)[0])
    for t in TARGETS:
        row[t] = float(pred[t])
    return row
