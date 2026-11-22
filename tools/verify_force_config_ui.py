"""浏览器实测：对战双方实力 GUI 录入 → 注入推演 → 方案敌我对比。

用法：
  PYTHONPATH=src python tools/verify_force_config_ui.py

自动完成：打开抽屉 → 红蓝 tab 切换 → 载入想定默认 → 修改双方数量与乘子 →
保存 → 2 次推演（注入验证）→ 生成方案 → 检查敌我兵力槽位 → 截图。
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

        # 1) 双方实力 UI 存在性
        print("side tabs:", page.locator(".sf-side-tab").count())
        print("ground fields red:", page.locator('.sf-side-pane[data-pane="red"] [data-cat="equipment"]').count())
        print("ground fields blue:", page.locator('.sf-side-pane[data-pane="blue"] [data-cat="equipment"]').count())
        print("air fields blue:", page.locator('.sf-side-pane[data-pane="blue"] [data-cat="air"]').count())
        print("modifier fields:", page.locator('[data-group="forces"][data-cat="modifiers"]').count())

        # 2) 默认未启用 = 零漂移
        print("badge initial:", page.locator("#sf-forces-badge").inner_text())

        # 3) 切到完整编成想定 → 载入想定默认
        page.locator('#sf-form [data-field="base_scenario"]').select_option("example_brigade")
        page.locator("#sf-forces-load").click()
        page.wait_for_timeout(700)
        print("badge after load:", page.locator("#sf-forces-badge").inner_text())
        print("summary (example_brigade):", page.locator("#sf-forces-summary").inner_text())

        # 3b) 专属平台类型想定（medium_brigade 的 wheel_*）也能载入
        page.locator('#sf-form [data-field="base_scenario"]').select_option("medium_brigade")
        page.locator("#sf-forces-load").click()
        page.wait_for_timeout(700)
        print("summary (medium_brigade):", page.locator("#sf-forces-summary").inner_text())
        extra = page.locator('.sf-side-pane[data-pane="red"] [data-cat="equipment"][data-key="wheel_assault"]')
        print("wheel_assault field:", extra.count(), "value:", extra.input_value() if extra.count() else "N/A")
        # 回到 example_brigade 做后续推演验证
        page.locator('#sf-form [data-field="base_scenario"]').select_option("example_brigade")
        page.locator("#sf-forces-load").click()
        page.wait_for_timeout(600)

        # 4) 切到蓝方 tab，确认面板切换
        page.locator('.sf-side-tab[data-side="blue"]').click()
        page.wait_for_timeout(200)
        print("blue pane active:", "active" in (page.locator('.sf-side-pane[data-pane="blue"]').get_attribute("class") or ""))
        # 修改蓝方 atgm_team 数量与 pk 乘子
        page.locator('[data-group="forces"][data-side="blue"][data-cat="equipment"][data-key="atgm_team"]').fill("5")
        page.locator('[data-group="forces"][data-side="blue"][data-cat="modifiers"][data-key="pk_scale"]').fill("1.35")
        # 切回红方，修改坦克数
        page.locator('.sf-side-tab[data-side="red"]').click()
        page.wait_for_timeout(150)
        page.locator('[data-group="forces"][data-side="red"][data-cat="equipment"][data-key="tank"]').fill("10")
        print("summary edited:", page.locator("#sf-forces-summary").inner_text())
        page.screenshot(path=str(OUT / "force_config_input.png"))

        # 5) 保存 + 快捷推演
        page.locator('#sf-form [data-field="duration_ticks"]').fill("14")
        page.locator("#sf-nruns").fill("2")
        page.locator("#sf-use-llm").uncheck()
        page.locator("#sf-save").click()
        page.wait_for_timeout(800)
        print("save hint:", page.locator("#sf-job-hint").inner_text())

        page.locator("#sf-deduce").click()
        for _ in range(90):
            page.wait_for_timeout(1000)
            if "清单已就绪" in page.locator("#sf-plan-hint").inner_text():
                break
        print("loss hint:", page.locator("#sf-plan-hint").inner_text())

        # 6) 生成方案，检查敌我对比槽位
        page.locator("#sf-gen-plan").click()
        for _ in range(40):
            page.wait_for_timeout(500)
            if "方案 PQ-" in page.locator("#sf-plan-hint").inner_text():
                break
        check = page.locator("#sf-check").inner_text()
        print("check:", check[:120])
        plan_text = page.locator("#sf-plan-doc").inner_text()
        print("has enemy strength:", "敌方兵力编成" in plan_text)
        print("has force balance:", "红蓝兵力对比" in plan_text)
        # 抓取敌我对比那一行
        for line in plan_text.split("\n"):
            if "敌方兵力编成" in line or "红蓝兵力对比" in line or "地面装备" in line:
                print("  >>", line.strip()[:110])
        page.screenshot(path=str(OUT / "force_config_plan.png"))
        print("errors:", errors[:8] if errors else "none")
        browser.close()


if __name__ == "__main__":
    main()
