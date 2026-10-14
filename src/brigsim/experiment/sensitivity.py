"""M3 灵敏度分析：

- Morris（一次一列法）：低成本筛选对指标影响最大的参数（μ* 总效应、σ 非线性/交互）；
- Sobol（方差分解）：Saltelli 采样 + S1（一阶主效应）/ST（含交互的总效应），
  回答"指标不确定性中各参数贡献占比"。

粗版做法（两法共用，参数均为全局乘子，跨所有平台类型统一缩放）：
- pk_scale / detect_scale / speed_scale / mtbf_scale：交战与保障参数全局缩放；
- visibility：环境能见度系数；
- 每个设计点跑若干蒙特卡洛复制取均值作为该点响应（公共随机数降噪）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from SALib.analyze.morris import analyze as morris_analyze
from SALib.analyze.sobol import analyze as sobol_analyze
from SALib.sample.morris import sample as morris_sample
from SALib.sample.sobol import sample as sobol_sample

from ..data.schema import Scenario
from .runner import run_once

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
    batch_device: str | None = None,
    batch_size: int = 128,
) -> pd.DataFrame:
    """Morris 灵敏度扫描；返回每个 (指标, 参数) 的 μ*/σ/置信半宽与 μ* 排序。

    batch_device 非 None 时走 M4 Warp env 维批量（设计点×复制整体上设备）。
    """
    rng = np.random.default_rng(base_seed)
    X = morris_sample(PROBLEM, N=trajectories, num_levels=num_levels, seed=rng)
    if batch_device is not None:
        Y = _evaluate_points_batch(
            scenario, X, reps_per_point, base_seed, batch_device, batch_size
        )
    else:
        if kernel_cls is None:
            from ..combat.mesa_kernel import MesaKernel

            kernel_cls = MesaKernel
        Y = _evaluate_points(scenario, X, reps_per_point, base_seed, kernel_cls)

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


def _evaluate_points(
    scenario: Scenario,
    X: np.ndarray,
    reps_per_point: int,
    base_seed: int,
    kernel_cls,
) -> np.ndarray:
    """在每个设计点上跑 reps 次蒙特卡洛复制（公共随机数），返回各指标均值矩阵。"""
    seeds = [base_seed + r for r in range(reps_per_point)]
    Y = np.zeros((X.shape[0], len(METRICS)))
    for p, x in enumerate(X):
        sc = apply_point(scenario, x)
        vals = np.zeros((reps_per_point, len(METRICS)))
        for r, seed in enumerate(seeds):
            metrics, _ = run_once(sc, seed=seed, kernel_cls=kernel_cls)
            vals[r] = [metrics[m] for m in METRICS]
        Y[p] = vals.mean(axis=0)
    return Y


def _evaluate_points_batch(
    scenario: Scenario,
    X: np.ndarray,
    reps_per_point: int,
    base_seed: int,
    device: str,
    batch_size: int = 128,
) -> np.ndarray:
    """M4：全部 (设计点×复制) 作为 env 维批量上设备。

    env 顺序点主序 (p, r)；种子只随复制号 r 变化（公共随机数 CRN），
    与单环境 WarpKernel 同种子轨迹一致。每环境一个 apply_point 后的想定，
    要求编制规模与地形不变（本灵敏度只缩放参数/能见度）。
    """
    from ..combat.warp_batch import run_batch

    P = X.shape[0]
    crn_seeds = [base_seed + r for r in range(reps_per_point)]
    V = np.zeros((P * reps_per_point, len(METRICS)))
    for begin in range(0, P * reps_per_point, batch_size):
        end = min(begin + batch_size, P * reps_per_point)
        scs = [apply_point(scenario, X[i // reps_per_point]) for i in range(begin, end)]
        seeds = [crn_seeds[i % reps_per_point] for i in range(begin, end)]
        rows = run_batch(scs, seeds, device=device)
        for k, m in enumerate(rows):
            V[begin + k] = [m[name] for name in METRICS]
    return V.reshape(P, reps_per_point, len(METRICS)).mean(axis=1)


def run_sobol(
    scenario: Scenario,
    n_base: int = 16,
    reps_per_point: int = 4,
    base_seed: int = 20261013,
    kernel_cls=None,
    batch_device: str | None = None,
    batch_size: int = 128,
) -> pd.DataFrame:
    """Sobol 方差分解（Saltelli 采样，不算二阶）；返回每个 (指标, 参数) 的
    S1（一阶主效应）/ST（总效应，含交互）与 95% 自助置信半宽，按 ST 排序。

    设计点数 = n_base × (D + 2)；D=5，n_base=16 时为 112 点。
    batch_device 非 None 时走 M4 Warp env 维批量（全部点×复制一次平铺）。
    """
    rng = np.random.default_rng(base_seed)
    X = sobol_sample(PROBLEM, N=n_base, calc_second_order=False, seed=rng)
    if batch_device is not None:
        Y = _evaluate_points_batch(
            scenario, X, reps_per_point, base_seed, batch_device, batch_size
        )
    else:
        if kernel_cls is None:
            from ..combat.mesa_kernel import MesaKernel

            kernel_cls = MesaKernel
        Y = _evaluate_points(scenario, X, reps_per_point, base_seed, kernel_cls)

    frames = []
    for j, metric in enumerate(METRICS):
        si = sobol_analyze(
            PROBLEM, Y[:, j], calc_second_order=False, conf_level=0.95, seed=rng
        )
        df = pd.DataFrame(
            {
                "metric": metric,
                "param": PROBLEM["names"],
                "S1": si["S1"],
                "S1_conf": si["S1_conf"],
                "ST": si["ST"],
                "ST_conf": si["ST_conf"],
            }
        )
        df["rank"] = df["ST"].rank(ascending=False).astype(int)
        frames.append(df.sort_values("rank"))
    return pd.concat(frames, ignore_index=True)
