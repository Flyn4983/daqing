# -*- coding: utf-8 -*-
"""细节内嵌模块 GUI 测试：ℹ按钮 / 报告映射说明带 / 图表锚点 / 浮层开关 / 下拉title。
运行前需有服务：DASH_URL=http://127.0.0.1:8850 python tests/test_gui_insight.py
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DASH_URL", "http://127.0.0.1:8850")
SHOT = os.path.join(os.path.dirname(__file__), "..", "report", "_gui_test_shots")
os.makedirs(SHOT, exist_ok=True)

results = []


def log(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, "|", detail)


def main():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, channel="msedge")
        except Exception:
            b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f"{BASE}/index.html?mode=replay", wait_until="domcontentloaded")
        page.evaluate("sessionStorage.setItem('user', JSON.stringify({username:'admin',role:'admin'}))")
        page.wait_for_timeout(3000)

        # 1. insight.js 已加载、数据注入完成
        log("insight模块加载", page.evaluate("typeof window.DASH === 'object'"),
            "DASH ready")

        # 2. 配置弹窗参数项 ℹ 按钮数量（应 >= 20）—— 元素已在DOM，无需打开弹窗即可计数
        n_cfg_info = page.locator(".cfg-item .i-btn").count()
        log("配置参数ℹ按钮注入", n_cfg_info >= 20, f"count={n_cfg_info}")

        # 3. 组说明带数量（应 = 7）
        n_bands = page.locator(".cfg-group .insight-band").count()
        log("报告映射说明带", n_bands == 7, f"count={n_bands}")

        # 4. 图表 ℹ 按钮（应 >= 5）
        n_chart_info = page.locator(".panel h3 .i-btn").count()
        log("图表面板ℹ按钮", n_chart_info >= 5, f"count={n_chart_info}")

        # 5. 图表锚点带（应 >= 5）
        n_anchor = page.locator(".chart-anchor").count()
        log("图表报告锚点带", n_anchor >= 5, f"count={n_anchor}")

        # 6. 工况下拉 option title
        n_title = page.locator("#scenario-select option[title]").count()
        log("工况下拉title", n_title >= 3, f"count={n_title}")

        # 7. 关键：点开能见度ℹ，浮层显示 ST=0.758
        # 先打开配置弹窗（replay模式下顶栏▶隐藏，直接JS显示弹窗，测的是控件交互本身）
        page.evaluate("document.getElementById('config-modal').hidden=false")
        page.wait_for_timeout(400)
        log("配置弹窗打开", page.locator("#config-modal").is_visible(), "visible")
        vis_btn = page.locator("#cfg-visibility_factor").locator("xpath=ancestor::div[contains(@class,'cfg-item')]").locator(".i-btn")
        if vis_btn.count():
            vis_btn.first.click()
            page.wait_for_timeout(400)
            pop = page.locator("#insight-pop")
            visible = pop.is_visible()
            txt = pop.inner_text() if visible else ""
            log("ℹ浮层-能见度打开", visible, txt[:60].replace("\n", " "))
            log("ℹ浮层-ST=0.758", "0.758" in txt, "found" if "0.758" in txt else txt[:80])
            log("ℹ浮层-蓝方战损率rank1", "attrition_blue" in txt and "rank 1" in txt,
                "found" if "attrition_blue" in txt else "missing")
            page.screenshot(path=os.path.join(SHOT, "i01_visibility_pop.png"))
            # 8. 关闭按钮
            page.click("#insight-pop .ip-close")
            page.wait_for_timeout(250)
            log("ℹ浮层-✕关闭", not page.locator("#insight-pop").is_visible(), "closed")
            # 9. Esc 关闭
            vis_btn.first.click(); page.wait_for_timeout(250)
            page.keyboard.press("Escape"); page.wait_for_timeout(250)
            log("ℹ浮层-Esc关闭", not page.locator("#insight-pop").is_visible(), "closed")
            # 10. 点遮罩/空白处关闭（用浮层外的固定空白坐标，避开控件）
            vis_btn.first.click(); page.wait_for_timeout(250)
            page.mouse.click(250, 900); page.wait_for_timeout(250)
            log("ℹ浮层-点空白关闭", not page.locator("#insight-pop").is_visible(), "closed")
            # 关闭配置弹窗（✕），恢复图表可点
            page.evaluate("document.getElementById('config-modal').hidden=true") if page.locator("#config-modal").count() else None
            page.wait_for_timeout(300)
        else:
            log("ℹ浮层-能见度打开", False, "未找到能见度ℹ按钮")

        # 11. 图表面板ℹ：Pareto 显示 74 解与成本
        pareto_btn = page.locator("#pareto-svg").locator("xpath=ancestor::section[contains(@class,'panel')]").locator("h3 .i-btn")
        if pareto_btn.count():
            pareto_btn.first.click(); page.wait_for_timeout(400)
            txt = page.locator("#insight-pop").inner_text() if page.locator("#insight-pop").is_visible() else ""
            ok74 = "74" in txt
            okcost = "0.458" in txt and "0.165" in txt
            log("图表面板ℹ-Pareto打开", len(txt) > 0, txt[:50].replace("\n", " "))
            log("图表面板ℹ-74解数字", ok74, "found" if ok74 else txt[:80])
            log("图表面板ℹ-成本口径", okcost, "found" if okcost else "missing")
            page.screenshot(path=os.path.join(SHOT, "i02_pareto_pop.png"))
            page.keyboard.press("Escape"); page.wait_for_timeout(200)
        else:
            log("图表面板ℹ-Pareto打开", False, "未找到Paretoℹ按钮")

        # 12. MC分布ℹ：130/200 与 Wilson
        dist_btn = page.locator("#dist-svg").locator("xpath=ancestor::section[contains(@class,'panel')]").locator("h3 .i-btn")
        if dist_btn.count():
            dist_btn.first.click(); page.wait_for_timeout(400)
            txt = page.locator("#insight-pop").inner_text() if page.locator("#insight-pop").is_visible() else ""
            ok130 = "130/200" in txt and "65.0%" in txt
            okwil = "0.582" in txt and "0.713" in txt
            log("图表面板ℹ-130/200终审", ok130, "found" if ok130 else txt[:80])
            log("图表面板ℹ-Wilson区间", okwil, "found" if okwil else "missing")
            page.keyboard.press("Escape"); page.wait_for_timeout(200)
        else:
            log("图表面板ℹ-MC分布打开", False, "未找到MC分布ℹ按钮")

        # 13. 无 JS 错误
        log("无页面JS错误", len(errs) == 0, f"{len(errs)} errors" + (f": {errs[0][:80]}" if errs else ""))

        # 14. 截图整体
        page.screenshot(path=os.path.join(SHOT, "i03_full.png"))
        b.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n结果: {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
