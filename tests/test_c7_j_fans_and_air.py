"""C7-j 回归：
1) 扇面被地形全部遮挡时不得 log 空 triangle_indices 的 Mesh3D（峡谷场景
   "Mesh has no triangle indices" 根因）；outline=True/False 两分支都覆盖。
2) 空中单位 use_models=True 时改用 GLB 模型 + Transform3D，且带型号标签。

运行：PYTHONPATH=src python -m pytest tests/test_c7_j_fans_and_air.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import rerun as rr

from brigsim.viz import rerun_view as rv


class _FakeTerrain:
    """所有射线都判为遮挡（模拟峡谷四周高山）。"""

    def has_los(self, ox, oy, px, py, h):
        return False

    def height_at(self, x, y):
        return 1000.0


class _FakeGeo:
    def height_at(self, x, y):
        x = np.asarray(x)
        return np.full(x.shape, 1000.0) if x.ndim else 1000.0


def _capture(monkeypatch):
    logged = []
    orig = rv.rr.log

    def spy(entity, data, **kw):
        logged.append((entity, data, kw))
        # 不真正 log，避免初始化 rerun recording

    monkeypatch.setattr(rv.rr, "log", spy)
    return logged


def _mini_cur():
    return pd.DataFrame(
        {"x": [5000.0], "y": [5000.0], "heading": [0.0], "alive": [True]},
        index=["u1"],
    )


def _info():
    # (typ, side, ?, range, role, cov)
    return {"u1": ("tank", 0, None, 3000.0, "main", 0.0)}


def _assert_no_empty_mesh(logged):
    for ent, data, kw in logged:
        if isinstance(data, rr.Mesh3D):
            tri = data.triangle_indices
            arr = tri.as_arrow_array() if hasattr(tri, "as_arrow_array") else None
            if arr is not None:
                n = len(arr)
                assert n > 0, f"{ent} 出现空 triangle_indices（会导致 viewer 报错）"


def test_fans_outline_true_all_occluded_emits_clear(monkeypatch):
    """大屏 live 分支：全部遮挡时应 Clear，不得 log 空 Mesh。"""
    logged = _capture(monkeypatch)
    rv._log_fans(_mini_cur(), np.array([1000.0]), _info(),
                 outline=True, geo=_FakeGeo(), los_terrain=_FakeTerrain())
    fan_logs = [(e, d) for e, d, _ in logged if e == "world/fans"]
    assert fan_logs, "应至少对 world/fans 有一次操作"
    _assert_no_empty_mesh(logged)
    # 最后一条应为 Clear
    assert isinstance(fan_logs[-1][1], rr.Clear), "全部遮挡时 world/fans 应为 Clear"


def test_fans_outline_false_all_occluded_emits_clear(monkeypatch):
    """桌面/独立窗口分支：全部遮挡时同样不得 log 空 Mesh。"""
    logged = _capture(monkeypatch)
    rv._log_fans(_mini_cur(), np.array([1000.0]), _info(),
                 outline=False, geo=_FakeGeo(), los_terrain=_FakeTerrain())
    fan_logs = [(e, d) for e, d, _ in logged if e == "world/fans"]
    assert fan_logs
    _assert_no_empty_mesh(logged)
    assert isinstance(fan_logs[-1][1], rr.Clear)


def test_fans_visible_still_emits_mesh(monkeypatch):
    """可见时仍正常画 Mesh（确保修复没有过度裁剪）。"""
    logged = _capture(monkeypatch)
    rv._log_fans(_mini_cur(), np.array([1000.0]), _info(),
                 outline=True, geo=_FakeGeo(), los_terrain=None)
    meshes = [d for e, d, _ in logged if e == "world/fans" and isinstance(d, rr.Mesh3D)]
    assert meshes, "无遮挡时应有 Mesh3D"
    _assert_no_empty_mesh(logged)


def test_air_uses_real_model_and_transform(monkeypatch):
    """use_models=True：应发 Asset3D（静态）+ Transform3D + 型号标签。"""
    logged = _capture(monkeypatch)
    air = pd.DataFrame(
        {
            "tick": [0, 1], "asset_id": ["A1", "A1"], "side": [0, 0],
            "kind": ["uav", "uav"], "x": [1000.0, 1300.0],
            "y": [2000.0, 2000.0], "alt": [1500.0, 1500.0],
            "state": ["active", "active"],
        }
    )
    trails, done, model_logged = {}, set(), set()
    rv._log_air(0, air, trails, done, use_models=True, model_logged=model_logged)
    rv._log_air(1, air, trails, done, use_models=True, model_logged=model_logged)
    assets = [d for e, d, _ in logged if e.endswith("/model") and isinstance(d, rr.Asset3D)]
    transforms = [d for e, d, _ in logged if e == "world/air/A1" and isinstance(d, rr.Transform3D)]
    labels = [d for e, d, _ in logged if e.endswith("/label") and isinstance(d, rr.Points3D)]
    assert len(assets) == 1, "Asset3D 应只静态发一次"
    assert len(transforms) >= 2, "每个 tick 应更新 Transform3D"
    def _labels_of(d):
        lab = getattr(d, "labels", None)
        arr = lab.as_arrow_array() if hasattr(lab, "as_arrow_array") else lab
        try:
            return [str(x) for x in arr.to_pylist()]
        except Exception:
            return [str(lab)]

    assert labels and any("无人机" in s for d in labels for s in _labels_of(d))


def test_air_without_models_falls_back_to_point(monkeypatch):
    """use_models=False（轻量回放）应回退 Points3D 圆球。"""
    logged = _capture(monkeypatch)
    air = pd.DataFrame(
        {
            "tick": [0], "asset_id": ["A1"], "side": [0], "kind": ["helo"],
            "x": [1000.0], "y": [2000.0], "alt": [1500.0], "state": ["active"],
        }
    )
    rv._log_air(0, air, {}, set(), use_models=False)
    marks = [d for e, d, _ in logged if e.endswith("/mark") and isinstance(d, rr.Points3D)]
    assert marks, "轻量模式应有 Points3D 圆球"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
