"""B1/B2 本地大模型适配层测试（不依赖真实模型，起线程内 mock OpenAI 服务）。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from brigsim.agent.engine import AgentEngine
from brigsim.agent.llm_client import LLMConfig, LocalLLMClient


class _MockOpenAI(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "Qwen3-32B"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n))
        _MockOpenAI.calls.append(payload)
        body = json.dumps({
            "choices": [{"message": {"content": "这是本地大模型生成的测试方案说明，压减抢修组后依靠双闸守卫与复验维持战力再生。"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def openai_server():
    _MockOpenAI.calls.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockOpenAI)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _cfg(port, mode="real"):
    return LLMConfig(
        base_url=f"http://127.0.0.1:{port}/v1", model="Qwen3-32B",
        api_key="EMPTY", timeout=10, mode=mode, temperature=0.3, max_tokens=256,
    )


def test_llm_client_real_chat(openai_server):
    client = LocalLLMClient(_cfg(openai_server))
    assert client.is_available() is True
    r = client.chat("你是参谋", "生成方案")
    assert r.ok and r.source == "local_llm"
    assert "大模型生成" in r.text
    assert r.latency_ms >= 0


def test_llm_client_mock_mode():
    client = LocalLLMClient(_cfg(1, mode="mock"))
    assert client.is_available() is False
    r = client.chat("sys", "user")
    assert not r.ok and r.source == "mock"


def test_llm_client_auto_degrade():
    client = LocalLLMClient(_cfg(1, mode="auto"))  # 端口1必然不可用
    r = client.chat("sys", "user")
    assert not r.ok and r.source == "fallback"


def test_engine_plan_with_real_llm(openai_server):
    _MockOpenAI.calls.clear()
    engine = AgentEngine(llm_client=LocalLLMClient(_cfg(openai_server)))
    reply = engine.plan("knee")
    assert reply.llm_source == "local_llm"
    assert "方案制定说明" in reply.answer
    assert reply.llm_narrative
    assert reply.llm_latency_ms >= 0
    # prompt 里必须含真实数字（0.458 基线成本、抢修组等），确保不让模型编数字
    prompt = _MockOpenAI.calls[0]["messages"][1]["content"]
    assert "0.458" in prompt or "抢修组" in prompt
    assert reply.as_dict()["llm_source"] == "local_llm"


def test_engine_plan_degrade_without_model():
    engine = AgentEngine(llm_client=LocalLLMClient(_cfg(1, mode="real")))
    reply = engine.plan("knee")
    assert reply.llm_source == "fallback"
    assert "本地大模型未就绪" in reply.answer
    assert "方案" in reply.answer  # 模板数字仍在
