"""本地大模型推理服务（模拟部署，不加载真实权重）。

比赛要求（2026-10-14 用户明确）：**模拟本地化部署，不真实部署**——
软件中必须可见"本地大模型推理服务"：运行状态、显存占用、推理延迟、
请求计数等运维指标可见。本类按"7B 国产开源模型 INT4 量化部署在本机
4GB 显存 GPU"的典型量级模拟这些指标；推理内核本身是 engine 里的
规则/模板引擎，回答内容全部由 tools 的真实仿真数据驱动。

延迟口径（诚实）：latency_ms 记录的是一次问答端到端真实墙钟时间
（含可选的"逐字输出"等待 simulate_delay），不是伪造数字；显存占用
按对话上下文长度做确定性模拟，仅供部署形态展示，不代表真实推理。
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field

# 部署形态常量（按本机实际硬件 GTX 1650 Ti 4GB 设定，C6 录屏可见）
MODEL_NAME = "国产开源大模型 7B·装备保障微调版（INT4 量化）"
DEVICE_NAME = "本地 GPU：NVIDIA GTX 1650 Ti（4GB）"
TOTAL_VRAM_GB = 4.0
# INT4 7B 权重约 3.5-4GB 偏满，4GB 卡部署不现实；原型展示取 KV/权重
# 乐观估值 2.4GB，避免演示时显存爆条，数值在 DEVLOG 中明确标注为模拟假设
WEIGHT_VRAM_GB = 2.4
MAX_CONTEXT_TOKENS = 8192
SERVICE_VERSION = "brig-llm-local/0.1"


@dataclass
class InferenceRecord:
    """一次推理调用的留痕（C6 服务监控/请求日志用）。"""

    ts: float
    kind: str                 # plan / whatif / rates_def / pareto / status / chat ...
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    tool_calls: list[str] = field(default_factory=list)
    ok: bool = True


class LocalLLMService:
    """线程安全的本地推理服务状态机（ThreadingHTTPServer 下多请求并发）。"""

    def __init__(self, simulate_delay: bool = True) -> None:
        self.simulate_delay = simulate_delay
        self._lock = threading.Lock()
        self._start_ts = time.time()
        self._last_active_ts = 0.0
        self._records: list[InferenceRecord] = []
        self._context_tokens = 0
        self._latency_ema_ms = 0.0

    # ---- 推理留痕 ----

    def record(
        self,
        kind: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        tool_calls: list[str] | None = None,
        ok: bool = True,
    ) -> InferenceRecord:
        with self._lock:
            rec = InferenceRecord(
                ts=time.time(),
                kind=kind,
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                latency_ms=float(latency_ms),
                tool_calls=list(tool_calls or []),
                ok=ok,
            )
            self._records.append(rec)
            if len(self._records) > 200:
                self._records = self._records[-200:]
            self._last_active_ts = rec.ts
            # 累计上下文（多轮对话 KV 缓存的驱动量），缓慢衰减模拟历史滑窗
            self._context_tokens = min(
                MAX_CONTEXT_TOKENS,
                int(self._context_tokens * 0.7 + prompt_tokens + completion_tokens),
            )
            # EMA 平滑延迟（α=0.3）
            self._latency_ema_ms = (
                latency_ms
                if self._latency_ema_ms == 0.0
                else 0.3 * latency_ms + 0.7 * self._latency_ema_ms
            )
            return rec

    def maybe_delay(self, completion_tokens: int) -> None:
        """模拟流式生成的等待：约 0.8ms/输出 token，封顶 0.6s（录屏不卡顿）。"""
        if not self.simulate_delay or completion_tokens <= 0:
            return
        time.sleep(min(0.6, completion_tokens * 0.0008))

    # ---- 状态面板 ----

    def status(self) -> dict:
        """服务状态快照（C6 面板每 2s 轮询一次，数值随之"活"起来）。"""
        with self._lock:
            now = time.time()
            uptime = now - self._start_ts
            busy = (now - self._last_active_ts) < 1.2
            n = len(self._records)
            # KV 缓存随上下文增长（8192 上下文约占 0.9GB），再加 0.1GB 抖动余量
            kv_gb = 0.9 * self._context_tokens / MAX_CONTEXT_TOKENS
            used_vram = min(TOTAL_VRAM_GB - 0.2, WEIGHT_VRAM_GB + kv_gb)
            busy_util = 62.0 + ((now * 17.0) % 28.0)   # 忙时 62-90% 确定性波动
            idle_util = 2.0 + ((now * 5.0) % 4.0)      # 空闲 2-6%
            return {
                "state": "运行中",
                "model": MODEL_NAME,
                "device": DEVICE_NAME,
                "version": SERVICE_VERSION,
                "backend": "本地推理（离线，数据不出场）",
                "quantization": "INT4",
                "vram_total_gb": TOTAL_VRAM_GB,
                "vram_used_gb": round(used_vram, 2),
                "vram_percent": round(100.0 * used_vram / TOTAL_VRAM_GB, 1),
                "context_tokens": self._context_tokens,
                "context_limit": MAX_CONTEXT_TOKENS,
                "gpu_util_percent": round(busy_util if busy else idle_util, 1),
                "busy": busy,
                "requests_total": n,
                "avg_latency_ms": round(self._latency_ema_ms, 1),
                "tools_served": sum(len(r.tool_calls) for r in self._records),
                "uptime_s": round(uptime, 0),
            }

    def recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            return [asdict(r) for r in self._records[-n:][::-1]]
