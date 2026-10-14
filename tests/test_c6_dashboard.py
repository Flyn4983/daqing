"""C6 指挥大屏测试（全部 CPU/Mesa + tmp_path，不依赖 GPU 与 outputs 大资产）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from brigsim.agent import AgentEngine, LocalLLMService
from brigsim.agent import server as server_mod
from brigsim.agent.server import AgentWebServer, _safe_join

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 共享 fixture：不挂 3D 回放的测试服务器（静态/数据根指向 tmp_path）
# ---------------------------------------------------------------------------


@pytest.fixture()
def web_server(tmp_path, monkeypatch):
    web_root = tmp_path / "web"
    (web_root / "css").mkdir(parents=True)
    (web_root / "data").mkdir()
    (web_root / "index.html").write_text("<html>大屏</html>", encoding="utf-8")
    (web_root / "css" / "base.css").write_text(":root{}", encoding="utf-8")
    (web_root / "data" / "manifest.json").write_text(
        json.dumps({"schema": "brigsim-dash/manifest/1"}), encoding="utf-8"
    )
    monkeypatch.setattr(server_mod, "_WEB_ROOT", web_root)
    monkeypatch.setattr(server_mod, "_DATA_ROOT", web_root / "data")

    server = AgentWebServer(
        engine=AgentEngine(
            service=LocalLLMService(simulate_delay=False), simulate_delay=False
        ),
        host="127.0.0.1",
        port=0,
        enable_replay=False,
    )
    url = server.start()
    try:
        yield url
    finally:
        server.stop()


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


# ---------------------------------------------------------------------------
# 1. 目录穿越防护
# ---------------------------------------------------------------------------


def test_safe_join_blocks_traversal(tmp_path):
    root = tmp_path / "web"
    root.mkdir()
    (root / "ok.js").write_text("x", encoding="utf-8")
    assert _safe_join(root, "ok.js") == (root / "ok.js").resolve()
    assert _safe_join(root, "../escape.js") is None
    assert _safe_join(root, "a/../../escape.js") is None
    assert _safe_join(root, "/etc/passwd") is None
    assert _safe_join(root, "") is None


def test_traversal_returns_404(web_server):
    for evil in ("/static/../server.py", "/data/../../config.yaml", "/static/..%2f..%2fx"):
        status, _, _ = _get(web_server + evil)
        assert status == 404, evil


# ---------------------------------------------------------------------------
# 2. 静态/数据路由
# ---------------------------------------------------------------------------


def test_static_and_data_routes(web_server):
    status, ctype, body = _get(web_server + "/static/css/base.css")
    assert status == 200 and "text/css" in ctype and b":root" in body

    status, ctype, body = _get(web_server + "/data/manifest.json")
    assert status == 200 and "application/json" in ctype
    assert json.loads(body)["schema"] == "brigsim-dash/manifest/1"

    # 不允许的扩展名/不存在文件 → 404
    assert _get(web_server + "/static/../index.html")[0] == 404
    assert _get(web_server + "/static/nope.js")[0] == 404
    assert _get(web_server + "/data/secret.txt")[0] == 404


def test_index_route_and_viewer_status(web_server):
    status, _, body = _get(web_server + "/")
    assert status == 200 and "大屏" in body.decode("utf-8")

    status, _, body = _get(web_server + "/api/viewer")
    assert status == 200
    assert json.loads(body)["ready"] is False


# ---------------------------------------------------------------------------
# 3/4. 仿真构建器（example_company + Mesa，小复制）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def company_scenario():
    from brigsim.dashboard.build import load_scenario

    return load_scenario("example_company")


def test_build_timeseries_structure(tmp_path, company_scenario):
    from brigsim.dashboard.build import (
        BASELINE_X,
        KNEE_X,
        build_timeseries,
    )

    duration = int(company_scenario.mission.duration_ticks)
    out = build_timeseries(
        tmp_path / "timeseries.json",
        company_scenario,
        {"knee": KNEE_X, "baseline": BASELINE_X},
        reps=3,
        seed=31001,
        kernel="mesa",
    )
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["schema"] == "brigsim-dash/timeseries/1"
    assert d["duration_ticks"] == duration
    assert len(d["minutes"]) == duration
    assert {p["key"] for p in d["plans"]} == {"knee", "baseline"}

    metrics = ("availability", "attrition", "repair_rate", "failure_rate")
    for plan in d["plans"]:
        assert set(plan["decisions"]) == {
            "red_repair_teams", "red_spare_stock",
            "resupply_interval_ticks", "resupply_qty",
        }
        for side in ("red", "blue"):
            for m in metrics:
                mean = plan["sides"][side][m]["mean"]
                std = plan["sides"][side][m]["std"]
                assert len(mean) == duration and len(std) == duration
                assert all(v is not None for v in mean + std)
                if m != "failure_rate":
                    assert all(0.0 <= v <= 1.0 for v in mean), (plan["key"], side, m)
                else:
                    assert all(v >= 0.0 for v in mean)
    json.dumps(d, allow_nan=False)


def test_build_distribution(tmp_path, company_scenario):
    from brigsim.dashboard.build import KNEE_X, build_distribution

    out = build_distribution(
        tmp_path / "distribution.json",
        company_scenario,
        KNEE_X,
        reps=12,
        seed=31002,
        kernel="mesa",
    )
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["schema"] == "brigsim-dash/distribution/1"
    assert d["n_reps"] == 12
    assert sum(d["histogram"]["counts"]) == 12
    assert 0.0 <= d["success_rate"] <= 1.0
    assert 0 <= d["success_count"] <= 12
    assert len(d["histogram"]["edges"]) == 11
    assert len(d["summary"]) == 9
    for t, s in d["summary"].items():
        assert s["label"] and s["kind"] in {"pct", "num"}
        assert s["min"] <= s["q1"] <= s["median"] <= s["q3"] <= s["max"]
    json.dumps(d, allow_nan=False)


# ---------------------------------------------------------------------------
# 共享：合成线性数据集 + 临时小代理模型（供 surrogate/pareto 构建器测试）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def synth_assets(tmp_path_factory):
    import numpy as np
    import pandas as pd

    from brigsim.surrogate.design import FEATURE_NAMES, TARGETS
    from brigsim.surrogate.model import SurrogateModel, TrainMetadata

    root = tmp_path_factory.mktemp("synth")
    rng = np.random.default_rng(7)
    W = rng.normal(0.0, 0.06, size=(10, 9))
    bias = rng.uniform(0.25, 0.65, size=9)

    def make(n: int, split: str, seed: int) -> pd.DataFrame:
        r = np.random.default_rng(seed)
        X = r.uniform(0.25, 0.95, size=(n, 10))
        y = X @ W + bias + r.normal(0.0, 0.004, size=(n, 9))
        data = {name: X[:, j] for j, name in enumerate(FEATURE_NAMES)}
        data["split"] = [split] * n
        data["n_reps"] = [8 if split == "train" else 12] * n
        for j, t in enumerate(TARGETS):
            data[t] = y[:, j]
            data[f"{t}_std"] = np.full(n, 0.01)
        return pd.DataFrame(data)

    df = pd.concat([make(120, "train", 1), make(36, "holdout", 2)], ignore_index=True)
    parquet_path = root / "synth.parquet"
    df.to_parquet(parquet_path, index=False)

    model = SurrogateModel(hidden=(16, 16))
    tr = df[df["split"] == "train"]
    model.fit(
        tr[FEATURE_NAMES],
        tr[TARGETS],
        TrainMetadata("synth", 120, 36, 8, 12, 0.1, sim_wall_seconds=120.0),
    )
    model_path = root / "synth.joblib"
    model.save(str(model_path))
    return model_path, parquet_path, root


# ---------------------------------------------------------------------------
# 5. 灵敏度 CSV 转换
# ---------------------------------------------------------------------------


def test_build_sensitivity_mini_csv(tmp_path):
    import pandas as pd

    from brigsim.dashboard.build import build_sensitivity

    metrics = ["success", "attrition_red", "readiness_red", "attrition_blue", "readiness_blue"]
    params = [
        "red_speed", "red_pk", "red_detect", "blue_speed",
        "blue_pk", "blue_detect", "mtbf", "visibility",
    ]
    rows = []
    for mi, m in enumerate(metrics):
        for pi, p in enumerate(params):
            rows.append(
                {"metric": m, "param": p, "S1": 0.3 - pi * 0.03 - mi * 0.01,
                 "S1_conf": 0.05, "ST": 0.8 - pi * 0.08 - mi * 0.01,
                 "ST_conf": 0.05, "rank": pi + 1}
            )
    csv_path = tmp_path / "sens.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    out = build_sensitivity(csv_path, tmp_path / "sensitivity.json")
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["schema"] == "brigsim-dash/sensitivity/1"
    assert len(d["metrics"]) == 5
    succ = d["metrics"][0]
    assert succ["key"] == "success"
    assert succ["rows"][0]["label"] == "红方机动速度"
    assert len(succ["rows"]) == 8
    assert len(succ["top3"]) == 3
    st_vals = [r["ST"] for r in succ["rows"]]
    assert st_vals == sorted(st_vals, reverse=True)


# ---------------------------------------------------------------------------
# 6. 代理模型 JSON（精度/parity/加速比）
# ---------------------------------------------------------------------------


def test_build_surrogate_synthetic(tmp_path, synth_assets):
    import json as _json

    from brigsim.dashboard.build import build_surrogate

    model_path, parquet_path, _ = synth_assets
    out = build_surrogate(parquet_path, model_path, tmp_path / "surrogate.json")
    d = _json.loads(out.read_text(encoding="utf-8"))
    assert d["schema"] == "brigsim-dash/surrogate/1"
    assert d["n_holdout"] == 36 and d["n_train"] == 120
    assert len(d["targets"]) == 9
    for t in d["targets"]:
        assert t["key"] and t["label"] and t["kind"] in {"pct", "num"}
        assert len(t["points"]) == 36
        assert all(isinstance(pt[0], float) and isinstance(pt[1], float) for pt in t["points"])
    mean_r2 = sum(t["r2"] for t in d["targets"]) / 9
    assert mean_r2 > 0.8, f"线性数据平均 R² 应 >0.8，实际 {mean_r2:.3f}"
    assert d["benchmark"]["speedup"] > 100
    # allow_nan=False：再次序列化不应抛
    _json.dumps(d, allow_nan=False)


# ---------------------------------------------------------------------------
# 7. Pareto 方案表 + 基线 + 复验
# ---------------------------------------------------------------------------


def test_build_pareto_mini(tmp_path, synth_assets):
    import numpy as np
    import pandas as pd

    from brigsim.dashboard.build import build_pareto
    from brigsim.surrogate.design import TARGETS

    model_path, _, _ = synth_assets
    cols = (
        ["red_repair_teams", "red_spare_stock", "resupply_interval_ticks", "resupply_qty"]
        + TARGETS + ["f_success_neg", "f_attrition_red", "f_cost"]
    )
    rng = np.random.default_rng(0)
    rows = []
    for i, (teams, stock, iv, qty, tag) in enumerate(
        [(2, 4, 40, 2, "leanest"), (3, 30, 40, 2, "knee"), (7, 30, 39, 2, "strongest")]
    ):
        vals = rng.uniform(0.2, 0.9, size=9)
        row = [teams, stock, iv, qty] + list(vals) + [-vals[0], vals[1], 0.3 + i * 0.05]
        rows.append(row)
    pareto_csv = tmp_path / "pareto.csv"
    pd.DataFrame(rows, columns=cols).assign(
        rank_tag=["leanest", "knee", "strongest"]
    ).to_csv(pareto_csv, index=False)

    val_rows = []
    for tag in ("knee", "leanest"):
        for metric in ("success", "attrition_red", "readiness_red"):
            val_rows.append(
                {"plan": tag, "metric": metric, "surrogate": 0.7,
                 "simulation": 0.68, "abs_error": 0.02, "sim_std": 0.1}
            )
    val_csv = tmp_path / "validation.csv"
    pd.DataFrame(val_rows).to_csv(val_csv, index=False)

    out = build_pareto(pareto_csv, val_csv, model_path, tmp_path / "pareto.json")
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["schema"] == "brigsim-dash/pareto/1"
    assert d["n_plans"] == 3
    assert d["tags"] == {"leanest": 0, "knee": 1, "strongest": 2}
    knee = d["plans"][1]
    assert knee["red_repair_teams"] == 3 and knee["red_spare_stock"] == 30
    assert "f_cost" not in knee
    assert set(["success", "cost"] + TARGETS) <= set(d["baseline"].keys())
    assert len(d["validation"]) == 6
    assert d["validation"][0]["metric_label"]


# ---------------------------------------------------------------------------
# 8. manifest
# ---------------------------------------------------------------------------


def test_build_manifest(tmp_path):
    from brigsim.dashboard.build import build_manifest

    out_dir = tmp_path / "data"
    out_dir.mkdir()
    (out_dir / "sensitivity.json").write_text('{"a":1}', encoding="utf-8")
    (out_dir / "pareto.json").write_text('{"b":22}', encoding="utf-8")
    rrd = tmp_path / "replay_knee.rrd"
    rrd.write_bytes(b"fake-rrd")

    out = build_manifest(
        out_dir,
        scenario="example_brigade",
        duration_ticks=59,
        vectors={"knee": [3, 4, 30, 8, 40, 2, 1, 1, 1, 1],
                 "baseline": [6, 4, 12, 8, 20, 8, 1, 1, 1, 1]},
        replay_path=rrd,
    )
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["schema"] == "brigsim-dash/manifest/1"
    assert d["duration_ticks"] == 59 and d["tick_seconds"] == 30
    assert d["files"]["sensitivity.json"] == 7
    assert d["files"]["pareto.json"] == 8
    assert "manifest.json" not in d["files"]
    assert d["replay"]["ready"] is True
    json.dumps(d, allow_nan=False)


# ---------------------------------------------------------------------------
# 9. replay_host：rrd 缺失时优雅降级（不抛异常）
# ---------------------------------------------------------------------------


def test_replay_host_missing_rrd(tmp_path):
    from brigsim.agent.replay_host import launch_replay_viewer

    status = launch_replay_viewer(tmp_path / "nope.rrd")
    assert status["ready"] is False
    assert "不存在" in status["reason"]
    assert status["rrd"].endswith("nope.rrd")


# ---------------------------------------------------------------------------
# 10. replay_host：用真实 rrd 启动 gRPC + WASM viewer（随机端口，可 HTTP 访问）
# ---------------------------------------------------------------------------


def _free_tcp_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_replay_host_real_boot():
    import time

    pytest.importorskip("rerun")
    from brigsim.agent.replay_host import launch_replay_viewer

    rrd = REPO_ROOT / "outputs" / "white_tank.rrd"
    if not rrd.is_file():
        pytest.skip(f"缺少 {rrd}")

    status = launch_replay_viewer(
        rrd, grpc_port=_free_tcp_port(), viewer_port=_free_tcp_port()
    )
    assert status["ready"] is True, status.get("reason")
    assert "?url=" in status["viewer_url"]

    # viewer 静态服务在 daemon 线程中异步就绪，短暂轮询
    last_err = None
    for _ in range(20):
        try:
            code, _, body = _get(status["viewer_url"])
            if code == 200:
                break
        except OSError as exc:  # pragma: no cover - 启动竞态
            last_err = exc
        time.sleep(0.5)
    else:  # pragma: no cover
        pytest.fail(f"viewer 首页未就绪：{last_err}")
    assert code == 200


# ---------------------------------------------------------------------------
# 11. 大屏 index.html 契约（3D 容器 / 6 KPI / C5 抽屉元素 / /api/viewer / 无录屏）
# ---------------------------------------------------------------------------

WEB_ROOT = REPO_ROOT / "src" / "brigsim" / "agent" / "web"


def test_index_contract():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    common_js = (WEB_ROOT / "js" / "common.js").read_text(encoding="utf-8")
    agent_js = (WEB_ROOT / "js" / "agent.js").read_text(encoding="utf-8")

    # 3D iframe 容器/降级卡
    for token in ('id="replay-wrap"', 'id="replay-frame"', 'id="replay-fallback"',
                  'id="replay-loading"'):
        assert token in html
    # 6 个 KPI 槽
    for kpi in ("kpi-success", "kpi-attr-red", "kpi-ready-red",
                "kpi-repair-red", "kpi-r2", "kpi-speedup"):
        assert f'id="{kpi}"' in html
    # 左列四图/右列三图容器
    for cid in ("rate-grid", "ts-svg", "dist-svg", "pareto-svg", "sens-svg", "parity-svg"):
        assert f'id="{cid}"' in html
    # 智能参谋抽屉 + C5 全部关键元素 id
    for token in ('id="agent-drawer"', 'id="btn-open-agent"', 'id="btn-close-agent"',
                  'id="msgs"', 'id="input"', 'id="send"', 'id="trace"',
                  'id="st-vram"', 'id="st-ctx"', 'id="ctx-kv"',
                  'id="svc-dot"', 'id="svc-state"', 'class="plan-btns"'):
        assert token in html
    for tag in ("knee", "leanest", "strongest"):
        assert f'data-tag="{tag}"' in html
    # C5 回归锚点：首页仍含本地大模型要素
    assert "本地大模型" in html
    # viewer 接入引用 /api/viewer
    assert "/api/viewer" in common_js
    # 静态资源引用且文件存在
    for rel in ("css/base.css", "css/dash.css", "js/common.js", "js/agent.js"):
        assert f"/static/{rel}" in html
        assert (WEB_ROOT / rel).is_file()
    # 明确不做页内录屏
    for blob in (html, common_js, agent_js):
        assert "MediaRecorder" not in blob
        assert "getDisplayMedia" not in blob


# ---------------------------------------------------------------------------
# 12. dash-build CLI 冒烟（Mesa 小复制，不出 rrd）
# ---------------------------------------------------------------------------


def test_cli_dash_build_smoke(tmp_path):
    from typer.testing import CliRunner

    from brigsim.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "dash-build", "example_company",
            "--kernel", "mesa",
            "--ts-reps", "2",
            "--dist-reps", "4",
            "--no-rrd",
            "--only", "timeseries,distribution,manifest",
            "--out", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "timeseries.json").is_file()
    assert (tmp_path / "distribution.json").is_file()
    assert (tmp_path / "manifest.json").is_file()
    d = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    # --no-rrd 只表示本次不重新录制；默认路径上若已有 rrd（如本机刚生成过），
    # manifest 仍如实报告可用，否则为未就绪。
    from brigsim.dashboard.build import DEFAULT_RRD

    assert d["replay"]["ready"] is DEFAULT_RRD.is_file()
