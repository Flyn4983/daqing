"""P3 真实业务约束（三源）：规格/校验/校核/Pareto 筛选/零漂移 单测。

红线：约束只在方案层与选优层生效，不注入 NSGA-II 内核，权威数字不变。
"""
from __future__ import annotations

import pytest

from brigsim.support import constraints as C
from brigsim.support import mission_input as M
from brigsim.support import optimize_pareto as O
from brigsim.support import plan


def _full_constraints(**over):
    d = C.defaults()
    d.update(over)
    return d


# ---------- 规格与默认值 ----------

def test_spec_three_sources_plus_soft():
    sources = {c["source"] for c in C.CONSTRAINT_SPEC}
    assert sources == {"resource", "command", "physical", "soft"}
    assert sum(1 for c in C.CONSTRAINT_SPEC if c["hard"]) >= 10
    assert sum(1 for c in C.CONSTRAINT_SPEC if not c["hard"]) >= 1


def test_spec_covers_user_required_constraints():
    keys = set(C.defaults())
    # 用户明确要求：修理车上限、人员上限、人员修理能力、抢修时限、上级命令
    for k in ("repair_veh_cap", "arv_cap", "supply_veh_cap", "personnel_cap",
              "personnel_per_team", "repair_rate_per_team", "repair_deadline_h",
              "evac_deadline_h", "superior_order", "priority_rule", "max_repair_grade"):
        assert k in keys, f"缺少约束字段 {k}"


def test_defaults_zero_drift():
    d = C.defaults()
    assert d["repair_veh_cap"] >= 6   # 前端默认录入 6 台抢修车
    assert d["arv_cap"] >= 3
    assert d["supply_veh_cap"] >= 2


# ---------- 校验：硬阻断 / 软告警 ----------

def test_validate_ok():
    p, w = C.validate({"superior_order": "四小时内恢复主战装备", "repair_rate_per_team": 2.5})
    assert p["repair_rate_per_team"] == 2.5
    assert "四小时内" in p["superior_order"]
    assert w == []


def test_validate_hard_out_of_range_raises():
    for bad in ({"repair_veh_cap": 0}, {"personnel_cap": -1},
                {"repair_deadline_h": 999}, {"max_repair_grade": "Z"}):
        with pytest.raises(ValueError):
            C.validate(bad)


def test_validate_logic_personnel_per_team_gt_cap():
    with pytest.raises(ValueError):
        C.validate({"personnel_cap": 5, "personnel_per_team": 8})


def test_validate_text_truncate_warns():
    p, w = C.validate({"superior_order": "X" * 5000})
    assert len(p["superior_order"]) <= C.SPEC_BY_KEY["superior_order"]["max_len"]
    assert any("截断" in x for x in w)


# ---------- 有效上界 ----------

def test_effective_bounds_clamps():
    b = C.effective_bounds(_full_constraints(repair_veh_cap=3, arv_cap=2, supply_veh_cap=1))
    assert b["red_repair_teams"] == 3
    assert b["red_arv"] == 2
    assert b["red_supply_veh"] == 1


def test_effective_bounds_none_empty():
    assert C.effective_bounds(None) == {}


# ---------- 校核 ----------

def test_check_feasibility_all_pass():
    mission = {"support_vehicles": [
        {"key": "repair_veh", "count": 4}, {"key": "arv", "count": 3},
        {"key": "supply_veh", "count": 2}]}
    ck = C.check_feasibility(mission, _full_constraints(repair_veh_cap=4))
    assert ck["n_fail"] == 0
    assert ck["overall"] in ("pass", "warn")


def test_check_feasibility_vehicle_over_cap_fails():
    mission = {"support_vehicles": [{"key": "repair_veh", "count": 10}]}
    ck = C.check_feasibility(mission, _full_constraints(repair_veh_cap=5))
    assert ck["n_fail"] >= 1
    assert "repair_veh_cap" in ck["blocking"]


def test_check_feasibility_personnel_over_cap():
    mission = {"support_vehicles": [{"key": "repair_veh", "count": 6}]}
    ck = C.check_feasibility(mission, _full_constraints(
        repair_veh_cap=10, personnel_cap=20, personnel_per_team=8))
    assert "personnel_cap" in ck["blocking"]


