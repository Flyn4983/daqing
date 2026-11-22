"""本地大模型适配层（OpenAI 兼容协议，可插拔、配置驱动）。

设计目标（2026-11-18 需求）：
- 软件与本地大模型**同机部署**，走 127.0.0.1 回环，零外网；
- 推理框架**暂不确定** → 统一走 OpenAI 兼容 ``/v1/chat/completions``，
  vLLM / Ollama(/v1) / LM Studio / Xinference 均可无缝接入，换框架不改代码；
- 现场演示必须**真实调用**；同时保留 mock/auto 模式，模型未就绪时自动降级，
  保证任何时刻都能演示（不翻车）。

【配置优先级】环境变量 > 项目根 llm_config.json > 默认值
    BRIGSIM_LLM_BASE_URL   默认 http://127.0.0.1:8000/v1
    BRIGSIM_LLM_MODEL      默认 Qwen3.8-27B（Ubuntu ARM 部署目标；以实际模型 ID 为准）
    BRIGSIM_LLM_API_KEY    默认 EMPTY（vLLM/Ollama/llama.cpp 本地无需）
    BRIGSIM_LLM_TIMEOUT    默认 180 秒（ARM CPU 上 27B 推理慢，60 秒容易超时）
    BRIGSIM_LLM_MODE       real | mock | auto（默认 auto）

仅用标准库 urllib，无第三方依赖 —— 迁移到 ARM 工作站无需 pip 安装额外包。
27B CPU 推理适配：方案叙述由 support/plan.py 拆为 4 段（每段≤900 tokens）分别请求，
单段失败只降级该段为模板文字；auto 模式下模型未就绪不影响战损清单等确定性环节。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


DEFAULT_CONFIG = {
    "base_url": "http://127.0.0.1:8000/v1",
    "model": "Qwen3.8-27B",
    "api_key": "EMPTY",
    "timeout": 180,
    "mode": "auto",
    "temperature": 0.3,
    "max_tokens": 1024,
}


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    timeout: float
    mode: str
    temperature: float
    max_tokens: int

    @classmethod
    def load(cls) -> "LLMConfig":
        cfg = dict(DEFAULT_CONFIG)
        # 1) 项目根 llm_config.json
        cfg_path = _repo_root() / "llm_config.json"
        if cfg_path.is_file():
            try:
                user_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(user_cfg, dict):
                    cfg.update({k: v for k, v in user_cfg.items() if k in DEFAULT_CONFIG})
            except Exception:
                pass
        # 2) 环境变量覆盖
        env_map = {
            "base_url": "BRIGSIM_LLM_BASE_URL",
            "model": "BRIGSIM_LLM_MODEL",
            "api_key": "BRIGSIM_LLM_API_KEY",
            "timeout": "BRIGSIM_LLM_TIMEOUT",
            "mode": "BRIGSIM_LLM_MODE",
        }
        for key, env in env_map.items():
            if env in os.environ:
                cfg[key] = os.environ[env]
        cfg["timeout"] = float(cfg["timeout"])
        cfg["temperature"] = float(cfg["temperature"])
        cfg["max_tokens"] = int(cfg["max_tokens"])
        mode = str(cfg["mode"]).lower()
        if mode not in ("real", "mock", "auto"):
            mode = "auto"
        cfg["mode"] = mode
        return cls(**cfg)


@dataclass
class ChatResult:
    text: str
    ok: bool
    source: str          # "local_llm" | "fallback" | "mock"
    latency_ms: float
    error: str = ""
    usage: dict | None = None


class LocalLLMClient:
    """OpenAI 兼容本地大模型客户端。线程安全无状态，可被多线程 HTTP handler 共享。"""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.load()
        self._available: bool | None = None  # None=未探测
        self._last_error = ""

    # ---- 连通性探测 ----

    def is_available(self, force: bool = False) -> bool:
        if self.config.mode == "mock":
            return False
        if self._available is not None and not force:
            return self._available
        url = self.config.base_url.rstrip("/") + "/models"
        req = urllib.request.Request(url, headers=self._auth_headers())
        try:
            with urllib.request.urlopen(req, timeout=min(5, self.config.timeout)) as resp:
                self._available = resp.status == 200
        except Exception as exc:  # noqa: BLE001 - 探测失败一律视为不可用（降级）
            self._last_error = f"{type(exc).__name__}: {exc}"[:200]
            self._available = False
        return self._available

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    # ---- 文本生成 ----

    def chat(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """调用本地大模型生成文本。失败返回 ok=False，调用方负责降级。"""
        if self.config.mode == "mock":
            return ChatResult("", False, "mock", 0.0, "mock 模式：未调用真实模型")
        if not self.is_available():
            return ChatResult(
                "", False, "fallback", 0.0,
                f"本地模型未就绪（{self._last_error or self.config.base_url}）",
            )

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._auth_headers(),
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["choices"][0]["message"]["content"] or ""
            latency = (time.perf_counter() - t0) * 1000
            self._available = True
            return ChatResult(text, True, "local_llm", latency, usage=body.get("usage"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:200]
            self._last_error = f"HTTP {exc.code}: {detail}"
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"[:200]
        self._available = False
        return ChatResult(
            "", False, "fallback", (time.perf_counter() - t0) * 1000, self._last_error
        )

    # ---- 状态读数（供 /api/status 展示真实后端） ----

    def status(self) -> dict:
        return {
            "mode": self.config.mode,
            "base_url": self.config.base_url,
            "model": self.config.model,
            "available": self.is_available() if self.config.mode != "mock" else False,
            "running_real": bool(self.config.mode == "real" and self.is_available()),
            "last_error": self._last_error,
        }
