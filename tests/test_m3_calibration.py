"""M3 标定器纯函数测试（不依赖 GPU）。"""
import numpy as np

from brigsim.data.provider import YamlFileProvider
from brigsim.experiment.calibration import (
    BANDS,
    apply_knobs,
    band_penalty,
)


def _brigade():
    return YamlFileProvider().load_scenario("example_brigade")


def test_apply_knobs_scales_speed_duration_blue_pk():
    from brigsim.kernel.state import SimState

    sc = _brigade()
    x = np.array([0.5, 90, 1.5])
    out = apply_knobs(sc, x)
    assert out.mission.duration_ticks == 90
    for name, p in sc.platform_types.items():
        assert out.platform_types[name].speed_m_per_tick == p.speed_m_per_tick * 0.5
    # 蓝方 pk 获既设防御乘子，红方不变（在 SimState 建列处生效）
    st = SimState(out)
    is_blue = st.side == 1
    base_pk = np.asarray([sc.platform_types[t].pk for t in st.type_names])
    assert np.allclose(st.pk[is_blue], np.minimum(base_pk[is_blue] * 1.5, 1.0))
    assert np.allclose(st.pk[~is_blue], base_pk[~is_blue])
    # 原想定不被修改（烘焙标定值 duration 57、blue pk_scale 1.036 保持不变）
    assert sc.mission.duration_ticks == 57
    assert sc.side_modifiers["blue"].pk_scale == 1.036


def test_band_penalty_zero_inside_band():
    inside = {k: 0.5 * (lo + hi) for k, (lo, hi, _) in BANDS.items()}
    inside.setdefault("readiness_red", 0.3)
    inside.setdefault("readiness_blue", 0.1)
    assert band_penalty(inside) == 0.0


def test_band_penalty_grows_with_distance():
    base = {k: 0.5 * (lo + hi) for k, (lo, hi, _) in BANDS.items()}
    far = dict(base)
    far["success"] = 1.0  # 天花板：在 success 带上界之外
    assert band_penalty(far) > 0.0
    farther = dict(far)
    farther["attrition_red"] = 1.0
    assert band_penalty(farther) > band_penalty(far)