def test_check_feasibility_deadline_with_loss():
    mission = {"support_vehicles": [{"key": "repair_veh", "count": 2}]}
    loss = {"summary": {"by_grade": {"M": 30, "F": 20}}}  # 待修 50，2 组×3 台/h×1h=6 < 50
    ck = C.check_feasibility(mission, _full_constraints(repair_deadline_h=1.0), loss=loss)
    assert "repair_deadline_h" in ck["blocking"]


def test_constraint_slots_deterministic():
    ck = C.check_feasibility({}, _full_constraints(superior_order="上级命令原文"))
    s = C.constraint_slots(_full_constraints(superior_order="上级命令原文"), ck)
    assert s["constraint_order"] == "上级命令原文"
    assert "抢修车上限" in s["constraint_summary"]
    assert "满足" in s["constraint_feasibility"]


# ---------- Pareto 硬筛选（只读、零漂移） ----------

def test_pareto_no_constraints_zero_drift():
    r = O.recommend(top_n=3)
    assert r["n_candidates"] == 74
    assert r.get("n_constraint_filtered") == 0


def test_pareto_constraint_filters_over_cap():
    r = O.recommend(top_n=5, constraints=_full_constraints(
        repair_veh_cap=2, arv_cap=2, supply_veh_cap=1))
    assert r["n_constraint_filtered"] > 0
    assert r["n_candidates"] < 74
    assert all(x["red_repair_teams"] <= 2 for x in r["ranked"])
    assert all(x["red_arv"] <= 2 for x in r["ranked"])


def test_pareto_impossible_constraint_returns_error_no_crash():
    r = O.recommend(top_n=5, constraints=_full_constraints(
        repair_veh_cap=1, arv_cap=1, supply_veh_cap=1))
    assert "error" in r
    assert r["n_candidates"] == 0


def test_pareto_does_not_mutate_authoritative_file():
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "src/brigsim/agent/web/data/pareto.json"
    before = p.read_bytes()
    O.recommend(top_n=5, constraints=_full_constraints(repair_veh_cap=2))
    assert p.read_bytes() == before  # 只读：权威 74 方案文件未被改动


# ---------- 录入契约集成 ----------

def _payload(**over):
    d = {
        "task_name": "约束测试", "mission_type": "进攻", "base_scenario": "example_brigade",
        "equipment": [{"key": "tank", "count": 10}],
        "support_vehicles": [{"key": "repair_veh", "count": 6},
                             {"key": "arv", "count": 3},
                             {"key": "supply_veh", "count": 2}],
    }
    d.update(over)
    return d


def test_mission_validate_integrates_constraints():
    p, w = M.validate(_payload(constraints={"repair_veh_cap": 8, "personnel_cap": 120}))
    assert p["constraints"]["repair_veh_cap"] == 8
    assert p["constraints_configured"] is True


def test_mission_validate_no_constraints_zero_drift():
    p, w = M.validate(_payload())
    assert p["constraints_configured"] is False
    assert p["constraints"]["repair_veh_cap"] == C.defaults()["repair_veh_cap"]


def test_mission_validate_vehicle_over_cap_raises():
    with pytest.raises(ValueError, match="硬约束违反"):
        M.validate(_payload(constraints={"repair_veh_cap": 4}))  # 录入 6 台 > 上限 4


def test_mission_upsert_returns_error_not_raise():
    from brigsim.support import api
    r = api.mission_upsert(_payload(constraints={"repair_veh_cap": 1}))
    assert "error" in r
    assert r.get("constraint_violation") is True


# ---------- 方案槽位集成 ----------

def test_build_slots_includes_constraint_slots():
    mission = M.validate(_payload(constraints={"superior_order": "限时抢修"}))[0]
    loss = {"summary": {"by_grade": {"K": 1, "M": 2, "F": 1}, "roster_size": 10},
            "rates": {"attrition": 0.1, "readiness": 0.8}, "n_runs": 8}
    slots = plan.build_slots(mission, loss)
    assert slots["constraint_order"] == "限时抢修"
    assert "抢修车上限" in slots["constraint_summary"]
    assert slots["_constraint_check"]["constraints"]["superior_order"] == "限时抢修"
