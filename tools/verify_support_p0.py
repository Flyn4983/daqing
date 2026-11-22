"""浏览器实测：P0 扩展批次（口径切换/筛选/汇总/CI/时间线/溯源/docx）。

用法：PYTHONPATH=src python tools/verify_support_p0.py
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
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto("http://127.0.0.1:8848/index.html", wait_until="domcontentloaded", timeout=30000)
        page.locator("#btn-support-flow").wait_for(state="attached", timeout=20000)
        page.wait_for_timeout(600)
        page.locator("#btn-support-flow").click()
        page.wait_for_timeout(600)

        # 阈值录入
        print("target inputs:", page.locator('#sf-form [data-group="targets"]').count())
        page.locator('#sf-form [data-field="duration_ticks"]').fill("14")
        page.locator('#sf-form [data-field="readiness"]').fill("93")
        page.locator('#sf-form [data-field="success"]').fill("88")
        page.locator('#sf-nruns').fill("5")
        page.locator("#sf-use-llm").uncheck()
        page.locator("#sf-save").click()
        page.wait_for_timeout(800)

        page.locator("#sf-deduce").click()
        for _ in range(100):
            page.wait_for_timeout(1000)
            if "清单已就绪" in page.locator("#sf-plan-hint").inner_text():
                break
        print("loss ready:", "清单已就绪" in page.locator("#sf-plan-hint").inner_text())

        # 口径切换
        print("doctrine btns:", page.locator(".sf-doctrine .dbtn").count())
        print("metric A:", page.locator(".sf-metric .v").first.inner_text())
        page.locator('.dbtn[data-doctrine="B"]').click()
        page.wait_for_timeout(200)
        print("metric B:", page.locator(".sf-metric .v").first.inner_text())
        page.locator('.dbtn[data-doctrine="A"]').click()

        # 子 tab：逐件清单筛选
        print("tabs:", page.locator(".sf-subtabs .sftab").count())
        print("filter selects:", page.locator("#sf-filter-bar select").count())
        rows_all = page.locator("#sf-loss-table tbody tr").count()
        page.locator('#sf-filter-bar select[data-filter="grade"]').select_option("K")
        page.wait_for_timeout(200)
        rows_k = page.locator("#sf-loss-table tbody tr").count()
        print(f"filter rows: all={rows_all} K={rows_k}")
        page.locator("#sf-filter-bar .sf-filter-clear").click()
        # 排序
        page.locator('#sf-loss-table th[data-sort="grade"]').click()
        page.wait_for_timeout(150)
        print("sort ok:", page.locator('#sf-loss-table th[data-sort="grade"]').inner_text())
        page.screenshot(path=str(OUT / "p0_items.png"))

        # 多维汇总
        page.locator('.sftab[data-tab="summary"]').click()
        page.wait_for_timeout(200)
        print("summary charts:", page.locator(".sf-chart-block").count())
        page.screenshot(path=str(OUT / "p0_summary.png"))

        # 复制明细/CI
        page.locator('.sftab[data-tab="ci"]').click()
        page.wait_for_timeout(200)
        print("ci cards:", page.locator(".sf-ci-card").count(),
              "ci rows:", page.locator(".sf-ci-table tbody tr").count())
        page.screenshot(path=str(OUT / "p0_ci.png"))

        # 环节时间线
        page.locator('.sftab[data-tab="timeline"]').click()
        page.wait_for_timeout(200)
        print("timeline stages:", page.locator(".sf-tl-stage").count(),
              "tracks:", page.locator(".sf-tl-track").count())
        page.screenshot(path=str(OUT / "p0_timeline.png"))

        # 方案 + 溯源 + docx
        page.locator("#sf-gen-plan").click()
        for _ in range(60):
            page.wait_for_timeout(500)
            if "方案 PQ-" in page.locator("#sf-plan-hint").inner_text():
                break
        print("check:", page.locator("#sf-check").inner_text()[:50])
        print("prov numbers:", page.locator(".sf-prov-num").count())
        if page.locator(".sf-prov-num").count():
            page.locator(".sf-prov-num").first.click()
            page.wait_for_timeout(150)
            print("prov pop:", page.locator("#sf-prov-pop").inner_text()[:60].replace("\n", " "))

        with page.expect_download(timeout=15000) as dl_info:
            page.locator("#sf-export-docx").click()
        dl = dl_info.value
        print("docx download:", dl.suggested_filename)
        page.screenshot(path=str(OUT / "p0_plan.png"))

        print("errors:", errors[:10] if errors else "none")
        browser.close()


if __name__ == "__main__":
    main()
