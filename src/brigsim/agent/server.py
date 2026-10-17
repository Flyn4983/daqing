"""本地推理服务的 HTTP 接口（仅标准库，无新依赖）。

- GET  /              指挥大屏 Web 界面（单 HTML，离线可用，无外部 CDN）
- GET  /static/<rel>  大屏静态资源（css/js/svg/png/ico，限定在 web 目录内）
- GET  /data/<rel>    dash-build 预生成数据（web/data 下的 JSON）
- GET  /api/status    推理服务状态 + 当前作战情景（界面 2s 轮询）
- GET  /api/viewer    3D 回放（rerun web viewer）挂载状态与 iframe URL
- POST /api/chat      {"text": "..."} → 智能体回答 + 函数调用轨迹
- POST /api/plan      {"tag": "knee|leanest|strongest"} → 一键生成保障方案
- GET  /api/requests  最近推理请求日志（服务监控用）
- GET  /api/scenarios 工况列表（C7-c：7 个 YAML，terrain/environment 元数据）
- POST /api/run       {"scenario": "...", "seed": 42, "max_ticks": 0} → 后台线程开跑，返回 run_id
- GET  /api/run?run_id=X → 轮询进度：tick / progress / 最新状态 / 四率瞬时
- POST /api/run-stop  {"run_id": "..."} → 停止推演（尽快）

用 ThreadingHTTPServer：推理含模拟流式等待时不阻塞状态轮询。
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import AgentEngine
from .service import LocalLLMService
from .tools import PLAN_TAGS

_WEB_ROOT = Path(__file__).parent / "web"
_DATA_ROOT = _WEB_ROOT / "data"
# C7-c：scenarios 目录约定在仓库根 scenarios/
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCENARIOS_ROOT = _REPO_ROOT / "scenarios"
_STATIC_EXTS = {".css", ".js", ".svg", ".png", ".ico", ".woff2"}
_DATA_EXTS = {".json"}

# ---- C7-c：实时推演会话池 ----
_RUNS_LOCK = threading.Lock()
_RUNS: dict[str, "RunSession"] = {}


class RunSession:
    """一个后台推演会话：tick 日志（环形最多 512 tick）+ 进度事件。"""

    def __init__(self, scenario_name: str, seed: int, max_ticks: int) -> None:
        self.run_id = uuid.uuid4().hex[:10]
        self.scenario_name = scenario_name
        self.seed = seed
        self.max_ticks = max_ticks  # 0=完整 duration_ticks
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.started_at = time.time()
        self.finished_at: float | None = None
        # tick 日志：list of dict，每项 {"tick", "alive_red", "alive_blue", "pos_red", "pos_blue"}
        self.log: list[dict] = []
        # 最新 tick 数（快速 query）
        self.last_tick: int = -1
        # 预估总 tick（Worker 真实加载后会刷新；POST /api/run 返回用这个）
        self.total_ticks: int = self._guess_duration(scenario_name, max_ticks)
        self.n_red: int = 0
        self.n_blue: int = 0
        self.error: str | None = None
        self.lock = threading.Lock()

    @staticmethod
    def _guess_duration(name: str, max_ticks: int) -> int:
        if max_ticks > 0:
            return max_ticks
        for s in _list_scenarios():
            if s.get("file") == name:
                return int(s.get("duration_ticks", 48))
        return 48


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


def _quick_parse_scenario_meta(yaml_path: Path) -> dict:
    """C7-c：快速 YAML 元数据读取——只解析顶级 name/mission/terrain/environment，
    不走 YamlFileProvider 的 preset factory + HeightField 完整加载（那个耗时）。"""
    try:
        import yaml  # 运行时延迟导入；本项目已装

        with yaml_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        return {"error": f"YAML 解析失败：{exc}"}

    name = str(raw.get("name", yaml_path.stem))
    mission = raw.get("mission", {}) or {}
    terrain = raw.get("terrain", {}) or {}
    env = raw.get("environment", {}) or {}
    forces = raw.get("forces", {}) or {}
    n_red = len(forces.get("red") or [])
    n_blue = len(forces.get("blue") or [])

    return {
        "name": name,
        "file": yaml_path.stem,
        "duration_ticks": int(mission.get("duration_ticks", 0)),
        "dt_seconds": int(mission.get("dt_seconds", 30)),
        "terrain_preset": terrain.get("preset_family"),
        "terrain_source": terrain.get("source", "synthetic"),
        "terrain_amplitude": terrain.get("amplitude"),
        "weather": env.get("weather", "clear"),
        "illumination": env.get("illumination", "day"),
        "visibility_factor": env.get("visibility_factor", 1.0),
        "entities": {"red": n_red, "blue": n_blue, "total": n_red + n_blue},
    }


def _list_scenarios() -> list[dict]:
    """扫 scenarios/*.yaml → 元数据列表；按 duration_ticks 升序。"""
    out: list[dict] = []
    if not _SCENARIOS_ROOT.is_dir():
        return out
    for p in sorted(_SCENARIOS_ROOT.glob("*.yaml")):
        out.append(_quick_parse_scenario_meta(p))
    out.sort(key=lambda d: d.get("duration_ticks", 0))
    return out


def _run_worker(sess: RunSession) -> None:
    """后台线程：加载 Scenario（含 preset 工厂）→ 跑 MesaKernel → 逐 tick 入 log。"""
    try:
        from ..combat.mesa_kernel import MesaKernel
        from ..data.provider import YamlFileProvider

        sc = YamlFileProvider().load_scenario(sess.scenario_name)
        duration = sess.max_ticks if sess.max_ticks > 0 else int(sc.mission.duration_ticks)
        sess.total_ticks = duration
        sess.n_red = len(sc.forces.get("red") or [])
        sess.n_blue = len(sc.forces.get("blue") or [])

        kernel = MesaKernel(sc, seed=sess.seed)
        side_vectors = [
            [u.unit_id for u in sc.forces.get("red") or []],
            [u.unit_id for u in sc.forces.get("blue") or []],
        ]

        for step_i in range(duration):
            if sess.stop_event.is_set():
                break
            kernel.step()
            tick = kernel.state.tick
            alive = kernel.state.alive  # bool array, 长度 n_total
            n_red = sess.n_red
            alive_r = int(alive[:n_red].sum())
            alive_b = int(alive[n_red:].sum())
            xs = kernel.state.x.tolist()
            ys = kernel.state.y.tolist()
            pos_red = [
                {"id": side_vectors[0][i], "x": xs[i], "y": ys[i]}
                for i in range(min(3, n_red))
            ]
            pos_blue = [
                {"id": side_vectors[1][i], "x": xs[i + n_red], "y": ys[i + n_red]}
                for i in range(min(3, sess.n_blue))
            ]
            with sess.lock:
                sess.log.append({
                    "tick": tick,
                    "alive_red": alive_r,
                    "alive_blue": alive_b,
                    "total_red": n_red,
                    "total_blue": sess.n_blue,
                    "pos_red": pos_red,
                    "pos_blue": pos_blue,
                })
                sess.last_tick = tick
            # 每 tick 约 0.1s，让 stop_event 能尽快响应（实际不用 sleep）
        sess.done_event.set()
    except Exception as exc:
        import traceback

        sess.error = f"{exc}\n{traceback.format_exc()}"
        sess.done_event.set()
    finally:
        sess.finished_at = time.time()


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
        # C7-c：先停所有后台推演会话，再关 HTTP server
        with _RUNS_LOCK:
            for s in _RUNS.values():
                s.stop_event.set()
            _RUNS.clear()
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
            qs = parse_qs(urlparse(self.path).query)
            if path == "/api/status":
                self._send_json({
                    "service": engine.service.status(),
                    "context": engine.tools.context_info(),
                })
            elif path == "/api/viewer":
                self._send_json(viewer_status)
            elif path == "/api/requests":
                self._send_json({"requests": engine.service.recent(20)})
            elif path == "/api/scenarios":
                # C7-c：快速返回 7 工况元数据，不完整加载（preset/HF 昂贵）
                self._send_json({"scenarios": _list_scenarios()})
            elif path == "/api/run":
                # C7-c：轮询实时推演状态
                rid = (qs.get("run_id") or [None])[0]
                if not rid:
                    self._send_json({"error": "需要 run_id"}, code=400)
                    return
                with _RUNS_LOCK:
                    sess = _RUNS.get(rid)
                if sess is None:
                    self._send_json({"error": "run_id 不存在"}, code=404)
                    return
                with sess.lock:
                    log_snapshot = sess.log[-120:]  # 环形尾部保留
                progress = sess.last_tick + 1 if sess.last_tick >= 0 else 0
                total = sess.total_ticks or 1
                self._send_json({
                    "run_id": sess.run_id,
                    "scenario": sess.scenario_name,
                    "tick": sess.last_tick,
                    "progress": min(progress / total, 1.0),
                    "total_ticks": sess.total_ticks,
                    "done": sess.done_event.is_set(),
                    "error": sess.error,
                    "n_red": sess.n_red,
                    "n_blue": sess.n_blue,
                    "log": log_snapshot,
                })
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
                elif path == "/api/run":
                    # C7-c：启动后台推演线程
                    scenario_name = str(payload.get("scenario", "")).strip()
                    if not scenario_name:
                        self._send_json({"error": "需要 scenario 名"}, code=400)
                        return
                    seed = int(payload.get("seed", 42))
                    max_ticks = int(payload.get("max_ticks", 0))
                    sess = RunSession(scenario_name=scenario_name, seed=seed, max_ticks=max_ticks)
                    with _RUNS_LOCK:
                        # 最多保留 8 个并发 run（前端一个即可）
                        if len(_RUNS) >= 8:
                            oldest_key = next(iter(_RUNS))
                            _RUNS[oldest_key].stop_event.set()
                            del _RUNS[oldest_key]
                        _RUNS[sess.run_id] = sess
                    threading.Thread(target=_run_worker, args=(sess,), daemon=True).start()
                    self._send_json({
                        "run_id": sess.run_id,
                        "scenario": scenario_name,
                        "seed": seed,
                        "total_ticks": sess.total_ticks,
                        "message": "推演已启动，前端用 GET /api/run?run_id=X 轮询",
                    })
                elif path == "/api/run-stop":
                    rid = str(payload.get("run_id", "")).strip()
                    with _RUNS_LOCK:
                        sess = _RUNS.pop(rid, None)
                    if sess is None:
                        self._send_json({"error": "run_id 不存在"}, code=404)
                        return
                    sess.stop_event.set()
                    self._send_json({"run_id": rid, "stopped": True})
                else:
                    self._send_json({"error": "not found"}, code=404)
            except (ValueError, json.JSONDecodeError):
                self._send_json({"error": "请求体必须是合法 JSON"}, code=400)
            except Exception as exc:  # 边界兜底：任何引擎异常返回 JSON 而非断连
                import traceback

                self._send_json({
                    "error": f"处理失败：{exc}",
                    "trace": traceback.format_exc(),
                }, code=500)

    return _Handler
