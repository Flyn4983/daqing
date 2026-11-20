# -*- coding: utf-8 -*-
"""正文与表格细节页 GUI 回归：新标签 + 背景三矛盾 + 四率表 + 8项指标 + 军事效益
+ 表4应用5环节 + 使用闭环 + 成果形式 + 推广升级；并验证翻页顺序与既有页无回归。
"""
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

        # 1. 新标签存在
        log("★正文与表格细节 标签存在", page.locator('.rtab[data-rtab="textable"]').count() == 1)

        # 2. 切到新页
        page.locator('.rtab[data-rtab="textable"]').click()
        page.wait_for_timeout(600)
        txt = body.inner_text()
        log("页标题", "正文与表格" in txt and "细节全量对照" in txt)

        # 3. 背景三矛盾 + 三问题 + 因果链
        log("因果链四节点", all(k in txt for k in ["保障方案", "保障过程", "装备四率", "战斗力再生"]))
        log("三问题算不清配不优说不清", all(k in txt for k in ["算不清", "配不优", "说不清"]))
        log("三对矛盾", all(k in txt for k in ["资源有限与需求不确定", "经验管理与精细化", "敢不敢用"]))

        # 4. 表1 四率完整口径
        log("四率定义齐", all(k in txt for k in ["完好率 R₀", "战损率 A", "修复率 Pᵣ", "故障率 F"]))
        log("四率统计口径", all(k in txt for k in ["可按装备价值加权", "含Ⅳ级报废与超能力不可修", "按修理级别与时限分档", "按类型除基准值后映射"]))
        log("四率ω角色", all(k in txt for k in ["ω₁", "ω₂", "ω₃", "ω₄"]))

        # 5. 8项指标（含此前缺失的端到端300倍/部署两档）
        log("8项指标表头", "立项目标" in txt and "实测达成" in txt)
        log("端到端≥300倍已补", "端到端" in txt and "300倍" in txt)
        log("部署两档已补", "96GB" in txt and "单机离线" in txt)

        # 6. 军事效益（此前缺失的6→2组/4→2台）
        log("资源压缩6→2组4→2台", "6组减至2组" in txt and "4台减至2台" in txt)
        log("三条机理量化", all(k in txt for k in ["加人有边际", "备件缺货会挂起", "Ⅳ级毁伤决定上限"]))

        # 7. 表4 应用5环节
        log("应用5环节齐", all(k in txt for k in ["方案拟制", "力量编组", "方案权衡", "结果解读", "可信度"]))
        log("应用前后对比列", all(k in txt for k in ["以往做法", "使用后", "效益"]))

        # 8. 使用闭环
        log("使用闭环三阶段", all(k in txt for k in ["战前", "战中", "战后"]))
        log("9维参数表+9固定条件", "9维参数表" in txt and "9项固定条件" in txt)

        # 9. 成果形式 + 推广升级
        log("成果形式5项", all(k in txt for k in ["决策支持软件1套", "系统设计方案1份", "算法模型库1套", "想定与实验数据集1份", "系统试用报告1份"]))
        log("推广升级", all(k in txt for k in ["弹药、油料、器材管理", "指挥信息系统", "仿真+实装双源"]))

        log("无 JS 报错", not errs, ";".join(errs[:2]))
        page.screenshot(path=str(SHOT / "textable_details.png"))

        # 10. 翻页顺序：textable 前一页 realdata，后一页 m1
        page.click("#research-prev")
        page.wait_for_timeout(250)
        prev = page.evaluate("document.querySelector('.rtab.active')?.dataset.rtab")
        log("前一页realdata", prev == "realdata", f"prev={prev}")
        page.click("#research-next")
        page.wait_for_timeout(250)
        page.click("#research-next")
        page.wait_for_timeout(250)
        nxt = page.evaluate("document.querySelector('.rtab.active')?.dataset.rtab")
        log("后一页m1", nxt == "m1", f"next={nxt}")

        browser.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
