"""浏览器实测：P2 扩展批次（批量工况对比/方案diff/签批流/成本权重/数据字典/部署示意/回退重算）。

用法：PYTHONPATH=src python tools/verify_support_p2.py
前置：8848 服务已启动并加载 P2 代码。
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
        # P2 与 3D 无关：拦截 rerun viewer/外链，避免 WebGL 内存拖垮无头浏览器
        page.route("**/rerun-viewer/**", lambda route: route.abort())
        page.route("**/proxy/**", lambda route: route.abort())
        page.route("**/app.rerun.io/**", lambda route: route.abort())
        page.route("**/telemetry.rerun.io/**", lambda route: route.abort())
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "CSP" not in m.text and "rerun" not in m.text.lower()
                else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto("http://127.0.0.1:8848/index.html", wait_until="domcontentloaded", timeout=30000)
        page.locator("#btn-support-flow").wait_for(state="attached", timeout=20000)
        page.wait_for_timeout(600)

        # P2-4 数据字典（顶栏入口，抽屉未开时先测）
        page.locator("#btn-data-dict").click()
        page.wait_for_timeout(500)
        dd = page.locator("#sf-modal-dict-body")
        results = {}
        results["dict_visible_topnav"] = not dd.is_hidden()
        results["dict_has_fields"] = "战损清单字段" in dd.inner_text()
        results["dict_has_slots"] = "方案槽位字典" in dd.inner_text()
        page.locator('[data-modal="sf-modal-dict"]').click()
        page.wait_for_timeout(200)
        # openDataDict 已通过 open() 打开抽屉（顶栏入口）；等待 boot 完成
        page.wait_for_timeout(700)

        # P2-5 成本权重输入框
        cw = page.locator('#sf-form [data-group="cost_weights"]')
        results["cost_weight_inputs"] = cw.count()
        results["budget_input"] = page.locator('#sf-form [data-field="budget_limit"]').count()

        # P2-7 环节回退重算条
        rc = page.locator("[data-recompute]")
        results["recompute_buttons"] = rc.count()

        # P2-1 批量工况对比（档案模态内入口）
        page.locator("#sf-archive").click()
        page.wait_for_timeout(400)
        page.locator("#sf-arch-compare").click()
        page.wait_for_timeout(600)
        body = page.locator("#sf-modal-compare-body")
        results["compare_visible"] = not body.is_hidden()
        results["compare_rows"] = page.locator(".sf-compare-table tbody tr").count()
        results["compare_has_note"] = "归档" in body.inner_text()
        page.locator('[data-modal="sf-modal-archive"]').click()
        page.wait_for_timeout(200)

        # P2-4 数据字典（抽屉内入口，验证抽屉打开时也可用）
        page.locator("#sf-data-dict").click()
        page.wait_for_timeout(400)
        dd = page.locator("#sf-modal-dict-body")
        results["dict_visible_in_drawer"] = not dd.is_hidden()
        results["dict_subtitles"] = page.locator(".sf-p2-sub").count()
        page.locator('[data-modal="sf-modal-dict"]').click()
        page.wait_for_timeout(200)

        # P2-2 方案版本对比（需要 >=2 份方案）
        page.locator("#sf-plan-compare").click()
        page.wait_for_timeout(600)
        pc = page.locator("#sf-modal-plancompare-body")
        results["plan_compare_visible"] = not pc.is_hidden()
        cb = page.locator(".sf-pc-cb")
        results["plan_compare_selectable"] = cb.count()
        if cb.count() >= 2:
            page.evaluate("""() => {
              var c = document.querySelectorAll('.sf-pc-cb');
              if (c[0]) c[0].checked = true;
              if (c[1]) c[1].checked = true;
            }""")
            page.locator("#sf-pc-run").evaluate("el => el.click()")
            page.wait_for_timeout(700)
            results["plan_compare_overview"] = "方案概览" in pc.inner_text()
            results["plan_compare_slot_diff"] = "数值槽位差异" in pc.inner_text()
        page.locator('[data-modal="sf-modal-plancompare"]').click()
        page.wait_for_timeout(200)

        # 生成一份方案（不推演，复用已有清单；禁用 LLM 走降级模板加速）
        page.locator("#sf-use-llm").uncheck()
        page.locator("#sf-gen-plan").evaluate("el => el.click()")
        page.wait_for_timeout(900)

        # P2-6 部署示意图（有方案时读真实槽位）
        page.locator("#sf-deploy-view").click()
        page.wait_for_timeout(400)
        dv = page.locator("#sf-modal-deploy-body")
        results["deploy_visible"] = not dv.is_hidden()
        results["deploy_svg"] = page.locator(".sf-deploy-svg").count()
        results["deploy_nodes"] = page.locator(".sf-deploy-svg rect").count()
        page.locator('[data-modal="sf-modal-deploy"]').click()
        page.wait_for_timeout(200)

        # P2-3 签批状态条（有方案时显示）
        rv = page.locator("#sf-review-bar")
        results["review_bar_visible"] = rv.count() > 0 and not rv.is_hidden()
        if results["review_bar_visible"]:
            results["review_state"] = rv.inner_text().strip()
            # P2-3 实测流转：草案 → 提交复核 → 复核中
            submit = page.locator('.sf-rv-btn[data-act="submit"]')
            if submit.count():
                submit.evaluate("el => el.click()")
                page.wait_for_timeout(600)
                results["review_after_submit"] = page.locator(".sf-rv-state").inner_text().strip()

        # P2-7 回退重算：重跑复核（不重跑仿真，秒级）
        if results["review_bar_visible"]:
            rr = page.locator('[data-recompute="review"]')
            page.on("dialog", lambda d: d.accept())
            rr.click()
            page.wait_for_timeout(800)
            results["recompute_review_clicked"] = True

        page.screenshot(path=str(OUT / "p2_final.png"), full_page=False)

        print("=== P2 浏览器实测 ===")
        for k, v in results.items():
            print(f"{k}: {v}")
        real_errors = [e for e in errors if "GitHub" not in e and "telemetry" not in e]
        print(f"console_errors(filtered): {len(real_errors)}")
        for e in real_errors[:5]:
            print("  ", e[:160])
        browser.close()


if __name__ == "__main__":
    main()
