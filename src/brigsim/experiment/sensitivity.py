"""M3 灵敏度分析：SALib Morris（一次一列法）筛选对三大指标影响最大的参数。

粗版做法（参数均为全局乘子，跨所有平台类型统一缩放）：
- pk_scale / detect_scale / speed_scale / mtbf_scale：交战与保障参数全局缩放；
- visibility：环境能见度系数；
- 每个 Morris 设计点跑若干蒙特卡洛复制取均值作为该点响应（公共随机数降噪）；
- 输出 μ*（总效应敏感度）、σ（非线性/交互作用）与排序，后续可用 Sobol 做定量分解。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from SALib.analyze.morris import analyze as morris_analyze
from SALib.sample.morris import sample as morris_sample

from ..data.schema import Scenario

PROBLEM = {
    "num_vars": 5,
    "names": ["pk_scale", "detect_scale", "speed_scale", "mtbf_scale", "visibility"],
    "bounds": [
        [0.5, 1.5],   # 单次击毁概率乘子
        [0.5, 1.5],   # 发现概率乘子
        [0.5, 1.5],   # 机动速度乘子
        [0.25, 3.0],  # MTBF 乘子（越大越可靠）
        [0.5, 1.0],   # 能见度系数
    ],
}

METRICS = ["success", "attrition_red", "attrition_blue", "readiness_red", "readiness_blue"]


def apply_point(scenario: Scenario, x: np.ndarray) -> Scenario:
    """把一组灵敏度乘子施加到想定（不改原对象）。"""
    pk_s, det_s, spd_s, mtbf_s, vis = x
    ptypes = {
        name: p.model_copy(
            update={
                "pk": min(max(p.pk * pk_s, 0.0), 1.0),
                "detect_prob": min(max(p.detect_prob * det_s, 0.0), 1.0),
                "speed_m_per_tick": p.speed_m_per_tick * spd_s,
                "mtbf_ticks": p.mtbf_ticks * mtbf_s,
            }
        )
        for name, p in scenario.platform_types.items()
    }
    env = scenario.environment.model_copy(update={"visibility_factor": float(vis)})
    return scenario.model_copy(update={"platform_types": ptypes, "environment": env})


def run_sensitivity(
    scenario: Scenario,
    trajectories: int = 12,
    reps_per_point: int = 12,
    base_seed: int = 20261013,
    num_levels: int = 4,
    kernel_cls=None,
) -> pd.DataFrame:
    """Morris 灵敏度扫描；返回每个 (指标, 参数) 的 μ*/σ/置信半宽与 μ* 排序。"""
    from .runner import run_once

    if kernel_cls is None:
        from ..combat.mesa_kernel import MesaKernel

        kernel_cls = MesaKernel

    rng = np.random.default_rng(base_seed)
    X = morris_sample(PROBLEM, N=trajectories, num_levels=num_levels, seed=rng)
    # 公共随机数：每个设计点用同一组复制种子
    seeds = [base_seed + r for r in range(reps_per_point)]

    Y = np.zeros((X.shape[0], len(METRICS)))
    for p, x in enumerate(X):
        sc = apply_point(scenario, x)
        vals = np.zeros((reps_per_point, len(METRICS)))
        for r, seed in enumerate(seeds):
            metrics, _ = run_once(sc, seed=seed, kernel_cls=kernel_cls)
            vals[r] = [metrics[m] for m in METRICS]
        Y[p] = vals.mean(axis=0)

    frames = []
    for j, metric in enumerate(METRICS):
        si = morris_analyze(
            PROBLEM, X, Y[:, j], conf_level=0.95, num_levels=num_levels, seed=rng
        )
        df = pd.DataFrame(
            {
                "metric": metric,
                "param": PROBLEM["names"],
                "mu": si["mu"],
                "mu_star": si["mu_star"],
                "sigma": si["sigma"],
                "mu_star_conf": si["mu_star_conf"],
            }
        )
        df["rank"] = df["mu_star"].rank(ascending=False).astype(int)
        frames.append(df.sort_values("rank"))
    return pd.concat(frames, ignore_index=True)
