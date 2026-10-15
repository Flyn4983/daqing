"""C5 智能体层测试：本地推理服务、函数工具（真实数据）、意图路由、HTTP 接口。

全部在 CPU 上跑（代理模型是 sklearn MLP，单次预测毫秒级），不依赖 GPU。
"""

from __future__ import annotations

import json
import urllib.request

import numpy as np
import pytest

from brigsim.agent import AgentEngine, AgentToolkit, LocalLLMService
from brigsim.agent.engine import parse_knobs
from brigsim.agent.server import AgentWebServer

# a2 口径 7 旋钮：决策名 → 工具参数名
_KNOB_TO_ARG = {
    "red_repair_teams": "teams",
    "red_arv": "arv",
    "red_supply_veh": "supply_veh",
    "carry_level": "carry",
    "operating_level": "operating",
    "wave_interval_ticks": "interval_ticks",
    "wave_qty": "wave_qty",
}


def _as_kwargs(decisions: dict) -> dict:
    return {_KNOB_TO_ARG[k]: int(v) for k, v in decisions.items()}


@pytest.fixture(scope="module")
def toolkit():
    return AgentToolkit()


@pytest.fixture(scope="module")
def baseline_kwargs(toolkit):
    return _as_kwargs(toolkit.context_info()["baseline_decisions"])


@pytest.fixture(scope="module")
def knee_kwargs(toolkit):
    return _as_kwargs(toolkit.generate_support_plan("knee")["decisions"])


@pytest.fixture()
def engine(toolkit):
    return AgentEngine(toolkit=toolkit, service=LocalLLMService(simulate_delay=False),
                       simulate_delay=False)


# ---- 本地推理服务 ----

def test_service_status_fields_and_recording():
    svc = LocalLLMService(simulate_delay=False)
    s0 = svc.status()
    assert s0["state"] == "运行中"
    assert s0["vram_used_gb"] < s0["vram_total_gb"] == 4.0
    assert s0["requests_total"] == 0

    svc.record("chat", prompt_tokens=100, completion_tokens=200, latency_ms=12.5,
               tool_calls=["predict_four_rates"])
    s1 = svc.status()
    assert s1["requests_total"] == 1 and s1["tools_served"] == 1
    assert s1["avg_latency_ms"] == pytest.approx(12.5, abs=0.01)
    assert s1["context_tokens"] > 0
    log = svc.recent()
    assert log[0]["kind"] == "chat" and log[0]["tool_calls"] == ["predict_four_rates"]


# ---- 函数工具：真实数据 ----

def test_predict_four_rates_returns_real_bounded_metrics(toolkit, baseline_kwargs):
    r = toolkit.predict_four_rates(**baseline_kwargs)
    assert set(r) >= {
        "success", "attrition_red", "attrition_blue", "readiness_red", "readiness_blue",
        "repair_rate_red", "repair_rate_blue", "failure_rate_100h_red", "failure_rate_100h_blue",
        "cost", "decisions",
    }
    for m in ("success", "attrition_red", "readiness_red", "repair_rate_red"):
        assert 0.0 <= r[m] <= 1.0
    assert r["failure_rate_100h_red"] >= 0.0
    assert 0.0 <= r["cost"] <= 1.0
    # v4 基线 6 抢修组；决策回显为完整 7 旋钮
    assert r["decisions"]["red_repair_teams"] == 6
    assert len(r["decisions"]) == 7


def test_knee_config_lies_on_pareto_front(toolkit, knee_kwargs):
    cmp_ = toolkit.compare_with_baseline(**knee_kwargs)
    assert cmp_["on_pareto_front"] is True
    assert cmp_["rank_tag"] == "knee"


def test_generate_support_plan_all_tags(toolkit):
    from brigsim.optimize.problem import DECISION_VARS

    for tag in ("knee", "leanest", "strongest"):
        p = toolkit.generate_support_plan(tag)
        assert p["tag"] == tag
        assert set(p["decisions"]) == set(DECISION_VARS)
        assert p["schedule"]["interval_minutes"] == p["decisions"]["wave_interval_ticks"] * 0.5
        # 每波器材 = 每波每部件件数 × 4 类部件
        assert p["schedule"]["per_wave_parts"] == p["decisions"]["wave_qty"] * 4
        # 9 指标 + 成本基线齐全
        assert len(p["metrics"]) == 9
        assert "cost" in p["baseline_metrics"]
        # 仿真复验证据（真实蒙特卡洛记录）
        metrics_checked = {r["metric"] for r in p["validation"]}
        assert {"success", "attrition_red", "readiness_red"} <= metrics_checked
        assert all(np.isfinite(r["simulation"]) for r in p["validation"])
    # strongest 成功率不低于 leanest
    strong = toolkit.generate_support_plan("strongest")["metrics"]["success"]
    lean = toolkit.generate_support_plan("leanest")["metrics"]["success"]
    assert strong >= lean


def test_pareto_query_and_bad_tag(toolkit):
    front = toolkit.query_pareto_front(top_n=8)
    assert front["n_plans"] >= 5
    assert len(front["plans"]) == min(8, front["n_plans"])
    knee_only = toolkit.query_pareto_front(tag="knee")
    assert len(knee_only["plans"]) == 1 and knee_only["plans"][0]["rank_tag"] == "knee"
    with pytest.raises(ValueError):
        toolkit.query_pareto_front(tag="nonsense")
    with pytest.raises(ValueError):
        toolkit.generate_support_plan("nonsense")


