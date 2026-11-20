"""采集中国真实地形 DEM（AWS Terrarium / SRTM 派生）并生成项目可用栅格。

数据源：AWS Open Data 'elevation-tiles-prod' Terrarium 瓦片（SRTM/ASTER GDEM 派生）
  https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
Terrarium 编码： elevation = R*256 + G + B/256 - 32768

输出：
  scenarios/terrain/dem_real_taihang.tif   高程（米，float32）
  scenarios/terrain/pass_real_taihang.tif  通行性（0~1，基于真实坡度+河谷走廊）
  scenarios/terrain/REAL_TERRAIN_META.json 采集元数据（经纬度/zoom/数据源/时间）

注意：本项目内核 from_geotiff 忽略 CRS，直接把波段数组线性拉伸到 size_m。
      故输出为 385x385 的 float32 栅格，与既有 dem_demo.tif 形态一致。

核实日期：2026-11-20
原型阶段按用户授权暂不考虑版权；Terrarium 数据源为 AWS Open Data 公共数据。
"""
from __future__ import annotations

import io
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "scenarios" / "terrain"

# 太行山中段（山西-河北交界，华北典型山地丘陵），10km 战区中心
# 实测高程 883~1742m、起伏 859m（SRTM 派生 Terrarium），显著山地地形
CENTER_LAT = 37.00
CENTER_LON = 113.50
ZOOM = 12          # ~30m/px，接近 SRTM 原始精度
N_OUT = 385        # 与既有 dem_demo.tif 一致
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def decode_terrarium(rgba: np.ndarray) -> np.ndarray:
    a = rgba.astype(np.float64)
    return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0


def fetch_tile(z: int, x: int, y: int) -> np.ndarray:
    url = TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": "brigsim-prototype/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = resp.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    return decode_terrarium(np.asarray(img))


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fx, fy = lonlat_to_tile(CENTER_LON, CENTER_LAT, ZOOM)
    # 拼接 3x3 瓦片，从中心瓦片向四周扩展
    tx0, ty0 = int(fx) - 1, int(fy) - 1
    mosaic = np.full((3 * 256, 3 * 256), np.nan, dtype=np.float64)
    print(f"[DEM] center=({CENTER_LAT},{CENTER_LON}) z={ZOOM} frac=({fx:.3f},{fy:.3f}) tile0=({tx0},{ty0})")
    for dy in range(3):
        for dx in range(3):
            try:
                tile = fetch_tile(ZOOM, tx0 + dx, ty0 + dy)
                mosaic[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = tile
                print(f"  tile ({tx0+dx},{ty0+dy}) ok  elev {tile.min():.0f}~{tile.max():.0f}m")
            except Exception as e:  # noqa: BLE001
                print(f"  tile ({tx0+dx},{ty0+dy}) FAIL {e}", file=sys.stderr)

    if not np.isfinite(mosaic).any():
        print("[DEM] 无有效瓦片", file=sys.stderr)
        return 1

    # NaN 填为邻域均值（边缘瓦片失败时）
    if not np.isfinite(mosaic).all():
        from scipy.ndimage import distance_transform_edt
        _, idx = distance_transform_edt(~np.isfinite(mosaic), return_indices=True)
        mosaic = mosaic[tuple(idx)]

    # 以中心像素裁剪 N_OUT x N_OUT（约 11.6km，内核拉伸到 10km）
    cx = int((fx - tx0) * 256)
    cy = int((fy - ty0) * 256)
    half = N_OUT // 2
    y0, x0 = cy - half, cx - half
    crop = mosaic[y0:y0 + N_OUT, x0:x0 + N_OUT]
    if crop.shape != (N_OUT, N_OUT):
        pad_y = N_OUT - crop.shape[0]
        pad_x = N_OUT - crop.shape[1]
        crop = np.pad(crop, ((max(0, pad_y // 2), max(0, pad_y - pad_y // 2)),
                             (max(0, pad_x // 2), max(0, pad_x - pad_x // 2))), mode="edge")
        crop = crop[:N_OUT, :N_OUT]

    elev = crop.astype(np.float32)

    # 通行性：基于真实坡度（缓坡=1，陡坡低）+ 一条沿最低高程的河谷走廊
    dzdy, dzdx = np.gradient(elev.astype(np.float64))
    slope = np.sqrt(dzdx ** 2 + dzdy ** 2)  # 米/像素
    passab = np.clip(1.0 - slope / 45.0, 0.15, 1.0)  # 坡度>45m/px 视为难行
    # 河谷走廊：低于 25 分位的高程带抬升通行性（真实河谷公路/平缓谷地）
    valley_thr = np.percentile(elev, 25)
    valley = elev < valley_thr + 12.0
    from scipy.ndimage import binary_dilation
    valley = binary_dilation(valley, iterations=2)
    passab[valley] = np.maximum(passab[valley], 0.9)
    passab = passab.astype(np.float32)

    # 写 GeoTIFF
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS

    transform = from_bounds(0.0, -10000.0, 10000.0, 0.0, N_OUT, N_OUT)
    for name, arr in [("dem_real_taihang.tif", elev), ("pass_real_taihang.tif", passab)]:
        with rasterio.open(
            OUT_DIR / name, "w", driver="GTiff", height=N_OUT, width=N_OUT,
            count=1, dtype="float32", crs=CRS.from_epsg(32650), transform=transform,
        ) as ds:
            ds.write(arr, 1)
        print(f"[DEM] wrote {name}  {arr.min():.1f}~{arr.max():.1f}")

    meta = {
        "source": "AWS Open Data elevation-tiles-prod (Terrarium, SRTM/ASTER GDEM derived)",
        "tile_url": TILE_URL,
        "center_lat": CENTER_LAT, "center_lon": CENTER_LON,
        "region": "太行山中段（山西-河北交界，华北典型山地丘陵）",
        "zoom": ZOOM, "tile_range": [tx0, ty0, tx0 + 2, ty0 + 2],
        "n_out": N_OUT, "approx_resolution_m": 30,
        "elev_min_m": float(elev.min()), "elev_max_m": float(elev.max()),
        "elev_mean_m": float(elev.mean()),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "license_note": "AWS Open Data 公共数据集；原型阶段按用户授权忽略版权",
    }
    (OUT_DIR / "REAL_TERRAIN_META.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[DEM] meta written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
