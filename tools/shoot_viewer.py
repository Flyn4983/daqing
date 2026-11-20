"""截图 rerun viewer（WebGL 强制），保存到临时文件供查看。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    from playwright.sync_api import sync_playwright

    rrd = sys.argv[1] if len(sys.argv) > 1 else "replay_example_brigade.rrd"
    out = sys.argv[2] if len(sys.argv) > 2 else str(Path(REPO) / "tmp_rr" / "viewer_shot.png")
    url = (f"http://127.0.0.1:8848/rerun-viewer/?url=http%3A%2F%2F127.0.0.1%3A8848%2Frerun-file%2F{rrd}"
           f"&notebook=0&renderer=webgl")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader", "--use-gl=angle"])
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(25000)  # 等 rrd 加载 + 渲染
        page.screenshot(path=out)
        print("saved:", out)
        browser.close()


if __name__ == "__main__":
    main()
