"""M3 旅级想定冒烟：C7-a0 合成兵种版 119v57 规模展开、三阶段任务、整局可跑完。"""

import math

from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.data.provider import YamlFileProvider
from brigsim.experiment.runner import run_once


def test_brigade_expands_and_runs():
    sc = YamlFileProvider("scenarios").load_scenario("example_brigade")
    m = MesaKernel(sc, seed=20261013)
    n_red = int((m.state.side == 0).sum())
    n_blue = int((m.state.side == 1).sum())
    assert (n_red, n_blue) == (119, 57)
    assert len(sc.mission.phase_list) == 3
    assert m.terrain.passability.shape == (65, 65)

    metrics, kernel = run_once(sc, seed=20261013, kernel_cls=MesaKernel)
    assert 0 < kernel.state.tick <= sc.mission.duration_ticks
    for name in ("success", "attrition_red", "attrition_blue", "readiness_red", "readiness_blue"):
        assert math.isfinite(metrics[name])
        assert 0.0 <= metrics[name] <= 1.0
