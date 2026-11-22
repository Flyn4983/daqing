"""浏览器实测：成本约束 Pareto 选优（权重评分/成本上限/锚点对照/一键采用附节）。

用法：PYTHONPATH=src python tools/verify_support_pareto.py
前置：8848 服务已启动并加载 pareto 端点。
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
        # 与 3D 无关：拦截 rerun viewer/外链，避免 WebGL 拖垮无头浏览器
        for pat in ("**/rerun-viewer/**", "**/proxy/**", "**/app.rerun.io/**", "**/telemetry.rerun.io/**"):
            page.route(pat, lambda route: route.abort())
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "CSP" not in m.text and "rerun" not in m.text.lower() else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto("http://127.0.0.1:8848/index.html", wait_until="domcontentloaded", timeout=30000)
        page.locator("#btn-support-flow").wait_for(state="attached", timeout=20000)
        page.wait_for_timeout(500)
        page.locator("#btn-support-flow").click()
        page.wait_for_timeout(800)

        results: dict[str, object] = {}

        # 1. 成本选优按钮存在
        pick = page.locator("#sf-pareto-pick")
        results["pick_btn_visible"] = pick.count() == 1 and pick.is_visible()

        # 2. 打开选优模态（等 Top 表格渲染）
        pick.click()
        page.wait_for_timeout(1200)
        modal = page.locator("#sf-modal-pareto")
        body = page.locator("#sf-modal-pareto-body")
        results["modal_visible"] = not modal.is_hidden()
        txt = body.inner_text()
        results["has_lead"] = "反推最优保障参数" in txt
        results["has_formula"] = "score" in txt
        results["has_caveat"] = "量纲不同" in txt
        results["has_anchors"] = "折中推荐" in txt or "现行基线" in txt
        rows = body.locator("table.sf-pareto-table tbody tr")
        results["top_rows"] = rows.count()
        results["has_adopt_btn"] = body.locator(".sf-pareto-adopt").count() > 0
        results["best_highlighted"] = body.locator("tr.sf-pareto-best").count() == 1

        # 3. 成本上限硬约束：输入 0.15 → 应用 → 过滤数增加、首行成本≤0.15
        before_n = txt.split("已按上限过滤 ")[1].split(" 个")[0] if "已按上限过滤 " in txt else "?"
        ce = page.locator("#sf-pareto-ceiling")
        results["ceiling_input_exists"] = ce.count() == 1
        if ce.count() == 1:
            ce.fill("0.15")
            page.locator("#sf-pareto-apply-ce").click()
            page.wait_for_timeout(1200)
            txt2 = body.inner_text()
            results["ceiling_applied"] = ("已按上限过滤 " in txt2)
            after_n = txt2.split("已按上限过滤 ")[1].split(" 个")[0] if "已按上限过滤 " in txt2 else "?"
            results["filtered_increased"] = (after_n != before_n)
            # 首行成本列（第5列）数值应≤0.15
            first_cost = rows.first.locator("td").nth(4).inner_text()
            try:
                results["ceiling_respected"] = float(first_cost) <= 0.1501
            except ValueError:
                results["ceiling_respected"] = False

        # 4. 一键采用：点首行"采用" → 模态关闭 → 附节渲染
        body.locator(".sf-pareto-adopt").first.click()
        page.wait_for_timeout(1600)
        results["modal_closed_after_adopt"] = modal.is_hidden()
        note = page.locator("#sf-pareto-note")
        results["note_visible"] = not note.is_hidden()
        note_txt = note.inner_text() if note.count() else ""
        results["note_has_params"] = ("前沿抢修组" in note_txt)
        results["note_has_score"] = ("评分" in note_txt)
        results["plan_hint_updated"] = "PQ-" in (page.locator("#sf-plan-hint").inner_text() or "")

        page.screenshot(path=str(OUT / "pareto_final.png"), full_page=False)

        print("=== PARETO UI RESULTS ===")
        for k, v in results.items():
            print(f"  {k}: {v}")
        print(f"  console_errors: {len(errors)}")
        for e in errors[:6]:
            print("    !", e)

        ok = all(bool(v) for v in results.values()) and not errors
        print("VERDICT:", "PASS" if ok else "FAIL")
        browser.close()
        if not ok:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
