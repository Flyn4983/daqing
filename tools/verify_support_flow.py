"""浏览器实测：作战保障全过程抽屉的 8 节点流程与三栏联动。

用法：
  PYTHONPATH=src python tools/verify_support_flow.py

自动完成：打开 → 保存录入 → 2 次推演 → 生成方案（关闭 LLM 加速）→ 截图。
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
        page.wait_for_timeout(1200)

        page.locator("#btn-support-flow").click()
        page.wait_for_timeout(800)
        print("drawer open:", "open" in (page.locator("#sf-drawer").get_attribute("class") or ""))
        print("nodes:", page.locator(".sf-node").count())
        print("form inputs:", page.locator("#sf-form input").count())
        print("templates:", page.locator("#sf-template option").count())

        # 缩短推演以加速验证
        page.locator('#sf-form [data-field="duration_ticks"]').fill("16")
        page.locator("#sf-nruns").fill("2")
        page.locator("#sf-use-llm").uncheck()
        page.locator("#sf-save").click()
        page.wait_for_timeout(900)
        print("after save hint:", page.locator("#sf-job-hint").inner_text())
        print("done nodes after save:", page.locator(".sf-node.done").count())

        page.locator("#sf-deduce").click()
        for _ in range(90):
            page.wait_for_timeout(1000)
            hint = page.locator("#sf-job-hint").inner_text()
            if "清单已就绪" in page.locator("#sf-plan-hint").inner_text():
                break
        print("loss hint:", page.locator("#sf-plan-hint").inner_text())
        print("metrics cards:", page.locator(".sf-metric").count())
        print("recon:", page.locator(".sf-recon").inner_text() if page.locator(".sf-recon").count() else "N/A")
        print("loss rows:", page.locator(".sf-table tbody tr").count())
        page.screenshot(path=str(OUT / "support_flow_loss.png"))

        page.locator("#sf-gen-plan").click()
        for _ in range(40):
            page.wait_for_timeout(500)
            if "方案 PQ-" in page.locator("#sf-plan-hint").inner_text():
                break
        print("plan hint:", page.locator("#sf-plan-hint").inner_text())
        print("check:", page.locator("#sf-check").inner_text())
        print("plan paragraphs:", page.locator("#sf-plan-doc p").count())
        page.screenshot(path=str(OUT / "support_flow_plan.png"))
        print("errors:", errors[:8] if errors else "none")
        browser.close()


if __name__ == "__main__":
    main()
