"""M3 备件供应链测试：库存消耗、缺货挂起、周期补给解束；None 时保持无限备件行为。"""

import numpy as np

from brigsim.data.provider import YamlFileProvider
from brigsim.data.schema import SustainmentSpec
from brigsim.kernel.state import SimState
from brigsim.sustainment.repair import RED, SustainmentModel


def _scenario(spare_stock, interval=15, qty=2, teams_red=5):
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    # 抑制自然故障的随机性，只保留人为 combat_damage 路径
    ptypes = {
        name: p.model_copy(update={"mtbf_ticks": 1e9}) for name, p in sc.platform_types.items()
    }
    sustain = SustainmentSpec(
        repair_teams={"red": teams_red, "blue": 1},
        repair_delay_ticks=0,
        spare_stock=spare_stock,
        resupply_interval_ticks=interval,
        resupply_qty=qty,
    )
    return sc.model_copy(update={"platform_types": ptypes, "sustainment": sustain})


def test_stock_consumption_blocks_until_resupply():
    sc = _scenario(spare_stock={"red": 1, "blue": 1})
    state = SimState(sc)
    model = SustainmentModel(sc, state, np.random.default_rng(0))
    red = np.where((state.side == RED) & state.alive)[0][:3]
    for i in red:
        model.combat_damage(int(i))

    model.advance(8)
    # 初始 1 件只够 1 台维修，其余挂起等件，库存见底
    assert model.parts[RED].level == 0
    assert state.under_repair[red].sum() >= 2

    model.advance(35)
    # t=15 补给 2 件解束两台，t=30 再补 2 件；三台均修复，库存剩 2
    assert not state.damaged[red].any()
    assert not state.under_repair[red].any()
    assert model.parts[RED].level == 2


def test_none_stock_means_unlimited():
    sc = _scenario(spare_stock=None)
    state = SimState(sc)
    model = SustainmentModel(sc, state, np.random.default_rng(0))
    assert model.parts is None
    red = np.where((state.side == RED) & state.alive)[0][:3]
    for i in red:
        model.combat_damage(int(i))
    model.advance(40)
    # 无库存约束：抢修组充足，全部修复归建
    assert not state.damaged[red].any()
    assert not state.under_repair[red].any()
