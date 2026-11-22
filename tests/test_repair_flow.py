"""人工 Word 战损清单导入 + 抢修方案生成 单元测试。

覆盖：
- 模板生成是有效 docx、含填写说明与等级口径；
- 解析容错：别名表头 / K/M/F 映射 / 数量真实 / 示例行排除 / 器材部位映射；
- 数字不伪造：未填 roster_size 时 roster=0、告警提示、不臆造战损率分母；
- 两种来源（仿真 events / 人工 docx）都能进同一抢修生成链路；
- 抢修方案：plan_kind、数字校验通过、0 未替换槽位、plan_name 标注抢修；
- 只读性：解析与生成不改权威 pareto/verification 数据文件。
"""
from __future__ import annotations

import base64
import hashlib
import io

import pytest

from brigsim.support import loss_import, mission_input, repair_plan
from brigsim.support import api as support_api


def _docx_bytes(rows: list[list[str]], *, header: list[str] | None = None) -> bytes:
    """基于真实模板生成一份填好的 Word。"""
    tpl = loss_import.build_report_template_docx()
    from docx import Document
    d = Document(io.BytesIO(tpl))
    t = d.tables[0]
    if header:
        for i, h in enumerate(header):
            t.rows[0].cells[i].text = h
    # 覆盖示例行
    if rows:
        for i, v in enumerate(rows[0]):
            t.rows[1].cells[i].text = v
        for r in rows[1:]:
            rr = t.add_row()
            for i, v in enumerate(r):
                rr.cells[i].text = v
    else:
        # 清空示例行
        for i in range(len(t.rows[1].cells)):
            t.rows[1].cells[i].text = ""
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _mission() -> dict:
    m, _ = mission_input.validate({
        "task_name": "山地进攻", "base_scenario": "medium_brigade",
        "equipment": [{"key": "tank", "count": 100}, {"key": "ifv", "count": 20}],
        "targets": {"readiness": "90", "success": "85", "repair_timeliness": "80"},
    })
    m["mission_id"] = "T-TEST-001"
    return m


def _loss_items() -> list[dict]:
    return [
        {"side_code": 0, "grade": "M", "grade_code": 1, "damage_part": "mobility",
         "disposition": "现地处置/抢修", "repair_status": "待修", "count": 3, "platform": "主战坦克"},
        {"side_code": 0, "grade": "F", "grade_code": 2, "damage_part": "fire_control",
         "disposition": "后送", "repair_status": "待修", "count": 2, "platform": "步兵战车"},
        {"side_code": 0, "grade": "K", "grade_code": 3, "damage_part": "",
         "disposition": "报废", "repair_status": "不可修", "count": 1, "platform": "自行火炮"},
    ]


# ---------------- 模板 ----------------

def test_template_is_valid_docx():
    data = loss_import.build_report_template_docx()
    assert data[:2] == b"PK"
    assert len(data) > 5000
    from docx import Document
    d = Document(io.BytesIO(data))
    assert len(d.tables) >= 1
    head = "".join(c.text for c in d.tables[0].rows[0].cells)
    assert "装备" in head and "数量" in head and "战损等级" in head
    body = "\n".join(p.text for p in d.paragraphs)
    assert "战毁" in body and "可现地抢修" in body  # 等级判定说明


# ---------------- 解析容错 ----------------

def test_parse_grades_counts_parts():
    rows = [
        ["1", "一连", "主战坦克", "3", "M 机动受损", "行走总成", "反坦克导弹", "现地抢修", "高地西侧", ""],
        ["2", "二连", "步兵战车", "2", "F 火力受损", "火控系统", "炮弹", "后送", "谷地", ""],
        ["3", "三连", "自行火炮", "1", "K 战毁", "车体", "导弹", "报废", "阵地内", ""],
    ]
    loss = loss_import.parse_loss_docx(_docx_bytes(rows), roster_size=120)
    assert loss["source"] == "manual_docx"
    assert len(loss["items"]) == 3
    grades = {it["grade"] for it in loss["items"]}
    assert grades == {"M", "F", "K"}
    assert loss["summary"]["by_grade"] == {"M": 3, "F": 2, "K": 1}
    parts = {it["platform"]: it["damage_part"] for it in loss["items"]}
    assert parts["主战坦克"] == "mobility"
    assert parts["步兵战车"] == "fire_control"
    k = [it for it in loss["items"] if it["grade"] == "K"][0]
    assert k["disposition"] == "报废" and k["repair_status"] == "不可修"
    assert loss["summary"]["roster_size"] == 120


@pytest.mark.parametrize("grade_text,expected", [
    ("战毁", "K"), ("报废", "K"), ("重损", "F"), ("需后送", "F"),
    ("机动受损", "M"), ("轻损", "M"), ("可修", "M"), ("K级", "K"),
])
def test_grade_aliases(grade_text, expected):
    assert loss_import._map_grade(grade_text) == expected


