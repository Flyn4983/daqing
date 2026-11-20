# -*- coding: utf-8 -*-
"""从最终版 docx 提取全部图片，并按文档顺序建立 图片->图题 映射。"""
import re
import shutil
import zipfile
from pathlib import Path

from lxml import etree

DOCX = Path(r"C:\Users\flyn4\Desktop\最终版本-比赛--.docx")
OUT = Path(r"C:\Users\flyn4\Desktop\daqing\report\_final_figs")
OUT.mkdir(parents=True, exist_ok=True)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

with zipfile.ZipFile(DOCX) as z:
    doc = etree.fromstring(z.read("word/document.xml"))
    rels = etree.fromstring(z.read("word/_rels/document.xml.rels"))
    rid2target = {}
    for rel in rels.findall("pr:Relationship", NS):
        rid = rel.get("Id")
        tgt = rel.get("Target")
        if tgt and "media/" in tgt:
            rid2target[rid] = tgt.split("media/")[-1]

    # 逐段落扫描：记录含图片的段落，以及随后段落的图题文字
    body = doc.find("w:body", NS)
    paras = body.findall("w:p", NS)
    items = []  # (即)x
    for i, p in enumerate(paras):
        blips = p.findall(".//a:blip", NS)
        if not blips:
            continue
        text = "".join(p.itertext()).strip()
        # 找图题：同段落文字 or 紧随其后的段落中匹配 图N
        caption = ""
        # 先看后续 3 段
        for j in range(i + 1, min(i + 4, len(paras))):
            t = "".join(paras[j].itertext()).strip()
            if re.match(r"^图\s*\d+", t):
                caption = t
                break
            if t and not caption:
                # 可能是图题但没编号，取短的
                if len(t) < 60:
                    caption = t
        for blip in blips:
            rid = blip.get("{%s}embed" % NS["r"])
            fname = rid2target.get(rid, "")
            if fname:
                items.append((fname, caption, text[:40]))

    # 提取到输出目录，按顺序编号
    manifest = []
    for idx, (fname, caption, ptext) in enumerate(items, 1):
        src = "word/media/" + fname
        ext = Path(fname).suffix or ".png"
        outname = f"fig_{idx:02d}__{Path(fname).stem}{ext}"
        outpath = OUT / outname
        with zipfile.ZipFile(DOCX) as z:
            outpath.write_bytes(z.read(src))
        manifest.append({"seq": idx, "media": fname, "file": outname, "caption": caption})

    import json
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"extracted {len(manifest)} images to {OUT}")
    for m in manifest:
        print(f"[{m['seq']:2d}] {m['media']:14s} -> {m['caption']}")
