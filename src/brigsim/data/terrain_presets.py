"""C7-b：8 地形族 preset 工厂（地形+通行性，全部程序生成，无需真实 DEM）。

不手写 8 个 YAML 片段，而是在这里按族给出 FBM 参数 + water_level +
passability 结构覆盖（城镇建筑足迹、水网稻田格网、山地河沟条带、
沙漠/雪原仅装饰通行仍 1.0），Scenario.terrain 直接拿到一个合法
Terrain spec；HeightField.from_spec 会自动派到 synthetic 路径并
按 passability 字段注入栅格。

8 族对应计划文档 C1：
  canyon（峡谷走廊）/ plain（开阔平原）/ rolling（起伏丘陵）
  / mountain（山地通道）/ paddy（水网稻田）/ urban（城镇街区）
  / desert（沙漠戈壁）/ snowfield（雪原）

暴露 preset(enum_or_str, size_m, grid_n, seed) → Terrain；
同时给出 default_environment/side_modifiers/obstacles 供想定
拼装时一键取默认配置（工况 YAML 里只需给出 override 项）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np

from .schema import Terrain


class TerrainFamily(str, Enum):
    canyon = "canyon"          # 峡谷走廊：深切 FBM + 窄河沟 + 两侧山体遮蔽
    plain = "plain"            # 开阔平原：平缓 FBM + 河渠带
    rolling = "rolling"        # 起伏丘陵：中等 FBM + 小型山脊（雷达盲区多）
    mountain = "mountain"      # 山地通道：强 FBM + 深河谷 + 少通行走廊
    paddy = "paddy"            # 水网稻田：低平 FBM + 大面积 0.2 通行格网 + 田埂 0.8
    urban = "urban"            # 城镇街区：高平 FBM + 建筑足迹 0.3 通行 + 街道 1.0
    desert = "desert"          # 沙漠戈壁：低平 FBM + 全 1.0 通行（无遮蔽无障碍）
    snowfield = "snowfield"    # 雪原：低平 FBM + 轻微 0.9 通行（积雪不挡）


# ---------------------------------------------------------------------------
# 程序化 passability 生成
# ---------------------------------------------------------------------------


def _make_passability(
    family: TerrainFamily,
    grid_n: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """返回 (n, n) 通行性栅格；n=grid_n，约定 x 行=y 列（与 HeightField 一致）。"""
    n = grid_n
    # 默认全道路（1.0）；以下按族结构覆盖
    p = np.ones((n, n), dtype=np.float64)

    if family is TerrainFamily.plain:
        # 横向两条河沟带（通行 0.3），宽度 2-3 栅格
        for y0 in (n // 4, 3 * n // 4):
            width = max(2, int(round(rng.integers(2, 4))))
            y_idx = np.clip(np.arange(y0 - width, y0 + width), 0, n - 1)
            p[y_idx] = 0.3

    elif family is TerrainFamily.canyon:
        # 中间一条深切走廊（全 1.0），两侧山体（0.7），边缘绝壁（0.4）
        x = np.linspace(-1.0, 1.0, n)
        corridor = np.clip(1.0 - 5.0 * np.abs(x), 0.0, 1.0)
        edge = np.clip(5.0 * np.abs(x) - 4.0, 0.0, 0.6)
        p = np.ones_like(p) * 0.7 + np.outer(np.ones(n), corridor) * 0.3
        p -= np.outer(np.ones(n), edge) * 0.3
        p = np.clip(p, 0.4, 1.0)
        # 沿走廊中心线一条窄河沟 0.2
        y0 = n // 2
        p[y0 - 1: y0 + 1] = 0.2

    elif family is TerrainFamily.rolling:
        # 一条主山脊（斜线条带，遮挡通视，通行 0.6）+ 小型溪流（0.5）
        diag = np.zeros_like(p)
        for i in range(n):
            j = int(round(0.5 * i + n * 0.3))
            if 0 <= j < n:
                diag[max(0, j - 1):min(n, j + 2), i] = 1.0
        p = np.where(diag > 0.5, 0.6, p)
        # 两条小溪
        for j0 in (n // 3, 2 * n // 3):
            p[j0:j0 + 1] = 0.5

    elif family is TerrainFamily.mountain:
        # 主山脊多条平行（通行 0.4），一条唯一通道（1.0）+ 通道两侧坡地（0.7）
        p[:] = 0.4
        # 通道：中间 20% 列 全 1
        lo = n // 5
        hi = n - n // 5
        p[:, lo:hi] = 0.7
        # 通道核心 5 列（沿走廊中心线，略有弯曲）
        cx = n // 2
        for i in range(n):
            bend = int(round(2.0 * np.sin(6.0 * i / n)))
            c = cx + bend
            if 0 <= c < n:
                p[i, max(0, c - 2):min(n, c + 3)] = 1.0

    elif family is TerrainFamily.paddy:
        # 田埂格网：每 ~6 格一个田块，内部水田 0.2，田埂 0.8；田块间田埂横竖线
        block = max(4, n // 10)
        # 先全田块 0.2
        p[:] = 0.2
        # 横向田埂
        p[::block, :] = 0.8
        # 纵向田埂
        p[:, ::block] = 0.8
        # 一条主道路（横向贯穿）1.0
        p[n // 2: n // 2 + 1, :] = 1.0
        # 田块中心偶尔有 0.0 水湾（随机 ~3% 格）
        rng.random((n, n), dtype=np.float64)
        mask = rng.random((n, n)) < 0.03
        p = np.where(mask & (p < 0.5), 0.0, p)
        p = np.clip(p, 0.0, 1.0)

    elif family is TerrainFamily.urban:
        # 街道网：~200m 一个街区，街道 1.0，建筑足迹 0.3；建筑物随机 ~60% 格
        block = max(3, n // 12)
        p[:] = 1.0
        # 建筑块：每个 block 内随机 ~60% 格建筑
        for by in range(0, n - block, block):
            for bx in range(0, n - block, block):
                sub = rng.random((block, block))
                p[by:by + block, bx:bx + block] = np.where(sub < 0.6, 0.3, 1.0)
        # 横纵街道强制保持 1.0
        p[::block, :] = 1.0
        p[:, ::block] = 1.0

    elif family is TerrainFamily.desert:
        # 通行 1.0，沙尘不影响机动（仅雷达/光电衰减由 Environment 处理）
        pass  # 全 1.0

    elif family is TerrainFamily.snowfield:
        # 积雪略微减慢（0.9），河沟（0.3，冻结）
        p[:] = 0.9
        for y0 in (n // 3, 2 * n // 3):
            p[y0 - 1:y0 + 1] = 0.3

    return np.clip(p, 0.0, 1.0)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


_FBM = {
    # family → (amplitude, base_height, octaves, noise_scale, water_level)
    TerrainFamily.canyon:   (160.0, 40.0, 5, 4.0, -10.0),   # 深切 FBM
    TerrainFamily.plain:    (40.0,  50.0, 4, 3.0,   5.0),   # 平缓
    TerrainFamily.rolling:  (90.0,  60.0, 5, 3.0,  10.0),   # 中等起伏
    TerrainFamily.mountain: (220.0,  80.0, 5, 5.0, -30.0),  # 高山
    TerrainFamily.paddy:    (15.0,  40.0, 3, 2.0,  35.0),   # 低平，水面较高
    TerrainFamily.urban:    (10.0,  45.0, 2, 1.5,  20.0),   # 极平缓（人工平整）
    TerrainFamily.desert:   (20.0, 120.0, 3, 2.5,  -5.0),   # 低平戈壁
    TerrainFamily.snowfield:(50.0,  20.0, 4, 3.0,   0.0),   # 雪原
}


def preset(
    family: TerrainFamily | str,
    size_m: float | tuple[float, float] = 10_000.0,
    grid_n: int = 65,
    seed: int = 20261016,
) -> Terrain:
    """按族生成一个 synthetic Terrain spec（含程序 passability）。"""
    fam = family if isinstance(family, TerrainFamily) else TerrainFamily(family)
    amp, base, octv, scale, wl = _FBM[fam]
    size = (size_m, size_m) if isinstance(size_m, (int, float)) else size_m
    rng = np.random.default_rng(seed)
    passability = _make_passability(fam, grid_n, rng)
    return Terrain(
        source="synthetic",
        size_m=size,
        grid_n=grid_n,
        noise_seed=seed,
        noise_scale=scale,
        octaves=octv,
        base_height=base,
        amplitude=amp,
        water_level=wl,
        passability_array=passability.tolist(),
    )


def passability(
    family: TerrainFamily | str,
    grid_n: int = 65,
    seed: int = 20261016,
) -> np.ndarray:
    """独立导出 passability 栅格（给 HeightField 构造/单测用）。"""
    fam = family if isinstance(family, TerrainFamily) else TerrainFamily(family)
    rng = np.random.default_rng(seed)
    return _make_passability(fam, grid_n, rng)


def default_environment(family: TerrainFamily | str, offensive: bool = True) -> dict[str, Any]:
    """该族默认环境（晴日；可由 override 改雾/夜）。"""
    fam = family if isinstance(family, TerrainFamily) else TerrainFamily(family)
    env: dict[str, Any] = {"weather": "clear", "illumination": "day",
                            "visibility_factor": 1.0, "wind_speed_mps": 3.0}
    # 山地/峡谷/水网 默认稍减能见度（地形遮蔽而非气象）
    if fam in (TerrainFamily.mountain, TerrainFamily.canyon):
        env["visibility_factor"] = 0.85
    return env


__all__ = ["TerrainFamily", "preset", "passability", "default_environment"]
