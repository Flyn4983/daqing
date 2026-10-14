"""M3 灵敏度分析冒烟：乘子施加正确，Morris 输出结构与排序合法。"""

import numpy as np

from brigsim.data.provider import YamlFileProvider
from brigsim.experiment.sensitivity import (
    METRICS,
    PROBLEM,
    apply_point,
    run_sensitivity,
    run_sobol,
)


def test_apply_point_side_factors_and_mtbf():
    from brigsim.kernel.state import SimState

    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    # 顺序: red_pk, blue_pk, red_detect, blue_detect, red_speed, blue_speed, mtbf, visibility
    sc2 = apply_point(sc, np.array([1.5, 1.0, 1.0, 0.5, 1.0, 2.0, 0.5, 0.7]))
    base, moved = SimState(sc), SimState(sc2)
    red, blue = moved.side == 0, moved.side == 1
    # 红方 pk×1.5（截断 1.0）、蓝方不变；蓝方 detect×0.5、红方不变
    np.testing.assert_allclose(moved.pk[red], np.clip(base.pk[red] * 1.5, 0.0, 1.0))
    np.testing.assert_allclose(moved.pk[blue], base.pk[blue])
    np.testing.assert_allclose(moved.detect_prob[red], base.detect_prob[red])
    np.testing.assert_allclose(moved.detect_prob[blue], base.detect_prob[blue] * 0.5)
    # 蓝方速度×2、红方不变；MTBF 全局×0.5
    np.testing.assert_allclose(moved.speed[red], base.speed[red])
    np.testing.assert_allclose(moved.speed[blue], base.speed[blue] * 2.0)
    for name, p in sc2.platform_types.items():
        assert abs(p.mtbf_ticks - sc.platform_types[name].mtbf_ticks * 0.5) < 1e-9
    assert sc2.environment.visibility_factor == 0.7
    # 原想定不被修改
    assert sc.environment.visibility_factor == 1.0
    assert sc.side_modifiers == {}


def test_morris_output_structure():
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    df = run_sensitivity(sc, trajectories=2, reps_per_point=2)
    assert len(df) == len(METRICS) * PROBLEM["num_vars"]
    assert set(df["metric"].unique()) == set(METRICS)
    assert set(df["param"]) == set(PROBLEM["names"])
    assert (df["mu_star"] >= 0).all()
    for metric, grp in df.groupby("metric", sort=False):
        # μ* 非升序排列；名次在 1..k（小样本并列时 average 名次可无 1）
        assert grp["mu_star"].is_monotonic_decreasing
        assert set(grp["rank"]) <= set(range(1, PROBLEM["num_vars"] + 1))


def test_sobol_output_structure():
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    df = run_sobol(sc, n_base=4, reps_per_point=2)
    # Saltelli（不算二阶）设计点 N×(D+2)；输出每指标 D 行
    assert len(df) == len(METRICS) * PROBLEM["num_vars"]
    assert set(df["metric"].unique()) == set(METRICS)
    assert set(df["param"]) == set(PROBLEM["names"])
    # 小样本下指数允许轻微越界（数值噪声），但必须有限；ST 非升序排列
    assert np.isfinite(df[["S1", "S1_conf", "ST", "ST_conf"]].to_numpy()).all()
    for metric, grp in df.groupby("metric", sort=False):
        assert grp["ST"].is_monotonic_decreasing
        assert set(grp["rank"]) <= set(range(1, PROBLEM["num_vars"] + 1))
