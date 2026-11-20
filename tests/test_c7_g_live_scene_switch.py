# -*- coding: utf-8 -*-
"""C7-g 回归：live 工况切换必须真正驱动 3D（静态 rrd 直载），启动推演切回 gRPC sink。

根因：log_file_from_path 把 replay_knee.rrd 导入为独立 recording，viewer 显示它；
preview 用 SDK rr.log 写当前 recording——static 地形对 viewer 不可见，切工况地形不变。
修复：live 切工况返回预构建 rrd → 前端 loadViewerFile 静态直载（replay 已验证可靠）；
启动推演时 ensureLiveSink() 切回 gRPC sink 看实时 tick。

运行：DASH_URL=http://127.0.0.1:8861 python tests/test_c7_g_live_scene_switch.py
"""
import os
import json
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DASH_URL", "http://127.0.0.1:8861")
PASS = FAIL = 0


def log(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def main():
    # ---- 后端：preview 返回静态直载描述 ----
    r = post("/api/scene-switch", {"scenario": "brigade_canyon_clear", "mode": "preview"})
    log("preview返回mode=static", r.get("mode") == "static" and r.get("ok") is True, str(r)[:100])
    log("preview返回正确rrd文件", r.get("file") == "replay_brigade_canyon_clear.rrd", r.get("file", ""))

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel="msedge")
        except Exception:
            browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        switches = []
        page.on("response", lambda resp: switches.append(resp)
                if "scene-switch" in resp.url else None)

        page.goto(BASE + "/login.html", wait_until="domcontentloaded")
        page.evaluate("sessionStorage.setItem('user',JSON.stringify({username:'admin',role:'admin'}))")
        page.goto(BASE + "/index.html?mode=live", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        if page.locator("#config-modal").count() and page.locator("#config-modal").is_visible():
            page.click("#config-close")
        page.wait_for_timeout(800)

        # ---- 前端：切工况 → 静态直载（iframe src 指向 /rerun-file/）----
        page.select_option("#scenario-select", "brigade_plain_fog")
        page.wait_for_timeout(3500)
        from urllib.parse import unquote
        src = unquote(page.evaluate("document.getElementById('replay-frame')?.src") or "")
        log("切工况iframe走静态rrd", "/rerun-file/" in src and "plain_fog" in src, src[-80:])
        log("静态直载标记置位", page.evaluate("window.__rerun_viewer_static === true"))
        title = page.locator("#replay-tag-title").text_content() if page.locator("#replay-tag-title").count() else ""
        log("3D标题跟随工况", "平原浓雾突击" in (title or ""), title or "")
        tag = page.locator("#baseline-tag").text_content() if page.locator("#baseline-tag").count() else ""
        log("状态提示3D已切换", "3D 态势已切换" in (tag or ""), (tag or "")[:60])

        # ---- 启动推演：切回 gRPC sink ----
        # btn-run 打开配置弹窗（live 模式 detectModeAndRun 已绑定该按钮）
        if page.locator("#btn-run").count():
            page.click("#btn-run")
        page.wait_for_timeout(700)
        if page.locator("#config-modal").count() and page.locator("#config-modal").is_visible():
            page.click("#cfg-submit")
        page.wait_for_timeout(5000)
        src2 = unquote(page.evaluate("document.getElementById('replay-frame')?.src") or "")
        log("启动推演切回gRPC", "/proxy" in src2 and "/rerun-file/" not in src2, src2[-80:])

        log("无JS错误", not errs, str(errs[:2]))
        browser.close()

    print(f"\n结果: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