def test_custom_headers_aliases():
    header = ["序", "分队", "车型", "台数", "损伤程度", "损坏部件", "原因", "处理", "地域", "说明"]
    rows = [["1", "四连", "装甲车", "5", "轻损", "履带", "地雷", "就地抢修", "山路", ""]]
    loss = loss_import.parse_loss_docx(_docx_bytes(rows, header=header), roster_size=60)
    assert len(loss["items"]) == 1
    it = loss["items"][0]
    assert it["count"] == 5 and it["grade"] == "M"
    assert it["damage_part"] == "mobility"  # 履带 → 行走总成
    assert it["disposition"] == "现地处置/抢修"


def test_example_and_total_rows_excluded():
    # 模板自带一行"示例"，未覆盖时应被排除且报错（无有效行）
    with pytest.raises(ValueError, match="没有解析到任何有效战损行"):
        loss_import.parse_loss_docx(loss_import.build_report_template_docx(), roster_size=100)


def test_no_roster_size_no_fabrication():
    rows = [["1", "一连", "主战坦克", "2", "轻损", "行走", "", "现地抢修", "", ""]]
    loss = loss_import.parse_loss_docx(_docx_bytes(rows), roster_size=0)
    assert loss["summary"]["roster_size"] == 0
    assert any("参战总数" in w or "分母" in w for w in loss["warnings"])


def test_bad_input_raises():
    with pytest.raises(ValueError):
        loss_import.parse_loss_docx(b"not a docx")


# ---------------- API 导入落盘 ----------------

def test_api_import_roundtrip(tmp_path):
    rows = [["1", "一连", "主战坦克", "4", "机动受损", "动力", "", "现地", "", ""]]
    b64 = base64.b64encode(_docx_bytes(rows)).decode()
    r = support_api.loss_import_docx({"content_b64": b64, "roster_size": 100, "note": "单测"})
    assert "error" not in r, r
    assert r["n_items"] == 1 and r["source"] == "manual_docx"
    from brigsim.support import battle_loss
    loaded = battle_loss.load(r["loss_id"])
    assert loaded and loaded["items"][0]["count"] == 4


# ---------------- 抢修方案生成 ----------------

def test_repair_plan_from_manual_loss():
    loss = {"source": "manual_docx", "n_runs": 0, "items": _loss_items(),
            "summary": {"roster_size": 120, "roster_size_red": 120,
                        "by_grade": {"M": 3, "F": 2, "K": 1}, "by_disposition": {}}}
    plan = repair_plan.generate(_mission(), loss, use_llm=False)
    assert plan["plan_kind"] == "repair"
    assert "人工填报" in plan["loss_source"]
    assert "抢修方案" in plan["plan_name"]
    assert plan["validation"]["passed"]
    assert not plan["validation"]["unfilled_slots"]
    assert "{{" not in plan["text"]
    assert "报损情况" in plan["text"] and "分级抢修处置" in plan["text"]
    assert plan["slots"]["pred_k"] == 1 and plan["slots"]["pred_m"] == 3


def test_repair_plan_from_sim_loss():
    # 仿真来源同构：source=events 走同一链路，无分支
    loss = {"source": "events", "n_runs": 8, "items": _loss_items(),
            "summary": {"roster_size": 120, "roster_size_red": 120,
                        "by_grade": {"M": 3, "F": 2, "K": 1}, "by_disposition": {}}}
    plan = repair_plan.generate(_mission(), loss, use_llm=False)
    assert plan["plan_kind"] == "repair"
    assert "事件流" in plan["loss_source"]
    assert plan["validation"]["passed"]


def test_grade_legend_matches_catalog():
    leg = repair_plan.grade_legend()
    assert "K" in leg and "F" in leg and "M" in leg
    assert "只计 K" in leg["authoritative_attrition"]


def test_no_authoritative_file_mutation():
    from pathlib import Path
    import sys
    root = Path(__file__).resolve().parents[1]
    pf = root / "src" / "brigsim" / "agent" / "web" / "data" / "pareto.json"
    vf = root / "src" / "brigsim" / "agent" / "web" / "data" / "verification.json"
    h0 = (hashlib.md5(pf.read_bytes()).hexdigest(), hashlib.md5(vf.read_bytes()).hexdigest())
    loss = {"source": "manual_docx", "items": _loss_items(),
            "summary": {"roster_size": 120, "by_grade": {"M": 3, "F": 2, "K": 1}, "by_disposition": {}}}
    repair_plan.generate(_mission(), loss, use_llm=False)
    h1 = (hashlib.md5(pf.read_bytes()).hexdigest(), hashlib.md5(vf.read_bytes()).hexdigest())
    assert h0 == h1  # 只读，不改权威数字
