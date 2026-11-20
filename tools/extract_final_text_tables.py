# -*- coding: utf-8 -*-
"""按文档顺序提取最终版报告的全部正文段落与全部表格，用于细节全量核查。
输出 report/_final_audit/body.md（正文+表格交错）与 tables.md（仅表格汇总）。
"""
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn

SRC = Path(r"C:\Users\flyn4\Desktop\最终版本-比赛--.docx")
OUT = Path("report/_final_audit")
OUT.mkdir(parents=True, exist_ok=True)

doc = Document(str(SRC))


def iter_block_items(parent):
    """按文档顺序遍历段落与表格（python-docx 顶层）。"""
    from docx.document import Document as _Doc
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    if isinstance(parent, _Doc):
        parent_elm = parent.element.body
    else:
        parent_elm = parent._element
    for child in parent_elm.iterchildren():
        if child.tag == qn('w:p'):
            yield Paragraph(child, parent)
        elif child.tag == qn('w:tbl'):
            yield Table(child, parent)


body_lines = []
table_lines = []
para_n = 0
table_n = 0
for block in iter_block_items(doc):
    cls = block.__class__.__name__
    if cls == "Paragraph":
        txt = block.text.strip()
        style = block.style.name if block.style else ""
        if not txt:
            continue
        para_n += 1
        if style.startswith("Heading") or style.startswith("标题"):
            body_lines.append(f"\n## [P{para_n}|{style}] {txt}")
        else:
            body_lines.append(f"[P{para_n}] {txt}")
    elif cls == "Table":
        table_n += 1
        rows = block.rows
        cols = len(block.columns) if rows else 0
        table_lines.append(f"\n### 表{table_n}（{len(rows)}行×{cols}列）")
        body_lines.append(f"\n===== 表{table_n}（{len(rows)}行×{cols}列）=====")
        for ri, row in enumerate(rows):
            cells = [c.text.strip().replace("\n", " / ") for c in row.cells]
            line = " | ".join(cells)
            table_lines.append(line)
            body_lines.append(f"  R{ri}: " + line)

(OUT / "body.md").write_text("\n".join(body_lines), encoding="utf-8")
(OUT / "tables.md").write_text("\n".join(table_lines), encoding="utf-8")
print(f"段落数={para_n} 表格数={table_n}")
print(f"写出: {OUT/'body.md'}  {OUT/'tables.md'}")
