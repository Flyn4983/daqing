"""地形高程模型：FBM 噪声或 GeoTIFF DEM 生成高度场，供内核机动与可视化共用。

- synthetic：OpenSimplex FBM 程序地形；
- geotiff：rasterio 读取 DEM 首波段，由 GDAL 双线性重采样到 grid_n，
  像元阵列线性拉伸到 size_m（粗版暂不使用地理变换/CRS，真实测绘数据
  预先裁剪为方形战区即可；后续接坐标变换与通行性栅格时接口保持不变）。
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
        passability: np.ndarray | None = None,
    ) -> None:
        self.size = size
        self.n = n
        self.heights = heights  # (n, n)，行=y，列=x
        self.water_level = water_level
        # 通行性系数（n,n），1=道路/硬地，越小越难走（水面/林地/沼泽）
        self.passability = passability if passability is not None else np.ones((n, n))

    @classmethod
    def from_spec(cls, spec: Terrain) -> "HeightField":
        if spec.source == "geotiff":
            if not spec.geotiff_path:
                raise ValueError("terrain.source=geotiff 时必须提供 geotiff_path")
            return cls.from_geotiff(
                spec.geotiff_path,
                size=spec.size_m,
                grid_n=spec.grid_n,
                water_level=spec.water_level,
                passability_path=spec.passability_path,
            )
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
        # C7-b：程序合成地形可以内联 passability_array（Terrain schema 新增）
        passability: np.ndarray | None = None
        if spec.passability_array:
            pa = np.asarray(spec.passability_array, dtype=np.float64)
            if pa.shape == (n, n):
                passability = pa
        return cls(spec.size_m, n, heights, spec.water_level, passability)

    @classmethod
    def from_geotiff(
        cls,
        path: str,
        size: tuple[float, float] = (10_000.0, 10_000.0),
        grid_n: int = 65,
        water_level: float = 10.0,
        passability_path: str | None = None,
    ) -> "HeightField":
        """读取单波段 DEM（可选通行性栅格）：GDAL 双线性重采样到 (grid_n, grid_n)。"""
        import rasterio
        from rasterio.enums import Resampling

        with rasterio.open(path) as ds:
            band = ds.read(
                1, out_shape=(grid_n, grid_n), resampling=Resampling.bilinear
            ).astype(np.float64)
        if not np.isfinite(band).all():
            band = np.nan_to_num(band, nan=water_level, neginf=water_level)

        passability = None
        if passability_path:
            with rasterio.open(passability_path) as ds:
                passability = ds.read(
                    1, out_shape=(grid_n, grid_n), resampling=Resampling.bilinear
                ).astype(np.float64)
            passability = np.clip(np.nan_to_num(passability, nan=1.0), 0.0, 1.0)
        return cls(size, grid_n, band, water_level, passability)

    def _ij(self, x: np.ndarray | float, y: np.ndarray | float):
        fx = np.clip(np.asarray(x) / self.size[0], 0.0, 1.0) * (self.n - 1)
        fy = np.clip(np.asarray(y) / self.size[1], 0.0, 1.0) * (self.n - 1)
        return fx, fy

    def _bilinear(self, grid: np.ndarray, x, y) -> np.ndarray:
        fx, fy = self._ij(x, y)
        i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
        i1, j1 = np.minimum(i0 + 1, self.n - 1), np.minimum(j0 + 1, self.n - 1)
        tx, ty = fx - i0, fy - j0
        v00 = grid[j0, i0]
        v10 = grid[j0, i1]
        v01 = grid[j1, i0]
        v11 = grid[j1, i1]
        return (1 - tx) * (1 - ty) * v00 + tx * (1 - ty) * v10 + (1 - tx) * ty * v01 + tx * ty * v11

    def height_at(self, x: np.ndarray | float, y: np.ndarray | float) -> np.ndarray:
        """双线性插值高程（米）。"""
        return self._bilinear(self.heights, x, y)

    def has_los(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        clearance_m: float = 3.0,
    ) -> bool:
        """两点间高程通视判定（直瞄射击与地面雷达使用）。

        沿连线按一个栅格步长采样，任一中间点地表高程超过视线（两端地表高度线性插值）
        加 clearance_m 即判定遮挡。空中平台不做地形遮挡（俯瞰）。
        """
        dist = float(np.hypot(x2 - x1, y2 - y1))
        cell = self.size[0] / self.n
        n_steps = max(2, int(np.ceil(dist / cell)))
        if n_steps <= 1:
            return True
        ts = np.linspace(0.0, 1.0, n_steps + 1)[1:-1]
        xs = x1 + (x2 - x1) * ts
        ys = y1 + (y2 - y1) * ts
        ground = self._bilinear(self.heights, xs, ys)
        h1 = float(self.height_at(x1, y1))
        h2 = float(self.height_at(x2, y2))
        sightline = h1 + (h2 - h1) * ts + clearance_m
        return bool(np.all(ground <= sightline))

    def move_factor(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """机动修正系数：坡度 + 水面 + 通行性栅格（道路/河流/植被）。粗略经验式，后标定。"""
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
        # 通行性栅格：道路 1.0、一般地表 <1、无桥河面由栅格给低值（强制依赖渡场）
        factor = factor * self._bilinear(self.passability, x, y)
        return np.clip(factor, 0.05, 1.0)
