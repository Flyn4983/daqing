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


def test_apply_point_scales_and_clips():
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    # pk 乘子 1.5 应放大但截断在 1.0；能见度取显式值
    sc2 = apply_point(sc, np.array([1.5, 1.0, 2.0, 0.5, 0.7]))
    for name, p in sc2.platform_types.items():
        assert p.pk == min(sc.platform_types[name].pk * 1.5, 1.0)
        assert abs(p.speed_m_per_tick - sc.platform_types[name].speed_m_per_tick * 2.0) < 1e-9
        assert abs(p.mtbf_ticks - sc.platform_types[name].mtbf_ticks * 0.5) < 1e-9
    assert sc2.environment.visibility_factor == 0.7
    # 原想定不被修改
    assert sc.environment.visibility_factor == 1.0


def test_morris_output_structure():
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    df = run_sensitivity(sc, trajectories=2, reps_per_point=2)
    assert len(df) == len(METRICS) * PROBLEM["num_vars"]
    assert set(df["metric"].unique()) == set(METRICS)
    assert set(df["param"]) == set(PROBLEM["names"])
    assert (df["mu_star"] >= 0).all()
    for metric, grp in df.groupby("metric", sort=False):
        # μ* 非升序排列；名次在 1..k（允许并列）
        assert grp["mu_star"].is_monotonic_decreasing
        assert grp["rank"].min() == 1
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
        assert grp["rank"].min() == 1
        assert set(grp["rank"]) <= set(range(1, PROBLEM["num_vars"] + 1))
