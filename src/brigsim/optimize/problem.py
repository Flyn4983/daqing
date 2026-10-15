"""保障方案多目标优化问题定义（C7-a3 a2 实体化口径）。

业务对齐（评审三原则之"落地"）：
- **决策变量只取红方（我方）保障参谋真正能调配的 7 个旋钮**：
  抢修组数、装甲抢救车数、器材补给车数、携行档基数档级、运行档基数档级、
  前送波次节拍、每波每部件件数；
  装备可靠性与红蓝交战效能是"情景"，固定为上下文不进优化
  （可在不同情景下重复优化做鲁棒预案）。
- 三个相互冲突的目标：
  f1 = -任务完成率（越大越好 → pymoo 最小化取负）
  f2 =  红方战损率（越小越好）
  f3 =  保障资源成本（越小越好）：人力 + 保障车辆 + 两档器材基数 + 波次流量
- 可选约束：红方终值完好率不低于阈值（保底要求）。

实现说明：决策变量在 [0,1]^7 连续松弛，评估端按 SPACE 域映射并取整
（标准 relax+round，避开混合整数算子的版本差异；代理预测整批向量化）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..surrogate.design import FEATURE_NAMES, FEATURES, N_DECISION

# 我方可调的决策变量（FEATURES 前 7 维）
DECISION_VARS = list(FEATURE_NAMES[:N_DECISION])
# 情景上下文变量（优化中固定，FEATURES 后 3 维）
CONTEXT_VARS = list(FEATURE_NAMES[N_DECISION:])

_BOUNDS = {name: (lo, hi, is_int) for name, lo, hi, is_int in FEATURES}

# 资源成本权重（业务原型假设，均归一化到 [0,1]，和为 1，可调）：
# 抢修人力 + 抢救/补给车辆 0.45；两档初始器材 0.30；任务期波次流量 0.25。
W_TEAMS = 0.25
W_ARV = 0.12
W_SUPPLY_VEH = 0.08
W_CARRY = 0.18
W_OPERATING = 0.12
W_WAVE = 0.25

# a2 口径器材部件数（流量归一化用：每波器材 = wave_qty × 部件种类）
N_PARTS = 4


@dataclass(frozen=True)
class ScenarioContext:
    """优化时固定的战场/装备情景（物理单位值）。"""

    mtbf_scale: float = 1.0
    red_pk_scale: float = 1.0
    blue_pk_scale: float = 1.0

    def as_row(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in CONTEXT_VARS}

    @classmethod
    def baseline(cls) -> "ScenarioContext":
        """标定基线情景：标称可靠性与双方交战效能（乘子 1.0）。"""
        return cls()


def map_decisions(u: np.ndarray) -> np.ndarray:
    """[0,1]^7 松弛变量 → 物理单位决策值（整数维取整）。"""
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
    """归一化保障资源成本 [0,1]：人力/车辆 0.45 + 两档器材 0.30 + 波次流量 0.25。

    波次流量 = 任务期波次次数 × 每波器材数（wave_qty×4 部件），
    用"最勤节拍×最大单波"的最大可能流量归一。
    """
    d = pd.DataFrame(decisions, columns=DECISION_VARS)

    def _norm(name: str, v: np.ndarray) -> np.ndarray:
        lo, hi, _ = _BOUNDS[name]
        return (np.asarray(v, float) - lo) / (hi - lo)

    teams_n = _norm("red_repair_teams", d["red_repair_teams"].to_numpy())
    arv_n = _norm("red_arv", d["red_arv"].to_numpy())
    sup_n = _norm("red_supply_veh", d["red_supply_veh"].to_numpy())
    carry_n = _norm("carry_level", d["carry_level"].to_numpy())
    oper_n = _norm("operating_level", d["operating_level"].to_numpy())
    interval = d["wave_interval_ticks"].to_numpy(float)
    qty = d["wave_qty"].to_numpy(float)
    iv_lo, _, _ = _BOUNDS["wave_interval_ticks"]
    _, qty_hi, _ = _BOUNDS["wave_qty"]
    volume = duration_ticks / interval * qty * N_PARTS
    volume_max = duration_ticks / iv_lo * qty_hi * N_PARTS
    vol_n = np.clip(volume / volume_max, 0.0, 1.0)
    return (
        W_TEAMS * teams_n
        + W_ARV * arv_n
        + W_SUPPLY_VEH * sup_n
        + W_CARRY * carry_n
        + W_OPERATING * oper_n
        + W_WAVE * vol_n
    )


def baseline_decisions() -> np.ndarray:
    """v4 标定基线保障方案（a2 口径）：
    6 抢修组 / 4 抢救车 / 2 器材车 / 携行档 2 / 运行档 2 / 20tick 波次 / 每波每部件 2 件。
    """
    return np.array([[6.0, 4.0, 2.0, 2.0, 2.0, 20.0, 2.0]])


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
