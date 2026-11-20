# -*- coding: utf-8 -*-
"""C7-h GUI 验证：作用范围 LOS 遮挡 + rolling 地形差异化。

运行：DASH_URL=http://127.0.0.1:8848 python tests/test_c7_h_gui.py
"""
import os
from pathlib import Path
from urllib.parse import unquote

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DASH_URL", "http://127.0.0.1:8848")
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


def main():
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

        # 1) 切到 example_brigade（21个雷达作用圈，LOS遮挡最明显）
        page.select_option("#scenario-select", "example_brigade")
        page.wait_for_timeout(3000)
        src = unquote(page.evaluate("document.getElementById('replay-frame')?.src") or "")
        log("作用圈想定走静态rrd", "/rerun-file/" in src and "example_brigade" in src, src[-80:])
        page.wait_for_timeout(28000)
        shot1 = SHOTS / "c7h_los_example_brigade.png"
        page.screenshot(path=str(shot1))
        log("example_brigade作用圈截图", shot1.exists())

        # 2) 地形差异化：medium_brigade vs brigade_rolling_fortified
        page.select_option("#scenario-select", "medium_brigade")
        page.wait_for_timeout(26000)
        shot2 = SHOTS / "c7h_medium_terrain.png"
        page.screenshot(path=str(shot2))
        log("medium地形截图", shot2.exists())

        page.select_option("#scenario-select", "brigade_rolling_fortified")
        page.wait_for_timeout(26000)
        shot3 = SHOTS / "c7h_rolling_fortified_terrain.png"
        page.screenshot(path=str(shot3))
        log("rolling_fortified地形截图", shot3.exists())

        # 像素差异（两个丘陵想定地形应不同）
        try:
            from PIL import Image, ImageChops
            import numpy as np
            a = np.asarray(Image.open(shot2).convert("RGB"), dtype=np.int16)
            b = np.asarray(Image.open(shot3).convert("RGB"), dtype=np.int16)
            diff = float(np.abs(a - b).mean())
            log("两丘陵想定画面差异>3%", diff > 3.0, f"平均像素差={diff:.1f}")
        except Exception as e:
            log("像素对比", False, str(e)[:60])

        log("无JS异常", len(errs) == 0, "; ".join(errs[:2])[:120])
        print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
        browser.close()


if __name__ == "__main__":
    main()
