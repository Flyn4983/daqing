# -*- coding: utf-8 -*-
"""研究细节面板「真实数据溯源」页渲染验证。
运行：DASH_URL=http://127.0.0.1:8861 python tests/test_realdata_panel.py
"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DASH_URL", "http://127.0.0.1:8861")
SHOTS = Path(__file__).resolve().parent.parent / "report" / "_gui_test_shots"
PASS = FAIL = 0


def log(name, ok, detail=""):
    global PASS, FAIL
    if ok: PASS += 1
    else: FAIL += 1
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
        page.goto(BASE + "/index.html", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # 打开研究细节
        page.click("#btn-research") if page.locator("#btn-research").count() else None
        page.wait_for_timeout(1200)
        log("真实数据溯源标签存在", page.locator('[data-rtab="realdata"]').count() == 1)

        page.click('[data-rtab="realdata"]')
        page.wait_for_timeout(800)
        body = page.locator("#research-body").inner_text()
        log("显示太行山地形", "太行山" in body, "")
        log("显示真实高程", "2052" in body and "879" in body, "")
        log("显示ERA5气象", "ERA5" in body, "")
        log("显示72小时样本", "72" in body, "")
        log("显示天气分布", "晴" in body and "多云" in body and "降雨" in body, "")
        log("显示许可说明", "2026-11-20" in body and "SRTM" in body, "")

        shot = SHOTS / "realdata_panel.png"
        page.screenshot(path=str(shot))
        log("面板截图已存", shot.exists(), str(shot))
        log("无JS异常", len([e for e in errs if "favicon" not in e.lower()]) == 0, str(errs[:2]))
        browser.close()
    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
