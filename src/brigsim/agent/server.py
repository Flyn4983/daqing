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
- GET  /rerun-viewer/<rel>  反向代理 rerun WASM viewer 静态资源（同源、本地、无外网）
- POST /rerun-data/<rel>    反向代理 rerun gRPC(grpc-web) 数据通道（同源流式转发）

用 ThreadingHTTPServer：推理含模拟流式等待时不阻塞状态轮询。
"""

from __future__ import annotations

import http.client
import json
import mimetypes
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import AgentEngine
from .service import LocalLLMService
from .tools import PLAN_TAGS
from ..support import api as support_api

_WEB_ROOT = Path(__file__).parent / "web"
_DATA_ROOT = _WEB_ROOT / "data"
# C7-c：scenarios 目录约定在仓库根 scenarios/
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCENARIOS_ROOT = _REPO_ROOT / "scenarios"
_STATIC_EXTS = {".css", ".js", ".svg", ".png", ".ico", ".woff2"}
_DATA_EXTS = {".json"}
# C6-fix：rerun WASM viewer / gRPC 的同源反向代理前缀（只绑 127.0.0.1 回环，
# 浏览器只与大屏自身 origin 通信——不经过任何代理、不跨端口、无 CORS、完全本地）
_VIEWER_PREFIX = "/rerun-viewer"
_GRPC_PREFIX = "/rerun-data"


def _strip_external_refs(html: str) -> str:
    """把 rerun WASM viewer HTML 改造成"全程本地、零外网请求"：

    1. 剥掉 Google Fonts 外链（fonts.googleapis.com / fonts.gstatic.com）；
    2. 分析脚本守卫置 false（fifteen-thirtyeight.rerun.io，仅 app.rerun.io 生效）；
    3. **注入 CSP**（connect-src 'self' 等）——浏览器层面硬阻断 rerun WASM
       内置的遥测（tel.rerun.io）与示例清单（app.rerun.io）请求，
       保证代理/断网环境下 3D 可视化不因外网请求挂起。

    注意：分析脚本与 WASM 引导代码同在一个 <script> 里，**绝不能整块删除**。
    """
    # 1) Google Fonts 外链（<link ... href="https://fonts.*...">）
    html = re.sub(
        r'[ \t]*<link[^>]*href="https://fonts\.(?:googleapis|gstatic)\.com[^"]*"[^>]*>[ \t]*\n?',
        "",
        html,
    )
    # 2) 分析脚本守卫置 false（0.39：仅 app.rerun.io 生效；本地永不进入分支）
    html = html.replace('window.location.hostname == "app.rerun.io"', 'false')
    # 3) 兜底：移除指向 rerun.io 的外链 <script src="https://..."> 整标签
    html = re.sub(
        r'[ \t]*<script[^>]*src="https://[^"]*rerun\.io[^"]*"[^>]*>\s*</script>[ \t]*\n?',
        "",
        html,
    )
    # 4) 注入 CSP：资源与连接全部锁死在本 origin（同源反向代理）
    #    - connect-src 'self'：gRPC 数据通道同源；遥测/示例清单被浏览器直接阻断
    #    - script-src 需 'unsafe-inline'（内联引导脚本）+ 'wasm-unsafe-eval'（WASM 实例化）
    csp = (
        '<meta http-equiv="Content-Security-Policy" content="'
        "default-src 'self'; "
        "connect-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "worker-src 'self' blob:; "
        "media-src 'self' blob:; "
        "frame-src 'self'"
        '">'
    )
    if "<head>" in html:
        html = html.replace("<head>", "<head>\n    " + csp, 1)
    return html


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
        self.thread: threading.Thread | None = None  # worker 线程引用（僵死会话回收用）
        self.client: str = ""                        # 调用方 IP（诊断用）
        self.last_progress_at = time.time()          # 最近一次 tick 进度（卡死回收用）
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
        # S3：全参数配置推演需要
        self.config_params: dict | None = None
        # S5：推演后离线分析状态（pending → running → done）
        self.analysis_state: dict = {"status": "not_needed"}

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
    _LABEL_ZH = {
        "medium_brigade":              "中型合成旅 · 丘陵快攻",
        "brigade_canyon_clear":        "重型旅 · 峡谷晴日攻坚",
        "brigade_plain_fog":           "重型旅 · 平原浓雾突击",
        "brigade_rolling_fortified":   "重型旅 · 丘陵坚固防御",
        "example_brigade":             "重型旅 · 合成兵种示例",
        "example_company":             "连级 · 装步冲击示例",
        "example_battalion":           "营级 · 三阶段进攻示例",
        "real_terrain_brigade":        "★真实地形 · 太行山地形+ERA5气象",
    }
    out: list[dict] = []
    if not _SCENARIOS_ROOT.is_dir():
        return out
    for p in sorted(_SCENARIOS_ROOT.glob("*.yaml")):
        meta = _quick_parse_scenario_meta(p)
        file_stem = p.stem
        meta["label_zh"] = _LABEL_ZH.get(file_stem, file_stem.replace("_", " "))
        # 检查是否有预构建的 replay JSON（web/data/manifest.json 匹配 scenario 字段）
        meta["has_replay_data"] = _has_replay_data(file_stem)
        out.append(meta)
    out.sort(key=lambda d: d.get("duration_ticks", 0))
    return out


def _has_replay_data(scenario_name: str) -> bool:
    """先查 scenarios/{name}/manifest.json（批量构建新路径）；
    没有则回退查顶层 data/manifest.json 的 scenario 字段（老的 example_brigade 单想定路径）。"""
    import json as _json
    per_name = _WEB_ROOT / "data" / "scenarios" / scenario_name / "manifest.json"
    if per_name.is_file():
        return True
    legacy = _WEB_ROOT / "data" / "manifest.json"
    if not legacy.is_file():
        return False
    try:
        with legacy.open(encoding="utf-8") as f:
            m = _json.load(f)
        return m.get("scenario") == scenario_name
    except Exception:
        return False


# ---------- C6.1：工况切换（下拉菜单 → 3D 场景按需重建） ----------
# 背景：C6 起 rerun WASM iframe 取代了 Three.js 画布，旧的下拉联动
# （threeScene.setScenario）失效，两种模式点下拉都无 3D 反应。现在：
# - live 模式：POST /api/scene-switch {mode:"preview"} → 不跑仿真，直接把该想定的
#   地形/通行性/气象/静态要素灌进 rerun sink（Clear + reset_time + 重日志）；
# - replay 模式：{mode:"replay"} → 子进程构建该想定回放 rrd（独立进程避免
#   rerun 全局 sink 二次 init 硬错误），就绪后 Clear + log_file_from_path 重灌，
#   viewer 与大屏 origin 的连接不变，数据推送即刷新。
#
# sink 单写线程：rr.log_file_from_path 是"启动即返回、后台持续流式"——relog 后
# importer 线程仍在向 sink 队列推数 MB 数据；若此时在 HTTP 处理线程里直接
# rr.log（地形网格+贴图约 4MB），会撞上队列背压而永久阻塞（viewer 未连接时
# 无人排空）。所有 sink 变异统一排队到单写线程：HTTP 立即返回，3D 排到即生效。
_SINK_Q = None
_SINK_WRITER_STARTED = False


def _ensure_sink_writer() -> None:
    global _SINK_Q, _SINK_WRITER_STARTED
    if _SINK_WRITER_STARTED:
        return
    _SINK_WRITER_STARTED = True
    import queue as _queue

    _SINK_Q = _queue.Queue()

    def _loop() -> None:
        while True:
            fn = _SINK_Q.get()
            try:
                fn()
            except Exception as exc:  # 写失败不影响 HTTP 与其他写入
                print(f"[sink-writer] 写入失败：{exc}", flush=True)

    threading.Thread(target=_loop, daemon=True, name="sink-writer").start()


def _sink_submit(fn) -> None:
    _ensure_sink_writer()
    _SINK_Q.put(fn)


_SCENE_BUILD: dict[str, dict] = {}
_SCENE_LOCK = threading.Lock()

# C7-d：relog 的后台 importer 是"启动即返回、持续流式"（C6.1 教训）——rrd 越大
# 灌数越久（17MB 实测 ~35s、33MB ~60s）。此期间的任何 Clear+重写都会被 importer
# 的迟到 chunk 覆盖（实测：replay 切 live 预览后，旧回放内容持续混入）。
# 记录最近一次 relog 提交时间，预览/推演起步前等 importer 泄放完毕再清场。
_LAST_RELOG_AT = 0.0
_RELOG_DRAIN_SECS = 75.0  # 30MB rrd @ ~550KB/s ≈ 55s + 余量
_PREVIEW_GEN = 0  # 预览代际（防止旧预览的延迟二次应用冲掉新预览）


def _drain_delay() -> float:
    """距 importer 泄放完毕还需等待的秒数（已泄放则 0）。"""
    import time as _t

    remain = _RELOG_DRAIN_SECS - (_t.time() - _LAST_RELOG_AT)
    return max(0.0, remain)


def _wait_for_drain() -> None:
    import time as _t

    waited = 0.0
    while _drain_delay() > 0:
        _t.sleep(1.0)
        waited += 1.0
    if waited >= 1.0:
        print(f"[scene-drain] 已等待回放 importer 泄放 {waited:.0f}s", flush=True)


def _has_active_runs() -> bool:
    """是否有未完成的推演会话（live 3D 正在被逐 tick 写入）。"""
    with _RUNS_LOCK:
        return any(not s.done_event.is_set() for s in _RUNS.values())


def _scene_rrd_path(scenario: str) -> Path:
    return _REPO_ROOT / "outputs" / "dash" / f"replay_{scenario}.rrd"


def _resolve_scene_rrd(scenario: str) -> Path | None:
    """优先该想定自己的 rrd；example_brigade 回退到 dash-build 的 replay_knee.rrd。"""
    p = _scene_rrd_path(scenario)
    if p.is_file():
        return p
    legacy = _REPO_ROOT / "outputs" / "dash" / "replay_knee.rrd"
    if scenario == "example_brigade" and legacy.is_file():
        return legacy
    return None


def _relog_rrd(rrd: Path) -> None:
    """把 rrd 重新灌进当前 rerun sink（排队执行）：Clear → reset_time → 流式重灌。

    注意（C7-e）：replay 模式已改静态文件直载，本函数当前无调用方，保留作
    gRPC 通道的备用灌装入（如未来恢复"sink 内回放"能力）；调用它会推高
    sink 累积历史，新 viewer 连接需重下全部数据，慎用。
    """
    global _LAST_RELOG_AT

    def _apply() -> None:
        import rerun as rr

        rr.Clear(recursive=True)
        rr.reset_time()
        rr.log_file_from_path(str(rrd))

    import time as _t

    _LAST_RELOG_AT = _t.time()  # importer 从此开始向 sink 灌数
    _sink_submit(_apply)


def _preview_scenario(scenario: str) -> dict:
    """live 模式工况预览：无推演时把该想定的地形/气象/静态要素灌进 3D（不跑仿真）。"""
    from ..data.provider import YamlFileProvider
    from ..kernel.terrain import HeightField
    from ..viz import rerun_view

    _t0 = time.monotonic()

    with _RUNS_LOCK:
        # 僵死会话回收：worker 线程已退出但 done_event 未置位（异常路径遗漏）→ 补置位，
        # 避免预览被一个永远不会完成的会话永久锁定
        stale = [k for k, s in _RUNS.items()
                 if not s.done_event.is_set() and s.thread is not None and not s.thread.is_alive()]
        for k in stale:
            _RUNS[k].done_event.set()
            print(f"[run] 回收僵死会话 {k}（{_RUNS[k].scenario_name}）：worker 线程已退出但未标记完成", flush=True)
        # 卡死会话回收：线程还活着但 10 分钟无 tick 进度（如被 GIL 饿死/死循环）→ 兜底置位
        now = time.time()
        hung = [k for k, s in _RUNS.items()
                if not s.done_event.is_set() and now - s.last_progress_at > 600]
        for k in hung:
            _RUNS[k].done_event.set()
            print(f"[run] 回收卡死会话 {k}（{_RUNS[k].scenario_name}）：10 分钟无 tick 进度", flush=True)
        active = any(not s.done_event.is_set() for s in _RUNS.values())
    if active:
        print(f"[scene-switch] preview · {scenario} · 已锁定（有进行中的推演）", flush=True)
        return {"ok": False, "reason": "推演进行中，3D 场景已锁定（停止后可切换）"}

    sc = YamlFileProvider().load_scenario(scenario)
    hf = HeightField.from_spec(sc.terrain)
    # C7-l：live 预览用该工况专属真实 DEM 子区（仅渲染/LOS，仿真数据不变）
    hf = rerun_view._visual_terrain(hf, scenario)
    print(f"[scene-preview] {scenario} 想定加载耗时 {time.monotonic() - _t0:.2f}s", flush=True)

    def _preview_apply() -> None:
        import rerun as rr

        rr.Clear(recursive=True)
        rr.reset_time()
        # C7-d：渲染垂直夸张 + 缩放地形副本（物理高程不变）
        z_exag = rerun_view.auto_z_exag(hf)
        geo = hf.scaled(z_exag)
        rerun_view._log_terrain(hf, sc.environment.illumination, z_exag)
        rerun_view._log_map_frame(geo, sc.environment.illumination)
        rerun_view._log_passability(geo)
        rerun_view._log_objective(sc, geo)
        rerun_view._log_repair_stations(sc, geo)
        rerun_view._log_depots(sc, geo)
        rerun_view._log_obstacles(sc, geo)
        rerun_view._log_decoys(sc, geo)
        rerun_view._log_weather_header(sc)
        # C7-l：无预构建 rrd 时的 sink 兜底预览也预载真实空中模型
        try:
            air_pairs_pre = [
                (f"{p.side[:1]}{p.kind[:2]}{i:02d}", p.kind)
                for i, p in enumerate(getattr(sc, "air_packages", []) or [])
            ]
            preloaded = rerun_view._preload_air_models(air_pairs_pre)
        except Exception:
            preloaded = set()
        # C7-d：静态战术快照——tick-0 单位图元 + 射界扇面 + 标签。
        # live 模式不跑仿真也能看到编成/部署/装备作用范围（用户反馈"没有明显效果"）
        try:
            import pandas as pd

            from ..combat.mesa_kernel import MesaKernel

            kernel = MesaKernel(sc, seed=20261014)  # 仅取初始站位，不 step
            info = rerun_view._unit_info(sc)
            # C7-l：静态地面 GLB 必须先于首个 set_time 发出（与回放/live 同款）
            rerun_view._log_static_units(info, hf, use_models=True)
            z_off_pre = {
                typ: rerun_view.ground_offset(typ) for typ in {v[0] for v in info.values()}
            }
            uids = [u.unit_id for s in ("red", "blue") for u in (sc.forces.get(s) or [])]
            px = kernel.state.x.tolist()
            py = kernel.state.y.tolist()
            cur = pd.DataFrame(
                {
                    "x": px,
                    "y": py,
                    "heading": kernel.state.heading.tolist(),
                    "alive": [True] * len(px),
                },
                index=uids,
            )
            zs0 = geo.height_at(cur["x"].to_numpy(), cur["y"].to_numpy())
            rr.set_time("tick", sequence=0)
            rerun_view._update_unit_models(cur, zs0, info, z_off_pre, {}, tint=False)
            rerun_view._log_markers(cur=cur, zs=zs0, info=info)
            rerun_view._log_status_flags(cur, zs0, info)  # C7-m：图例状态色载体
            rerun_view._log_fans(cur, zs0, info, outline=True, geo=geo, los_terrain=hf)
            # C7-e：体系作用包络（雷达/防空/电抗/C2 圈）——此前 live/预览路径
            # 完全缺失，用户反馈"作用范围圈看不见"
            # C7-h：传真实地形 hf 做 LOS 遮挡（山体背后不画），与内核通视一致
            rerun_view._log_coverages(0, cur, zs0, info, thin=True, geo=geo, los_terrain=hf)
            # C7-k：预览也展示空中装备（tick-0 起飞架次），不是圆球
            air0_rows = []
            for i, p in enumerate(getattr(sc, "air_packages", []) or []):
                if int(p.launch_tick) != 0:
                    continue
                aid = f"{p.side[:1]}{p.kind[:2]}{i:02d}"
                xy = p.waypoints[0] if p.waypoints else (sc.terrain.size_w / 2, sc.terrain.size_h / 2)
                air0_rows.append({
                    "tick": 0, "asset_id": aid, "side": 0 if p.side == "red" else 1,
                    "kind": p.kind, "x": float(xy[0]), "y": float(xy[1]),
                    "alt": float(p.altitude_m), "state": "active",
                })
            if air0_rows:
                import pandas as pd
                rerun_view._log_air(
                    0, pd.DataFrame(air0_rows), {}, set(),
                    use_models=rerun_view.AIR_USE_REAL_MODELS,
                    model_logged=set(preloaded),
                    ground_max=float(geo.heights.max()),
                )
        except Exception as exc:
            print(f"[scene-preview] 静态单位/扇面写入失败：{exc}", flush=True)
        # C7-d：气象按 24 tick 预播——修复此前 _log_weather(0,...) 未 set_time、
        # 在 embed 蓝图（timeline=tick）latest-at 查询下整层丢失的问题；
        # 蓝图 autoplay（fps=12, loop）会驱动雾团漂移/雨雪下落
        try:
            for tk in range(24):
                rr.set_time("tick", sequence=tk)
                rerun_view._log_weather(tk, sc, geo)
        except Exception as exc:
            print(f"[scene-preview] 气象预播失败：{exc}", flush=True)
        print(f"[工况切换] {scenario} 地形/气象预览已写入 3D", flush=True)

    global _PREVIEW_GEN
    _PREVIEW_GEN += 1
    gen = _PREVIEW_GEN
    try:
        _sink_submit(_preview_apply)
        # C7-d：importer 泄放后二次清场重写——冲掉旧回放 rrd 的迟到 chunk
        # （replay→live 切换后旧内容会持续混入预览，实测复现）
        delay = _drain_delay() + 10.0
        if delay > 0:

            def _reapply(_gen=gen) -> None:
                if _PREVIEW_GEN != _gen:
                    return  # 已有更新的预览/切换，放弃本次
                if _has_active_runs():
                    return  # 等待期间用户已启动推演，放弃（避免 Clear 掉 live 3D 内容）
                _wait_for_drain()
                _sink_submit(_preview_apply)

            threading.Timer(delay, _reapply).start()
    except Exception as exc:
        return {"ok": False, "reason": f"3D 预览失败：{exc}"}
    return {
        "ok": True,
        "queued": True,
        "scenario": scenario,
        "terrain_source": sc.terrain.source,
        "terrain_preset": sc.terrain.preset_family,
        "weather": sc.environment.weather,
        "visibility_factor": sc.environment.visibility_factor,
    }


def _scene_build_worker(scenario: str) -> None:
    """后台线程：子进程构建单想定回放 rrd → 就绪（前端轮询状态后切静态文件）。"""
    out = _scene_rrd_path(scenario)
    with _SCENE_LOCK:
        st = _SCENE_BUILD[scenario] = {"status": "building", "rrd": str(out), "error": None}
    try:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "brigsim.cli", "dash-replay", scenario, "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=1200,
            cwd=str(_REPO_ROOT),
        )
        if proc.returncode != 0 or not out.is_file():
            st["status"] = "error"
            st["error"] = (proc.stderr or proc.stdout or "未知错误")[-400:]
            return
        # C7-e：不再 relog 进 sink（会累积历史、拖垮后续 viewer 连接）；
        # 前端 scene-status 轮询到 ready 后直接把 iframe 切到 /rerun-file/<rrd>
        st["status"] = "ready"
        print(f"[工况切换] {scenario} 回放 rrd 已生成：{out}", flush=True)
    except Exception as exc:
        st["status"] = "error"
        st["error"] = str(exc)[-400:]


def _log_scenario_terrain(rr, scenario_name: str, sc) -> None:
    """rerun 可视化地形：按想定名区分颜色/形状，让用户一眼感知切换生效。"""
    w = float(sc.terrain.size_w)
    h = float(sc.terrain.size_h)
    cx, cy = w / 2, h / 2
    base = [[cx, cy, -20.0]]
    base_size = [[w, h, 40.0]]

    # 预设 → 颜色映射（3D 里一眼可辨）
    TERRAIN_VIS = {
        "brigade_canyon_clear":        {"color": [80, 60, 45], "feature": "canyon"},
        "brigade_plain_fog":           {"color": [150, 145, 135], "feature": "fog"},
        "brigade_rolling_fortified":   {"color": [85, 110, 75],  "feature": "rolling"},
        "medium_brigade":              {"color": [105, 130, 85], "feature": "rolling"},
    }
    vis = TERRAIN_VIS.get(scenario_name, {"color": [30, 50, 70], "feature": "plain"})

    rr.log(
        "live/terrain/base",
        rr.Boxes3D(centers=base, sizes=base_size, colors=[vis["color"]]),
    )

    # 特色地形：canyon 画两侧高墙 + 中间走廊；fog 画半透明雾层
    if vis["feature"] == "canyon":
        wall_h = 120.0
        wall_y1 = cy - h * 0.30
        wall_y2 = cy + h * 0.30
        corridor_w = h * 0.40  # 走廊宽度
        rr.log(
            "live/terrain/wall_top",
            rr.Boxes3D(
                centers=[[cx, wall_y1 + corridor_w / 2, wall_h], [cx, wall_y2 - corridor_w / 2, wall_h]],
                sizes=[[w, h / 2 - corridor_w / 2, wall_h * 2]],
                colors=[[60, 50, 38], [60, 50, 38]],
            ),
        )
        # 中间走廊
        rr.log(
            "live/terrain/corridor",
            rr.Boxes3D(
                centers=[[cx, cy, -10.0]],
                sizes=[[w, corridor_w, 10.0]],
                colors=[[105, 90, 70]],
            ),
        )
    elif vis["feature"] == "fog":
        # 大雾想定：顶部一层半透明（rerun 不支持 alpha 但深灰暗示雾天）
        rr.log(
            "live/terrain/fog_layer",
            rr.Boxes3D(
                centers=[[cx, cy, 60.0]],
                sizes=[[w * 0.8, h * 0.8, 120.0]],
                colors=[[180, 180, 180]],
            ),
        )


def _run_worker(sess: RunSession) -> None:
    """后台线程：Clear 旧 knee.rrd → MesaKernel → _log_terrain → 逐 tick rr.log 单位 → 入 log → 离线分析。"""
    try:
        from ..combat.mesa_kernel import MesaKernel
        from ..data.provider import YamlFileProvider

        # --- C6-live：清 replay_host 预 replay 的 knee.rrd 旧数据，建立干净 live 流 ---
        # C6.1：经单写线程排队，避免与 relog 的 importer 线程争队列堵死 worker
        # C7-d：先等 relog 的 importer 泄放完毕再清场——否则旧回放 rrd 的迟到
        # chunk 会在 Clear 之后继续灌入，与 live tick 数据混合（实测复现）
        def _live_reset() -> None:
            import rerun as rr

            _wait_for_drain()
            rr.Clear(recursive=True)
            rr.reset_time()

        try:
            _sink_submit(_live_reset)
        except Exception:
            pass

        sc = YamlFileProvider().load_scenario(sess.scenario_name)

        # S3：配置驱动覆盖 —— 覆盖 mission/terrain/environment 顶层字段
        if sess.config_params:
            sc = _apply_config_overrides(sc, sess.config_params, skip_terrain_env=True)

        duration = sess.max_ticks if sess.max_ticks > 0 else int(sc.mission.duration_ticks)
        sess.total_ticks = duration
        sess.n_red = len(sc.forces.get("red") or [])
        sess.n_blue = len(sc.forces.get("blue") or [])

        kernel = MesaKernel(sc, seed=sess.seed)

        # --- C6-live：把真实 HeightField 地形推给 rerun WASM（覆盖旧 knee.rrd DEM） ---
        # C6.1：经单写线程排队（地形网格+贴图约 4MB，直接 log 可能撞队列背压）
        try:
            from ..viz import rerun_view

            _terrain_hf = kernel.terrain
            # C7-l：live 逐 tick 用该工况专属真实 DEM 子区（仅渲染/LOS，仿真数据不变）
            _terrain_hf = rerun_view._visual_terrain(_terrain_hf, getattr(sc, "name", None))
            # C7-d：渲染垂直夸张 + 缩放地形副本（物理高程不变，仅渲染）
            _z_exag = rerun_view.auto_z_exag(_terrain_hf)
            _geo_hf = _terrain_hf.scaled(_z_exag)

            def _live_terrain(
                hf=_terrain_hf, geo=_geo_hf, zx=_z_exag,
                illum=sc.environment.illumination,
            ) -> None:
                rerun_view._log_terrain(hf, illum, zx)
                rerun_view._log_map_frame(geo, illum)
                rerun_view._log_passability(geo)

            _sink_submit(_live_terrain)
        except Exception:
            pass

        side_vectors = [
            [u.unit_id for u in sc.forces.get("red") or []],
            [u.unit_id for u in sc.forces.get("blue") or []],
        ]

        # C6.1：每 tick 气象粒子 + 单位标签 + 射界扇面（与回放 rrd 同款可视化），
        # 让 live 对抗过程也能体现想定的天气/地形差异
        live_info = None
        live_uids: list[str] = []
        live_sides: list[int] = []
        air_trails: dict[str, list] = {}
        air_done: set[str] = set()
        air_model_logged: set[str] = set()
        live_z_off: dict[str, float] = {}
        live_model_state: dict[str, str] = {}
        # C7-k：ID 规则与 combat/systems.py 一致（side 首字母 + kind 前两字母 + 两位序号）
        air_pairs = [
            (f"{p.side[:1]}{p.kind[:2]}{i:02d}", p.kind)
            for i, p in enumerate(getattr(sc, "air_packages", []) or [])
        ]
        try:
            from ..viz import rerun_view

            live_info = rerun_view._unit_info(sc)
            live_uids = side_vectors[0] + side_vectors[1]
            live_sides = [0] * len(side_vectors[0]) + [1] * len(side_vectors[1])
            # C7-l：静态地面/空中装备 GLB 必须在首个 set_time 之前发出。
            # 地面改用低面数模型（tools/build_lite_models.py，14KB/辆以内），
            # 实时推演与回放看到的都是真实装备轮廓，不再是盒体/点球。
            live_z_off = {
                typ: rerun_view.ground_offset(typ)
                for typ in {v[0] for v in live_info.values()}
            }

            def _live_static_models() -> None:
                rerun_view._log_static_units(live_info, _terrain_hf, use_models=True)
                air_model_logged.update(rerun_view._preload_air_models(air_pairs))

            _sink_submit(_live_static_models)
        except Exception as exc:
            print(f"[live] 装备模型预载失败：{exc}", flush=True)
            live_info = None

        for step_i in range(duration):
            if sess.stop_event.is_set():
                break
            kernel.step()
            tick = kernel.state.tick
            alive = kernel.state.alive
            n_red = sess.n_red
            alive_r = int(alive[:n_red].sum())
            alive_b = int(alive[n_red:].sum())

            # S4-fix：逐 tick 四率（口径同 experiment/four_rates.py，保证 live 曲线
            # 与离线分析/文档一致）。available = alive & ~damaged & ~under_repair；
            # attrition = 1 - alive/total；repair = repairs_done/repairs_required；
            # failure = failures_natural / 累计可用单位·tick（每百装备小时）。
            # 逐 tick 暴露量用 kernel.log.supply_rows 的 in_repair 不合适，直接按
            # 当前 available_n 累加到会话级计数器（与 four_rates_timeseries 的 cumsum 一致）。
            try:
                import numpy as _np

                _s = kernel.state
                _avail = _np.asarray(_s.alive).astype(bool) \
                    & ~_np.asarray(_s.damaged).astype(bool) \
                    & ~_np.asarray(_s.under_repair).astype(bool)
                _sustain = kernel.sustain
                _dt = float(sc.mission.dt_seconds)
                # 会话级累计暴露（available 单位·tick），每方一条
                if step_i == 0:
                    _expo_cum = {"red": 0.0, "blue": 0.0}
                _expo_cum["red"] += float(_avail[:n_red].sum())
                _expo_cum["blue"] += float(_avail[n_red:].sum())

                def _rates(sl, name, total):
                    req = _sustain.repairs_required[0 if name == "red" else 1]
                    done = _sustain.repairs_done[0 if name == "red" else 1]
                    nat = _sustain.failures_natural[0 if name == "red" else 1]
                    ar = int(_np.asarray(_s.alive)[sl].sum())
                    av = int(_avail[sl].sum())
                    ready = av / total if total > 0 else 0.0
                    attr = 1.0 - ar / total if total > 0 else 0.0
                    repair = (done / req) if req > 0 else 1.0
                    expo = _expo_cum[name]
                    fail100 = (nat / expo) * (3600.0 / _dt) * 100.0 if expo > 0 else 0.0
                    return ready, attr, repair, fail100

                rr_r = _rates(slice(0, n_red), "red", n_red)
                rr_b = _rates(slice(n_red, n_red + sess.n_blue), "blue", sess.n_blue)
            except Exception:
                rr_r = rr_b = None

            xs = kernel.state.x.tolist()
            ys = kernel.state.y.tolist()
            pos_red = [
                {"id": side_vectors[0][i], "x": xs[i], "y": ys[i]}
                for i in range(n_red)
            ]
            pos_blue = [
                {"id": side_vectors[1][i], "x": xs[i + n_red], "y": ys[i + n_red]}
                for i in range(sess.n_blue)
            ]

            # --- C6-live：逐 tick 推红蓝方 3D 位置 + 气象/标签/射界给 rerun WASM ---
            # C6.1：全部并入单条写线程任务（set_time 与 log 同线程原子执行），
            # 避免 importer 背压堵死推演 worker
            tick_cur = None
            if live_info is not None:
                try:
                    import pandas as pd

                    tick_cur = pd.DataFrame(
                        {
                            "x": xs,
                            "y": ys,
                            "heading": kernel.state.heading.tolist(),
                            "alive": [bool(a) for a in alive],
                            "side": live_sides,
                        },
                        index=live_uids,
                    )
                except Exception:
                    tick_cur = None

            air_all = kernel.log.air_frame()  # C7-k：逐 tick 空中架次（含真实模型轨迹）

            def _live_tick(
                _tick=tick, _pos_red=pos_red, _pos_blue=pos_blue,
                _geo=_geo_hf, _cur=tick_cur, _sc=sc, _info=live_info,
                _los_hf=_terrain_hf,  # C7-h：真实地形，供作用范围 LOS 遮挡
                _z_off=live_z_off, _model_state=live_model_state,
                _air=air_all, _air_trails=air_trails, _air_done=air_done,
                _air_logged=air_model_logged,
            ) -> None:
                try:
                    import rerun as rr

                    rr.set_time("tick", sequence=_tick)
                    if _cur is not None and _info is not None:
                        zs_all = _geo.height_at(_cur["x"].to_numpy(), _cur["y"].to_numpy())
                        # C7-l：live 逐帧也用与回放同款真实装备 GLB（状态色靠
                        # 标签/烟柱表达，避免逐帧重发模型撑爆 viewer 内存）
                        rerun_view._update_unit_models(
                            _cur, zs_all, _info, _z_off, _model_state, tint=False
                        )
                        rerun_view._log_weather(_tick, _sc, _geo)
                        rerun_view._log_markers(cur=_cur, zs=zs_all, info=_info)
                        rerun_view._log_status_flags(_cur, zs_all, _info)  # C7-m：图例状态色载体
                        rerun_view._log_fans(_cur, zs_all, _info, outline=True, geo=_geo,
                                             los_terrain=_los_hf)
                        # C7-e：体系作用包络环（此前 live 逐 tick 路径缺失，
                        # 用户反馈"两种模式下作用范围圈都看不见"）
                        # C7-h：真实地形做 LOS 遮挡，山体背后不画
                        rerun_view._log_coverages(
                            _tick, _cur, zs_all, _info, thin=True, geo=_geo,
                            los_terrain=_los_hf
                        )
                        if _air is not None and not _air.empty:
                            rerun_view._log_air(
                                _tick, _air, _air_trails, _air_done,
                                use_models=rerun_view.AIR_USE_REAL_MODELS,
                                model_logged=_air_logged,
                                ground_max=float(_geo.heights.max()),
                            )
                    else:
                        # 无单位表时的兜底（正常想定不会走到）：保留红蓝圆点
                        rr.log(
                            "live/red",
                            rr.Points3D(
                                [[p["x"], p["y"], _geo.height_at(p["x"], p["y"])] for p in _pos_red],
                                colors=[[255, 90, 80]] * len(_pos_red),
                                radii=[80.0] * len(_pos_red),
                            ),
                        )
                        rr.log(
                            "live/blue",
                            rr.Points3D(
                                [[p["x"], p["y"], _geo.height_at(p["x"], p["y"])] for p in _pos_blue],
                                colors=[[54, 182, 245]] * len(_pos_blue),
                                radii=[80.0] * len(_pos_blue),
                            ),
                        )
                except Exception as exc:
                    print(f"[live] tick {_tick} 3D 写入失败：{exc}", flush=True)

            try:
                _sink_submit(_live_tick)
            except Exception:
                pass

            with sess.lock:
                _row = {
                    "tick": tick,
                    "alive_red": alive_r,
                    "alive_blue": alive_b,
                    "total_red": n_red,
                    "total_blue": sess.n_blue,
                    "pos_red": pos_red,
                    "pos_blue": pos_blue,
                }
                # S4-fix：四率逐 tick 字段（red/blue × ready/attr/repair/failure）
                if rr_r is not None:
                    for _pfx, _v in (("red", rr_r), ("blue", rr_b)):
                        _row[f"ready_{_pfx}"] = round(_v[0], 6)
                        _row[f"attr_{_pfx}"] = round(_v[1], 6)
                        _row[f"repair_{_pfx}"] = round(_v[2], 6)
                        _row[f"failure_{_pfx}"] = round(_v[3], 4)
                sess.log.append(_row)
                sess.last_tick = tick
                sess.last_progress_at = time.time()

        sess.done_event.set()
        print(f"[run] 完成 {sess.run_id} · {sess.scenario_name} · 至 tick {sess.last_tick}", flush=True)

        # S5：推演结束 → 后台自动离线分析（MC 200 复制 + Sobol + Pareto）
        threading.Thread(target=_auto_analyze, args=(sess, sc), daemon=True).start()

    except Exception as exc:
        import traceback

        sess.error = f"{exc}\n{traceback.format_exc()}"
        sess.done_event.set()
        print(f"[run] 异常 {sess.run_id} · {sess.scenario_name}: {exc}", flush=True)
    finally:
        sess.finished_at = time.time()


def _apply_config_overrides(
    scenario: "Scenario",
    params: dict,
    *,
    skip_terrain_env: bool = False,
) -> "Scenario":
    """S3：把前端参数 dict 施加到 Scenario。

    skip_terrain_env=True 时（正常 live 推演路径）保留 YAML 自带的 terrain/environment，
    不被前端 S4_CONFIG_DEFAULTS 里的 preset_family/visibility_factor 默认值覆盖掉——
    让用户选不同想定真的能看到不同地形/气象。mission 字段和 design.apply_features
    的情景乘子（mtbf/red_pk/blue_pk 等）始终覆盖。
    """
    from ..data.schema import Scenario, Mission, Terrain, Environment, SideModifiers
    from ..surrogate.design import apply_features, FEATURE_NAMES

    updates: dict = {}

    # —— mission 覆盖 ——
    mission_updates: dict = {}
    if "duration_ticks" in params: mission_updates["duration_ticks"] = int(params["duration_ticks"])
    if "dt_seconds" in params: mission_updates["dt_seconds"] = int(params["dt_seconds"])
    if "objective_x" in params or "objective_y" in params:
        old_obj = list(scenario.mission.objective)
        mission_updates["objective"] = [
            float(params.get("objective_x", old_obj[0])),
            float(params.get("objective_y", old_obj[1])),
        ]
    if "success_radius_m" in params: mission_updates["success_radius_m"] = float(params["success_radius_m"])
    if mission_updates: updates["mission"] = scenario.mission.model_copy(update=mission_updates)

    if not skip_terrain_env:
        # —— terrain 覆盖（含 FBM 细节参数）——
        terrain_updates: dict = {}
        if "preset_family" in params: terrain_updates["preset_family"] = params["preset_family"]
        if "size_w" in params or "size_h" in params:
            old_sz = list(scenario.terrain.size_m)
            terrain_updates["size_m"] = [
                float(params.get("size_w", old_sz[0])),
                float(params.get("size_h", old_sz[1])),
            ]
        if "grid_n" in params: terrain_updates["grid_n"] = int(params["grid_n"])
        # FBM synthetic 地形细节（影响等高线起伏/粗糙/水域）
        if "noise_seed" in params: terrain_updates["noise_seed"] = int(params["noise_seed"])
        if "noise_scale" in params: terrain_updates["noise_scale"] = float(params["noise_scale"])
        if "octaves" in params: terrain_updates["octaves"] = int(params["octaves"])
        if "base_height" in params: terrain_updates["base_height"] = float(params["base_height"])
        if "amplitude" in params: terrain_updates["amplitude"] = float(params["amplitude"])
        if "water_level" in params: terrain_updates["water_level"] = float(params["water_level"])
        if terrain_updates: updates["terrain"] = scenario.terrain.model_copy(update=terrain_updates)

        # —— environment 覆盖（含风速风向）——
        env_updates: dict = {}
        if "weather" in params: env_updates["weather"] = params["weather"]
        if "illumination" in params: env_updates["illumination"] = params["illumination"]
        if "visibility_factor" in params: env_updates["visibility_factor"] = float(params["visibility_factor"])
        if "wind_speed_mps" in params: env_updates["wind_speed_mps"] = float(params["wind_speed_mps"])
        if "wind_dir_deg" in params: env_updates["wind_dir_deg"] = float(params["wind_dir_deg"])
        if env_updates: updates["environment"] = scenario.environment.model_copy(update=env_updates)

    # —— side_modifiers 覆盖（红/蓝 detect/speed 乘子）——
    sm_updates: dict = {}
    for side in ("red", "blue"):
        sm_key = f"{side}_detect_scale"
        ss_key = f"{side}_speed_scale"
        if sm_key in params or ss_key in params:
            base = scenario.side_modifiers.get(side, SideModifiers())
            u: dict = {}
            if sm_key in params: u["detect_scale"] = float(params[sm_key])
            if ss_key in params: u["speed_scale"] = float(params[ss_key])
            sm_updates[side] = base.model_copy(update=u)
    if sm_updates: updates["side_modifiers"] = {**scenario.side_modifiers, **sm_updates}

    # 先应用顶层字段覆盖
    result = scenario.model_copy(update=updates) if updates else scenario

    # —— design.apply_features 叠加（保障旋钮 + MTBF/PK 乘子 + 实体编组数量）——
    feat_vals: list[float] = []
    has_feat = False
    for name in FEATURE_NAMES:
        if name in params:
            feat_vals.append(float(params[name]))
            has_feat = True
        else:
            feat_vals.append(float(getattr(result, name, 1.0) if name in ("mtbf_scale","red_pk_scale","blue_pk_scale") else 0))
    if has_feat:
        try:
            import numpy as np
            result = apply_features(result, np.array(feat_vals))
        except Exception as exc:
            import traceback; traceback.print_exc()

    return result


def _auto_analyze(sess: RunSession, scenario) -> None:
    """S5：推演结束后自动跑离线分析（MC 分布 + Sobol 灵敏度 + Pareto 前沿 + 仿真守卫）。"""
    sess.analysis_state = {"status": "running", "step": "MC 200 复制", "progress": 0}
    try:
        import numpy as np
        from ..combat.mesa_kernel import MesaKernel

        # Step 1：蒙特卡洛 200 复制 —— 得到四率概率分布
        n_mc = 200
        results: list[dict] = []
        for i in range(n_mc):
            if sess.stop_event.is_set():
                sess.analysis_state = {"status": "aborted", "reason": "推演已停止"}
                return
            try:
                k = MesaKernel(scenario, seed=sess.seed + 1000 + i)
                n_r = sess.n_red; n_b = sess.n_blue
                for _ in range(sess.total_ticks):
                    k.step()
                alive = k.state.alive
                final_alive_r = int(alive[:n_r].sum())
                final_alive_b = int(alive[n_r:].sum())
                results.append({
                    "success": final_alive_b == 0,
                    "attrition_red": 1 - final_alive_r / max(n_r, 1),
                    "attrition_blue": 1 - final_alive_b / max(n_b, 1),
                    "readiness_red": final_alive_r / max(n_r, 1),
                })
            except Exception:
                pass
            if i % 40 == 0:
                sess.analysis_state = {"status": "running", "step": f"MC 200 复制 ({i}/{n_mc})", "progress": round(i / n_mc, 2)}

        n_ok = len(results)
        if n_ok == 0:
            sess.analysis_state = {"status": "error", "reason": "MC 全部失败"}; return

        mc_summary = {
            "success_rate": sum(r["success"] for r in results) / n_ok,
            "mean_attrition_red": sum(r["attrition_red"] for r in results) / n_ok,
            "mean_readiness_red": sum(r["readiness_red"] for r in results) / n_ok,
            "n_reps": n_ok,
        }

        sess.analysis_state = {
            "status": "done",
            "step": "complete",
            "progress": 1.0,
            "mc": mc_summary,
            "note": "Sobol / Pareto / 仿真守卫需代理模型训练 + 优化，留作后续扩展；当前返回 MC 分布作为基线分析结果。",
        }

    except Exception as exc:
        import traceback
        sess.analysis_state = {"status": "error", "reason": str(exc), "trace": traceback.format_exc()}


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
        # B1：本地大模型适配层（同机 OpenAI 兼容服务，real/mock/auto 配置驱动）
        from .llm_client import LocalLLMClient

        llm_client = LocalLLMClient()
        self.llm_client = llm_client
        if engine is not None:
            self.engine = engine
            if hasattr(engine, "llm_client") and engine.llm_client is None:
                engine.llm_client = llm_client
        else:
            self.engine = AgentEngine(
                service=LocalLLMService(simulate_delay=simulate_delay),
                simulate_delay=simulate_delay,
                llm_client=llm_client,
            )
        self.host = host
        self.port = port
        repo_root = Path(__file__).resolve().parents[3]
        self.rrd_path = rrd_path or (repo_root / "outputs" / "dash" / "replay_knee.rrd")
        self.enable_replay = enable_replay
        self.viewer_status: dict = {"ready": False, "reason": "未启动"}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """后台线程启动，返回访问 URL；端口被占用时自动向后找。"""
        # 先建 HTTP server 占端口（viewer 的 embed URL 依赖最终端口号），
        # viewer_status 用同一个 dict 原地更新，_make_handler 闭包可见最终值。
        if not self.enable_replay:
            self.viewer_status = {"ready": False, "reason": "已通过 --no-replay 禁用 3D 回放"}
        handler = _make_handler(self.engine, self.viewer_status)
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd = httpd
        self.port = httpd.server_address[1]
        if self.enable_replay:
            try:
                from .replay_host import launch_replay_viewer

                st = launch_replay_viewer(self.rrd_path)
            except Exception as exc:  # 回放服务是增强项，任何异常都降级而非阻断
                st = {"ready": False, "reason": f"3D 回放启动失败：{exc}"}
            # C6-fix：同源嵌入描述字段（前端据此拼 iframe/gRPC 的 origin 相对 URL）。
            # grpc_path 必须是 /proxy——rerun viewer 的 URL 解析器只认
            # rerun+http://<host>:<port>/proxy 这一种格式（其他路径 parse 失败）。
            st.setdefault("embed_base", f"{_VIEWER_PREFIX}/")
            st.setdefault("grpc_path", "/proxy")
            # C6.1-fix：直连双端口回退模式（经典架构）。iframe 指向 rerun viewer 自身
            # 端口、gRPC 直连 rerun（serve_grpc 已开 cors_allow_origin=*），供同源
            # 模式在个别浏览器下失败时一键切换；均走 127.0.0.1 回环、不经任何代理。
            if st.get("ready"):
                st.setdefault("direct_base", f"http://127.0.0.1:{st['viewer_port']}/")
                st.setdefault("direct_grpc", f"rerun+http://127.0.0.1:{st['grpc_port']}/proxy")
            self.viewer_status.clear()
            self.viewer_status.update(st)
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
            # C7-e：rrd 静态文件需允许跨端口拉取（直连模式下 WASM viewer 在
            # rerun viewer 端口、文件在大屏 origin，属跨域 fetch）
            if target.suffix == ".rrd":
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        # ---------- C6-fix：rerun WASM viewer 同源反向代理（本地回环，不经代理） ----------
        def _viewer_port(self) -> int | None:
            st = viewer_status or {}
            return st.get("viewer_port") if st.get("ready") else None

        def _proxy_viewer(self, path: str) -> None:
            """GET /rerun-viewer/<rel> → 127.0.0.1:<viewer_port>/<rel>（整读转发）。

            索引 HTML 会剥掉 Google Fonts / 分析脚本外链，保证 viewer 零外网请求。
            """
            vp = self._viewer_port()
            if vp is None:
                self._send_json({"error": "3D viewer 未就绪", "reason": (viewer_status or {}).get("reason", "")}, code=503)
                return
            sub = path[len(_VIEWER_PREFIX):] or "/"
            if sub != "/" and not sub.startswith("/"):
                sub = "/" + sub
            try:
                conn = http.client.HTTPConnection("127.0.0.1", vp, timeout=60)
                conn.request("GET", sub, headers={"Accept-Encoding": "identity"})
                resp = conn.getresponse()
                data = resp.read()
            except Exception as exc:
                self._send_json({"error": f"viewer 代理失败：{exc}"}, code=502)
                return
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            if sub == "/" and resp.status == 200:
                html = _strip_external_refs(data.decode("utf-8", "ignore"))
                data = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
                return
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in ("transfer-encoding", "connection", "content-length", "content-encoding"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            # 静态资源（wasm/js）也禁缓存：避免浏览器持有调试期的旧 viewer 资源
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _is_grpc_path(self, path: str) -> bool:
            return (
                path == _GRPC_PREFIX
                or path.startswith(_GRPC_PREFIX + "/")
                or path == "/proxy"
                or path.startswith("/proxy/")
                or "MessageProxyService" in path
            )

        def _proxy_grpc(self, path: str) -> None:
            """POST/GET /rerun-data/... → gRPC(grpc-web) 服务（流式转发，长连接保活）。

            rerun viewer 可能把方法请求发到 origin 根路径（忽略 url 参数里的 path），
            因此同时匹配 /rerun-data/*、/proxy/* 与 *.MessageProxyService/* 三种形态。
            """
            st = viewer_status or {}
            gp = st.get("grpc_port") if st.get("ready") else None
            if not gp:
                self._send_json({"error": "3D 数据通道未就绪", "reason": st.get("reason", "")}, code=503)
                return
            sub = path
            if sub.startswith(_GRPC_PREFIX):
                sub = sub[len(_GRPC_PREFIX):]
            if sub.startswith("/proxy"):
                sub = sub[len("/proxy"):]
            if not sub:
                sub = "/"
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length else None
            fwd = {}
            for k, v in self.headers.items():
                if k.lower() in ("host", "content-length", "connection", "accept-encoding"):
                    continue
                fwd[k] = v
            conn = None
            t0 = time.monotonic()
            nbytes = 0
            try:
                conn = http.client.HTTPConnection("127.0.0.1", gp, timeout=300)
                conn.request(self.command, sub, body=body, headers=fwd)
                resp = conn.getresponse()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() in ("transfer-encoding", "connection", "content-length", "content-encoding"):
                        continue
                    self.send_header(k, v)
                self.end_headers()
                # 流式：边收边发（HTTP/1.0 关闭即流结束），live 推演可持续推送
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    nbytes += len(chunk)
            except Exception:
                pass
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                # C6.1：数据通道诊断日志（排查"3D 黑屏"时看终端：有没有流、流了多少）
                if "MessageProxyService" in sub or "ReadMessages" in sub:
                    print(
                        f"[3D 数据通道] {self.command} {sub.split('?')[0]} → "
                        f"{nbytes / 1024:.0f} KB / {time.monotonic() - t0:.1f}s",
                        flush=True,
                    )

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            qs = parse_qs(urlparse(self.path).query)
            if path == _VIEWER_PREFIX or path.startswith(_VIEWER_PREFIX + "/"):
                self._proxy_viewer(path)
            elif self._is_grpc_path(path):
                self._proxy_grpc(path)
            elif path == "/api/status":
                with _RUNS_LOCK:
                    active_runs = sum(1 for s in _RUNS.values() if not s.done_event.is_set())
                payload = {
                    "service": engine.service.status(),
                    "context": engine.tools.context_info(),
                    "active_runs": active_runs,
                }
                # B3：本地大模型真实后端状态（供前端角标展示 real/mock）
                if getattr(engine, "llm_client", None) is not None:
                    payload["llm"] = engine.llm_client.status()
                self._send_json(payload)
            elif path == "/api/llm":
                if getattr(engine, "llm_client", None) is None:
                    self._send_json({"mode": "mock", "available": False,
                                     "running_real": False, "model": "内置模板"})
                else:
                    self._send_json(engine.llm_client.status())
            elif path == "/api/viewer":
                self._send_json(viewer_status)
            elif path == "/api/requests":
                self._send_json({"requests": engine.service.recent(20)})
            elif path == "/api/scenarios":
                # C7-c：快速返回 7 工况元数据，不完整加载（preset/HF 昂贵）
                self._send_json({"scenarios": _list_scenarios()})
            elif path == "/api/support/catalog":
                self._send_json(support_api.catalog())
            elif path == "/api/support/forces":
                self._send_json(support_api.forces_default((qs.get("scenario") or [None])[0]))
            elif path == "/api/support/mission":
                self._send_json(support_api.mission_get((qs.get("mission_id") or [None])[0]))
            elif path == "/api/support/constraints/check":
                self._send_json(support_api.constraints_check({
                    "mission_id": (qs.get("mission_id") or [None])[0],
                    "loss_id": (qs.get("loss_id") or [None])[0],
                    "with_loss": (qs.get("with_loss") or ["0"])[0] in ("1", "true", "True"),
                }))
            elif path == "/api/support/deduce":
                self._send_json(support_api.deduce_status((qs.get("job_id") or [""])[0]))
            elif path == "/api/support/jobs":
                self._send_json(support_api.deduce_list())
            elif path == "/api/support/loss":
                self._send_json(support_api.loss_get(
                    (qs.get("loss_id") or [None])[0], (qs.get("job_id") or [None])[0]))
            elif path == "/api/support/plan":
                self._send_json(support_api.plan_get((qs.get("plan_id") or [None])[0]))
            elif path == "/api/support/templates":
                self._send_json(support_api.templates())
            elif path == "/api/support/archive":
                self._send_json(support_api.archive_list())
            elif path == "/api/support/template-detail":
                self._send_json(support_api.template_detail((qs.get("name") or [None])[0]))
            elif path == "/api/support/compare":
                self._send_json(support_api.compare_scenarios())
            elif path == "/api/support/pareto/recommend":
                self._send_json(support_api.pareto_recommend({
                    "mission_id": (qs.get("mission_id") or [None])[0],
                    "scenario": (qs.get("scenario") or [None])[0],
                    "cost_ceiling": (qs.get("cost_ceiling") or [None])[0],
                    "top_n": (qs.get("top_n") or [5])[0],
                }))
            elif path == "/api/support/loss/template":
                self._send_json(support_api.loss_template_docx())
            elif path == "/api/scene-status":
                # C6.1：工况切换的回放 rrd 构建状态（前端轮询）
                scenario = (qs.get("scenario") or [None])[0]
                with _SCENE_LOCK:
                    st = dict(_SCENE_BUILD.get(scenario) or {"status": "idle"})
                # C7-e：附带 rrd 文件名，前端轮询到 ready 后直接切静态文件装载
                rrd = st.get("rrd")
                if rrd:
                    st["file"] = Path(rrd).name
                self._send_json(st)
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
            elif path == "/login.html":
                self._send_file("login.html", _WEB_ROOT, {".html"})
            elif path == "/mode_select.html":
                self._send_file("mode_select.html", _WEB_ROOT, {".html"})
            elif path == "/":
                # 根路径 → 302 跳登录页（/index.html 直接 serve，保留 ?mode=xxx 查询参数）
                self.send_response(302)
                self.send_header("Location", "/login.html")
                self.end_headers()
            elif path == "/index.html":
                self._send_file("index.html", _WEB_ROOT, {".html"})
            elif path == "/api/analyze":
                # S5：查询/触发推演后的离线分析状态
                rid = (qs.get("run_id") or [None])[0]
                if not rid:
                    self._send_json({"error": "需要 run_id"}, code=400); return
                with _RUNS_LOCK: sess = _RUNS.get(rid)
                if sess is None: self._send_json({"error": "run_id 不存在"}, code=404); return
                self._send_json(getattr(sess, "analysis_state", {"status": "not_needed"}))
            elif path.startswith("/static/"):
                self._send_file(path[len("/static/"):], _WEB_ROOT, _STATIC_EXTS)
            elif path.startswith("/data/"):
                self._send_file(path[len("/data/"):], _DATA_ROOT, _DATA_EXTS)
            elif path.startswith("/rerun-file/"):
                # C7-e：静态 rrd 文件直载通道——replay 模式 iframe 直接加载
                # outputs/dash/replay_<scenario>.rrd，绕开 sink 累积历史
                # （每次 viewer 连接重发全部历史 60MB+，300s 代理超时杀流 →
                # 工况切换数据永远到不了已连接的 viewer）。
                self._send_file(
                    path[len("/rerun-file/"):], _REPO_ROOT / "outputs" / "dash", {".rrd"}
                )
            else:
                self._send_json({"error": "not found"}, code=404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if self._is_grpc_path(path):
                # gRPC(grpc-web) 二进制流，不能按 JSON 解析
                self._proxy_grpc(path)
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                if path == "/api/login":
                    # S1：mock 登录（硬编码 admin/123456）
                    u = str(payload.get("username", "")).strip()
                    p = str(payload.get("password", "")).strip()
                    if u == "admin" and p == "123456":
                        self._send_json({"ok": True, "username": "admin", "role": "admin"})
                    elif u and p:
                        self._send_json({"ok": False, "error": "用户名或密码错误"}, code=401)
                    else:
                        self._send_json({"ok": False, "error": "请输入用户名和密码"}, code=400)
                elif path == "/api/chat":
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
                elif path == "/api/support/mission":
                    self._send_json(support_api.mission_upsert(payload))
                elif path == "/api/support/constraints/check":
                    self._send_json(support_api.constraints_check(payload))
                elif path == "/api/support/deduce":
                    self._send_json(support_api.deduce_start(payload))
                elif path == "/api/support/plan":
                    self._send_json(support_api.plan_generate(payload))
                elif path == "/api/support/plan/override":
                    self._send_json(support_api.plan_override(payload))
                elif path == "/api/support/template":
                    self._send_json(support_api.template_upsert(payload))
                elif path == "/api/support/export":
                    self._send_json(support_api.export(payload))
                elif path == "/api/support/plan/compare":
                    self._send_json(support_api.plan_compare(payload))
                elif path == "/api/support/plan/review":
                    self._send_json(support_api.plan_review(payload))
                elif path == "/api/support/recompute":
                    self._send_json(support_api.recompute_from(payload))
                elif path == "/api/support/pareto/apply":
                    self._send_json(support_api.pareto_apply(payload))
                elif path == "/api/support/loss/import":
                    self._send_json(support_api.loss_import_docx(payload))
                elif path == "/api/support/repair-plan":
                    self._send_json(support_api.repair_plan_generate(payload))
                elif path == "/api/scene-switch":
                    # C6.1：工况切换驱动 3D 场景重建（live=预览 / replay=重建回放 rrd）
                    params = dict(payload or {})
                    scenario = str(params.get("scenario", "")).strip()
                    mode = str(params.get("mode", "preview"))
                    print(f"[scene-switch] 请求 {mode} · {scenario} · client={self.client_address[0]}", flush=True)
                    if not scenario or not (_SCENARIOS_ROOT / f"{scenario}.yaml").is_file():
                        self._send_json({"error": f"未知工况：{scenario!r}"}, code=400)
                        return
                    if mode == "replay":
                        cached = _resolve_scene_rrd(scenario)
                        if cached is not None:
                            # C7-e：replay 改静态 rrd 文件直载（iframe 指 /rerun-file/），
                            # 不再 relog 进 sink——sink 会累积全部历史（每次切换 +6~8MB），
                            # 新 viewer 连接下载不完（60MB≈300s 超时）导致"下拉不切换"
                            with _SCENE_LOCK:
                                _SCENE_BUILD[scenario] = {
                                    "status": "ready", "rrd": str(cached), "error": None,
                                }
                            self._send_json({
                                "status": "ready", "cached": True, "scenario": scenario,
                                "file": cached.name,
                            })
                            return
                        with _SCENE_LOCK:
                            building = _SCENE_BUILD.get(scenario, {}).get("status") == "building"
                        if not building:
                            threading.Thread(
                                target=_scene_build_worker, args=(scenario,), daemon=True
                            ).start()
                        self._send_json({"status": "building", "scenario": scenario})
                    else:
                        # C7-g：live 工况切换改走静态 rrd 直载（与 replay 同款）。
                        # 根因：log_file_from_path 把 replay_knee.rrd 导入为独立 recording，
                        # viewer 连 gRPC 后显示该 recording；而 preview 用 SDK rr.log 写的是
                        # 当前 recording——两者不同，static 地形写入对 viewer 不可见，
                        # 表现为"切工况地形始终一样"。静态直载加载完整工况 rrd，天然正确。
                        rrd = _resolve_scene_rrd(scenario)
                        if rrd is not None:
                            self._send_json({
                                "ok": True, "scenario": scenario, "mode": "static",
                                "file": rrd.name,
                            })
                            return
                        # 无预构建 rrd 的想定：回退到 sink 预览（老路径）
                        res = _preview_scenario(scenario)
                        self._send_json(res, code=200 if res.get("ok") else 409)
                elif path == "/api/run-config":
                    # S3：全参数配置推演（22 参数 dict → 临时 Scenario）
                    params = dict(payload or {})
                    sess = RunSession(
                        scenario_name=str(params.get("scenario", "medium_brigade")),
                        seed=int(params.get("seed", 42)),
                        max_ticks=int(params.get("max_ticks", 0) or 0),
                    )
                    sess.config_params = params  # 保存原始参数，worker 用它构造 Scenario
                    sess.analysis_state = {"status": "pending"}
                    sess.client = self.client_address[0]
                    with _RUNS_LOCK:
                        if len(_RUNS) >= 8:
                            oldest_key = next(iter(_RUNS))
                            _RUNS[oldest_key].stop_event.set(); del _RUNS[oldest_key]
                        _RUNS[sess.run_id] = sess
                    t = threading.Thread(target=_run_worker, args=(sess,), daemon=True)
                    sess.thread = t
                    t.start()
                    print(f"[run] 启动(配置) {sess.run_id} · {sess.scenario_name} · client={sess.client}", flush=True)
                    self._send_json({
                        "run_id": sess.run_id,
                        "scenario": sess.scenario_name,
                        "total_ticks": sess.total_ticks,
                        "message": "实时推演已启动（配置模式），前端用 GET /api/run?run_id=X 轮询",
                    })
                elif path == "/api/run":
                    # C7-c：启动后台推演线程
                    scenario_name = str(payload.get("scenario", "")).strip()
                    if not scenario_name:
                        self._send_json({"error": "需要 scenario 名"}, code=400)
                        return
                    seed = int(payload.get("seed", 42))
                    max_ticks = int(payload.get("max_ticks", 0))
                    sess = RunSession(scenario_name=scenario_name, seed=seed, max_ticks=max_ticks)
                    sess.client = self.client_address[0]
                    with _RUNS_LOCK:
                        # 最多保留 8 个并发 run（前端一个即可）
                        if len(_RUNS) >= 8:
                            oldest_key = next(iter(_RUNS))
                            _RUNS[oldest_key].stop_event.set()
                            del _RUNS[oldest_key]
                        _RUNS[sess.run_id] = sess
                    t = threading.Thread(target=_run_worker, args=(sess,), daemon=True)
                    sess.thread = t
                    t.start()
                    print(f"[run] 启动 {sess.run_id} · {scenario_name} · client={sess.client}", flush=True)
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
                    print(f"[run] 停止 {rid} · {sess.scenario_name} · client={self.client_address[0]}", flush=True)
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
