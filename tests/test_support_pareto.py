"""成本约束 Pareto 选优单测（只读派生层：不重跑仿真、不改权威数字）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from brigsim.support import api, optimize_pareto

_REPO = Path(__file__).resolve().parents[1]
_PF = _REPO / "src" / "brigsim" / "agent" / "web" / "data" / "pareto.json"


def _h() -> str:
    return hashlib.md5(_PF.read_bytes()).hexdigest()


def test_weights_normalized():
    r = optimize_pareto.recommend({"attrition_weight": 0.4, "time_weight": 0.4,
                                   "cost_weight": 0.4})
    w = r["effective_weights"]
    assert abs(sum(w.values()) - 1.0) < 0.01
    assert all(0 <= v <= 1 for v in w.values())


def test_default_equal_weights():
    r = optimize_pareto.recommend({})
    w = r["effective_weights"]
    assert abs(w["attrition_weight"] - 1 / 3) < 0.01


def test_cost_focus_picks_low_cost():
    r = optimize_pareto.recommend({"attrition_weight": 0.01, "time_weight": 0.01,
                                   "cost_weight": 0.98}, top_n=1)
    top = r["ranked"][0]
    # 成本极高权重应选到接近 leanest（最低成本 0.016）
    assert top["cost"] < 0.1
    assert top["cost"] == min(p["cost"] for p in r["ranked"])


def test_attrition_focus_beats_cost_focus_on_attrition():
    a = optimize_pareto.recommend({"attrition_weight": 1, "time_weight": 0,
                                   "cost_weight": 0}, top_n=1)["ranked"][0]
    c = optimize_pareto.recommend({"attrition_weight": 0, "time_weight": 0,
                                   "cost_weight": 1}, top_n=1)["ranked"][0]
    assert a["attrition_red"] < c["attrition_red"]


def test_time_focus_picks_high_readiness():
    t = optimize_pareto.recommend({"attrition_weight": 0, "time_weight": 1,
                                   "cost_weight": 0}, top_n=1)["ranked"][0]
    assert t["readiness_red"] > 0.55


def test_cost_ceiling_hard_constraint():
    r = optimize_pareto.recommend({"attrition_weight": 0.4, "time_weight": 0.3,
                                   "cost_weight": 0.3}, cost_ceiling=0.2, top_n=8)
    assert r["n_filtered"] > 0
    assert r["n_candidates"] + r["n_filtered"] == r["n_plans"] == 74
    assert all(p["cost"] <= 0.2 + 1e-9 for p in r["ranked"])


def test_no_ceiling_no_filter():
    r = optimize_pareto.recommend({}, cost_ceiling=0, top_n=5)
    assert r["n_filtered"] == 0


def test_anchors_present():
    r = optimize_pareto.recommend({})
    assert "knee" in r["anchors"]
    assert "baseline" in r["anchors"]
    assert r["anchors"]["knee"]["cost"] == pytest.approx(0.165, abs=0.01)


def test_budget_limit_note_is_honest():
    r = optimize_pareto.recommend({})
    assert "量纲不同" in r["budget_limit_note"]


def test_pareto_json_never_modified():
    before = _h()
    for w in ({"cost_weight": 1}, {"attrition_weight": 1}, {}):
        optimize_pareto.recommend(w, cost_ceiling=0.2)
    assert _h() == before


def test_api_fallback_to_mission_weights():
    # 不传 weights → 后端回退到最近录入/缺省，不报错
    r = api.pareto_recommend({"top_n": 3})
    assert "ranked" in r and len(r["ranked"]) == 3
    assert "error" not in r


def test_api_invalid_ceiling_ignored():
    r = api.pareto_recommend({"cost_ceiling": "abc", "top_n": 2})
    assert r.get("cost_ceiling") is None or isinstance(r.get("cost_ceiling"), float)
    assert "ranked" in r


def test_apply_creates_new_plan_without_touching_slots():
    r = api.pareto_recommend({"cost_weight": 0.6, "top_n": 1})
    sel = r["ranked"][0]
    a = api.pareto_apply({"selection": sel, "weights": r["effective_weights"]})
    assert a.get("plan_id"), a
    ps = a["pareto_selection"]
    assert ps["decision"]["red_repair_teams"] == sel["red_repair_teams"]
    assert len(ps["decision"]) == 7
    # 附节字段独立，不含数值槽位/权威指标键
    assert "slots" not in ps and "readiness" not in ps


# ---------- 二维可行域投影（只读派生） ----------

def test_region_no_constraints_all_feasible():
    """缺省无约束 → 74 方案全部可行（零漂移）。"""
    r = optimize_pareto.feasible_region()
    assert r["n_total"] == 74
    assert r["n_feasible"] == 74
    assert r["n_filtered"] == 0
    assert all(pt["feasible"] for pt in r["points"])


def test_region_axes_complete():
    r = optimize_pareto.feasible_region()
    dec = {a["field"] for a in r["axes"]["decision"]}
    met = {a["field"] for a in r["axes"]["metric"]}
    assert {"red_repair_teams", "red_arv", "carry_level", "wave_qty"} <= dec
    assert {"cost", "attrition_red", "readiness_red"} <= met


def test_region_axis_meta_shape():
    r = optimize_pareto.feasible_region()
    for a in r["axes"]["decision"] + r["axes"]["metric"]:
        assert set(("field", "label", "unit", "kind")) <= set(a.keys())


def test_region_constraint_filters_and_flags():
    caps = {"repair_veh_cap": 2, "arv_cap": 6, "supply_veh_cap": 4}
    r = optimize_pareto.feasible_region(constraints=caps)
    assert r["bounds"]["red_repair_teams"] == 2
    assert 0 < r["n_filtered"] < 74
    assert r["n_feasible"] + r["n_filtered"] == 74
    flagged = [pt for pt in r["points"] if not pt["feasible"]]
    assert flagged
    assert all(any(v["axis"] == "red_repair_teams" for v in pt["violations"]) for pt in flagged)


def test_region_cost_ceiling():
    r = optimize_pareto.feasible_region(cost_ceiling=0.3)
    assert r["cost_ceiling"] == 0.3
    assert r["n_filtered"] > 0
    assert all(float(pt["m"]["cost"]) <= 0.3 + 1e-12
               for pt in r["points"] if pt["feasible"])


def test_region_knee_marked():
    r = optimize_pareto.feasible_region()
    knees = [pt for pt in r["points"] if pt["is_knee"]]
    assert len(knees) == 1
    assert knees[0]["idx"] == 27  # 权威 tags.knee


def test_region_point_has_both_dimensions():
    r = optimize_pareto.feasible_region()
    pt = r["points"][0]
    assert len(pt["d"]) == 7 and len(pt["m"]) == 6
    assert "rank_tag" in pt and "violations" in pt


def test_region_api_reads_mission_constraints(monkeypatch):
    mission = {"constraints_configured": True,
               "constraints": {"repair_veh_cap": 2, "arv_cap": 6, "supply_veh_cap": 4}}
    monkeypatch.setattr(api.mission_input, "latest", lambda: mission)
    monkeypatch.setattr(api.mission_input, "load", lambda mid: None)
    r = api.pareto_region({})
    assert r["bounds"]["red_repair_teams"] == 2
    assert r["n_filtered"] > 0


def test_region_pareto_json_untouched():
    before = _h()
    optimize_pareto.feasible_region(constraints={"repair_veh_cap": 2})
    optimize_pareto.feasible_region(cost_ceiling=0.3)
    assert _h() == before
