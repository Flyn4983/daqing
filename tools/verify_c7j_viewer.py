"""C7-j 浏览器端验证：加载 replay rrd，抓 console 错误 + 页面报错文本 + 截图。

用法：
  PYTHONPATH=src python tools/verify_c7j_viewer.py replay_brigade_canyon_clear.rrd
  PYTHONPATH=src python tools/verify_c7j_viewer.py replay_example_brigade.rrd shot2.png
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    from playwright.sync_api import sync_playwright

    rrd = sys.argv[1] if len(sys.argv) > 1 else "replay_brigade_canyon_clear.rrd"
    out = sys.argv[2] if len(sys.argv) > 2 else str(Path(REPO) / "tmp_rr" / f"c7j_{rrd}.png")
    url = (f"http://127.0.0.1:8848/rerun-viewer/?url=http%3A%2F%2F127.0.0.1%3A8848%2Frerun-file%2F{rrd}"
           f"&notebook=0&renderer=webgl")
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader", "--use-gl=angle"])
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(28000)
        body = page.locator("body").inner_text()
        page.screenshot(path=out)
        browser.close()

    bad_kw = ("no triangle indices", "triangle indices", "invalid index", "memory budget",
              "exceeds the memory", "try the native viewer", "failed to load mesh")
    page_hits = [ln for ln in body.splitlines() if any(k in ln.lower() for k in bad_kw)]
    console_hits = [e for e in errors if any(k in e.lower() for k in bad_kw)]
    print(f"=== {rrd} ===")
    print(f"截图: {out}")
    print(f"页面报错文本命中: {len(page_hits)}")
    for h in page_hits[:10]:
        print("  ", h)
    print(f"console 报错命中: {len(console_hits)}")
    for h in console_hits[:10]:
        print("  ", h)
    print(f"全部 console error 数: {len(errors)}")
    for e in errors[:8]:
        print("  ", e[:200])


if __name__ == "__main__":
    main()
