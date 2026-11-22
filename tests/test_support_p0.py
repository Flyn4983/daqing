"""P0 扩展批次单测：口径双列 / 复制CI / 环节时间线 / 阈值 / 溯源 / docx。

纯派生逻辑与契约测试，不改内核；不依赖服务进程。
"""
import pytest


def test_doctrines_both_columns():
    from brigsim.support.battle_loss import rates_both_doctrines
    loss = {
        "summary": {"roster_size_red": 10, "by_grade": {"K": 2, "M": 3, "F": 1}},
        "items": [
            {"side_code": 0, "side": "红方", "grade": "K", "count": 2, "repair_status": "不可修", "disposition": "报废",
             "category": "assault", "platform": "tank"},
            {"side_code": 0, "side": "红方", "grade": "M", "count": 3, "repair_status": "待修", "disposition": "现地处置/抢修",
             "category": "assault", "platform": "ifv"},
            {"side_code": 0, "side": "红方", "grade": "F", "count": 1, "repair_status": "待修", "disposition": "后送",
             "category": "artillery", "platform": "howitzer"},
        ],
    }
    r = rates_both_doctrines(loss)
    ds = {d["id"]: d for d in r["doctrines"]}
    assert set(ds) == {"A", "B"}
    assert ds["A"]["authoritative"] is True and ds["A"]["reconcilable"] is True
    assert ds["B"]["authoritative"] is False
    assert ds["A"]["value"] == pytest.approx(0.2)       # K/10
    assert ds["B"]["value"] == pytest.approx(0.6)       # (K+M+F)/10
    assert r["rate_basis_default"] == "A"


def test_replication_ci_shape():
    from brigsim.support.battle_loss import replication_ci
    rows = [
        {"attrition_red": 0.1, "readiness_red": 0.8, "success": True},
        {"attrition_red": 0.2, "readiness_red": 0.7, "success": False},
        {"attrition_red": 0.15, "readiness_red": 0.75, "success": True},
    ]
    rep = replication_ci(rows)
    assert rep["n"] == 3
    assert len(rep["rows"]) == 3
    a = rep["summary"]["attrition_red"]
    assert a["mean"] == pytest.approx(0.15)
    assert a["ci95"] > 0
    assert a["lo"] < a["mean"] < a["hi"]
    assert rep["summary"]["success"]["count"] == 2


def test_replication_ci_single_run():
    from brigsim.support.battle_loss import replication_ci
    rep = replication_ci([{"attrition_red": 0.1, "success": True}])
    assert rep["summary"]["attrition_red"]["ci95"] == 0.0
    assert rep["summary"]["success"]["mean"] == 1.0


def test_business_timeline_empty():
    from brigsim.support.battle_loss import business_timeline
    assert business_timeline(None)["source"] == "empty"
    assert business_timeline([])["stages"] == [] if not hasattr([], "empty") else True


def test_targets_validate_and_plan_slot():
    from brigsim.support.mission_input import validate
    p, w = validate({
        "task_name": "阈值测试", "base_scenario": "medium_brigade", "duration_ticks": 12,
        "equipment": [{"key": "tank", "count": 10}],
        "targets": {"readiness": 93, "success": 88, "repair_timeliness": 86},
    })
    assert p["targets"] == {"readiness": "93%", "success": "88%", "repair_timeliness": "86%"}
    from brigsim.support.plan import build_slots
    loss = {"summary": {"by_grade": {}}, "items": []}
    slots = build_slots(p, loss)
    assert slots["target_readiness"] == "93%"
    assert slots["target_success"] == "88%"
    assert slots["target_repair_timeliness"] == "86%"


def test_targets_default():
    from brigsim.support.mission_input import validate
    p, _ = validate({"equipment": [{"key": "tank", "count": 10}]})
    assert p["targets"]["readiness"] == "90%"


def test_provenance_map():
    from brigsim.support.plan import build_provenance
    slots = {
        "pred_k": "3", "roster_total": 40, "enemy_situation": "敌情文字",
        "target_readiness": "92%",
    }
    prov = build_provenance(slots, "预计战毁 3 台，参战 40 台，完好率不低于 92%。")
    s = prov["slots"]
    assert "pred_k" in s and s["pred_k"]["in_text"] is True
    assert s["pred_k"]["source_label"] == "战损清单"
    assert "enemy_situation" not in s       # LLM 叙述槽位不进溯源
    assert s["target_readiness"]["source_label"] == "决策阈值"


def test_docx_bytes():
    from brigsim.support.plan import to_docx_bytes
    data = to_docx_bytes({"text": "**一、情况判断**\n敌情：.test。\n\n**| 表 |**\n| a | b |\n|---|---|\n| 1 | 2 |"})
    assert data[:2] == b"PK"                 # zip/docx magic
    assert len(data) > 1000
