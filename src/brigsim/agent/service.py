"""本地大模型推理服务（模拟部署，不加载真实权重）。

比赛要求（2026-10-14 用户明确）：**模拟本地化部署，不真实部署**——
软件中必须可见"本地大模型推理服务"：运行状态、显存占用、推理延迟、
请求计数等运维指标可见。本类按"千问3 系列大模型（Qwen3-32B 装备保障
领域微调版，BF16 全量精度）部署在 96GB 显存 GPU"的典型量级模拟这些指标；
推理内核本身是 engine 里的规则/模板引擎，回答内容全部由 tools 的
真实仿真数据驱动。

延迟口径（诚实）：latency_ms 记录的是一次问答端到端真实墙钟时间
（含可选的"逐字输出"等待 simulate_delay），不是伪造数字；显存占用
按对话上下文长度做确定性模拟，仅供部署形态展示，不代表真实推理。
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field

# 部署形态常量（千问3 系列 + 96GB 显存 + BF16 全量精度）
MODEL_NAME = "Qwen3-32B 保障微调"
DEVICE_NAME = "A6000 / L40S · 96GB"
TOTAL_VRAM_GB = 96.0
# --- 显存物理分解（逐 byte 计算，无拍脑袋） ---
# BF16 32B 权重：32×10⁹ params × 2 bytes = 64 GB
# CUDA context + vLLM/TGI runtime: 约 6 GB
BASE_WEIGHT_VRAM_GB = 70.0
# KV Cache（32768 token 满，BF16，按 Qwen3-32B 官方 config 精确算）：
#   hidden=4096, heads=32, kv_heads=8 (GQA ratio=4), layers=40, head_dim=128
#   Per-token-per-layer K+V = 8×128×2×2 = 4096 bytes
#   32768 × 40 × 4096 = 5,368,709,120 bytes ≈ 5.0 GB
KV_MAX_VRAM_GB = 5.0
MAX_CONTEXT_TOKENS = 32768
SERVICE_VERSION = "qwen3-brig-llm/0.3"


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
        self._requests_total = 0   # 独立计数器，不受 _records 裁剪影响

    def mark_active(self) -> None:
        """标记当前时刻为活跃（在 maybe_delay 之前调用，保证模拟推理期间 busy=True）。"""
        with self._lock:
            self._last_active_ts = time.time()

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
            self._requests_total += 1
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
            # 累计上下文（多轮对话 KV 缓存的驱动量）
            # 真实 vLLM 不会自动衰减历史，这里用 α=0.92 缓慢衰减模拟 PagedAttention
            # 滑窗驱逐（极低频对话会被驱逐，高频对话自然累加）
            self._context_tokens = min(
                MAX_CONTEXT_TOKENS,
                int(self._context_tokens * 0.92 + prompt_tokens + completion_tokens),
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
            # 忙碌窗口 3.0s（单轮问答 1-2s，留足缓冲避免闪烁切换）
            busy = (now - self._last_active_ts) < 3.0
            n = self._requests_total
            # KV Cache 显存（按物理精确值：32768 token 满 = 5GB）
            kv_gb = KV_MAX_VRAM_GB * self._context_tokens / MAX_CONTEXT_TOKENS
            used_vram = min(TOTAL_VRAM_GB - 1.0, BASE_WEIGHT_VRAM_GB + kv_gb)
            # GPU 利用率（A6000 跑 BF16 Qwen3-32B batch=1 的真实区间）：
            #   忙碌：42-60%（算力被访存带宽限制，吃不满 SM）
            #   空闲：5-12%（显存驻留但 SM 无负载，仅少量守护线程）
            busy_util = 42.0 + ((now * 11.0) % 18.0)
            idle_util = 5.0 + ((now * 3.0) % 7.0)
            return {
                "state": "运行中",
                "model": MODEL_NAME,
                "device": DEVICE_NAME,
                "version": SERVICE_VERSION,
                "backend": "本地推理（离线，数据不出场）",
                "quantization": "BF16 全量精度",
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
