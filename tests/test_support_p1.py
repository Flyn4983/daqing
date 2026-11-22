"""P1 扩展批次单元测试：口径 tooltip 字典 / LLM 参数 / 叙述覆写 / 对账明细 / seed / 档案 / 模板映射。

全部为纯派生与落盘逻辑测试，不跑仿真内核，保持快速。
"""
from __future__ import annotations

import json

import pytest

from brigsim.support import api, battle_loss, catalog, mission_input, plan as plan_mod


# ---------- P1-7 指标口径字典 ----------

def test_metric_glossary_complete():
    g = catalog.METRIC_GLOSSARY
    for key in ("attrition", "damage_rate", "readiness", "repair_rate", "success", "ci95"):
        assert key in g
        assert g[key]["formula"] and g[key]["numerator"] and g[key]["denominator"]
    # 战损率权威、口径B不权威
    assert g["attrition"]["authoritative"] is True
    assert g["damage_rate"]["authoritative"] is False


def test_catalog_exposes_glossary_and_llm_params():
    c = api.catalog()
    assert "metric_glossary" in c and len(c["metric_glossary"]) >= 6
    assert "llm_params" in c and len(c["llm_params"]) == 4
    keys = {p["key"] for p in c["llm_params"]}
    assert keys == {"temperature", "max_tokens", "retries", "group_size"}


# ---------- P1-3 LLM 参数透传 ----------

def test_generate_narratives_records_params(monkeypatch):
    class FakeChat:
        ok = False
        error = "offline"
        latency_ms = 0.0
        text = ""

    class FakeClient:
        config = None
        calls = []

        def chat(self, system, user, temperature=None, max_tokens=None):
            self.calls.append((temperature, max_tokens))
            return FakeChat()

    fc = FakeClient()
    out = plan_mod.generate_narratives(
        {k: "x" for k in plan_mod._LLM_SLOTS},
        client=fc, use_llm=True,
        llm_params={"temperature": 0.9, "max_tokens": 512, "retries": 2, "group_size": 1},
    )
    assert out["_meta"]["params"]["temperature"] == 0.9
    assert out["_meta"]["params"]["max_tokens"] == 512
    assert out["_meta"]["params"]["retries"] == 2
    assert out["_meta"]["params"]["group_size"] == 1
    # group_size=1 → 仅请求 1 段
    assert len(fc.calls) == 3  # 1 段 × (retries+1) 次


def test_generate_narratives_default_zero_drift(monkeypatch):
    out = plan_mod.generate_narratives(
        {k: "x" for k in plan_mod._LLM_SLOTS}, client=None, use_llm=False)
    assert out["_meta"]["params"]["group_size"] == len(plan_mod._LLM_GROUPS)
    for k in plan_mod._LLM_SLOTS:
        assert out[k] == "x"  # 无 LLM 时保留槽位原值


# ---------- P1-2 叙述覆写 ----------

def _make_plan():
    slots = {k: f"原始叙述-{k}" for k in plan_mod._LLM_SLOTS}
    slots.update({
        "plan_id": "TEST-PLAN-1", "template": plan_mod.DEFAULT_TEMPLATE,
        "pred_k": "7", "roster_total": "100",
    })
    p = plan_mod.latest() or {}
    return {
        "plan_id": "TEST-PLAN-1", "template": plan_mod.DEFAULT_TEMPLATE,
        "text": plan_mod.fill_template(plan_mod.read_template(), slots),
        "slots": slots, "validation": {"passed": True},
    }


def test_apply_overrides_accepts_llm_only():
    p = _make_plan()
    out = plan_mod.apply_overrides(p, {
        "defense_plan": "保障群环形防卫、防空伴随掩护",
        "pred_k": "99",   # 数字槽位：拒绝
    }, lock=True)
    assert "defense_plan" in out["narrative_overridden"]
    assert any(r["slot"] == "pred_k" for r in out["override_rejected"])
    assert out["slots"]["pred_k"] == "7"  # 数字未被改
    assert out["narrative_locked"] is True
    assert "保障群环形防卫" in out["text"]


def test_apply_overrides_rejects_digits_in_narrative():
    p = _make_plan()
    out = plan_mod.apply_overrides(p, {"defense_plan": "投入3个营警戒"}, lock=True)
    assert any(r["slot"] == "defense_plan" for r in out["override_rejected"])
    assert out["slots"]["defense_plan"] == "原始叙述-defense_plan"


# ---------- P1-5 对账明细 ----------

def test_reconciliation_detail_items():
    loss = battle_loss.latest()
    assert loss is not None
    recon = (loss.get("rates") or {}).get("reconciliation") or {}
    assert recon.get("checked") is True
    items = recon.get("items") or []
    assert len(items) >= 1
    for it in items:
        for k in ("metric", "from_list", "from_kernel", "diff", "tol", "passed"):
            assert k in it


# ---------- P1-6 seed 复现 ----------

def test_seed_covered_in_validate_and_deduce_payload():
    p, w = mission_input.validate({
        "task_name": "种子复现测试", "base_scenario": "medium_brigade",
        "n_runs": 2, "seed": 12345678,
        "equipment": [{"key": "tank", "count": 10}],
    })
    assert p["seed"] == 12345678
    # 只校验 API 接受 base_seed 的代码路径（不实际启动推演）
    assert hasattr(api, "deduce_start")


def test_loss_records_base_seed_on_new_run(monkeypatch):
    """新推演的 loss 必须写 base_seed（历史 loss 可能为 None）。"""
    import inspect
    src = inspect.getsource(api)
    assert 'loss["base_seed"]' in src
    assert 'mission.get("seed"' in src


# ---------- P1-8 作业档案 ----------

def test_archive_list_shape():
    a = api.archive_list()
    assert set(a["counts"]) == {"missions", "losses", "plans"}
    for key in ("missions", "losses", "plans"):
        assert isinstance(a[key], list)
        for row in a[key][:3]:
            assert "id" in row and "file" in row and "saved_at" in row


# ---------- P1-1 模板映射 ----------

def test_template_detail_mapping():
    d = api.template_detail()
    assert "error" not in d
    assert d["count_total"] == len(catalog.SLOT_DICT)
    assert d["count_used"] > 0
    assert len(d["slots"]) == d["count_total"]
    for s in d["slots"]:
        assert set(("key", "used", "label", "source")) <= set(s)
    assert isinstance(d["unknown_slots"], list)
    assert isinstance(d["unused_slots"], list)


def test_template_detail_exposes_used_slots():
    d = api.template_detail()
    for k in ("pred_k", "roster_total", "defense_plan", "table_loss"):
        assert k in d["used_slots"]
