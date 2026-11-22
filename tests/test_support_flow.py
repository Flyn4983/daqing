"""作战保障全过程扩展层的单元测试（不跑真实推演，秒级完成）。

覆盖：输入校验 / 映射、战损清单口径（红蓝分离）、四率派生与对账、
方案槽位数字隔离、LLM 编造数字拦截、模板槽位校验、API 编排。
"""
from __future__ import annotations

import pytest

from brigsim.support import api, battle_loss, mission_input, plan as plan_mod


@pytest.fixture
def mission() -> dict:
    p, warns = mission_input.validate({
        "task_name": "测试任务",
        "base_scenario": "medium_brigade",
        "duration_ticks": 20,
        "n_runs": 3,
        "equipment": [{"key": "tank", "count": 24}, {"key": "ifv", "count": 36}],
        "ammo": [{"class": "ap", "basic_load": 40, "reserve": 120}],
        "fuel": [{"grade": "柴油", "stock_l": 10000, "resupply_l": 3000}],
        "support_vehicles": [
            {"key": "repair_veh", "count": 6},
            {"key": "arv", "count": 3},
            {"key": "supply_veh", "count": 2},
        ],
        "resources": [{"key": "mobility", "qty": {"carry": 16, "operating": 24, "depot": 40}}],
        "daily_ops": {"readiness_level": "二级战备"},
    })
    assert not warns
    return p


def test_validate_rejects_bad_equipment():
    with pytest.raises(ValueError, match="unknown|未知"):
        mission_input.validate({"equipment": [{"key": "not_exist", "count": 1}]})


def test_run_config_maps_knobs(mission):
    cfg = mission_input.to_run_config(mission)
    assert cfg["scenario"] == "medium_brigade"
    assert cfg["red_repair_teams"] == 6
    assert cfg["red_arv"] == 3
    assert cfg["red_supply_veh"] == 2
    assert cfg["duration_ticks"] == 20


def test_loss_list_red_blue_separation():
    """关键回归：红蓝混在快照里时，战损率分母只能是红方。"""
    pd = pytest.importorskip("pandas")
    snap = pd.DataFrame([
        {"tick": 1, "unit_id": "r_1", "side": 0, "alive": True, "damaged": False,
         "under_repair": False, "damage_grade": 0, "disposition": 0, "part": "",
         "x": 0.0, "y": 0.0},
        {"tick": 2, "unit_id": "r_1", "side": 0, "alive": False, "damaged": True,
         "under_repair": False, "damage_grade": 3, "disposition": 5, "part": "hull",
         "x": 0.0, "y": 0.0},
        {"tick": 2, "unit_id": "r_2", "side": 0, "alive": True, "damaged": False,
         "under_repair": False, "damage_grade": 0, "disposition": 0, "part": "",
         "x": 0.0, "y": 0.0},
        {"tick": 2, "unit_id": "b_1", "side": 1, "alive": False, "damaged": True,
         "under_repair": False, "damage_grade": 3, "disposition": 5, "part": "",
         "x": 0.0, "y": 0.0},
    ])
    loss = battle_loss.from_event_frames(snap, n_runs=1)
    assert loss["summary"]["roster_size_red"] == 2
    assert loss["summary"]["roster_size_blue"] == 1
    rates = battle_loss.rates_from_loss_list(
        loss, core_rates={"attrition_red": 0.5, "readiness_red": 0.5})
    assert rates["attrition"] == 0.5           # 1/2，不是 2/3
    assert rates["n_roster"] == 2
    assert rates["reconciliation"]["passed"] is True


def test_rates_reconciliation_detects_mismatch():
    pd = pytest.importorskip("pandas")
    snap = pd.DataFrame([
        {"tick": 1, "unit_id": "r_1", "side": 0, "alive": False,
         "damage_grade": 3, "disposition": 5},
        {"tick": 1, "unit_id": "r_2", "side": 0, "alive": True,
         "damage_grade": 0, "disposition": 0},
    ])
    loss = battle_loss.from_event_frames(snap)
    rates = battle_loss.rates_from_loss_list(loss, core_rates={"attrition_red": 0.1})
    assert rates["reconciliation"]["passed"] is False
    assert rates["reconciliation"]["items"][0]["diff"] >= 0.4


def test_plan_numeric_slots_from_loss(mission):
    loss = battle_loss.from_metrics(
        {"summary": {"attrition_red": 0.1, "readiness_red": 0.6}, "n_reps": 3}, mission)
    rates = battle_loss.rates_from_loss_list(loss)
    slots = plan_mod.build_slots(mission, loss, rates=rates)
    assert slots["roster_total"] == 60
    assert slots["pred_k"] == 6               # 0.1*60
    assert slots["pred_attrition"] == "10.0%"
    assert slots["repair_teams"] == 6
    assert slots["need_mobility"] == 0        # 清单无损伤部件


class _FakeChat:
    def __init__(self, text, ok=True):
        self.text, self.ok, self.latency_ms, self.error = text, ok, 12.0, ""


class _FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def chat(self, system, user, temperature=None, max_tokens=None):
        return self.replies.pop(0) if self.replies else _FakeChat("{}")


def test_llm_fabricated_numbers_are_rejected(mission):
    slots = plan_mod.build_slots(mission, {"items": [], "summary": {"roster_size_red": 60}})
    # 模型在叙述里写了数字 → 该槽位应被判无效并回退为模板默认文字
    reply = _FakeChat('{"enemy_situation":"敌投入3个营","enemy_fire":"远程火力",'
                      '"threat_type":"复合威胁","superior_intent":"坚决完成任务"}')
    out = plan_mod.generate_narratives(slots, client=_FakeClient([reply]), use_llm=True)
    assert out["enemy_situation"] == slots["enemy_situation"]   # 回退
    assert out["enemy_fire"] == "远程火力"                       # 合法保留
    assert out["_meta"]["groups_failed"] >= 1


def test_plan_generate_and_validation(mission):
    loss = battle_loss.from_metrics(
        {"summary": {"attrition_red": 0.1, "readiness_red": 0.6}, "n_reps": 3}, mission)
    result = plan_mod.generate(mission, loss, use_llm=False)
    assert result["validation"]["passed"] is True
    assert "战损预计" in result["text"]
    assert "{{" not in result["text"]
    assert result["llm"]["source"] == "fallback"


def test_template_rejects_unknown_slot():
    with pytest.raises(ValueError, match="未知槽位"):
        plan_mod.save_template("__test_bad.md", "数量 {{not_a_real_slot}}")


def test_api_catalog_and_mission(mission):
    c = api.catalog()
    assert len(c["process_nodes"]) == 8
    assert "fuel" in c["catalog"] and "daily_ops" in c["catalog"]
    assert len(c["slot_dict"]) >= 60
    r = api.mission_upsert(mission)
    assert r["mission_id"] and r["run_config"]["scenario"] == "medium_brigade"
    got = api.mission_get(r["mission_id"])
    assert got["mission"]["task_name"] == "测试任务"
