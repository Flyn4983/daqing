"""C3 四率代理模型测试（统一走 Mesa + example_company，无 GPU 可复现）。

覆盖：特征空间采样与施加、批量数据集列结构、MLP 训练/预测/物理界、
模型存取一致；另在有 CUDA 时验证批量端暴露累计→故障率键。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from brigsim.data.provider import YamlFileProvider
from brigsim.surrogate.dataset import build_dataset, evaluate_points
from brigsim.surrogate.design import (
    FEATURE_NAMES,
    SPACE,
    TARGETS,
    apply_features,
    sample_lhs,
)
from brigsim.surrogate.model import SurrogateModel


def test_feature_space_sampling():
    X = sample_lhs(32, seed=7)
    assert X.shape == (32, SPACE.n_dim) == (32, len(FEATURE_NAMES))
    for j, (_, lo, hi, is_int) in enumerate(SPACE.features):
        assert X[:, j].min() >= lo and X[:, j].max() <= hi
        if is_int:
            assert np.allclose(X[:, j], np.rint(X[:, j]))


def test_apply_features_old_scenario_zero_drift():
    """旧想定 example_company 无战损分级/保障实体结构：只改抢修组与乘子，
    a2 口径的档级/波次/实体旋钮全部条件跳过（零漂移）。"""
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    x = np.array([5, 3, 2, 2, 2, 20, 2, 1.5, 1.1, 0.9])
    sc2 = apply_features(sc, x)
    # 红方抢修组被特征覆盖；蓝方保障不再是决策变量，沿用想定值（company=1）
    assert sc2.sustainment.repair_teams == {"red": 5, "blue": 1}
    assert sc2.sustainment.spare_stock is None
    assert sc2.sustainment.support_echelon is None
    # 情景乘子：MTBF 累乘 1.5，红蓝 pk 分别乘 1.1/0.9
    assert sc2.platform_types["tank"].mtbf_ticks == pytest.approx(
        sc.platform_types["tank"].mtbf_ticks * 1.5
    )
    assert sc2.side_modifiers["red"].pk_scale == pytest.approx(1.1)
    assert sc2.side_modifiers["blue"].pk_scale == pytest.approx(0.9)
    # 旧想定无 rarv/rsup 保障编组，实体旋钮不动 forces
    assert len(sc2.forces.get("red", [])) == len(sc.forces.get("red", []))
    # 原想定不被修改
    assert sc.sustainment.repair_teams["red"] != 5
    assert sc2 is not sc


def test_apply_features_brigade_levels_and_entities():
    """合成兵种想定：档级/波次覆盖红方部件配置，抢救车/器材车改编制实体数。"""
    sc = YamlFileProvider("scenarios").load_scenario("example_brigade")
    from brigsim.surrogate.design import CARRY_LEVELS, OPERATING_LEVELS

    n_red_base = len(sc.forces["red"])
    # 角点：最大档级与实体扩编
    x_hi = np.array([10, 6, 3, 4, 3, 10, 4, 1.0, 1.0, 1.0])
    sc_hi = apply_features(sc, x_hi)
    assert sc_hi.sustainment.part_stock["red"] == CARRY_LEVELS[4]
    ech = sc_hi.sustainment.support_echelon
    assert ech.operating_stock["red"] == OPERATING_LEVELS[3]
    assert ech.wave_interval_ticks == 10 and ech.wave_qty == 4
    assert len(sc_hi.forces["red"]) > n_red_base  # 扩编计入参战总数
    # 基线档级 2
    x_base = np.array([6, 4, 2, 2, 2, 20, 2, 1.0, 1.0, 1.0])
    sc_base = apply_features(sc, x_base)
    assert sc_base.sustainment.part_stock["red"] == CARRY_LEVELS[2]
    assert len(sc_base.forces["red"]) == n_red_base


@pytest.fixture(scope="module")
def small_dataset():
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    return sc, build_dataset(
        sc, n_train=16, n_holdout=6, reps_train=2, reps_holdout=2,
        base_seed=20261014, device=None,
    )


def test_dataset_columns(small_dataset):
    _, df = small_dataset
    assert set(df["split"]) == {"train", "holdout"}
    for name in FEATURE_NAMES:
        assert name in df.columns
    for name in TARGETS:
        assert name in df.columns and f"{name}_std" in df.columns
    assert len(df) == 22


def test_surrogate_fit_predict_bounds(small_dataset):
    _, df = small_dataset
    train = df[df["split"] == "train"]
    hold = df[df["split"] == "holdout"]

    model = SurrogateModel().fit(train[FEATURE_NAMES], train[TARGETS])
    pred = model.predict(hold[FEATURE_NAMES])
    assert list(pred.columns) == TARGETS
    assert np.isfinite(pred.to_numpy()).all()
    # 物理界：概率目标 [0,1]，故障率非负
    prob_cols = [t for t in TARGETS if not t.startswith("failure_rate_100h")]
    assert ((pred[prob_cols] >= 0.0) & (pred[prob_cols] <= 1.0)).all().all()
    assert (pred[["failure_rate_100h_red", "failure_rate_100h_blue"]] >= 0).all().all()


def test_mlp_pipeline_learns_smooth_surface():
    """学习能力验证（无噪声合成响应面，隔离仿真噪声）：标准化/网络/逆变换
    管线正确时，128 点对平滑面的训练 R² 应接近 1。"""
    rng = np.random.default_rng(0)
    X = pd.DataFrame(sample_lhs(128, seed=11), columns=FEATURE_NAMES)
    z = (X.to_numpy() - X.to_numpy().mean(0)) / (X.to_numpy().std(0) + 1e-9)
    Y = pd.DataFrame()
    for j, name in enumerate(TARGETS):
        w = rng.normal(size=z.shape[1])
        raw = z @ w / np.sqrt(z.shape[1])
        if name.startswith("failure_rate_100h"):
            Y[name] = 5.0 + 40.0 / (1.0 + np.exp(-raw))  # 5-45 的平滑面
        else:
            Y[name] = 1.0 / (1.0 + np.exp(-(raw * 1.5 + 0.2 * (j - 4))))
    model = SurrogateModel().fit(X, Y)
    report = model.evaluate(Y, model.predict(X))
    assert (report["r2"] > 0.9).all(), report[["target", "r2"]]


def test_model_save_load_roundtrip(tmp_path, small_dataset):
    _, df = small_dataset
    train = df[df["split"] == "train"]
    model = SurrogateModel().fit(train[FEATURE_NAMES], train[TARGETS])
    path = tmp_path / "surrogate_test.joblib"
    model.save(str(path))
    loaded = SurrogateModel.load(str(path))
    p1 = model.predict(train[FEATURE_NAMES])
    p2 = loaded.predict(train[FEATURE_NAMES].to_numpy())
    assert np.allclose(p1.to_numpy(), p2.to_numpy(), atol=1e-10)
    assert loaded.feature_names == FEATURE_NAMES
    assert loaded.target_names == TARGETS


def test_evaluate_points_mesa_keys():
    """Mesa 路径：评估点输出矩阵列序与 TARGETS 对齐且值有限。"""
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    X = sample_lhs(2, seed=3)
    means, stds, _ = evaluate_points(sc, X, reps=2, device=None)
    assert means.shape == (2, len(TARGETS))
    assert np.isfinite(means).all()
    assert (means >= 0).all()


def test_warp_batch_exposure_failure_rate():
    """有 CUDA 时：批量端暴露累计生效，rows 直接带 failure_rate_100h 键。"""
    warp = pytest.importorskip("warp")
    try:
        if not warp.get_device("cuda").is_cuda:
            pytest.skip("无 CUDA 设备")
    except Exception:
        pytest.skip("无 CUDA 设备")
    from brigsim.combat.warp_batch import run_batch

    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    rows = run_batch(sc, [20261014], device="cuda:0")
    assert "failure_rate_100h_red" in rows[0]
    assert rows[0]["failure_rate_100h_red"] >= 0
