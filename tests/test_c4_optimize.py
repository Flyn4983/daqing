"""C4 多目标保障优化测试（用入库预训练代理模型；复验走 Mesa 免 GPU）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from brigsim.data.provider import YamlFileProvider
from brigsim.optimize.nsga import evaluate_baseline, optimize_support
from brigsim.optimize.problem import (
    DECISION_VARS,
    ScenarioContext,
    build_feature_matrix,
    make_problem,
    map_decisions,
    resource_cost,
)
from brigsim.optimize.validation import (
    representative_plans,
    sim_guarded_knee,
    validate_plans,
)
from brigsim.surrogate.design import FEATURE_NAMES, TARGETS
from brigsim.surrogate.model import SurrogateModel

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "surrogate_brigade_v1.joblib"


@pytest.fixture(scope="module")
def model():
    return SurrogateModel.load(str(MODEL_PATH))


@pytest.fixture(scope="module")
def duration():
    sc = YamlFileProvider("scenarios").load_scenario("example_brigade")
    return sc.mission.duration_ticks


def test_map_decisions_bounds_and_integer():
    u = np.array([[0.0] * 7, [1.0] * 7])
    x = map_decisions(u)
    assert x.shape == (2, 7)
    assert np.allclose(x, np.rint(x))  # 全部整数维
    # 角点映射到域端点（7 决策：组/抢救车/器材车/携行档/运行档/间隔/每波件数）
    assert list(x[0]) == [2.0, 2.0, 1.0, 0.0, 0.0, 10.0, 1.0]
    assert list(x[1]) == [10.0, 6.0, 3.0, 4.0, 3.0, 40.0, 4.0]


def test_build_feature_matrix_context_fixed(model):
    ctx = ScenarioContext(mtbf_scale=1.2, red_pk_scale=0.9)
    X = build_feature_matrix(np.array([[3, 4, 2, 1, 2, 30, 2]]), ctx)
    assert list(X.columns) == FEATURE_NAMES
    row = X.iloc[0]
    assert row["red_repair_teams"] == 3 and row["red_arv"] == 4
    assert row["carry_level"] == 1 and row["wave_interval_ticks"] == 30
    # 情景因子固定为上下文值
    assert row["mtbf_scale"] == pytest.approx(1.2)
    assert row["red_pk_scale"] == pytest.approx(0.9)
    assert row["blue_pk_scale"] == 1.0


def test_resource_cost_range_and_monotone(duration):
    # 最小配置与最大配置
    lo = map_decisions(np.zeros((1, 7)))
    hi = map_decisions(np.ones((1, 7)))
    c_lo = resource_cost(lo, duration)[0]
    c_hi = resource_cost(hi, duration)[0]
    assert 0.0 <= c_lo < c_hi <= 1.0
    # 抢修组增加 → 成本增加（其余不变）
    base = np.array([[6, 4, 2, 2, 2, 20, 2]])
    more = np.array([[8, 4, 2, 2, 2, 20, 2]])
    assert resource_cost(more, duration)[0] > resource_cost(base, duration)[0]
    # 前送更勤（周期更短）→ 成本增加
    frequent = np.array([[6, 4, 2, 2, 2, 10, 2]])
    assert resource_cost(frequent, duration)[0] > resource_cost(base, duration)[0]
    # 携行档加高 → 成本增加
    stocked = np.array([[6, 4, 2, 4, 2, 20, 2]])
    assert resource_cost(stocked, duration)[0] > resource_cost(base, duration)[0]


def test_nsga_pareto_tradeoff_and_tags(model, duration):
    ctx = ScenarioContext.baseline()
    df = optimize_support(model, duration, ctx, pop_size=20, n_gen=8)
    assert len(df) >= 5
    # 决策列整数且在域内
    for name in DECISION_VARS:
        assert np.allclose(df[name], np.rint(df[name]))
    # 三目标有限、物理界
    assert df["success"].between(0, 1).all()
    assert df["attrition_red"].between(0, 1).all()
    assert df["cost"].between(0, 1).all()
    # 前沿内互不支配：不存在方案在 success/attrition/cost 上同时不差且一项严格更好
    F = df[["success", "attrition_red", "cost"]].to_numpy()
    F[:, 0] = -F[:, 0]  # 统一为最小化
    for i in range(len(F)):
        others = np.delete(F, i, axis=0)
        dominated = np.all(others <= F[i], axis=1) & np.any(others < F[i], axis=1)
        assert not dominated.any()
    # 标签：knee 唯一，且两个端点标签存在
    assert (df["rank_tag"] == "knee").sum() == 1
    assert {"leanest", "strongest"} <= set(df["rank_tag"])
    # 最省方案成本最低、最强方案成功率最高
    assert df.loc[df["rank_tag"] == "leanest", "cost"].iloc[0] == df["cost"].min()
    assert df.loc[df["rank_tag"] == "strongest", "success"].iloc[0] == df["success"].max()


def test_constraint_min_readiness(model, duration):
    """启用完好率保底约束：返回解预测完好率应全部 ≥ 阈值（容差 0.02）。"""
    ctx = ScenarioContext.baseline()
    df = optimize_support(
        model, duration, ctx, pop_size=24, n_gen=12, min_readiness_red=0.30
    )
    assert len(df) >= 3
    assert (df["readiness_red"] >= 0.28).all()


def test_problem_objectives_shape(model, duration):
    ctx = ScenarioContext.baseline()
    p = make_problem(model, duration, ctx, min_readiness_red=0.25)
    out = {}
    p._evaluate(np.array([[0.5] * 7, [0.0] * 7]), out)
    assert out["F"].shape == (2, 3)
    assert out["G"].shape == (2, 1)
    assert np.isfinite(out["F"]).all()


def test_baseline_and_validation_smoke(model, duration):
    """基线评估 + 代表方案 Mesa 仿真复验（3 方案×2 复制），指标有限可对比。"""
    sc = YamlFileProvider("scenarios").load_scenario("example_brigade")
    ctx = ScenarioContext.baseline()
    base = evaluate_baseline(model, duration, ctx)
    assert 0 <= base["success"] <= 1
    df = optimize_support(model, duration, ctx, pop_size=16, n_gen=6)
    reps = representative_plans(df)
    assert len(reps) >= 2
    cmp_df = validate_plans(sc, reps, ctx=ctx, reps=2, device=None, n_workers=1)
    assert cmp_df["plan"].nunique() == len(reps)
    assert len(cmp_df) == len(reps) * 9
    assert np.isfinite(cmp_df["simulation"]).all()
    prob_metrics = ~cmp_df["metric"].str.startswith("failure_rate_100h")
    assert cmp_df.loc[prob_metrics, "simulation"].between(-0.01, 1.01).all()
    assert (cmp_df.loc[~prob_metrics, "simulation"] >= 0).all()


def _toy_pareto() -> pd.DataFrame:
    """4 个候选的玩具前沿（公司级想定零漂移特征，仅用于仿真守卫测试）。"""
    decisions = [
        [2, 2, 1, 0, 0, 40, 1],
        [4, 2, 1, 1, 0, 30, 1],
        [6, 4, 2, 2, 2, 20, 2],
        [3, 3, 1, 2, 1, 20, 2],
    ]
    df = pd.DataFrame(decisions, columns=DECISION_VARS)
    df["cost"] = [0.1, 0.2, 0.3, 0.4]
    df["success"] = [0.9, 0.9, 0.9, 0.2]
    for t in TARGETS:
        if t not in df.columns:
            df[t] = 0.1
    df["rank_tag"] = ["leanest", "knee", "strongest", ""]
    return df


def _toy_baseline() -> pd.Series:
    row = pd.Series({k: 6 if i == 0 else 2 for i, k in enumerate(DECISION_VARS)})
    row["cost"] = 0.45
    for t in TARGETS:
        row[t] = 0.5
    return row


def test_sim_guarded_knee_passes_cheapest_and_baseline():
    """闸门 success≥0：knee 重选为过闸候选中成本最低者；baseline 进长表但不改标签。"""
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    pareto, val_df, info = sim_guarded_knee(
        sc, _toy_pareto(), reps=2, n_workers=1,
        guard_success=0.0, surr_min=0.7, baseline_row=_toy_baseline(),
    )
    assert info["passed"] is True
    assert (pareto["rank_tag"] == "knee").sum() == 1
    knee = pareto[pareto["rank_tag"] == "knee"].iloc[0]
    assert knee["cost"] == 0.1  # 过闸候选里最省
    # baseline 只进复验表，不进 pareto、不抢标签
    assert "baseline" not in set(pareto["rank_tag"])
    assert "baseline" in set(val_df["plan"])
    assert np.isfinite(val_df["simulation"]).all()
    # 每个被复验方案都有 9 个指标行
    assert (val_df.groupby("plan").size() == 9).all()


def test_sim_guarded_knee_none_pass_fallback():
    """闸门 success>1：无一过闸，knee 退化为仿真成功率最高者并显式告警标记。"""
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    pareto, _, info = sim_guarded_knee(
        sc, _toy_pareto(), reps=2, n_workers=1,
        guard_success=1.01, surr_min=0.7,
    )
    assert info["passed"] is False
    assert (pareto["rank_tag"] == "knee").sum() == 1
