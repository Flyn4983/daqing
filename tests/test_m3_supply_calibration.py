"""M3 备件网格标定器纯函数测试（不依赖 GPU）。"""

import pytest

from brigsim.data.provider import YamlFileProvider
from brigsim.experiment.supply_calibration import (
    FILL_BAND,
    apply_supply,
    select_feasible,
)


def _brigade():
    return YamlFileProvider().load_scenario("example_brigade")


def test_apply_supply_scales_stock_and_interval():
    sc = _brigade()
    base = sc.sustainment.spare_stock
    out = apply_supply(sc, z_stock=0.75, interval=30, qty=6)
    assert out.sustainment.spare_stock == {
        side: max(1, int(round(base[side] * 0.75))) for side in ("red", "blue")
    }
    assert out.sustainment.resupply_interval_ticks == 30
    assert out.sustainment.resupply_qty == 6
    # 原想定不被修改
    assert sc.sustainment.resupply_interval_ticks == 20
    assert sc.sustainment.spare_stock == base


def test_apply_supply_without_stock_raises():
    sc = _brigade().model_copy(
        update={"sustainment": _brigade().sustainment.model_copy(update={"spare_stock": None})}
    )
    with pytest.raises(ValueError):
        apply_supply(sc, 1.0, 20)


def _rec(z, iv, sr, sb, fill_r, fill_b, succ=0.6, ar=0.6, ab=0.8):
    return {
        "z_stock": z,
        "interval": iv,
        "stock_red": sr,
        "stock_blue": sb,
        "success": succ,
        "attrition_red": ar,
        "attrition_blue": ab,
        "fill_rate_red": fill_r,
        "fill_rate_blue": fill_b,
        "stockout_wait_red": 0.0,
        "stockout_wait_blue": 0.0,
        "fill_ok": (
            FILL_BAND[0] <= fill_r <= FILL_BAND[1] and FILL_BAND[0] <= fill_b <= FILL_BAND[1]
        ),
        "band_ok": 0.50 <= succ <= 0.75 and 0.45 <= ar <= 0.70 and 0.50 <= ab <= 0.85,
    }


def test_select_feasible_filters_and_orders_by_economy():
    records = [
        _rec(1.5, 20, 24, 15, 0.96, 0.96),  # 可行但库存大
        _rec(0.5, 30, 8, 5, 0.47, 0.51),  # fill 不达标
        _rec(0.75, 30, 12, 8, 0.91, 0.94),  # 可行，库存小
        _rec(0.75, 20, 12, 8, 0.92, 0.95),  # 可行，同库存周期更短
        _rec(1.0, 20, 16, 10, 0.90, 0.90, succ=0.90),  # 交战出带
    ]
    feasible = select_feasible(records)
    assert [(r["stock_red"], r["interval"]) for r in feasible] == [
        (12, 20),
        (12, 30),
        (24, 20),
    ]
