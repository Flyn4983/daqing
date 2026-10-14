"""M3 在修单位物理后送：配置修理所后，在修单位向本方修理所机动。

覆盖 Mesa / Warp 单环境 / Warp 批量三内核（CPU 后端，CI 可跑）。
未配置修理所时"在修原地不动"的旧行为由 test_m1_kernel 覆盖。
"""

import numpy as np

from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.data.provider import YamlFileProvider

# r-t1（红方坦克）初始位置 [200, 4600]；红方修理所放在其正西
STATION_RED = [0.0, 4600.0]
STATION_BLUE = [10000.0, 5000.0]


def _scenario_with_station():
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    sp = sc.sustainment.model_copy(
        update={"repair_station": {"red": STATION_RED, "blue": STATION_BLUE}}
    )
    return sc.model_copy(update={"sustainment": sp})


def _tank_idx(st) -> int:
    idx = np.where((np.abs(st.x - 200.0) < 1.0) & (np.abs(st.y - 4600.0) < 1.0))[0]
    return int(idx[0])


def _set_under_repair(state, i) -> None:
    state.under_repair[i] = True


def test_mesa_under_repair_moves_toward_station():
    sc = _scenario_with_station()
    kernel = MesaKernel(sc, seed=7)
    s = kernel.state
    i = _tank_idx(s)
    _set_under_repair(s, i)
    x0 = float(s.x[i])
    kernel.step()
    # 朝正西方向的修理所后送：x 减小、y 基本不变、仍存活（不参战不受击）
    assert s.x[i] < x0 - 1.0
    assert abs(s.y[i] - 4600.0) < 1.0
    assert s.alive[i]


def test_warp_under_repair_moves_toward_station():
    from brigsim.combat.warp_kernel import WarpKernel

    sc = _scenario_with_station()
    kernel = WarpKernel(sc, seed=7, device="cpu")
    s = kernel.state
    i = _tank_idx(s)
    _set_under_repair(s, i)
    x0 = float(s.x[i])
    kernel.step()
    assert s.x[i] < x0 - 1.0
    assert abs(s.y[i] - 4600.0) < 1.0
    assert s.alive[i]


def test_warp_batch_under_repair_moves_toward_station():
    from brigsim.combat.warp_batch import WarpBatchKernel

    sc = _scenario_with_station()
    kernel = WarpBatchKernel([sc], [7], device="cpu", use_graph=False)
    n = kernel.n
    i = _tank_idx(kernel.states[0])
    kernel.states[0].under_repair[i] = True
    x0 = float(kernel.h_x[i])
    kernel._step()
    x1 = float(kernel.h_x[i])
    assert x1 < x0 - 1.0
    # 同批其余单位位置不被后送逻辑影响（红方 r-t1 之外的在修标志均为 False）
    assert kernel.h_x[:n].sum() != 0.0
