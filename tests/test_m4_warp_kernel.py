"""M4 WarpKernel 测试（统一在 CPU 后端跑，保证无 GPU 机器可复现）。

覆盖：
- 单局可跑通：指标有限合法、事件日志结构正确；
- 行为不变量：存活数单调不增、战毁单位不再移动、机动不越界；
- 与 MesaKernel 的跨复制分布对拍（随机流不同源，比分布不比轨迹）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import warp as wp

from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.combat.warp_batch import run_batch
from brigsim.combat.warp_kernel import WarpKernel
from brigsim.data.provider import YamlFileProvider
from brigsim.experiment.runner import run_once


def _cuda_available() -> bool:
    try:
        return wp.get_device("cuda").is_cuda
    except Exception:
        return False


cuda_required = pytest.mark.skipif(
    not _cuda_available(), reason="需要 CUDA 设备"
)


@pytest.fixture(scope="module")
def scenario():
    return YamlFileProvider().load_scenario("example_battalion")


def _warp(scenario, seed=0):
    return WarpKernel(scenario, seed, device="cpu")


def test_warp_run_metrics_finite(scenario):
    metrics, kernel = run_once(scenario, seed=20261013, kernel_cls=_warp)
    assert 0 < kernel.state.tick <= scenario.mission.duration_ticks
    for key in ("attrition_red", "attrition_blue", "readiness_red", "readiness_blue"):
        assert np.isfinite(metrics[key]) and 0.0 <= metrics[key] <= 1.0
    assert metrics["success"] in (True, False)


def test_warp_event_log_structure(scenario):
    _, kernel = run_once(scenario, seed=7, kernel_cls=_warp)
    snapshots, engagements = kernel.log.to_frames()
    assert {"tick", "unit_id", "x", "y", "alive", "damaged"} <= set(snapshots.columns)
    assert {"shooter", "target", "killed", "damaged"} <= set(engagements.columns)
    # 击毁与受损记录不会同时为真（同一射击 outcome 互斥）
    assert not (engagements["killed"] & engagements["damaged"]).any()


def test_alive_count_monotone_and_dead_dont_move(scenario):
    _, kernel = run_once(scenario, seed=11, kernel_cls=_warp)
    snap, _ = kernel.log.to_frames()
    alive_count = snap.groupby("tick")["alive"].sum().sort_index()
    assert (alive_count.diff().dropna() <= 0).all()

    bound = scenario.terrain.size_m[0]
    moved = snap[snap.tick > 0]
    assert moved["x"].max() <= bound and moved["y"].max() <= bound
    assert moved["x"].min() >= 0.0 and moved["y"].min() >= 0.0

    # 每个单位：首帧死亡之后位置必须冻结
    for uid, grp in snap.groupby("unit_id"):
        grp = grp.sort_values("tick")
        dead = grp.index[~grp["alive"]]
        if len(dead) == 0:
            continue
        first_dead = grp.loc[dead[0], "tick"]
        after = grp[grp.tick > first_dead]
        if len(after) >= 2:
            assert after["x"].nunique() == 1, uid
            assert after["y"].nunique() == 1, uid


def test_warp_mesa_distribution_parity(scenario):
    """40 复制分布对拍：战损/完好率均值差 ≤0.12；成功率差 ≤0.30（二值，噪声大）。"""
    rng_seeds = range(8000, 8020)
    mesa_rows, warp_rows = [], []
    for seed in rng_seeds:
        m, _ = run_once(scenario, seed=seed, kernel_cls=MesaKernel)
        w, _ = run_once(scenario, seed=seed, kernel_cls=_warp)
        mesa_rows.append(m)
        warp_rows.append(w)
    mesa_df, warp_df = pd.DataFrame(mesa_rows), pd.DataFrame(warp_rows)
    for col in ("attrition_red", "attrition_blue", "readiness_red", "readiness_blue"):
        assert abs(mesa_df[col].mean() - warp_df[col].mean()) <= 0.12, col
    assert abs(mesa_df["success"].mean() - warp_df["success"].mean()) <= 0.30
    # 两侧都确实发生交战，避免静默不打的退化
    assert warp_df["attrition_blue"].mean() > 0.2


def test_batch_matches_single_environments_bit_exact(scenario):
    """批量 env 与同种子逐个单环境必须逐轨迹一致（播种公式相同的硬保证）。"""
    base = 9300
    E = 4
    single = []
    for e in range(E):
        metrics, _ = run_once(
            scenario, seed=base + e,
            kernel_cls=lambda sc, seed=0: WarpKernel(sc, seed, device="cpu"),
        )
        single.append(metrics)
    batch = run_batch(scenario, [base + e for e in range(E)], device="cpu")
    for e in range(E):
        assert batch[e]["success"] == single[e]["success"]
        for key in ("attrition_red", "attrition_blue", "readiness_red", "readiness_blue"):
            assert abs(batch[e][key] - single[e][key]) < 1e-9, (e, key)


def test_batch_sensitivity_matches_single_per_env_params(scenario):
    """设计点×复制批量（每环境不同参数/能见度）必须与逐点单环境结果逐位一致。"""
    from brigsim.experiment import sensitivity as sens

    X = np.array(
        [
            # red_pk, blue_pk, red_det, blue_det, red_spd, blue_spd, mtbf, vis
            [1.2, 0.8, 1.1, 0.9, 1.0, 1.0, 0.5, 0.9],
            [0.7, 1.3, 0.9, 1.1, 1.0, 1.0, 2.0, 0.6],
            [1.0, 1.0, 1.0, 1.4, 1.0, 1.0, 1.0, 1.0],
        ]
    )
    reps = 2
    base = 4242
    warp_factory = lambda sc, seed=0: WarpKernel(sc, seed, device="cpu")  # noqa: E731
    y_single = sens._evaluate_points(scenario, X, reps, base, warp_factory)
    y_batch = sens._evaluate_points_batch(
        scenario, X, reps, base, device="cpu", batch_size=4
    )
    assert y_single.shape == y_batch.shape
    assert np.allclose(y_single, y_batch, atol=1e-9)


@cuda_required
def test_cuda_graph_replay_matches_direct_launch(scenario):
    """CUDA 图重放与直接启动同种子必须逐位一致（tick 数组化、图固化参数的硬保证）。"""
    base = 9300
    E = 4
    direct = run_batch(scenario, [base + e for e in range(E)],
                       device="cuda", use_graph=False)
    graph = run_batch(scenario, [base + e for e in range(E)],
                      device="cuda", use_graph=True)
    for e in range(E):
        assert graph[e]["success"] == direct[e]["success"]
        for key in ("attrition_red", "attrition_blue", "readiness_red", "readiness_blue"):
            assert abs(graph[e][key] - direct[e][key]) < 1e-9, (e, key)


@cuda_required
def test_cuda_graph_per_env_matches_direct(scenario):
    """per-env 参数批量（灵敏度路径）下图重放与直接启动逐位一致。"""
    X = np.array(
        [
            # red_pk, blue_pk, red_det, blue_det, red_spd, blue_spd, mtbf, vis
            [1.2, 0.8, 1.1, 0.9, 1.0, 1.0, 0.5, 0.9],
            [0.7, 1.3, 0.9, 1.1, 1.0, 1.0, 2.0, 0.6],
            [1.0, 1.0, 1.0, 1.4, 1.0, 1.0, 1.0, 1.0],
        ]
    )
    reps = 2
    base = 4242
    y_direct = _batch_points_cuda(scenario, X, reps, base, use_graph=False)
    y_graph = _batch_points_cuda(scenario, X, reps, base, use_graph=True)
    assert np.allclose(y_graph, y_direct, atol=1e-9)


def _batch_points_cuda(scenario, X, reps, base, use_graph):
    """与 sensitivity._evaluate_points_batch 同结构，但可切换图路径。"""
    from brigsim.experiment import sensitivity as sens

    P = X.shape[0]
    seeds = [base + r for r in range(reps)]
    scs = [sens.apply_point(scenario, X[i // reps]) for i in range(P * reps)]
    sd = [seeds[i % reps] for i in range(P * reps)]
    rows = run_batch(scs, sd, device="cuda", use_graph=use_graph)
    V = np.array([[m[name] for name in sens.METRICS] for m in rows])
    return V.reshape(P, reps, len(sens.METRICS)).mean(axis=1)
