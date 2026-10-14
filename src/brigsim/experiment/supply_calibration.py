"""M3 备件供应链网格标定：在交战标定基线上选备件基数与补给周期。

目标（显式原型假设，真实保障数据到位后重标定）：
- fill_rate（备件请领即时满足率）双方落在 FILL_BAND：进攻战斗允许短时
  缺货但大部分维修不等件（库存管理常用服务水平 85-95%）；
- 交战三指标保持在 calibration.BANDS 内（备件只温和影响战局）；
- 在满足条件的配置中取初始库存总量最小者（携行基数经济性），
  再以补给周期较短者优先（小批量多批次）。

方法为穷举小网格（旋钮对 fill_rate 单调、无需 DE）：z_stock（初始库存
同比缩放）× resupply_interval；每点 CRN 蒙特卡洛取均值。
"""

from __future__ import annotations

import numpy as np

from ..data.schema import Scenario
from .calibration import BANDS as COMBAT_BANDS

# fill_rate 目标带（红/蓝共用）
FILL_BAND: tuple[float, float] = (0.85, 0.97)

GRID_KEYS = (
    "success",
    "attrition_red",
    "attrition_blue",
    "fill_rate_red",
    "fill_rate_blue",
    "stockout_wait_red",
    "stockout_wait_blue",
)


def apply_supply(
    scenario: Scenario,
    z_stock: float,
    interval: int,
    qty: int | None = None,
) -> Scenario:
    """把备件旋钮施加到想定（不改原对象）：初始库存同比缩放后取整（≥1）。"""
    spec = scenario.sustainment
    if spec.spare_stock is None:
        raise ValueError("想定未启用 spare_stock，无法标定备件供应链")
    stock = {
        side: max(1, int(round(spec.spare_stock[side] * float(z_stock))))
        for side in ("red", "blue")
    }
    update = {"spare_stock": stock, "resupply_interval_ticks": int(interval)}
    if qty is not None:
        update["resupply_qty"] = int(qty)
    return scenario.model_copy(update={"sustainment": spec.model_copy(update=update)})


def point_record(
    scenario: Scenario,
    z_stock: float,
    interval: int,
    seeds: list[int],
    device: str = "cuda:0",
    qty: int | None = None,
) -> dict:
    """单组备件旋钮的 CRN 蒙特卡洛均值记录（含实际取整后库存）。"""
    from ..combat.warp_batch import run_batch

    sc = apply_supply(scenario, z_stock, interval, qty)
    rows = run_batch(sc, seeds, device=device)
    rec: dict = {
        "z_stock": float(z_stock),
        "interval": int(interval),
        "stock_red": sc.sustainment.spare_stock["red"],
        "stock_blue": sc.sustainment.spare_stock["blue"],
    }
    for k in GRID_KEYS:
        rec[k] = float(np.mean([float(r[k]) for r in rows]))
    rec["fill_ok"] = (
        FILL_BAND[0] <= rec["fill_rate_red"] <= FILL_BAND[1]
        and FILL_BAND[0] <= rec["fill_rate_blue"] <= FILL_BAND[1]
    )
    rec["band_ok"] = all(lo <= rec[k] <= hi for k, (lo, hi, _w) in COMBAT_BANDS.items())
    return rec


def grid_calibrate(
    scenario: Scenario,
    z_grid: tuple[float, ...] = (0.5, 0.75, 1.0, 1.5),
    intervals: tuple[int, ...] = (20, 30, 45),
    reps: int = 256,
    base_seed: int = 20261014 + 7000,
    device: str = "cuda:0",
    verbose: bool = True,
) -> list[dict]:
    """穷举备件网格；返回全部点记录（按 z_stock、interval 排序）。"""
    seeds = [base_seed + r for r in range(reps)]
    records = []
    for z in z_grid:
        for interval in intervals:
            rec = point_record(scenario, z, interval, seeds, device=device)
            records.append(rec)
            if verbose:
                print(
                    f"z={z:.2f} iv={interval:3d} stock=({rec['stock_red']:2d},"
                    f"{rec['stock_blue']:2d}) succ={rec['success']:.3f} "
                    f"fillR={rec['fill_rate_red']:.3f} fillB={rec['fill_rate_blue']:.3f} "
                    f"{'FILL_OK' if rec['fill_ok'] else ''} "
                    f"{'BAND_OK' if rec['band_ok'] else ''}",
                    flush=True,
                )
    return records


def select_feasible(records: list[dict]) -> list[dict]:
    """筛选 fill 入带且交战不出带的点，按携行经济性排序（库存和↑、周期↑）。"""
    feasible = [r for r in records if r["fill_ok"] and r["band_ok"]]
    return sorted(
        feasible,
        key=lambda r: (
            int(r["stock_red"]) + int(r["stock_blue"]),
            int(r["interval"]),
        ),
    )
