"""C3 轻量代理模型：标准化 + 两层 MLP（scikit-learn）。

选型（开源优先，不重复造轮子）：scikit-learn MLPRegressor——
- 隐层 64×64 全连接（约 4.5 千参数），单机 CPU 训练秒级~十几秒、
  预测单批万点毫秒级，满足"秒级四率预测"且可嵌入 C4 NSGA-II 内层
  （优化需对成千上万个候选保障方案评估目标函数）；
- 目标做标准化（故障率数值量级 0-100，与概率目标 0-1 不同），预测后
  逆变换并按物理界 clip；
- 多目标共享同一网络隐层（相关性共享表示），输出 9 维。

模型持久化为单个 joblib bundle（含特征/目标名与训练元数据），
供 C4 优化、C5 大模型工具调用、C6 仪表盘直接 load 使用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .design import FEATURE_NAMES, TARGETS, TARGET_BOUNDS

MODEL_FORMAT = "brigsim-surrogate/0.1"


@dataclass
class TrainMetadata:
    scenario: str
    n_train: int
    n_holdout: int
    reps_train: int
    reps_holdout: int
    train_seconds: float
    sim_wall_seconds: float | None = None  # 数据集生成总推演耗时（速度对比用）
    notes: str = ""


class SurrogateModel:
    """四率代理模型：特征向量（保障旋钮+情景因子）→ 四率+任务完成率。"""

    def __init__(self, hidden=(64, 64), alpha: float = 1e-4, seed: int = 20261014) -> None:
        from sklearn.neural_network import MLPRegressor
        from sklearn.preprocessing import StandardScaler

        self.feature_names = list(FEATURE_NAMES)
        self.target_names = list(TARGETS)
        self.x_scaler = StandardScaler()
        self.y_scaler = StandardScaler()
        self.mlp = MLPRegressor(
            hidden_layer_sizes=tuple(hidden),
            activation="relu",
            solver="adam",
            alpha=alpha,
            learning_rate_init=1e-3,
            max_iter=3000,
            early_stopping=True,
            n_iter_no_change=30,
            validation_fraction=0.1,
            random_state=seed,
        )
        self.metadata: TrainMetadata | None = None

    # ---- 训练 / 预测 ----
    def fit(self, X: pd.DataFrame, y: pd.DataFrame, metadata: TrainMetadata | None = None) -> "SurrogateModel":
        Xs = self.x_scaler.fit_transform(X[self.feature_names].to_numpy(float))
        ys = self.y_scaler.fit_transform(y[self.target_names].to_numpy(float))
        self.mlp.fit(Xs, ys)
        self.metadata = metadata
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> pd.DataFrame:
        arr = X[self.feature_names].to_numpy(float) if isinstance(X, pd.DataFrame) else np.asarray(X, float)
        pred = self.mlp.predict(self.x_scaler.transform(arr))
        # MLP 单样本时 predict 返回一维
        if pred.ndim == 1:
            pred = pred[None, :]
        out = self.y_scaler.inverse_transform(pred)
        out = self._clip(out)
        return pd.DataFrame(out, columns=self.target_names)

    def _clip(self, arr: np.ndarray) -> np.ndarray:
        for j, name in enumerate(self.target_names):
            lo, hi = TARGET_BOUNDS[name]
            if lo is not None:
                np.maximum(arr[:, j], lo, out=arr[:, j])
            if hi is not None:
                np.minimum(arr[:, j], hi, out=arr[:, j])
        return arr

    # ---- 评估 ----
    def evaluate(self, y_true: pd.DataFrame, y_pred: pd.DataFrame) -> pd.DataFrame:
        """逐目标 R²/MAE/RMSE + 标签噪声地板（reps 复制均值的标准差均值）。"""
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

        rows = []
        for name in self.target_names:
            yt = y_true[name].to_numpy(float)
            yp = y_pred[name].to_numpy(float)
            noise = y_true.get(f"{name}_std")
            rows.append(
                {
                    "target": name,
                    "r2": float(r2_score(yt, yp)),
                    "mae": float(mean_absolute_error(yt, yp)),
                    "rmse": float(np.sqrt(mean_squared_error(yt, yp))),
                    "label_noise_mean": float(noise.mean()) if noise is not None else np.nan,
                }
            )
        return pd.DataFrame(rows)

    def predict_benchmark(self, n_points: int = 10000, seed: int = 1) -> tuple[float, int]:
        """预测 n_points 个随机点，返回 (墙钟秒, 点数)；供'秒级预测'取证。"""
        rng = np.random.default_rng(seed)
        lo = self.x_scaler.mean_ - 2 * self.x_scaler.scale_
        hi = self.x_scaler.mean_ + 2 * self.x_scaler.scale_
        X = rng.uniform(lo, hi, size=(n_points, len(self.feature_names)))
        t0 = time.perf_counter()
        _ = self.mlp.predict(X)
        return time.perf_counter() - t0, n_points

    # ---- 持久化 ----
    def save(self, path: str) -> None:
        import joblib

        joblib.dump(
            {
                "format": MODEL_FORMAT,
                "feature_names": self.feature_names,
                "target_names": self.target_names,
                "x_scaler": self.x_scaler,
                "y_scaler": self.y_scaler,
                "mlp": self.mlp,
                "metadata": None
                if self.metadata is None
                else self.metadata.__dict__,
            },
            path,
            compress=3,
        )

    @classmethod
    def load(cls, path: str) -> "SurrogateModel":
        import joblib

        bundle = joblib.load(path)
        if bundle.get("format") != MODEL_FORMAT:
            raise ValueError(f"模型格式不匹配：{bundle.get('format')}（期望 {MODEL_FORMAT}）")
        m = cls()
        m.feature_names = bundle["feature_names"]
        m.target_names = bundle["target_names"]
        m.x_scaler = bundle["x_scaler"]
        m.y_scaler = bundle["y_scaler"]
        m.mlp = bundle["mlp"]
        md = bundle.get("metadata")
        m.metadata = None if md is None else TrainMetadata(**md)
        return m
