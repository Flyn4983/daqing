"""C6：把 rerun web viewer（WASM）挂载到本地，供指挥大屏 iframe 内嵌 3D 回放。

链路（rerun 0.39 已实测）：
    rr.init(spawn=False)
    → rr.serve_grpc(cors_allow_origin=["*"])         本地 gRPC 数据服务（内存缓冲）
    → rr.log_file_from_path(rrd)                     灌入录制好的回放
    → rr.serve_web_viewer(open_browser=False)        SDK 自带 WASM viewer 静态服务

viewer 页面无 X-Frame-Options 限制、静态资源同源本地托管（断网可用），
gRPC CORS 放行为 *，仪表盘以 iframe 嵌入。所有服务随主进程存活（daemon 线程），
rerun 未提供停止 API；agent-serve 进程退出即释放。
"""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import quote

DEFAULT_GRPC_PORT = 9876
DEFAULT_VIEWER_PORT = 9090


def _free_port(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(start: int) -> int | None:
    for port in range(start, start + 10):
        if _free_port(port):
            return port
    return None


def launch_replay_viewer(
    rrd_path: str | Path,
    grpc_port: int = DEFAULT_GRPC_PORT,
    viewer_port: int = DEFAULT_VIEWER_PORT,
    host: str = "127.0.0.1",
) -> dict:
    """启动 3D 回放服务，返回状态 dict（失败也不抛异常，ready=False）。"""
    rrd = Path(rrd_path)
    if not rrd.is_file():
        return {
            "ready": False,
            "reason": f"回放文件不存在：{rrd}（运行 brigsim dash-build 生成 3D 回放）",
            "rrd": str(rrd),
        }
    try:
        import rerun as rr
    except ImportError as exc:  # pragma: no cover - viz extra 未装时的降级
        return {"ready": False, "reason": f"rerun 未安装：{exc}", "rrd": str(rrd)}

    gp = _pick_port(grpc_port)
    vp = _pick_port(viewer_port)
    if gp is None or vp is None:
        return {"ready": False, "reason": "9876/9090 起连续 10 个端口均被占用", "rrd": str(rrd)}

    rr.init("brigsim-dashboard", spawn=False)
    grpc_uri = rr.serve_grpc(grpc_port=gp, cors_allow_origin=["*"])
    rr.log_file_from_path(str(rrd))
    rr.serve_web_viewer(web_port=vp, open_browser=False, connect_to=grpc_uri)
    viewer_url = f"http://{host}:{vp}/?url={quote(grpc_uri, safe='')}"
    return {
        "ready": True,
        "viewer_url": viewer_url,
        "grpc_uri": grpc_uri,
        "grpc_port": gp,
        "viewer_port": vp,
        "rrd": str(rrd),
    }
