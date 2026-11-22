"""浏览器实测：P1 扩展批次（seed/LLM参数/对账明细/口径tooltip/定位/覆写/模板映射/档案）。

用法：PYTHONPATH=src python tools/verify_support_p1.py
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

        # P1-6 seed 输入 + 短推演
        page.locator('#sf-form [data-field="duration_ticks"]').fill("14")
        page.locator("#sf-nruns").fill("3")
        page.locator("#sf-use-llm").uncheck()
        page.locator("#sf-seed").fill("20261122")
        page.locator("#sf-save").click()
        page.wait_for_timeout(700)
        page.locator("#sf-deduce").click()
        for _ in range(100):
            page.wait_for_timeout(1000)
            if "清单已就绪" in page.locator("#sf-plan-hint").inner_text():
                break
        print("loss ready:", "清单已就绪" in page.locator("#sf-plan-hint").inner_text())
        print("seed backfilled:", page.locator("#sf-seed").input_value())

        # P1-5 对账明细展开
        rt = page.locator("#sf-recon-toggle")
        print("recon toggle present:", rt.count())
        if rt.count():
            rt.click()
            page.wait_for_timeout(200)
            print("recon detail rows:", page.locator("#sf-recon-detail tbody tr").count())

        # P1-7 指标口径 tooltip
        q = page.locator(".sf-gloss-q")
        print("glossary ? buttons:", q.count())
        if q.count():
            q.first.click()
            page.wait_for_timeout(150)
            print("gloss popup visible:", not page.locator("#sf-prov-pop").is_hidden())
            print("gloss has formula:", "公式" in page.locator("#sf-prov-pop").inner_text())

        # P1-4 清单定位
        loc = page.locator(".sf-locate")
        print("locate buttons:", loc.count())
        if loc.count():
            loc.first.click()
            page.wait_for_timeout(150)
            print("focus card visible:", not page.locator("#sf-focus-card").is_hidden())
            print("focus has entity path:", "world/units/" in page.locator("#sf-focus-card").inner_text())
            page.locator(".sf-focus-close").click()

        # P1-3 LLM 高级参数
        page.locator("#sf-adv-toggle").click()
        page.wait_for_timeout(150)
        print("LLM param inputs:", page.locator(".sf-adv-grid input").count())
        page.locator('#sf-llmp-temperature').fill("0.3")
        page.locator('#sf-llmp-group_size').fill("2")

        # 生成方案（关闭 LLM，快速）
        page.locator("#sf-gen-plan").click()
        for _ in range(40):
            page.wait_for_timeout(500)
            if "方案 PQ" in page.locator("#sf-plan-hint").inner_text():
                break
        print("plan hint:", page.locator("#sf-plan-hint").inner_text())

        # P1-2 叙述覆写
        page.locator("#sf-edit-narrative").click()
        page.wait_for_timeout(200)
        print("narr editor visible:", not page.locator("#sf-narr-editor").is_hidden())
        narr_boxes = page.locator("#sf-narr-editor textarea")
        print("narr slots:", narr_boxes.count())
        if narr_boxes.count():
            narr_boxes.first.fill("敌以空地一体火力实施联合突击，重点压制我机动与保障节点")
            page.locator("#sf-narr-save").click()
            for _ in range(30):
                page.wait_for_timeout(400)
                if "人工覆写锁定" in page.locator("#sf-check").inner_text():
                    break
            print("override locked:", "人工覆写锁定" in page.locator("#sf-check").inner_text())

        # P1-1 模板槽位映射
        page.locator("#sf-template-map").click()
        page.wait_for_timeout(1200)
        print("template modal visible:", not page.locator("#sf-modal-template").is_hidden())
        print("slot map rows:", page.locator("#sf-modal-template-body tbody tr").count())
        page.locator("#sf-modal-template .sf-modal-x").click()

        # P1-8 作业档案
        page.locator("#sf-archive").click()
        page.wait_for_timeout(500)
        print("archive modal visible:", not page.locator("#sf-modal-archive").is_hidden())
        print("archive sections:", page.locator("#sf-modal-archive .sf-arch-sec").count())
        print("archive tables:", page.locator("#sf-modal-archive table").count())
        page.locator("#sf-modal-archive .sf-modal-x").click()

        page.screenshot(path=str(OUT / "p1_final.png"), full_page=True)

        real_errors = [e for e in errors if "CSP" not in e and "frame" not in e.lower()]
        print("errors (non-CSP):", real_errors[:5])
        browser.close()


if __name__ == "__main__":
    main()
