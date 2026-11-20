"""浏览器实测：下拉切换工况时 3D 地图与全部数据面板是否自动刷新。

用法：
  PYTHONPATH=src python tools/verify_scenario_switch.py [目标工况]

输出包含 iframe 地址、工况徽标、KPI 数字、5 个 SVG 的内容摘要在切换前后的
对比，以及页面/console 报错；同时保存切换前后截图到 tmp_rr/。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tmp_rr"


def snapshot(page):
    def digest(sel):
        txt = page.locator(sel).inner_html() if page.locator(sel).count() else ""
        return hashlib.md5(txt.encode("utf-8", "ignore")).hexdigest()[:10]

    kpis = page.locator(".kpi-v").all_inner_texts()
    return {
        "iframe": page.locator("#replay-frame").get_attribute("src") or "",
        "context": (page.locator("#scenario-context").inner_text()
                    if page.locator("#scenario-context").count() else ""),
        "baseline": page.locator("#baseline-tag").inner_text(),
        "subtitle": (page.locator("#replay-knee-sub").inner_text()
                     if page.locator("#replay-knee-sub").count() else ""),
        "kpis": "|".join(kpis),
        "charts": ",".join(digest(s) for s in
                           ["#ts-svg", "#dist-svg", "#pareto-svg", "#sens-svg", "#parity-svg"]),
    }


def main() -> None:
    from playwright.sync_api import sync_playwright

    target = sys.argv[1] if len(sys.argv) > 1 else "brigade_canyon_clear"
    OUT.mkdir(exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader", "--use-gl=angle"])
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
        page.goto("http://127.0.0.1:8848/index.html?mode=replay",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#scenario-select option", state="attached", timeout=30000)
        page.wait_for_timeout(14000)
        before = snapshot(page)
        page.screenshot(path=str(OUT / "switch_before.png"))
        page.select_option("#scenario-select", target)
        page.wait_for_function(
            "(old) => (document.querySelector('#replay-frame')?.src || '') !== old",
            arg=before["iframe"], timeout=60000,
        )
        page.wait_for_timeout(12000)
        after = snapshot(page)
        page.screenshot(path=str(OUT / "switch_after.png"))
        browser.close()

    print("=== 工况切换自动刷新验证 ===")
    for k in before:
        flag = "变化" if before[k] != after[k] else "未变"
        print(f"{k:10s} [{flag}]")
        print(f"  前: {before[k][:180]}")
        print(f"  后: {after[k][:180]}")
    bad = ("no triangle indices", "triangle indices", "invalid index",
           "memory budget", "failed to load mesh", "undefined", "is not a function")
    hits = [e for e in errors if any(k in e.lower() for k in bad)]
    print(f"页面/console 异常命中 {len(hits)}，全部 error {len(errors)}")
    for e in hits[:10]:
        print("  ", e[:220])
    print(f"截图: {OUT / 'switch_before.png'} / {OUT / 'switch_after.png'}")


if __name__ == "__main__":
    main()
