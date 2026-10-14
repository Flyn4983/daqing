"""本地推理服务的 HTTP 接口（仅标准库，无新依赖）。

- GET  /              智能参谋 Web 界面（单 HTML，离线可用，无外部 CDN）
- GET  /api/status    推理服务状态 + 当前作战情景（界面 2s 轮询）
- POST /api/chat      {"text": "..."} → 智能体回答 + 函数调用轨迹
- POST /api/plan      {"tag": "knee|leanest|strongest"} → 一键生成保障方案
- GET  /api/requests  最近推理请求日志（服务监控用）

用 ThreadingHTTPServer：推理含模拟流式等待时不阻塞状态轮询。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .engine import AgentEngine
from .service import LocalLLMService
from .tools import PLAN_TAGS

_INDEX_HTML = Path(__file__).parent / "web" / "index.html"


class AgentWebServer:
    def __init__(
        self,
        engine: AgentEngine | None = None,
        host: str = "127.0.0.1",
        port: int = 8848,
        simulate_delay: bool = True,
    ) -> None:
        self.engine = engine or AgentEngine(
            service=LocalLLMService(simulate_delay=simulate_delay),
            simulate_delay=simulate_delay,
        )
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """后台线程启动，返回访问 URL；端口被占用时自动向后找。"""
        httpd = ThreadingHTTPServer((self.host, self.port), _make_handler(self.engine))
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def _make_handler(engine: AgentEngine):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 静默标准访问日志
            pass

        def _send_json(self, obj: dict, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/status":
                self._send_json({
                    "service": engine.service.status(),
                    "context": engine.tools.context_info(),
                })
            elif path == "/api/requests":
                self._send_json({"requests": engine.service.recent(20)})
            elif path in ("/", "/index.html"):
                html = _INDEX_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            else:
                self._send_json({"error": "not found"}, code=404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                if path == "/api/chat":
                    text = str(payload.get("text", "")).strip()
                    if not text:
                        self._send_json({"error": "text 不能为空"}, code=400)
                        return
                    self._send_json(engine.ask(text).as_dict())
                elif path == "/api/plan":
                    tag = str(payload.get("tag", "knee"))
                    if tag not in PLAN_TAGS:
                        self._send_json({"error": f"tag 必须是 {sorted(PLAN_TAGS)}"}, code=400)
                        return
                    self._send_json(engine.plan(tag).as_dict())
                else:
                    self._send_json({"error": "not found"}, code=404)
            except (ValueError, json.JSONDecodeError):
                self._send_json({"error": "请求体必须是合法 JSON"}, code=400)
            except Exception as exc:  # 边界兜底：任何引擎异常返回 JSON 而非断连
                self._send_json({"error": f"智能体处理失败：{exc}"}, code=500)

    return _Handler
