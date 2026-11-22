"""浏览器实测：二维可行域投影（74 方案 × GUI 约束边界，只读派生）+ P3 约束录入回归。

用法：PYTHONPATH=src .venv/Scripts/python.exe tools/verify_region_gui.py
前置：8848 服务已启动并加载新代码（含 /api/support/pareto/region）。
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    results: dict[str, object] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for pat in ("**/rerun-viewer/**", "**/proxy/**", "**/app.rerun.io/**", "**/telemetry.rerun.io/**"):
            page.route(pat, lambda route: route.abort())
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "CSP" not in m.text and "rerun" not in m.text.lower() else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto("http://127.0.0.1:8848/index.html", wait_until="domcontentloaded", timeout=30000)
        page.locator("#btn-support-flow").wait_for(state="attached", timeout=20000)
        page.wait_for_timeout(400)
        page.locator("#btn-support-flow").click()
        page.wait_for_timeout(1000)

        # --- 回归：P3 四源标题（text_content 抓折叠体，修复假阴性）---
        cgroup = page.locator("#sf-form .sf-constraints")
        results["cgroup_exists"] = cgroup.count() == 1
        ctext = cgroup.text_content() if cgroup.count() else ""
        results["has_four_sources"] = all(
            s in ctext for s in ("资源现实源", "指挥权威源", "战损物理源", "软约束"))
        results["field_count"] = page.locator('#sf-form [data-group="constraints"]').count()

        # --- 打开 Pareto 模态（成本选优入口）---
        # 入口按钮在方案栏；直接调用全局不存在，走 UI 点击
        # 先保存一次录入，保证 mission 存在
        page.locator("#sf-save").click()
        page.wait_for_timeout(800)

        # 找 Pareto 入口按钮（文本含"成本选优"/"反推最优保障参数"）
        opened = False
        for sel in ("#sf-pareto-pick", "#sf-open-pareto", "#sf-cost-pareto", ".sf-pareto-entry-btn"):
            loc = page.locator(sel)
            if loc.count() >= 1:
                loc.first.click()
                opened = True
                break
        if not opened:
            btns = page.get_by_text("成本选优", exact=False)
            if btns.count() >= 1:
                btns.first.click()
                opened = True
        if not opened:
            btns = page.get_by_text("反推最优保障参数", exact=False)
            if btns.count() >= 1:
                btns.first.click()
                opened = True
        results["pareto_opened"] = opened
        page.wait_for_timeout(1400)

        pareto_modal = page.locator("#sf-modal-pareto")
        results["pareto_modal_visible"] = pareto_modal.count() == 1 and not pareto_modal.is_hidden()

        # --- 点击"查看二维可行域投影"入口 ---
        region_btn = page.locator("#sf-open-region")
        results["region_entry_exists"] = region_btn.count() == 1
        if region_btn.count() == 1:
            region_btn.click()
            page.wait_for_timeout(1400)

        region_modal = page.locator("#sf-modal-region")
        results["region_modal_visible"] = region_modal.count() == 1 and not region_modal.is_hidden()
        rbody = page.locator("#sf-modal-region-body")
        rtxt = rbody.inner_text() if rbody.count() else ""

        # SVG 渲染检查
        svg = rbody.locator("svg.sf-region-svg")
        results["region_svg_exists"] = svg.count() == 1
        if svg.count() == 1:
            results["region_svg_points"] = svg.locator("circle").count()
            results["region_svg_knee"] = svg.locator("polygon").count()
            results["region_svg_has_dash"] = svg.locator("line[stroke-dasharray]").count() > 0
        results["region_lead_ok"] = ("74" in rtxt and "可行" in rtxt and "knee" in rtxt.lower())
        results["region_note_ok"] = ("非连续可行域" in rtxt or "只读" in rtxt)

        # 轴切换：改 X 轴为 cost（metric），看 SVG 是否重绘不报错
        sel_x = page.locator("#sf-region-x")
        results["axis_selectors"] = sel_x.count() == 1 and page.locator("#sf-region-y").count() == 1
        if sel_x.count() == 1:
            sel_x.select_option("cost")
            page.wait_for_timeout(500)
            results["axis_switch_ok"] = rbody.locator("svg.sf-region-svg circle").count() > 0

        # 关闭 modal
        close = page.locator('#sf-modal-region .sf-modal-x')
        if close.count() == 1:
            close.click()
            page.wait_for_timeout(300)
        results["region_closed"] = region_modal.is_hidden()

        # --- 带紧约束场景：验证虚线边界与红点确实渲染（核心卖点）---
        tight = {
            "task_name": "可行域验证", "mission_type": "进攻", "base_scenario": "example_brigade",
            "equipment": [{"key": "tank", "count": 10}],
            "support_vehicles": [{"key": "repair_veh", "count": 2},
                                 {"key": "arv", "count": 2}, {"key": "supply_veh", "count": 1}],
            "constraints": {"repair_veh_cap": 2, "arv_cap": 6, "supply_veh_cap": 4},
        }
        save_resp = page.request.post("http://127.0.0.1:8848/api/support/mission", data=tight)
        results["tight_mission_saved"] = save_resp.ok and ("error" not in (save_resp.json() or {}))
        page.reload(wait_until="domcontentloaded")
        page.locator("#btn-support-flow").wait_for(state="attached", timeout=20000)
        page.wait_for_timeout(500)
        page.locator("#btn-support-flow").click()
        page.wait_for_timeout(900)
        pp2 = page.locator("#sf-pareto-pick")
        if pp2.count() == 1:
            pp2.click()
            page.wait_for_timeout(1300)
        rb2 = page.locator("#sf-open-region")
        if rb2.count() == 1:
            rb2.click()
            page.wait_for_timeout(1400)
        svg2 = page.locator("#sf-modal-region-body svg.sf-region-svg")
        results["tight_svg_exists"] = svg2.count() == 1
        if svg2.count() == 1:
            results["tight_has_dash"] = svg2.locator("line[stroke-dasharray]").count() > 0
            red = svg2.locator('circle[fill="#f87171"]').count()
            results["tight_red_points"] = red
            results["tight_lead_filtered"] = "剔除" in page.locator("#sf-modal-region-body").inner_text()

        browser.close()

    critical = ["cgroup_exists", "has_four_sources", "pareto_opened", "pareto_modal_visible",
                "region_entry_exists", "region_modal_visible", "region_svg_exists",
                "region_lead_ok", "axis_selectors", "axis_switch_ok", "region_closed",
                "tight_mission_saved", "tight_svg_exists", "tight_has_dash", "tight_lead_filtered"]
    failed = [k for k in critical if results.get(k) is not True]
    # 数字项单独断言
    if not isinstance(results.get("field_count"), int) or results["field_count"] < 1:
        failed.append("field_count<1")
    if not isinstance(results.get("region_svg_points"), int) or results["region_svg_points"] < 50:
        failed.append("region_svg_points<50")
    if results.get("region_svg_knee", 0) != 1:
        failed.append("region_svg_knee!=1")
    if not isinstance(results.get("tight_red_points"), int) or results["tight_red_points"] < 1:
        failed.append("tight_red_points<1")

    print("=== 结果 ===")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print(f"=== 浏览器错误 {len(errors)} 条 ===")
    for e in errors[:10]:
        print("  ", e)
    ok = not failed and not errors
    print("VERDICT:", "PASS ✓" if ok else f"FAIL: {failed}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
