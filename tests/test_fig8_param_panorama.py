# -*- coding: utf-8 -*-
"""全图要素与图8参数全景 GUI 回归：两个新标签 + 9/9/10 参数 + 20 图索引 + Sobol 8 因子。"""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DASH_URL", "http://127.0.0.1:8861")
SHOT = Path("report/_gui_test_shots")
SHOT.mkdir(parents=True, exist_ok=True)

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

        page.click("#btn-research")
        page.wait_for_timeout(1000)
        body = page.locator("#research-body")
        log("面板打开", page.locator("#research-modal").is_visible())

        # 1. 两个新标签存在
        log("★图8参数全景 标签存在", page.locator('.rtab[data-rtab="fig8"]').count() == 1)
        log("全图要素索引 标签存在", page.locator('.rtab[data-rtab="figmap"]').count() == 1)

        # 2. 图8参数全景
        page.locator('.rtab[data-rtab="fig8"]').click()
        page.wait_for_timeout(500)
        txt = body.inner_text()
        log("图8页标题", "图8" in txt and "参数全景" in txt)
        log("一阶9维齐", all(k in txt for k in ["抢修组数", "抢救车数", "补给车数", "携行基数", "运行基数", "波次间隔", "每波件数", "后送比例", "抢修优先规则档"]))
        log("一阶真实可调7项", txt.count("真实可调") >= 7, f"count={txt.count('真实可调')}")
        log("二阶9维齐", all(k in txt for k in ["修理所位置与数量", "拖救范围上限", "专业配比", "修理级别分派比例", "响应时限", "重点装备清单", "战损分级处置规则", "抢修组轮换策略", "备件补充方式"]))
        log("全集十类齐", all(k in txt for k in ["任务与想定", "战损输入", "维修任务", "保障资源", "力量部署", "流程与时序", "保障策略", "优先级与权衡", "约束集", "不确定性与鲁棒性"]))
        log("内核固定标注存在", "内核固定" in txt)
        log("方法层标注存在", "方法层" in txt)
        log("三源约束ABCD", all(k in txt for k in ["A 损伤源", "B 权威源", "C 资源源", "D 逻辑随机源"]))
        page.screenshot(path=str(SHOT / "fig8_panorama.png"))

        # 3. 全图要素索引
        page.locator('.rtab[data-rtab="figmap"]').click()
        page.wait_for_timeout(500)
        txt2 = body.inner_text()
        log("索引页标题", "20 张图" in txt2 or "20张图" in txt2)
        # 20 张图编号全在
        missing = [n for n in range(1, 21) if f"图{n} " not in txt2]
        log("20张图编号齐", not missing, f"missing={missing}")
        log("图18八因子", all(k in txt2 for k in ["气象能见度", "红方命中毁伤", "蓝方机动速度", "装备可靠性 MTBF"]))
        log("能见度ST=0.758", "0.758" in txt2)
        page.screenshot(path=str(SHOT / "figmap_index.png"))

        # 4. 翻页顺序包含新页（上一节从 figmap 回到 fig8）
        page.click("#research-prev")
        page.wait_for_timeout(300)
        active = page.evaluate("document.querySelector('.rtab.active')?.dataset.rtab")
        log("翻页回到fig8", active == "fig8", f"active={active}")

        log("无JS运行时错误", len(errs) == 0, "; ".join(errs[:3]))
        browser.close()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n结果: {passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
