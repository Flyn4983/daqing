# -*- coding: utf-8 -*-
"""真实地形/气象验证：新增想定 real_terrain_brigade 的 DEM 与 ERA5 气象落地检查。

运行：DASH_URL=http://127.0.0.1:8861 python tests/test_real_terrain_visual.py
"""
import os
import json
import urllib.request
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DASH_URL", "http://127.0.0.1:8861")
SHOTS = Path(__file__).resolve().parent.parent / "report" / "_gui_test_shots"
SHOTS.mkdir(parents=True, exist_ok=True)
PASS = FAIL = 0


def log(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=10).read())


def main():
    # ---- 后端：数据资产存在性 ----
    meta = json.loads((Path(__file__).resolve().parent.parent / "scenarios" / "terrain" / "REAL_TERRAIN_META.json").read_text(encoding="utf-8"))
    log("真实DEM元数据-太行山区", "太行山" in meta["region"], meta["region"])
    log("真实DEM高程跨度>500m", meta["elev_max_m"] - meta["elev_min_m"] > 500,
        f"{meta['elev_min_m']:.0f}~{meta['elev_max_m']:.0f}m")

    wx = json.loads((Path(__file__).resolve().parent.parent / "src" / "brigsim" / "agent" / "web" / "data" / "real_weather.json").read_text(encoding="utf-8"))
    log("ERA5气象72小时", len(wx["hourly"]) == 72, f"n={len(wx['hourly'])}")
    log("ERA5含真实天气枚举", {"clear", "rain", "overcast"} <= set(r["weather"] for r in wx["hourly"]),
        str(set(r["weather"] for r in wx["hourly"])))

    scs = get("/api/scenarios")["scenarios"]
    real = [s for s in scs if s["file"] == "real_terrain_brigade"]
    log("下拉含真实想定", len(real) == 1, real[0]["label_zh"] if real else "缺失")
    if real:
        log("真实想定气象=ERA5 overcast", real[0]["weather"] == "overcast" and abs(real[0]["visibility_factor"] - 0.7) < 1e-6,
            f"{real[0]['weather']} vis={real[0]['visibility_factor']}")
        log("真实想定地形=geotiff", real[0]["terrain_source"] == "geotiff")

    # ---- 后端：preview 返回真实 rrd ----
    r = post("/api/scene-switch", {"scenario": "real_terrain_brigade", "mode": "preview"})
    log("preview返回真实rrd", r.get("ok") is True and r.get("file") == "replay_real_terrain_brigade.rrd", str(r)[:110])

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel="msedge")
        except Exception:
            browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        page.goto(BASE + "/login.html", wait_until="domcontentloaded")
        page.evaluate("sessionStorage.setItem('user',JSON.stringify({username:'admin',role:'admin'}))")
        page.goto(BASE + "/index.html?mode=live", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        if page.locator("#config-modal").count() and page.locator("#config-modal").is_visible():
            page.click("#config-close")
        page.wait_for_timeout(800)

        # 切到真实地形想定
        page.select_option("#scenario-select", "real_terrain_brigade")
        page.wait_for_timeout(3000)
        src = unquote(page.evaluate("document.getElementById('replay-frame')?.src") or "")
        log("真实地形走静态rrd直载", "/rerun-file/" in src and "real_terrain_brigade" in src, src[-90:])
        log("静态直载标记置位", page.evaluate("window.__rerun_viewer_static === true"))

        # 充分等待 wasm + rrd 数据流
        page.wait_for_timeout(28000)
        shot = SHOTS / "real_terrain_3d.png"
        page.screenshot(path=str(shot))
        log("真实地形截图已存", shot.exists(), str(shot))

        # 对比：切到峡谷想定（合成地形）
        page.select_option("#scenario-select", "brigade_canyon_clear")
        page.wait_for_timeout(26000)
        shot2 = SHOTS / "real_terrain_compare_canyon.png"
        page.screenshot(path=str(shot2))
        log("对比峡谷截图已存", shot2.exists(), str(shot2))

        # 像素差异（确认两者画面确实不同）
        try:
            from PIL import Image, ImageChops
            import numpy as np
            a = np.asarray(Image.open(shot).convert("RGB"), dtype=np.int16)
            b = np.asarray(Image.open(shot2).convert("RGB"), dtype=np.int16)
            diff = float((np.abs(a - b).mean(axis=2) > 18).mean())
            log("真实地形与合成峡谷画面显著不同(>10%)", diff > 0.10, f"差异={diff*100:.1f}%")
        except Exception as e:
            log("像素差异计算", False, str(e))

        real_errs = [e for e in errs if "favicon" not in e.lower()]
        log("无前端JS异常", len(real_errs) == 0, str(real_errs[:2]))
        browser.close()

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
