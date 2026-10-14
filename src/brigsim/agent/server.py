"""本地推理服务的 HTTP 接口（仅标准库，无新依赖）。

- GET  /              指挥大屏 Web 界面（单 HTML，离线可用，无外部 CDN）
- GET  /static/<rel>  大屏静态资源（css/js/svg/png/ico，限定在 web 目录内）
- GET  /data/<rel>    dash-build 预生成数据（web/data 下的 JSON）
- GET  /api/status    推理服务状态 + 当前作战情景（界面 2s 轮询）
- GET  /api/viewer    3D 回放（rerun web viewer）挂载状态与 iframe URL
- POST /api/chat      {"text": "..."} → 智能体回答 + 函数调用轨迹
- POST /api/plan      {"tag": "knee|leanest|strongest"} → 一键生成保障方案
- GET  /api/requests  最近推理请求日志（服务监控用）

用 ThreadingHTTPServer：推理含模拟流式等待时不阻塞状态轮询。
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .engine import AgentEngine
from .service import LocalLLMService
from .tools import PLAN_TAGS

_WEB_ROOT = Path(__file__).parent / "web"
_DATA_ROOT = _WEB_ROOT / "data"
_STATIC_EXTS = {".css", ".js", ".svg", ".png", ".ico", ".woff2"}
_DATA_EXTS = {".json"}


def _safe_join(root: Path, rel: str) -> Path | None:
    """把相对路径约束在 root 内（防目录穿越）；越界/绝对路径返回 None。"""
    if not rel or rel.startswith(("/", "\\")):
        return None
    try:
        root = root.resolve()
        target = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if root not in target.parents and target != root:
        return None
    return target


class AgentWebServer:
    def __init__(
        self,
        engine: AgentEngine | None = None,
        host: str = "127.0.0.1",
        port: int = 8848,
        simulate_delay: bool = True,
        rrd_path: str | Path | None = None,
        enable_replay: bool = True,
    ) -> None:
        self.engine = engine or AgentEngine(
            service=LocalLLMService(simulate_delay=simulate_delay),
            simulate_delay=simulate_delay,
        )
        self.host = host
        self.port = port
        # C6：3D 回放 rrd（默认 outputs/dash/replay_knee.rrd，仓库根目录）
        repo_root = Path(__file__).resolve().parents[3]
        if rrd_path is not None:
            p = Path(rrd_path)
            if not p.is_absolute() and not p.exists():
                p = repo_root / p  # 相对路径按仓库根再找一次
            self.rrd_path = p
        else:
            self.rrd_path = repo_root / "outputs" / "dash" / "replay_knee.rrd"
        self.enable_replay = enable_replay
        self.viewer_status: dict = {"ready": False, "reason": "未启动"}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """后台线程启动，返回访问 URL；端口被占用时自动向后找。"""
        if self.enable_replay:
            try:
                from .replay_host import launch_replay_viewer

                self.viewer_status = launch_replay_viewer(self.rrd_path)
            except Exception as exc:  # 回放服务是增强项，任何异常都降级而非阻断
                self.viewer_status = {"ready": False, "reason": f"3D 回放启动失败：{exc}"}
        handler = _make_handler(self.engine, self.viewer_status)
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
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


def _make_handler(engine: AgentEngine, viewer_status: dict | None = None):
    viewer_status = viewer_status or {"ready": False, "reason": "未启用"}

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

        def _send_file(self, rel: str, root: Path, allowed_exts: set[str]) -> None:
            target = _safe_join(root, rel)
            if target is None or target.suffix not in allowed_exts or not target.is_file():
                self._send_json({"error": "not found"}, code=404)
                return
            body = target.read_bytes()
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if target.suffix in {".js", ".css", ".json"}:
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/status":
                self._send_json({
                    "service": engine.service.status(),
                    "context": engine.tools.context_info(),
                })
            elif path == "/api/viewer":
                self._send_json(viewer_status)
            elif path == "/api/requests":
                self._send_json({"requests": engine.service.recent(20)})
            elif path in ("/", "/index.html"):
                self._send_file("index.html", _WEB_ROOT, {".html"})
            elif path.startswith("/static/"):
                self._send_file(path[len("/static/"):], _WEB_ROOT, _STATIC_EXTS)
            elif path.startswith("/data/"):
                self._send_file(path[len("/data/"):], _DATA_ROOT, _DATA_EXTS)
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
