"""浏览器实测：P3 真实业务约束（三源）录入 / 校核 / Pareto 硬筛选。

用法：PYTHONPATH=src .venv/Scripts/python.exe tools/verify_constraints_gui.py
前置：8848 服务已启动并加载新代码。
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tmp_rr"
OUT.mkdir(exist_ok=True)


def main() -> None:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
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

        results: dict[str, object] = {}
        form = page.locator("#sf-form")

        # 1. 约束录入组渲染（details 存在 + 13 个字段 + 四源小标题）
        cgroup = form.locator(".sf-constraints")
        results["cgroup_exists"] = cgroup.count() == 1
        # 展开约束 details 使内部控件可见可点
        if cgroup.count() == 1:
            cgroup.evaluate("(el) => el.setAttribute('open', '')")
            page.wait_for_timeout(300)
        cfields = form.locator('[data-group="constraints"]')
        results["field_count"] = cfields.count()
        # 四源标题位于 details 折叠体内，inner_text 只返回可见文本会假阴性；
        # 改用 text_content 抓完整 DOM 文本，与展开状态无关。
        cgroup_text = cgroup.text_content() if cgroup.count() else ""
        results["has_four_sources"] = all(
            s in cgroup_text
            for s in ("资源现实源", "指挥权威源", "战损物理源", "软约束"))
        results["has_hard_soft_badges"] = (form.locator(".sf-hard").count() > 0 and
                                           form.locator(".sf-soft").count() > 0)
        results["has_order_textarea"] = form.locator(
            '[data-group="constraints"][data-field="superior_order"]').count() == 1

        # 2. 校核按钮点击 → 结果表渲染
        ccheck = page.locator("#sf-ccheck")
        results["ccheck_btn"] = ccheck.count() == 1
        if ccheck.count() == 1:
            ccheck.click()
            page.wait_for_timeout(900)
            box = page.locator("#sf-ccheck-result")
            results["ccheck_result_shown"] = box.count() == 1 and not box.is_hidden()
            txt = box.inner_text() if box.count() else ""
            results["ccheck_has_table"] = "约束" in txt and ("状态" in txt or "说明" in txt)
            results["ccheck_has_summary"] = ("硬约束不满足" in txt)

        # 3. 保存录入（带约束）→ 后端接受，mission 含 constraints_configured
        #    先确保抢修车数不超过默认上限（8），当前默认 6 合法
        page.locator("#sf-save").click()
        page.wait_for_timeout(900)
        hint = page.locator("#sf-job-hint").inner_text()
        results["mission_saved"] = ("录入已保存" in hint or "已读取" in hint)

        # 4. hard-block 前端验证：把抢修车改成 20（>上限8）→ 保存应失败并 alert
        repair_input = form.locator('[data-group="support_vehicles"][data-key="repair_veh"][data-field="count"]')
        if repair_input.count() == 1:
            repair_input.fill("20")
            # 监听 alert
            dialogs: list[str] = []
            page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
            page.locator("#sf-save").click()
            page.wait_for_timeout(900)
            results["hard_block_alert"] = any("硬约束" in d or "超过上级" in d for d in dialogs)
            repair_input.fill("6")  # 还原
            page.wait_for_timeout(200)

        # 5. Pareto 选优模态：约束筛选说明出现（若已显式录入约束）
        pick = page.locator("#sf-pareto-pick")
        if pick.count() == 1:
            # 先重新保存一次合法录入
            page.locator("#sf-save").click()
            page.wait_for_timeout(700)
            pick.click()
            page.wait_for_timeout(1300)
            body = page.locator("#sf-modal-pareto-body")
            ptxt = body.inner_text()
            results["pareto_modal"] = "反推最优保障参数" in ptxt
            results["pareto_constraint_note"] = ("GUI 真实约束筛选" in ptxt or
                                                  "资源硬筛选" in ptxt or
                                                  "已按 GUI" in ptxt or
                                                  "constraint" in ptxt.lower() or
                                                  "剔除超编方案" in ptxt)

        # 结果汇总
        for k, v in results.items():
            print(f"{k}: {v}")
        passed = sum(1 for v in results.values() if v is True)
        print(f"\nPASS {passed}/{len(results)}")
        if errors:
            print("BROWSER ERRORS:")
            for e in errors:
                print("  ", e)
        else:
            print("No browser console/page errors.")
        browser.close()

        # 关键项必须全部通过
        critical = ["cgroup_exists", "ccheck_btn", "ccheck_result_shown",
                    "mission_saved", "hard_block_alert", "pareto_modal"]
        failed = [k for k in critical if results.get(k) is not True]
        if results.get("field_count") != 13:
            failed.append("field_count!=13")
        if failed:
            raise SystemExit(f"CRITICAL FAIL: {failed}")


if __name__ == "__main__":
    main()
