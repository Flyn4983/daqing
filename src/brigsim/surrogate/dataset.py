"""C3 数据集生成：特征空间批量推演 → 四率标签。

每个设计点跑 reps 次蒙特卡洛复制（公共随机数 CRN：所有点共用同一组复制种子，
把仿真随机噪声转为"点间系统差异"，显著降低代理模型的标签噪声），
标签取 reps 次的均值（另存标准差用于评估噪声地板）。

生产走 M4 Warp env 维批量（全部 点×复制 平铺上 GPU）；
device=None 时回退 Mesa（小数据/无 GPU 的测试环境用）。
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from ..data.schema import Scenario
from .design import FEATURE_NAMES, TARGETS, apply_features, sample_lhs, sample_random


def evaluate_points(
    scenario: Scenario,
    X: np.ndarray,
    reps: int = 8,
    base_seed: int = 20261014,
    device: str | None = "cuda:0",
    batch_size: int = 512,
    echo: bool = False,
) -> tuple[np.ndarray, np.ndarray, float]:
    """在 X 的每个设计点上跑 reps 次复制，返回 (均值矩阵, 标准差矩阵, 墙钟秒)。

    矩阵形状 (n_points, len(TARGETS))，列序同 design.TARGETS。
    """
    X = np.asarray(X, dtype=float)
    n_points = X.shape[0]
    crn_seeds = [base_seed + r for r in range(reps)]
    t0 = time.perf_counter()

    if device is not None:
        from ..combat.warp_batch import run_batch

        values = np.zeros((n_points * reps, len(TARGETS)))
        total = n_points * reps
        for begin in range(0, total, batch_size):
            end = min(begin + batch_size, total)
            scs = [apply_features(scenario, X[i // reps]) for i in range(begin, end)]
            seeds = [crn_seeds[i % reps] for i in range(begin, end)]
            rows = run_batch(scs, seeds, device=device)
            for k, m in enumerate(rows):
                values[begin + k] = [float(m[name]) for name in TARGETS]
            if echo:
                print(f"  推演 {end}/{total} 复制")
        values = values.reshape(n_points, reps, len(TARGETS))
    else:
        from ..combat.mesa_kernel import MesaKernel
        from ..experiment.runner import run_once

        values = np.zeros((n_points, reps, len(TARGETS)))
        for p in range(n_points):
            sc = apply_features(scenario, X[p])
            for r, seed in enumerate(crn_seeds):
                metrics, _ = run_once(sc, seed=seed, kernel_cls=MesaKernel)
                values[p, r] = [float(metrics[name]) for name in TARGETS]
            if echo and (p + 1) % 16 == 0:
                print(f"  推演 {p + 1}/{n_points} 设计点")

    elapsed = time.perf_counter() - t0
    return values.mean(axis=1), values.std(axis=1, ddof=1) if reps > 1 else np.zeros_like(values[:, 0, :]), elapsed


def build_dataset(
    scenario: Scenario,
    n_train: int = 512,
    n_holdout: int = 96,
    reps_train: int = 8,
    reps_holdout: int = 12,
    base_seed: int = 20261014,
    device: str | None = "cuda:0",
    batch_size: int = 512,
    echo: bool = False,
) -> pd.DataFrame:
    """生成"训练(LHS) + 留出(均匀随机)"数据集。

    返回单个 DataFrame：特征列 + 目标均值列 + 目标 *_std 列 + split 列。
    留出点复制数更多（标签噪声更低），用于报告无偏的泛化误差。
    """
    X_train = sample_lhs(n_train, seed=base_seed)
    X_hold = sample_random(n_holdout, seed=base_seed + 7777)

    frames = []
    for split, X, reps in (
        ("train", X_train, reps_train),
        ("holdout", X_hold, reps_holdout),
    ):
        means, stds, elapsed = evaluate_points(
            scenario, X, reps=reps, base_seed=base_seed,
            device=device, batch_size=batch_size, echo=echo,
        )
        df = pd.DataFrame(X, columns=FEATURE_NAMES)
        df["split"] = split
        df["n_reps"] = reps
        for j, name in enumerate(TARGETS):
            df[name] = means[:, j]
            df[f"{name}_std"] = stds[:, j]
        frames.append(df)
        if echo:
            print(f"{split}: {X.shape[0]} 点 × {reps} 复制，推演耗时 {elapsed:.1f}s")
    return pd.concat(frames, ignore_index=True)
