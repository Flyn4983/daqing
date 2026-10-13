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
    ) -> None:
        self.size = size
        self.n = n
        self.heights = heights  # (n, n)，行=y，列=x
        self.water_level = water_level

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
        return cls(spec.size_m, n, heights, spec.water_level)

    @classmethod
    def from_geotiff(
        cls,
        path: str,
        size: tuple[float, float] = (10_000.0, 10_000.0),
        grid_n: int = 65,
        water_level: float = 10.0,
    ) -> "HeightField":
        """读取单波段 DEM：GDAL 双线性重采样到 (grid_n, grid_n)，NaN 填水域高度。"""
        import rasterio
        from rasterio.enums import Resampling

        with rasterio.open(path) as ds:
            band = ds.read(1, out_shape=(grid_n, grid_n), resampling=Resampling.bilinear).astype(
                np.float64
            )
        if not np.isfinite(band).all():
            band = np.nan_to_num(band, nan=water_level, neginf=water_level)
        return cls(size, grid_n, band, water_level)

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
