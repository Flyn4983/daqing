"""地形高程模型：FBM 噪声生成高度场，供内核机动修正与可视化 Mesh3D 共用。

M1.5 轮：仅影响机动速度（坡度/地表粗糙度）；M3 接真实 GeoTIFF 高程、
通行性栅格与视线遮挡时，保持本类接口不变。
"""

from __future__ import annotations

import numpy as np
from opensimplex import OpenSimplex

from ..data.schema import Terrain


class HeightField:
    def __init__(
        self,
        size: tuple[float, float],
        n: int,
        heights: np.ndarray,
        water_level: float,
    ) -> None:
        self.size = size
        self.n = n
        self.heights = heights  # (n, n)，行=y，列=x
        self.water_level = water_level

    @classmethod
    def from_spec(cls, spec: Terrain) -> "HeightField":
        n = spec.grid_n
        noise = OpenSimplex(seed=spec.noise_seed)
        # noise2array(x, y) 返回 shape=(y.size, x.size)，行=y、列=x，即高度场布局
        xs = np.linspace(0.0, spec.noise_scale, n, endpoint=False)
        ys = np.linspace(0.0, spec.noise_scale, n, endpoint=False)
        h = np.zeros((n, n))
        amp, freq, total = 1.0, 1.0, 0.0
        for _ in range(spec.octaves):
            h += amp * np.asarray(noise.noise2array(xs * freq, ys * freq))
            total += amp
            amp *= 0.5
            freq *= 2.0
        h = h / total  # [-1, 1]
        heights = spec.base_height + h * spec.amplitude
        return cls(spec.size_m, n, heights, spec.water_level)

    def _ij(self, x: np.ndarray | float, y: np.ndarray | float):
        fx = np.clip(np.asarray(x) / self.size[0], 0.0, 1.0) * (self.n - 1)
        fy = np.clip(np.asarray(y) / self.size[1], 0.0, 1.0) * (self.n - 1)
        return fx, fy

    def height_at(self, x: np.ndarray | float, y: np.ndarray | float) -> np.ndarray:
        """双线性插值高程（米）。"""
        fx, fy = self._ij(x, y)
        i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
        i1, j1 = np.minimum(i0 + 1, self.n - 1), np.minimum(j0 + 1, self.n - 1)
        tx, ty = fx - i0, fy - j0
        h = self.heights
        v00 = h[j0, i0]
        v10 = h[j0, i1]
        v01 = h[j1, i0]
        v11 = h[j1, i1]
        return (1 - tx) * (1 - ty) * v00 + tx * (1 - ty) * v10 + (1 - tx) * ty * v01 + tx * ty * v11

    def move_factor(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """机动修正系数：坡度越陡、水面越慢。粗略经验式，后续标定。"""
        cell = self.size[0] / self.n
        hx = (self.height_at(np.asarray(x) + cell, y) - self.height_at(np.asarray(x) - cell, y)) / (
            2 * cell
        )
        hy = (self.height_at(x, np.asarray(y) + cell) - self.height_at(x, np.asarray(y) - cell)) / (
            2 * cell
        )
        slope = np.hypot(hx, hy)
        factor = np.clip(1.0 - 1.6 * slope, 0.25, 1.0)
        wet = self.height_at(x, y) < self.water_level
        factor = np.where(wet, factor * 0.4, factor)
        return factor
