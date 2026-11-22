"""浏览器实测：人工 Word 战损清单导入 + 抢修方案生成 + 两种来源同构。

用法：PYTHONPATH=src python tools/verify_repair_flow.py
前置：8848 服务已启动并加载 /api/support/loss/template、/loss/import、/repair-plan 端点。
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tmp_rr"
OUT.mkdir(exist_ok=True)


def make_filled_docx_b64() -> str:
    """构造一份填好的战损清单 Word（基于真实模板），返回 base64。"""
    from brigsim.support.loss_import import build_report_template_docx
    from docx import Document

    tpl = build_report_template_docx()
    d = Document(io.BytesIO(tpl))
    t = d.tables[0]
    r = t.rows[1]
    vals = ["1", "一连", "主战坦克", "3", "M 机动受损", "行走总成", "反坦克导弹", "现地抢修", "高地西侧", ""]
    for i, v in enumerate(vals):
        r.cells[i].text = v
    for v2 in [
        ["2", "二连", "步兵战车", "2", "F 火力受损", "火控系统", "炮弹", "后送", "谷地", ""],
        ["3", "三连", "自行火炮", "1", "K 战毁", "车体", "导弹", "报废", "阵地内", ""],
    ]:
        rr = t.add_row()
        for i, v in enumerate(v2):
            rr.cells[i].text = v
    buf = io.BytesIO()
    d.save(buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main() -> None:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    docx_b64 = make_filled_docx_b64()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for pat in ("**/rerun-viewer/**", "**/proxy/**", "**/app.rerun.io/**", "**/telemetry.rerun.io/**"):
            page.route(pat, lambda route: route.abort())
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "CSP" not in m.text and "rerun" not in m.text.lower() else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto("http://127.0.0.1:8848/index.html", wait_until="domcontentloaded", timeout=30000)
        page.locator("#btn-support-flow").wait_for(state="attached", timeout=20000)
        page.wait_for_timeout(500)
        page.locator("#btn-support-flow").click()
        page.wait_for_timeout(900)

        results: dict[str, object] = {}

        # 1. 新控件存在
        results["tpl_btn"] = page.locator("#sf-loss-tpl").count() == 1
        results["import_btn"] = page.locator("#sf-loss-import").count() == 1
        results["file_input"] = page.locator("#sf-loss-file").count() == 1
        results["repair_btn"] = page.locator("#sf-gen-repair").count() == 1
        results["src_tag"] = page.locator("#sf-src-tag").count() == 1

        # 2. 下载模板（监听 download）
        with page.expect_download(timeout=15000) as dl:
            page.locator("#sf-loss-tpl").click()
        download = dl.value
        results["template_filename"] = download.suggested_filename
        results["template_ok"] = "战损清单" in download.suggested_filename and download.suggested_filename.endswith(".docx")

        # 3. 上传 Word 清单（set_input_files 用内存 bytes）
        page.locator("#sf-loss-file").set_input_files({
            "name": "战损清单_一连.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "buffer": base64.b64decode(docx_b64),
        })
        page.wait_for_timeout(2200)  # 解析 + loadLoss 渲染
        msg = page.locator("#sf-import-msg")
        results["import_msg_visible"] = not msg.is_hidden()
        msg_txt = msg.inner_text() if results["import_msg_visible"] else ""
        results["import_ok"] = "已导入" in msg_txt and "3 条" in msg_txt
        tag_txt = page.locator("#sf-src-tag").inner_text()
        results["source_label"] = tag_txt
        results["source_manual"] = "人工填报" in tag_txt
        # 清单表格出现 3 条
        rows = page.locator("#sf-loss-table tbody tr")
        results["loss_rows"] = rows.count()

        # 4. 关闭本地大模型加速（勾选框 uncheck），生成抢修方案
        llm = page.locator("#sf-use-llm")
        if llm.count() and llm.is_checked():
            page.locator("#sf-use-llm").uncheck()
        page.locator("#sf-gen-repair").click()
        page.wait_for_timeout(1800)
        hint = page.locator("#sf-plan-hint").inner_text()
        results["plan_hint"] = hint
        check = page.locator("#sf-check").inner_text()
        results["check_has_repair"] = "抢修方案" in check
        results["check_passed"] = "复核通过" in check
        results["check_source"] = "来源" in check
        doc = page.locator("#sf-plan-doc").inner_text()
        results["doc_has_repair_title"] = "抢修方案" in doc and "报损情况" in doc
        results["doc_has_grades"] = "K 级" in doc and "F 级" in doc and "M 级" in doc
        results["doc_no_unfilled"] = "{{" not in doc

        # 5. 截图
        page.screenshot(path=str(OUT / "repair_flow_final.png"), full_page=False)
        results["screenshot"] = str(OUT / "repair_flow_final.png")

        browser.close()

    print("=== 验证结果 ===")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print(f"控制台错误: {len(errors)}")
    for e in errors[:10]:
        print("  ", e)

    # 判定：核心项必须为真（数值项单独判断）
    core = ["tpl_btn", "import_btn", "file_input", "repair_btn", "src_tag",
            "template_ok", "import_msg_visible", "import_ok", "source_manual",
            "check_has_repair", "check_passed", "check_source",
            "doc_has_repair_title", "doc_has_grades", "doc_no_unfilled"]
    ok = all(results.get(k) is True for k in core) and results.get("loss_rows", 0) >= 3 and not errors
    print("VERDICT:", "PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
