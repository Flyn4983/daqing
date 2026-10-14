"""保障方案多目标优化问题定义。

业务对齐（评审三原则之"落地"）：
- **决策变量只取红方（我方）保障参谋真正能调配的 4 个旋钮**：
  抢修组数、初始备件基数、前送周期、前送批量；
  蓝方抢修/备件是"敌情"，装备可靠性与红蓝交战效能、气象是"情景"，
  二者均作为固定上下文，不进优化（可在不同情景下重复优化做鲁棒预案）。
- 三个相互冲突的目标：
  f1 = -任务完成率（越大越好 → pymoo 最小化取负）
  f2 =  红方战损率（越小越好）
  f3 =  保障资源成本（越小越好）：抢修人力 + 初始备件 + 任务期前送总量的归一化
- 可选约束：红方终值完好率不低于阈值（保底要求）。

实现说明：决策变量在 [0,1]^4 连续松弛，评估端按 SPACE 域映射并取整
（标准 relax+round，避开混合整数算子的版本差异；代理预测整批向量化）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..surrogate.design import FEATURE_NAMES, FEATURES

# 我方可调的决策变量（FEATURES 前 6 维中的红方 4 项；蓝方 2 项属敌情）
DECISION_VARS = [
    "red_repair_teams",
    "red_spare_stock",
    "resupply_interval_ticks",
    "resupply_qty",
]
# 情景上下文变量（优化中固定）
CONTEXT_VARS = [
    "blue_repair_teams",
    "blue_spare_stock",
    "mtbf_scale",
    "red_pk_scale",
    "blue_pk_scale",
    "visibility",
]

_BOUNDS = {name: (lo, hi, is_int) for name, lo, hi, is_int in FEATURES}

# 资源成本权重（业务假设：抢修组=人员+抢救装备，代价最重；初始备件与
# 任务期前送流量各占四分之一；均归一化到 [0,1]，可调）
W_TEAMS = 0.5
W_STOCK = 0.25
W_RESUPPLY = 0.25


@dataclass(frozen=True)
class ScenarioContext:
    """优化时固定的战场/敌情上下文（物理单位值）。"""

    blue_repair_teams: float = 4.0
    blue_spare_stock: float = 8.0
    mtbf_scale: float = 1.0
    red_pk_scale: float = 1.0
    blue_pk_scale: float = 1.0
    visibility: float = 1.0

    def as_row(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in CONTEXT_VARS}

    @classmethod
    def baseline(cls) -> "ScenarioContext":
        """标定基线情景（敌情蓝方 v3：4 抢修组/8 备件；其余取标称）。"""
        return cls()


def map_decisions(u: np.ndarray) -> np.ndarray:
    """[0,1]^4 松弛变量 → 物理单位决策值（整数维取整）。"""
    u = np.asarray(u, dtype=float).reshape(-1, len(DECISION_VARS))
    x = np.empty_like(u)
    for j, name in enumerate(DECISION_VARS):
        lo, hi, is_int = _BOUNDS[name]
        v = lo + u[:, j] * (hi - lo)
        x[:, j] = np.rint(v) if is_int else v
    return x


def build_feature_matrix(decisions: np.ndarray, ctx: ScenarioContext) -> pd.DataFrame:
    """决策物理值 + 固定情景 → 代理模型 10 维输入矩阵（列序 FEATURE_NAMES）。"""
    decisions = np.asarray(decisions, dtype=float).reshape(-1, len(DECISION_VARS))
    n = decisions.shape[0]
    row = ctx.as_row()
    X = np.zeros((n, len(FEATURE_NAMES)))
    dec_map = dict(zip(DECISION_VARS, decisions.T, strict=True))
    for k, name in enumerate(FEATURE_NAMES):
        if name in dec_map:
            X[:, k] = dec_map[name]
        else:
            X[:, k] = row[name]
    return pd.DataFrame(X, columns=FEATURE_NAMES)


def resource_cost(decisions: np.ndarray, duration_ticks: int) -> np.ndarray:
    """归一化保障资源成本 [0,1]：抢修人力 0.5 + 初始备件 0.25 + 前送流量 0.25。

    前送流量 = 任务期内补给次数 × 每次件数（次数按 duration/interval 估算），
    用"最短周期×最大批量"的最大可能流量归一。
    """
    d = pd.DataFrame(decisions, columns=DECISION_VARS)

    def _norm(name: str, v: np.ndarray) -> np.ndarray:
        lo, hi, _ = _BOUNDS[name]
        return (np.asarray(v, float) - lo) / (hi - lo)

    teams_n = _norm("red_repair_teams", d["red_repair_teams"].to_numpy())
    stock_n = _norm("red_spare_stock", d["red_spare_stock"].to_numpy())
    interval = d["resupply_interval_ticks"].to_numpy(float)
    qty = d["resupply_qty"].to_numpy(float)
    iv_lo, iv_hi, _ = _BOUNDS["resupply_interval_ticks"]
    qty_lo, qty_hi, _ = _BOUNDS["resupply_qty"]
    volume = duration_ticks / interval * qty
    volume_max = duration_ticks / iv_lo * qty_hi
    vol_n = np.clip(volume / volume_max, 0.0, 1.0)
    return W_TEAMS * teams_n + W_STOCK * stock_n + W_RESUPPLY * vol_n


def baseline_decisions() -> np.ndarray:
    """v3 标定基线保障方案（红方 6 抢修组/12 备件/20tick 周期/8 件）。"""
    return np.array([[6.0, 12.0, 20.0, 8.0]])


def make_problem(
    surrogate,
    duration_ticks: int,
    ctx: ScenarioContext,
    min_readiness_red: float = 0.0,
):
    """构造 pymoo 多目标问题（延迟导入 pymoo）。

    目标 F = [-success, attrition_red, cost]；
    约束 G = min_readiness_red - readiness_red（≤0 可行）。
    """
    from pymoo.core.problem import Problem

    class SurrogateSupportProblem(Problem):
        def __init__(self) -> None:
            super().__init__(
                n_var=len(DECISION_VARS),
                n_obj=3,
                n_constr=1 if min_readiness_red > 0 else 0,
                xl=np.zeros(len(DECISION_VARS)),
                xu=np.ones(len(DECISION_VARS)),
            )

        def _evaluate(self, U, out, *args, **kwargs):
            decisions = map_decisions(U)
            X = build_feature_matrix(decisions, ctx)
            pred = surrogate.predict(X)
            cost = resource_cost(decisions, duration_ticks)
            out["F"] = np.column_stack(
                [-pred["success"].to_numpy(), pred["attrition_red"].to_numpy(), cost]
            )
            if self.n_constr > 0:
                out["G"] = (min_readiness_red - pred["readiness_red"].to_numpy())[:, None]

    return SurrogateSupportProblem()
