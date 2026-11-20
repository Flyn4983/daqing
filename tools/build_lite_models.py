"""生成 Web 回放专用的低面数地面装备 GLB（避免 rrd 体积爆炸）。

原 assets/models/{tank,ifv,atgm_team}.glb 为高面数模型（0.5~3MB/辆），
逐单位嵌入 164 辆旅级回放会让 rrd 达到 162~268MB，超过 Rerun Web Viewer
的 WASM 内存预算。这里复用 src/brigsim/viz/models.py 的程序化构建器生成
低面数版本（几 KB/辆），保留真实装备轮廓（炮塔/炮管/履带/车轮/三脚架）。

用法：
  .venv/Scripts/python.exe tools/build_lite_models.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import trimesh  # noqa: E402

from brigsim.viz import models  # noqa: E402


def main() -> None:
    models.LITE_DIR.mkdir(parents=True, exist_ok=True)
    for typ in models.LITE_TYPES:
        builder = models._BUILDERS[typ]
        mesh = trimesh.util.concatenate(builder())
        out = models.LITE_DIR / f"{typ}.glb"
        mesh.export(out, file_type="glb")
        print(f"{typ:12s} {out.stat().st_size / 1024:7.1f} KB  {out}")


if __name__ == "__main__":
    main()
