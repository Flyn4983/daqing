# -*- coding: utf-8 -*-
"""GUI 按钮交互自动化测试（用户硬约束：每个按钮有正确动作、返回按钮逐一测试）。

用真实服务 http://127.0.0.1:8849 + Playwright(Edge/Chromium headless)。
运行前需先启动：python -m brigsim.cli agent-serve --port 8849 --no-replay
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

BASE = os.environ.get("DASH_URL", "http://127.0.0.1:8849")
SHOT = Path(__file__).resolve().parents[1] / "report" / "_gui_test_shots"
SHOT.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        # 优先 msedge（本机已验证可启动），回退内置 chromium
        try:
            b = p.chromium.launch(headless=True, channel="msedge")
        except Exception:
            b = p.chromium.launch(headless=True)
        yield b
        b.close()


def _visible(page, sel):
    try:
        return page.locator(sel).is_visible()
    except Exception:
        return False


def test_01_login_flow(browser):
    """登录：错误密码报错；正确密码进模式选择；已登录直达。"""
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{BASE}/login.html", wait_until="domcontentloaded")
    page.evaluate("sessionStorage.clear()")
    page.reload(wait_until="domcontentloaded")

    assert _visible(page, "#login-form")
    # 错误密码
    page.fill("#username", "admin")
    page.fill("#password", "wrong")
    page.click("#btn-login")
    page.wait_for_timeout(600)
    assert "错误" in page.locator("#error-msg").inner_text() or page.locator("#error-msg").is_visible()
    assert "/login" in page.url  # 未跳转

    # 正确密码
    page.fill("#password", "123456")
    page.click("#btn-login")
    page.wait_for_url("**/mode_select.html*", timeout=5000)
    assert "mode_select" in page.url
    page.screenshot(path=str(SHOT / "01_mode_select.png"))

    # 已登录直达登录页 → 自动跳走
    page.goto(f"{BASE}/login.html", wait_until="domcontentloaded")
    page.wait_for_timeout(400)
    assert "mode_select" in page.url
    page.close()


def test_02_mode_select_navigation(browser):
    """模式选择：live/replay 卡片跳转正确；退出登录返回 login。"""
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{BASE}/mode_select.html", wait_until="domcontentloaded")
    if "login" in page.url:
        page.evaluate("sessionStorage.setItem('user', JSON.stringify({username:'admin',role:'admin'}))")
        page.reload(wait_until="domcontentloaded")

    # live 卡片
    page.locator('a.mode-card[href*="mode=live"]').click()
    page.wait_for_timeout(500)
    assert "mode=live" in page.url or "index.html" in page.url

    # 返回 mode_select，测 replay 卡片
    page.goto(f"{BASE}/mode_select.html", wait_until="domcontentloaded")
    page.locator('a.mode-card[href*="mode=replay"]').click()
    page.wait_for_timeout(500)
    assert "mode=replay" in page.url

    # 退出登录按钮 → 回到 login
    page.goto(f"{BASE}/mode_select.html", wait_until="domcontentloaded")
    page.click("#btn-logout")
    page.wait_for_url("**/login.html*", timeout=5000)
    assert "login" in page.url
    page.close()


def test_03_dashboard_core_buttons(browser):
    """大屏核心按钮：功能对照开关、智能参谋开/收、方案三按钮、发送。"""
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{BASE}/index.html?mode=live", wait_until="domcontentloaded")
    page.evaluate("sessionStorage.setItem('user', JSON.stringify({username:'admin',role:'admin'}))")
    page.wait_for_timeout(2500)

    # live 模式自动弹出配置窗——先验证其关闭按钮工作（返回类按钮验收）
    if _visible(page, "#config-modal"):
        page.screenshot(path=str(SHOT / "03_config_modal.png"))
        page.click("#config-close")
        page.wait_for_timeout(400)
        assert page.locator("#config-modal").get_attribute("hidden") is not None, "配置窗关闭失败"

    # 功能对照：打开 → 关闭
    if _visible(page, "#btn-func-map"):
        page.click("#btn-func-map")
        assert _visible(page, "#funcmap-modal")
        assert "战力再生" in page.content() or "F9" in page.content()
        page.screenshot(path=str(SHOT / "03_funcmap.png"))
        page.click("#funcmap-close")
        page.wait_for_timeout(300)
        assert not _visible(page, "#funcmap-modal") or page.locator("#funcmap-modal").get_attribute("hidden") is not None

    # 智能参谋：开 → 收
    page.click("#btn-open-agent")
    page.wait_for_timeout(400)
    assert "open" in (page.locator("#agent-drawer").get_attribute("class") or "")
    page.click("#btn-close-agent")
    page.wait_for_timeout(400)
    assert "open" not in (page.locator("#agent-drawer").get_attribute("class") or "")

    # 方案三按钮（重开抽屉）
    page.click("#btn-open-agent")
    page.wait_for_timeout(300)
    page.locator('.plan-btns button[data-tag="knee"]').click()
    page.wait_for_timeout(1500)
    plan_text = page.locator("#msgs").inner_text()
    assert "保障力量" in plan_text or "方案" in plan_text
    assert "模板" in plan_text or "大模型" in plan_text  # 来源角标存在
    page.screenshot(path=str(SHOT / "03_plan_generated.png"))

    # 发送按钮
    page.fill("#input", "四率是什么口径")
    page.click("#send")
    page.wait_for_timeout(1200)
    assert "完好率" in page.locator("#msgs").inner_text() or "口径" in page.locator("#msgs").inner_text()
    page.close()


def test_04_eta_kpi(browser):
    """A1：η KPI 卡显示 0.488→0.491（用 pareto 真实数据计算）。"""
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{BASE}/index.html", wait_until="domcontentloaded")
    page.evaluate("sessionStorage.setItem('user', JSON.stringify({username:'admin',role:'admin'}))")
    page.wait_for_timeout(3000)
    eta = page.locator("#kpi-eta .kpi-v").inner_text()
    sub = page.locator("#kpi-eta .kpi-s").inner_text()
    # 系数应在 0.48~0.50 区间
    val = float(eta)
    assert 0.48 <= val <= 0.50, f"η={eta} 超出预期"
    assert "0.488" in sub and "0.491" in sub, f"副标题={sub}"
    page.screenshot(path=str(SHOT / "04_eta_kpi.png"))
    page.close()
