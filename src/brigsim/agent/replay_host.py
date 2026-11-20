"""C6：把 rerun web viewer（WASM）挂载到本地，供指挥大屏 iframe 内嵌 3D 回放。

链路（rerun 0.39 已实测）：
    rr.init(spawn=False)
    → rr.serve_grpc(cors_allow_origin=["*"])         本地 gRPC 数据服务（内存缓冲）
    → rr.log_file_from_path(rrd)                     灌入录制好的回放
    → rr.serve_web_viewer(open_browser=False)        SDK 自带 WASM viewer 静态服务

C6-fix（2026-10-20）：浏览器**不直连** viewer/gRPC 端口，改由大屏 server 做
**同源反向代理**（/rerun-viewer/*、/rerun-data/* → 127.0.0.1 回环）：
iframe 与 gRPC 都走大屏自身 origin——不经任何代理、不跨端口、无 CORS、全程本地；
viewer HTML 里的 Google Fonts / 分析脚本外链由 server._strip_external_refs 剥除，
断网/代理异常环境下 3D 可视化依然可用。viewer_url（直连地址）仅作 CLI 展示与排查用。

viewer 页面无 X-Frame-Options 限制、静态资源同源本地托管（断网可用），
gRPC CORS 放行为 *，仪表盘以 iframe 嵌入。所有服务随主进程存活（daemon 线程），
rerun 未提供停止 API；agent-serve 进程退出即释放。
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from urllib.parse import quote

DEFAULT_GRPC_PORT = 9876
DEFAULT_VIEWER_PORT = 9090


def _free_port(port: int, host: str = "127.0.0.1") -> bool:
    """用**不带 SO_REUSEADDR** 的 socket 探测端口——和 rerun 内部 bind 行为一致，
    Windows TIME_WAIT 状态下也会返回 False，从而跳过去找下一个端口。

    ⚠️ 必须同时探测 0.0.0.0：rerun 的 serve_grpc/serve_web_viewer 实际 bind 的是
    0.0.0.0。只探 127.0.0.1 会漏判"0.0.0.0 已被占用"的情况 → rerun bind 失败，
    且 serve_grpc 的 bind 失败**只打日志不抛错**，直到 serve_web_viewer 才抛
    RuntimeError；更糟的是此时 rr.init 的全局 sink 已被污染，同进程后续任何
    rr.init 都会硬报 "Failed to flush previous sink: gRPC server thread shut down"。
    """
    for h in (host, "0.0.0.0"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((h, port))
            s.close()
        except OSError:
            return False
    return True


def _grpc_alive(port: int, retries: int = 5, delay: float = 0.3) -> bool:
    """serve_grpc 的 bind 失败只打 ERROR 日志、不抛异常——用 TCP 连接探测兜底，
    避免把"viewer 起好了但数据通道是死的"当成成功返回。"""
    for _ in range(retries):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(delay)
    return False


def _pick_port(start: int) -> int | None:
    for port in range(start, start + 10):
        if _free_port(port):
            return port
    return None


# 进程级幂等：rerun 全局 recording sink 每进程只能 init 一次（二次 init 会报
# "Failed to flush previous sink"）。同进程重复启动（测试/双开）时复用首次结果。
_LAUNCH_STATE: dict | None = None


def launch_replay_viewer(
    rrd_path: str | Path | None = None,
    grpc_port: int = DEFAULT_GRPC_PORT,
    viewer_port: int = DEFAULT_VIEWER_PORT,
    host: str = "127.0.0.1",
) -> dict:
    """启动 rerun WASM web viewer：serve_grpc + 可选 replay rrd + serve_web_viewer。

    rrd_path=None：live 模式空 grpc（worker 自己 Clear+log）；
    rrd_path 给了但文件不存在：返回 ready=False 降级（不白占 rerun 全局 sink）。
    """
    global _LAUNCH_STATE

    # 显式路径但文件缺失 → 优雅降级（与 C6 契约一致）
    if rrd_path is not None:
        rrd = Path(rrd_path)
        if not rrd.is_file():
            return {
                "ready": False,
                "reason": f"回放文件不存在：{rrd}（运行 brigsim dash-build 生成 3D 回放）",
                "rrd": str(rrd),
            }

    if _LAUNCH_STATE is not None:
        return dict(_LAUNCH_STATE)

    try:
        import rerun as rr
    except ImportError as exc:
        return {"ready": False, "reason": f"rerun 未安装：{exc}", "rrd": str(rrd_path) if rrd_path else "live"}

    gp = _pick_port(grpc_port)
    vp = _pick_port(viewer_port)
    if gp is None or vp is None:
        return {"ready": False, "reason": "9876/9090 起连续 10 个端口均被占用", "rrd": str(rrd_path) if rrd_path else "live"}

    try:
        rr.init("brigsim-dashboard", spawn=False)
        grpc_uri = rr.serve_grpc(grpc_port=gp, cors_allow_origin=["*"])
        if not _grpc_alive(gp):
            raise RuntimeError(f"gRPC 数据端口 {gp} 未就绪（serve_grpc bind 失败，通常是被占用）")
        if rrd_path is not None:
            rrd = Path(rrd_path)
            if rrd.is_file():
                rr.log_file_from_path(str(rrd))
        rr.serve_web_viewer(web_port=vp, open_browser=False, connect_to=grpc_uri)
    except Exception as exc:
        # rr.init 一旦执行，rerun 全局 recording sink 即被本进程独占：二次 init 会
        # 触发 "Failed to flush previous sink" 硬错误（sink 被污染，无恢复 API）。
        # 因此**失败结果也必须记住**：后续调用直接降级返回，绝不再碰 rr.init。
        result = {
            "ready": False,
            "reason": f"rerun 服务启动失败：{exc}",
            "rrd": str(rrd_path) if rrd_path else "live",
        }
        _LAUNCH_STATE = dict(result)
        return result
    viewer_url = f"http://{host}:{vp}/?url={quote(grpc_uri, safe='')}&notebook=0"
    result = {
        "ready": True,
        "viewer_url": viewer_url,
        "grpc_uri": grpc_uri,
        "grpc_port": gp,
        "viewer_port": vp,
        "rrd": str(rrd_path) if rrd_path else "live-stream",
    }
    _LAUNCH_STATE = dict(result)
    return result
