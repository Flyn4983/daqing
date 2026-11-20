# -*- coding: utf-8 -*-
"""S4 三问题修复回归测试：
1) 顶栏两行布局，7 张 KPI 卡互不重叠、不压右侧按钮；
2) live 模式四率指标按钮（完好/战损/修复/故障）真实切换曲线；
3) 断网（拦截一切外网请求）下实时推演可视化正常。
运行：DASH_URL=http://127.0.0.1:8848 python tests/test_gui_live_fixes.py
"""
import os
import json
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DASH_URL", "http://127.0.0.1:8848")
SHOT = os.path.join(os.path.dirname(__file__), "..", "report", "_gui_test_shots")
os.makedirs(SHOT, exist_ok=True)

PASS = FAIL = 0


def log(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")


def _wait_rates(page):
    for _ in range(30):
        if page.evaluate("!!(window.DASH && window.DASH.views && window.DASH.views.rates && window.DASH.views.rates.updateLive)"):
            return True
        page.wait_for_timeout(500)
    return False


def _open_live(page):
    page.goto(f"{BASE}/login.html", wait_until="domcontentloaded")
    page.evaluate("sessionStorage.setItem('user',JSON.stringify({username:'admin',role:'admin'}))")
    page.goto(f"{BASE}/index.html?mode=live", wait_until="domcontentloaded")
    _wait_rates(page)
    page.wait_for_timeout(1200)
    if page.locator("#config-close").count() and page.locator("#config-modal").is_visible():
        page.click("#config-close")
        page.wait_for_timeout(300)


def _fresh_run_log():
    req = urllib.request.Request(
        f"{BASE}/api/run",
        data=json.dumps({"scenario": "example_brigade", "seed": 42, "max_ticks": 40}).encode(),
        headers={"Content-Type": "application/json"},
    )
    rid = json.loads(urllib.request.urlopen(req, timeout=10).read())["run_id"]
    time.sleep(7)
    return json.loads(urllib.request.urlopen(f"{BASE}/api/run?run_id={rid}", timeout=10).read()).get("log", [])


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel="msedge")
        except Exception:
            browser = p.chromium.launch(headless=True)

        # 一个 context、一个 page 完成所有场景，避免多 page 资源竞争
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        # ---- 问题1：顶栏无重叠 ----
        _open_live(page)
        geo = page.evaluate("""() => {
          const o={};
          ['.topbar','.kpi-bar','.top-actions'].forEach(s=>{
            const r=document.querySelector(s).getBoundingClientRect();
            o[s]=[Math.round(r.top),Math.round(r.bottom),Math.round(r.left),Math.round(r.right)];
          });
          o.kpis=[...document.querySelectorAll('.kpis .kpi')].map(e=>{
            const r=e.getBoundingClientRect();return [e.id,Math.round(r.left),Math.round(r.right)];
          });
          return o;
        }""")
        tb, kb, ta = geo[".topbar"], geo[".kpi-bar"], geo[".top-actions"]
        log("顶栏拆两行", kb[0] >= tb[1] - 1, f"topbar底{tb[1]} kpi顶{kb[0]}")
        kps = geo["kpis"]
        overlap = any(kps[i][2] > kps[i + 1][1] for i in range(len(kps) - 1))
        log("KPI不与按钮区重叠", ta[0] < kb[0] or kps[0][1] <= ta[2],
            f"kpi顶{kb[0]} actions top{ta[0]}")
        log("7张KPI互不重叠", len(kps) == 7 and not overlap, f"n={len(kps)} overlap={overlap}")

        # ---- 问题2：四率按钮切换（复用同页）----
        log_data = _fresh_run_log()
        has_fields = bool(log_data) and all(
            k in log_data[0] for k in
            ["ready_red", "attr_red", "repair_red", "failure_red",
             "ready_blue", "attr_blue", "repair_blue", "failure_blue"])
        log("后端log含四率字段", has_fields, f"len={len(log_data)}")
        page.evaluate("(log)=>{window.DASH.views.rates.initEmpty();window.DASH.views.rates.updateLive(log);}", log_data)
        page.wait_for_timeout(400)
        expect = {
            "availability": "完好率", "attrition": "战损率",
            "repair_rate": "修复率", "failure_rate": "故障率",
        }
        for metric, label in expect.items():
            page.click(f"#ts-metric-toggle .seg[data-ts-metric={metric}]")
            page.wait_for_timeout(280)
            box = page.locator("#ts-svg").bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5)
            page.wait_for_timeout(160)
            tip = page.locator("#ts-tip").inner_text() if page.locator("#ts-tip").count() else ""
            log(f"按钮切换-{label}", label in tip, tip[:48].replace("\n", " "))
        log("无JS错误", not errs, str(errs[:2]))
        page.screenshot(path=os.path.join(SHOT, "fx_live_buttons.png"))
        ctx.close()

        # ---- 问题3：断网可视化（新 context，拦截外网）----
        ctx2 = browser.new_context(viewport={"width": 1920, "height": 1080})
        page2 = ctx2.new_page()
        blocked = []
        def handler(route):
            u = route.request.url
            if ("127.0.0.1" in u or "localhost" in u
                    or u.startswith("data:") or u.startswith("blob:")):
                route.continue_()
            else:
                blocked.append(u[:100])
                route.abort()
        page2.route("**/*", handler)
        _open_live(page2)
        canvas = 0
        for f in page2.frames:
            try:
                canvas += f.locator("canvas").count()
            except Exception:
                pass
        log("断网零外网请求", len(blocked) == 0, f"blocked={len(blocked)}")
        log("断网3D正常渲染", canvas >= 1, f"canvas={canvas}")
        page2.screenshot(path=os.path.join(SHOT, "fx_offline_live.png"))
        ctx2.close()

        browser.close()

    print(f"\n结果：{PASS}/{PASS + FAIL} 通过")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
