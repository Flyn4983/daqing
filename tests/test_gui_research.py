# -*- coding: utf-8 -*-
"""研究细节面板 GUI 测试：按钮、标签切换、翻页、三种关闭方式、数字正确性。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8850"
SHOT = Path("report/_gui_test_shots")
SHOT.mkdir(parents=True, exist_ok=True)

TABS = ["design", "m1", "m2", "m3", "m4", "verify"]
TAB_TITLES = {
    "design": "方案设计", "m1": "模型一", "m2": "模型二",
    "m3": "模型三", "m4": "模型四", "verify": "检验验证",
}

results: list[tuple[str, bool, str]] = []


def log(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}" + (f" | {detail}" if detail else ""))


def main() -> int:
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel="msedge")
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        page.goto(f"{BASE}/index.html", wait_until="domcontentloaded")
        page.evaluate("sessionStorage.setItem('user', JSON.stringify({username:'admin',role:'admin'}))")
        page.wait_for_timeout(2500)

        # 1. 打开按钮
        btn = page.locator("#btn-research")
        log("研究细节按钮存在", btn.count() == 1)
        btn.click()
        page.wait_for_timeout(1200)
        modal = page.locator("#research-modal")
        log("点击打开面板", modal.is_visible())

        # 2. 数据加载 + 默认设计页
        body = page.locator("#research-body")
        log("默认显示方案设计页", "反问题" in body.inner_text())
        page.screenshot(path=str(SHOT / "r01_design.png"))

        # 3. 六个标签逐一切换
        for i, tab in enumerate(TABS[1:], start=1):
            page.locator(f'.rtab[data-rtab="{tab}"]').click()
            page.wait_for_timeout(400)
            txt = body.inner_text()
            active = page.locator(f'.rtab[data-rtab="{tab}"]').get_attribute("class") or ""
            log(f"切到{TAB_TITLES[tab]}标签", "active" in active and len(txt) > 200, f"{len(txt)}字")
            page.screenshot(path=str(SHOT / f"r0{i+1}_{tab}.png"))

        # 4. 数字正确性（模型一/四）
        page.locator('.rtab[data-rtab="m1"]').click()
        page.wait_for_timeout(400)
        m1 = body.inner_text()
        log("模型一η分解0.488→0.491", "0.488" in m1 and "0.491" in m1)
        log("模型一扰动69~73号", "69" in m1 and "73" in m1 and "200" in m1)
        log("模型一零越界", "0 项越界" in m1)
        log("模型一三源约束", all(s in m1 for s in ["A 损伤源", "B 权威源", "C 资源源", "D 逻辑随机"]))

        page.locator('.rtab[data-rtab="m4"]').click()
        page.wait_for_timeout(400)
        m4 = body.inner_text()
        log("模型四终审130/200", "130/200" in m4 and "65.0%" in m4)
        log("模型四K1/K2", "48" in m4 and "200" in m4)
        log("模型四Wilson区间", "0.582" in m4)
        log("模型四消融8→2", "8" in m4 and "2" in m4)

        # 5. 翻页按钮
        page.locator('.rtab[data-rtab="design"]').click()
        page.wait_for_timeout(300)
        page.locator("#research-prev").click()
        page.wait_for_timeout(300)
        prev_active = page.locator('.rtab.active').get_attribute("data-rtab")
        log("上一节回绕到verify", prev_active == "verify", str(prev_active))
        page.locator("#research-next").click()
        page.wait_for_timeout(300)
        next_active = page.locator('.rtab.active').get_attribute("data-rtab")
        log("下一节回绕到design", next_active == "design", str(next_active))

        # 6. 三种关闭方式
        page.locator("#research-close").click()
        page.wait_for_timeout(300)
        log("✕关闭", not modal.is_visible())
        btn.click(); page.wait_for_timeout(600)
        page.locator("#research-done").click()
        page.wait_for_timeout(300)
        log("关闭按钮关闭", not modal.is_visible())
        btn.click(); page.wait_for_timeout(600)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        log("Esc关闭", not modal.is_visible())

        # 7. 遮罩关闭
        btn.click(); page.wait_for_timeout(600)
        box = modal.bounding_box()
        if box:
            page.mouse.click(box["x"] + 5, box["y"] + 5)
            page.wait_for_timeout(300)
            log("点遮罩关闭", not modal.is_visible())
        else:
            log("点遮罩关闭", False, "no bbox")

        log("无JS错误", len(errs) == 0, ";".join(errs[:2]))
        browser.close()

    ok = sum(1 for _, o, _ in results if o)
    print(f"\n结果：{ok}/{len(results)} 通过")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