def test_rate_definitions_gjb(toolkit):
    defs = toolkit.get_rate_definitions()
    for k in ("完好率", "故障率", "修复率", "战损率", "任务完成率", "适用边界"):
        assert k in defs and defs[k]


# ---- 中文旋钮解析 ----

def test_parse_knobs_arabic_and_minutes():
    knobs = parse_knobs(
        "如果派5个抢修组、4辆抢救车、2辆器材车、携行档3档，"
        "每10分钟一个波次、每波每部件2件，四率怎样？"
    )
    assert knobs == {
        "red_repair_teams": 5,
        "red_arv": 4,
        "red_supply_veh": 2,
        "carry_level": 3,
        "wave_interval_ticks": 20,   # 10 分钟 = 20 tick（1 tick=30s）
        "wave_qty": 2,
    }


def test_parse_knobs_chinese_numerals_and_tick():
    knobs = parse_knobs("三个抢修组、四辆抢救车、携行档三档、运行档2档、周期20tick、每波每部件3件")
    assert knobs["red_repair_teams"] == 3
    assert knobs["red_arv"] == 4
    assert knobs["carry_level"] == 3
    assert knobs["operating_level"] == 2
    assert knobs["wave_interval_ticks"] == 20
    assert knobs["wave_qty"] == 3


def test_parse_knobs_clips_to_domain():
    # 99 抢修组超出可行域 [2,10] → 截到 10；未提及的旋钮不出现
    knobs = parse_knobs("配99个抢修组")
    assert knobs == {"red_repair_teams": 10}


# ---- 意图路由与回答 ----

def test_engine_plan_whatif_rates_pareto_status(engine, toolkit):
    r = engine.ask("一键生成 knee 保障方案")
    assert r.intent == "plan"
    assert r.tool_calls[0].name == "generate_support_plan"
    assert r.tool_calls[0].elapsed_ms >= 0
    assert "抢修组" in r.answer and "Pareto" in r.answer and "仿真复验" in r.answer

    r2 = engine.ask("如果派5个抢修组、4辆抢救车、携行档3档，每10分钟一个波次，预测四率？")
    assert r2.intent == "whatif"
    assert r2.tool_calls[0].name == "compare_with_baseline"
    assert "v4 基线" in r2.answer and "|" in r2.answer

    r3 = engine.ask("完好率是什么口径？")
    assert r3.intent == "rates_def" and r3.tool_calls[0].name == "get_rate_definitions"
    assert "可用装备" in r3.answer

    n_plans = toolkit.query_pareto_front()["n_plans"]
    r4 = engine.ask("Pareto 前沿一共给出了哪些方案？")
    assert r4.intent == "pareto" and r4.tool_calls[0].name == "query_pareto_front"
    assert str(n_plans) in r4.answer

    r5 = engine.ask("推理服务什么状态？显存占多少？")
    assert r5.intent == "status" and "显存" in r5.answer and "运行中" in r5.answer


def test_engine_whatif_partial_knobs(engine):
    # 只给两个旋钮，其余 5 个以 None 透传（工具端按 v4 基线补齐）
    r = engine.ask("如果只给3个抢修组、4辆抢救车，四率会怎样？")
    assert r.intent == "whatif"
    assert r.tool_calls[0].arguments == {
        "teams": 3, "arv": 4, "supply_veh": None, "carry": None,
        "operating": None, "interval_ticks": None, "wave_qty": None,
    }
    assert "按基线取值" in r.answer


def test_engine_tagged_plans_and_fallback(engine):
    r = engine.ask("给我资源最省的保障方案")
    assert r.intent == "plan" and r.tool_calls[0].arguments == {"tag": "leanest"}
    r2 = engine.ask("保障最强的方案")
    assert r2.intent == "plan" and r2.tool_calls[0].arguments == {"tag": "strongest"}
    r3 = engine.ask("明天天气怎么样？")
    assert r3.intent == "fallback"
    assert len(engine.service.recent()) == 3


def test_engine_plan_helper(engine):
    r = engine.plan("knee")
    assert r.intent == "plan"
    assert r.tool_calls[0].arguments == {"tag": "knee"}


# ---- HTTP 接口（标准库服务，随机端口）----

def test_web_server_api(toolkit):
    server = AgentWebServer(
        engine=AgentEngine(toolkit=toolkit,
                           service=LocalLLMService(simulate_delay=False),
                           simulate_delay=False),
        host="127.0.0.1", port=0, simulate_delay=False,
    )
    url = server.start()
    try:
        with urllib.request.urlopen(url + "/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
            assert resp.status == 200 and "本地大模型" in html

        with urllib.request.urlopen(url + "/api/status", timeout=5) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            assert d["service"]["state"] == "运行中"
            assert d["context"]["duration_ticks"] == 96

        req = urllib.request.Request(
            url + "/api/chat",
            data=json.dumps({"text": "完好率是什么口径？"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            assert d["intent"] == "rates_def"
            assert d["tool_calls"][0]["name"] == "get_rate_definitions"
            assert d["latency_ms"] >= 0

        req = urllib.request.Request(
            url + "/api/plan",
            data=json.dumps({"tag": "knee"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            assert d["intent"] == "plan" and "折中" in d["answer"]

        bad = urllib.request.Request(
            url + "/api/chat",
            data=json.dumps({"text": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(bad, timeout=5)
        assert ei.value.code == 400
    finally:
        server.stop()
