"""对战双方实力 GUI 录入 → Scenario 注入的单元测试（秒级，不跑推演）。

覆盖：红蓝对称校验、地面/空中缩放到目标数、方别乘子覆盖、带病/工事比例、
零漂移（未录入时权威想定不变）、默认编成读取、方案槽位纳入双方兵力。
"""
from __future__ import annotations

import pytest

from brigsim.data.provider import YamlFileProvider
from brigsim.support import battle_loss, force_config, mission_input, plan as plan_mod


SCENARIO = "example_brigade"


@pytest.fixture
def scenario():
    return YamlFileProvider().load_scenario(SCENARIO)


def _counts(sc, side, ptype):
    return sum(1 for u in sc.forces[side] if u.type == ptype)


def _air(sc, side, kind):
    return sum(1 for p in sc.air_packages if p.side == side and p.kind == kind)


# ---------------- 校验 ----------------

def test_validate_empty_is_noop():
    f, w = force_config.validate({})
    assert f == {} and w == []
    assert force_config.has_content({}) is False


def test_validate_red_blue_symmetric():
    f, w = force_config.validate({
        "red": {"equipment": {"tank": 10}, "air": {"uav": 2},
                "modifiers": {"pk_scale": 1.2}},
        "blue": {"equipment": {"atgm_team": 5}, "air": {"helo": 1},
                 "modifiers": {"detect_scale": 0.8}},
    })
    assert not w
    assert f["red"]["equipment"]["tank"] == 10
    assert f["blue"]["equipment"]["atgm_team"] == 5
    assert f["red"]["modifiers"]["pk_scale"] == 1.2
    assert f["blue"]["modifiers"]["detect_scale"] == 0.8
    assert force_config.has_content(f) is True


def test_validate_rejects_bad_modifier():
    with pytest.raises(ValueError, match="pk_scale"):
        force_config.validate({"red": {"modifiers": {"pk_scale": 99}}})


def test_validate_accepts_scenario_specific_type():
    # medium_brigade 的 wheel_* 专属平台类型应被接受（不再限于标准 26 类）
    f, w = force_config.validate({"red": {"equipment": {"wheel_assault": 5}}})
    assert not w
    assert f["red"]["equipment"]["wheel_assault"] == 5

    _, w2 = force_config.validate({"red": {"equipment": {"bad-type!": 3}}})
    assert any("bad-type!" in x for x in w2)


# ---------------- 注入与零漂移 ----------------

def test_inject_zero_drift(scenario):
    before = scenario.model_copy(deep=True)
    sc, rep = force_config.inject(scenario, {})
    assert rep["injected"] is False
    assert sc.forces == before.forces
    assert sc.air_packages == before.air_packages
    assert sc.side_modifiers == before.side_modifiers


def test_inject_scales_red_and_blue(scenario):
    orig_red_tank = _counts(scenario, "red", "tank")
    orig_blue_atgm = _counts(scenario, "blue", "atgm_team")
    assert orig_red_tank > 10 and orig_blue_atgm > 5
    f, _ = force_config.validate({
        "red": {"equipment": {"tank": 10}, "air": {"uav": 2}},
        "blue": {"equipment": {"atgm_team": 5}},
    })
    sc, rep = force_config.inject(scenario, f)
    assert _counts(sc, "red", "tank") == 10
    assert _counts(sc, "blue", "atgm_team") == 5
    assert _air(sc, "red", "uav") == 2
    assert rep["sides"]["red"]["equipment_applied"]["tank"] == 10
    # 原对象不被修改（纯函数风格）
    assert _counts(scenario, "red", "tank") == orig_red_tank


def test_inject_modifiers_and_ratios(scenario):
    f, _ = force_config.validate({
        "red": {"modifiers": {"pk_scale": 1.2, "speed_scale": 0.9}, "worn_ratio": 0.5},
        "blue": {"modifiers": {"detect_scale": 0.7}, "dug_ratio": 1.0},
    })
    sc, _ = force_config.inject(scenario, f)
    assert sc.side_modifiers["red"].pk_scale == pytest.approx(1.2)
    assert sc.side_modifiers["red"].speed_scale == pytest.approx(0.9)
    assert sc.side_modifiers["blue"].detect_scale == pytest.approx(0.7)
    red_worn = sum(1 for u in sc.forces["red"] if u.initial_worn)
    blue_dug = sum(1 for u in sc.forces["blue"] if u.dug_in)
    assert red_worn > 0 and blue_dug == len(sc.forces["blue"])


def test_inject_missing_type_warns_not_fabricates(scenario):
    f, _ = force_config.validate({"blue": {"equipment": {"tank": 8}}})
    # blue 想定本身有 tank，选一个确实没有的类型验证不新增
    f2, _ = force_config.validate({"blue": {"equipment": {"hj10": 8}}})
    sc, rep = force_config.inject(scenario, f2)
    assert _counts(sc, "blue", "hj10") == 0
    assert any("hj10" in n for n in rep["notes"])


# ---------------- 默认编成读取 ----------------

def test_defaults_reads_both_sides():
    d = force_config.defaults(SCENARIO)
    assert d["sides"]["red"]["total_ground"] > 100
    assert d["sides"]["blue"]["total_ground"] > 30
    assert d["sides"]["red"]["total_air"] > 0
    assert d["sides"]["blue"]["total_air"] > 0


def test_defaults_scenario_specific_types():
    """medium_brigade 用 wheel_* 专属类型，默认编成不应读成 0。"""
    d = force_config.defaults("medium_brigade")
    assert d["sides"]["red"]["total_ground"] >= 14
    assert "wheel_assault" in d["sides"]["red"]["equipment"]
    assert d["equipment_names"]["wheel_assault"]
    assert d["sides"]["blue"]["total_ground"] == 9


def test_inject_scales_scenario_specific_type():
    sc = YamlFileProvider().load_scenario("medium_brigade")
    f, _ = force_config.validate({"red": {"equipment": {"wheel_assault": 3}}})
    out, rep = force_config.inject(sc, f)
    assert _counts(out, "red", "wheel_assault") == 3
    assert rep["sides"]["red"]["equipment_applied"]["wheel_assault"] == 3


# ---------------- 输入模型集成 ----------------

def test_mission_input_accepts_forces():
    p, w = mission_input.validate({
        "equipment": [{"key": "tank", "count": 24}],
        "forces": {"red": {"equipment": {"tank": 12}},
                   "blue": {"equipment": {"atgm_team": 6}}},
    })
    assert not any("forces" in x for x in w)   # 油料等既有警告与此无关
    assert p["forces"]["red"]["equipment"]["tank"] == 12
    s = mission_input.summary(p)
    assert s["forces_configured"] is True
    assert s["blue_total"] == 6
    assert "红" in s["force_balance"]


# ---------------- 方案槽位 ----------------

def test_plan_slots_include_force_balance():
    p, _ = mission_input.validate({
        "equipment": [{"key": "tank", "count": 24}],
        "forces": {"red": {"equipment": {"tank": 12}, "modifiers": {"pk_scale": 1.1}},
                   "blue": {"equipment": {"atgm_team": 6}, "air": {"uav": 2}}},
    })
    loss = battle_loss.from_metrics({"summary": {}, "n_reps": 1}, p)
    slots = plan_mod.build_slots(p, loss)
    assert "地面装备 6 台" in slots["enemy_strength_desc"]
    assert "红方地面 12 台" in slots["force_balance_desc"]
    assert "1.10 : 1.00" in slots["force_balance_desc"]
