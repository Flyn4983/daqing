"""P2 进阶能力单测：批量工况对比 / 方案版本 diff / 签批流转 / 环节回退 / 成本权重。

内核与权威数字零改动，全部基于 support 模块与落盘档案。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from brigsim.support import api, catalog, mission_input, plan  # noqa: E402


# ---------------- 目录暴露 ----------------

def test_catalog_exposes_p2_dicts():
    d = api.catalog()
    assert len(d["data_dictionary"]) == 16
    assert all("key" in f and "name" in f for f in d["data_dictionary"])
    assert {s["key"] for s in d["cost_weight_spec"]} == {
        "attrition_weight", "time_weight", "cost_weight", "budget_limit"}
    assert [s["id"] for s in d["review_states"]] == ["draft", "reviewing", "approved"]
    assert set(d["review_actions"]) == {"submit", "approve", "reject", "reopen"}


# ---------------- P2-5 成本权重 ----------------

def _minimal_mission(**over):
    p = {
        "task_name": "P2测试任务", "base_scenario": "example_brigade",
        "equipment": [{"key": "tank", "count": 10}],
    }
    p.update(over)
    return p


def test_cost_weights_default_and_normalize():
    m, _ = mission_input.validate(_minimal_mission())
    cw = m["cost_weights"]
    assert round(cw["attrition_weight"] + cw["time_weight"] + cw["cost_weight"], 2) == 1.0
    assert cw["budget_limit"] == 0


def test_cost_weights_custom_and_clamp():
    m, _ = mission_input.validate(_minimal_mission(cost_weights={
        "attrition_weight": 10, "time_weight": 0, "cost_weight": 0, "budget_limit": 500,
    }))
    cw = m["cost_weights"]
    assert cw["attrition_weight"] == 1.0  # 归一化后唯一非零权重
    assert cw["time_weight"] == 0.0 and cw["cost_weight"] == 0.0
    assert cw["budget_limit"] == 500  # int 保留


def test_cost_weights_not_in_run_config():
    m, _ = mission_input.validate(_minimal_mission(cost_weights={"budget_limit": 300}))
    cfg = mission_input.to_run_config(m)
    assert "cost_weights" not in cfg  # 成本权重不进内核旋钮


def test_cost_weights_bad_type_rejected():
    with pytest.raises(ValueError):
        mission_input.validate(_minimal_mission(cost_weights="bad"))


# ---------------- P2-1 批量工况对比 ----------------

def test_compare_scenarios_shape():
    d = api.compare_scenarios()
    assert "scenarios" in d and "not_deduced" in d and "count" in d
    assert d["count"] == len(d["scenarios"])
    for row in d["scenarios"]:
        assert row["source"] == "archive"
        assert "attrition" in row and "terrain" in row
    assert "未重新推演" in d["note"] or "not_deduced" in d["note"]


# ---------------- P2-2 方案版本 diff ----------------

def _fake_plan(pid, name, attrition, text):
    return {
        "plan_id": pid, "slots": {"plan_name": name, "pred_attrition": attrition,
                                  "repair_teams": "3 组"},
        "text": text, "validation": {"passed": True}, "review_status": "draft",
    }


def test_plan_compare_needs_two(monkeypatch):
    assert "error" in api.plan_compare({"plan_ids": ["A"]})


def test_plan_compare_slot_and_text_diff(monkeypatch):
    a = _fake_plan("A", "方案甲", "10.0%", "一、情况判断\n敌情描述甲。\n二、决心\n保留段落。")
    b = _fake_plan("B", "方案乙", "12.0%", "一、情况判断\n敌情描述乙。\n二、决心\n保留段落。")
    monkeypatch.setattr(plan, "load", lambda pid: {"A": a, "B": b}.get(pid))
    d = api.plan_compare({"plan_ids": ["A", "B"]})
    assert d["count"] == 2
    keys = {x["key"] for x in d["slot_diffs"]}
    assert "pred_attrition" in keys and "plan_name" in keys
    assert "repair_teams" not in keys  # 相同槽位不列为差异
    assert d["text_diffs"] and any("敌情描述" in ln for ln in d["text_diffs"][0]["lines"])


# ---------------- P2-3 签批流转 ----------------

def test_plan_review_happy_path(monkeypatch):
    p = _fake_plan("P", "方案", "10%", "正文")
    store = {"P": p}
    monkeypatch.setattr(plan, "load", lambda pid: store.get(pid))
    monkeypatch.setattr(plan, "save", lambda x: store.__setitem__(x["plan_id"], x) or x["plan_id"])

    r1 = api.plan_review({"plan_id": "P", "action": "submit", "note": "请复核"})
    assert r1["transition"] == "draft → reviewing"
    assert len(r1["plan"]["review_log"]) == 1
    r2 = api.plan_review({"plan_id": "P", "action": "approve"})
    assert r2["transition"] == "reviewing → approved"
    assert len(r2["plan"]["review_log"]) == 2


def test_plan_review_illegal_transition(monkeypatch):
    p = _fake_plan("P", "方案", "10%", "正文")
    monkeypatch.setattr(plan, "load", lambda pid: p)
    # draft 不能直接 approve
    r = api.plan_review({"plan_id": "P", "action": "approve"})
    assert "error" in r


def test_plan_review_unknown_action(monkeypatch):
    p = _fake_plan("P", "方案", "10%", "正文")
    monkeypatch.setattr(plan, "load", lambda pid: p)
    assert "error" in api.plan_review({"plan_id": "P", "action": "explode"})


# ---------------- P2-7 环节回退重算 ----------------

def test_recompute_rejects_sim_stages():
    r = api.recompute_from({"stage": "deduce"})
    assert "error" in r and "重跑仿真" in r["error"]


def test_recompute_rates(monkeypatch):
    loss = {"loss_id": "L1", "items": [], "rates": {"attrition": 0.1},
            "summary": {"roster_size": 10}}
    monkeypatch.setattr(api.battle_loss, "load", lambda lid: loss)
    monkeypatch.setattr(api.battle_loss, "save", lambda x: "L2")
    r = api.recompute_from({"stage": "rates", "loss_id": "L1"})
    assert r["stage"] == "rates" and r["loss_id"] == "L2"
    assert "attrition" in r["rates"]


def test_recompute_review(monkeypatch):
    p = _fake_plan("P", "方案", "10%", "正文")
    store = {"P": p}
    monkeypatch.setattr(plan, "load", lambda pid: store.get(pid))
    monkeypatch.setattr(plan, "save", lambda x: store.__setitem__(x["plan_id"], x) or x["plan_id"])
    monkeypatch.setattr(plan, "validate_document", lambda text, slots: {"passed": True})
    monkeypatch.setattr(plan, "build_provenance", lambda slots, text: {"n": 1})
    r = api.recompute_from({"stage": "review", "plan_id": "P"})
    assert r["stage"] == "review" and r["validation"]["passed"] is True
    assert "rechecked_at" in store["P"]
