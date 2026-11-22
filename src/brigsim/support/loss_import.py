"""人工战损清单（Word）导入：生成基层填写模板 + 容错解析为内部清单结构。

业务背景（2026-11-22 需求）：
- 抢修方案的战损来源有两条：①平台仿真计算得到的清单（battle_loss.from_event_frames）；
  ②基层单位人工填报的 Word 战损清单。两条来源最终都进入同一个"抢修方案生成"链路。
- 本模块只负责"第②条"的入口：把基层 Word 表格解析成与仿真清单同构的 dict
  （source=manual_docx），从而复用 battle_loss.aggregate / parts_needed / plan.generate。

设计红线：
- **数字不伪造**：所有数量、等级、部位都来自 Word 表格真实单元格；解析不到的字段留空/计数 0，
  不臆造装备数、不臆造战损率。汇总的 roster_size 只有填报人显式给出时才采信。
- **容错列名**：基层表头千差万别（"装备名称/车型/主战装备"、"战损等级/损伤程度/损坏等级"…），
  用别名表归一；无法识别的列忽略并在 warnings 里如实告知。
- **可选依赖**：python-docx 只在服务端导入时使用；未安装时给出可操作的安装提示，不影响仿真链路。

落盘复用 battle_loss.save（outputs/support/loss/），生成 loss_id 返回，供前端直接生成抢修方案。
"""

from __future__ import annotations

import base64
import io
import re
import time
import uuid
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
_TEMPLATE_DIR = _REPO / "templates" / "support"
_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

MANUAL_DOCX_TEMPLATE = "战损清单上报模板.docx"

# ---------------- 列名别名（基层实际用语 → 标准字段）----------------
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "seq": ("序号", "编号", "序", "no", "num", "序号."),
    "unit": ("单位", "部（分）队", "部队", "分队", "连队", "营", "所在 unit", "所属单位"),
    "platform": ("装备名称", "车型", "装备型号", "装备类型", "装备", "主战装备",
                 "装备种", "车辆类型", "器材名称", "名称", "装备(型号)"),
    "count": ("数量", "台数", "台", "辆", "件", "数", "损失数量", "受损数量", "台（辆）"),
    "grade": ("战损等级", "损伤等级", "损坏等级", "损伤程度", "受损等级", "毁伤等级",
              "等级", "判定", "损失等级", "战损程度"),
    "damage_part": ("损伤部位", "损坏部位", "受损部位", "故障部位", "损坏部件",
                    "部位", "受损部件", "失效部件"),
    "cause": ("致伤原因", "损伤原因", "战损原因", "损坏原因", "原因", "致损原因", "毁伤原因"),
    "disposition": ("处置", "处置方式", "当前处置", "处置建议", "处理", "拟处置", "处置意见"),
    "location": ("位置", "地域", "地点", "现地位置", "所在位置", "配置地域"),
    "repair_status": ("抢修状态", "修复状态", "维修状态", "状态"),
    "note": ("备注", "说明", "补充", "情况说明"),
}

# ---------------- 等级别名（基层实际用语 → K/M/F/N）----------------
_GRADE_ALIASES: list[tuple[str, str]] = [
    ("战毁", "K"), ("报废", "K"), ("摧毁", "K"), ("击毁", "K"), ("全损", "K"),
    ("毁灭", "K"), ("完全损失", "K"), ("k级", "K"), ("k", "K"), ("致命", "K"),
    ("火力受损", "F"), ("重损", "F"), ("重度", "F"), ("需后送", "F"), ("重伤", "F"),
    ("f级", "F"), ("f", "F"), ("火控失效", "F"), ("武器系统受损", "F"),
    ("机动受损", "M"), ("轻损", "M"), ("轻度", "M"), ("可修", "M"), ("现地抢修", "M"),
    ("m级", "M"), ("m", "M"), ("一般损伤", "M"), ("可修复", "M"), ("机动失效", "M"),
    ("无损", "N"), ("完好", "N"), ("正常", "N"), ("n级", "N"),
]

_GRADE_CODE = {"N": 0, "M": 1, "F": 2, "K": 3}
_GRADE_NAME = {"N": "无损", "M": "机动受损", "F": "火力受损", "K": "战毁"}

# 处置别名 → 标准处置词
_DISPOSITION_ALIASES: list[tuple[str, str]] = [
    ("现地", "现地处置/抢修"), ("抢修", "现地处置/抢修"), ("就地", "现地处置/抢修"),
    ("拖救", "拖救"), ("拖带", "拖救"), ("抢救", "拖救"),
    ("自行后送", "自行后送"), ("自后送", "自行后送"),
    ("后送", "后送"), ("送修", "后送"), ("修理所", "后送"),
    ("报废", "报废"), ("弃置", "报废"),
]
_DISPOSITION_CODE = {
    "未处置": 0, "现地处置/抢修": 1, "拖救": 2, "自行后送": 3, "后送": 4, "报废": 5,
}
# 损伤部位 → 内部器材部件 key（parts_needed 只用这 4 类可修件）
_PART_ALIASES: list[tuple[str, str]] = [
    ("行走", "mobility"), ("履带", "mobility"), ("机动", "mobility"), ("悬挂", "mobility"), ("传动", "mobility"),
    ("动力", "powerpack"), ("发动机", "powerpack"), ("动力包", "powerpack"), ("变速", "powerpack"),
    ("火控", "fire_control"), ("瞄准", "fire_control"), ("雷达", "fire_control"), ("观瞄", "fire_control"),
    ("武器", "weapon"), ("火炮", "weapon"), ("炮塔", "weapon"), ("导弹", "weapon"), ("枪", "weapon"),
]

_NUM_RE = re.compile(r"\d+")


def _require_docx():
    try:
        from docx import Document  # type: ignore
        from docx.shared import Pt, Cm  # type: ignore
        return Document, Pt, Cm
    except ImportError as exc:  # pragma: no cover - 取决于部署环境
        raise RuntimeError(
            "解析 Word 战损清单需要 python-docx。请在服务端执行：\n"
            "  .venv/Scripts/python -m pip install python-docx\n"
            "（fusion 工作站 ARM 环境：source .venv/bin/activate && pip install python-docx）"
        ) from exc


# ---------------- 1. 基层填写模板 ----------------

def build_report_template_docx() -> bytes:
    """生成基层单位填写用的 Word 战损清单模板（表头 + 填写说明 + 等级判定 + 示例行）。

    模板只给"表头与判定口径"，不预填任何虚构战损数字——示例行以"（示例，填报时删除）"标注。
    """
    Document, Pt, Cm = _require_docx()
    doc = Document()

    title = doc.add_heading("装备战损清单（抢修上报）", level=0)
    title.alignment = 1  # CENTER

    p = doc.add_paragraph()
    p.add_run("填报单位：").bold = True
    p.add_run("________________　　")
    p.add_run("填报时间：").bold = True
    p.add_run("____年__月__日__时　　")
    p.add_run("战斗阶段：").bold = True
    p.add_run("____________")

    doc.add_paragraph("填写说明：")
    doc.add_paragraph("1. 一行一装（同类同部位可合并并在“数量”填台数）；只填真实发生的战损，无则整表留空。", style="List Number")
    doc.add_paragraph("2. “战损等级”按三档判定：K=战毁/报废（不可恢复）；F=火力受损/重损（需后送修理）；M=机动受损/轻损（可现地抢修）。", style="List Number")
    doc.add_paragraph("3. “损伤部位”尽量填写：行走/动力/火控/武器，用于自动测算器材需求；不详可留空。", style="List Number")
    doc.add_paragraph("4. “处置”填写：现地抢修/拖救/自行后送/后送/报废；尚未处置填“未处置”。", style="List Number")
    doc.add_paragraph("5. 本表由本软件解析后自动生成抢修方案；填报内容即为方案数字的唯一来源，请确保与实情一致。", style="List Number")

    headers = ["序号", "单位", "装备名称/型号", "数量", "战损等级", "损伤部位",
               "致伤原因", "处置", "位置", "备注"]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = h
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True
                run.font.size = Pt(9)

    example = ["（示例）", "一排", "主战坦克", "2", "M 机动受损", "行走总成",
               "反坦克导弹", "现地抢修", "高地西侧", "示例行，填报时请删除"]
    row = table.add_row()
    for i, v in enumerate(example):
        row.cells[i].text = v
        for para in row.cells[i].paragraphs:
            for run in para.runs:
                run.font.size = Pt(9)

    doc.add_paragraph()
    note = doc.add_paragraph()
    note.add_run("判定口径提示：").bold = True
    note.add_run("战损率只统计 K 级战毁（不可恢复），M/F 可修损伤计入“战斗损伤率”但不计入战损率；"
                 "此口径与平台仿真一致，抢修方案据此分级处置。")

    for section in doc.sections:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(1.8)
        section.right_margin = Cm(1.8)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------- 2. 容错解析 ----------------

def _norm(s: Any) -> str:
    return re.sub(r"[\s\u3000（）()\[\]【】:：.、/\\-]", "", str(s or "")).strip().lower()


def _match_alias(text: str, aliases: tuple[str, ...]) -> bool:
    n = _norm(text)
    if not n:
        return False
    for a in aliases:
        an = _norm(a)
        if not an:
            continue
        # 短别名（≤2字，如"件""台""辆""数"）只接受完全相等，
        # 否则"损坏部件"会被 count 的"件"误命中。
        if len(an) <= 2:
            if an == n:
                return True
        elif an == n or an in n:
            return True
    return False


def _map_header(header: str) -> str | None:
    """返回表头对应的标准字段；同一表头可能命中多个字段时，按别名匹配精度取最优。

    精度 = 归一化后最长别名的长度；"损坏部件"（damage_part，4字精确）优先于
    "件"（count，短别名已排除）。避免字典顺序导致的误判。
    """
    best_field, best_score = None, 0
    n = _norm(header)
    for field, aliases in _COLUMN_ALIASES.items():
        score = 0
        for a in aliases:
            an = _norm(a)
            if not an or len(an) <= 2:
                if an and an == n:
                    score = max(score, len(an))
            elif an == n:
                score = max(score, len(an) + 10)  # 完全相等加权
            elif an in n:
                score = max(score, len(an))
        if score > best_score:
            best_field, best_score = field, score
    return best_field


def _map_grade(text: str) -> str:
    n = _norm(text)
    for alias, grade in _GRADE_ALIASES:
        an = _norm(alias)
        if an and an in n:
            return grade
    return ""  # 未识别，交由 warnings 处理


def _map_disposition(text: str) -> str:
    n = _norm(text)
    for alias, disp in _DISPOSITION_ALIASES:
        if _norm(alias) in n:
            return disp
    return "未处置" if not n else "未处置"


def _map_part(text: str) -> str:
    n = _norm(text)
    for alias, key in _PART_ALIASES:
        if _norm(alias) in n:
            return key
    return ""


def _parse_count(text: Any) -> int:
    m = _NUM_RE.search(str(text or ""))
    if not m:
        return 0
    try:
        return max(0, int(m.group()))
    except ValueError:
        return 0


def _decode_source(source: bytes | str) -> bytes:
    """接受 bytes、base64 字符串或 data URL。"""
    if isinstance(source, bytes):
        return source
    s = str(source).strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        return s.encode("utf-8")


def parse_loss_docx(source: bytes | str, *, roster_size: int = 0,
                    note: str = "") -> dict:
    """解析 Word 战损清单为内部清单结构（source=manual_docx）。

    Args:
        source: docx 字节 / base64 / data URL。
        roster_size: 填报人显式给出的参战总数（用于战损率分母）；0=留空，绝不臆造。
        note: 附加说明（如填报单位）。

    Returns:
        dict: 与 battle_loss.save 兼容的清单：{source, items, summary, warnings, note}。
    """
    Document, _Pt, _Cm = _require_docx()
    raw = _decode_source(source)
    try:
        doc = Document(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError(f"无法打开 Word 文件（可能不是有效 .docx）：{exc}") from exc

    warnings: list[str] = []
    items: list[dict] = []
    tables = list(doc.tables)

    if not tables:
        # 无表格：尝试从段落中解析"制表符/多空格"分隔的行（纯文本填报的兜底）
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        if not lines:
            raise ValueError("Word 中既没有表格也没有可解析的文本行，无法提取战损清单")
        warnings.append("未检测到表格，已按段落文本行尝试解析；建议改用《战损清单上报模板.docx》表格填写")
        rows_raw = [[c for c in re.split(r"[\t|]{1,}|\s{2,}", ln) if c.strip()] for ln in lines]
        header = rows_raw[0] if rows_raw else []
        body = rows_raw[1:]
    else:
        # 选择"最像战损清单"的表：能映射出的标准字段最多者优先
        best = None
        for t in tables:
            if not t.rows:
                continue
            hdr = [c.text for c in t.rows[0].cells]
            mapped = [f for f in (_map_header(h) for h in hdr) if f]
            score = len(set(mapped))
            if best is None or score > best[0]:
                best = (score, hdr, t)
        if best is None or best[0] < 2:
            raise ValueError("Word 表格无法识别战损清单表头（至少需识别“装备名称”与“战损等级”两列）")
        header = best[1]
        body = [[c.text for c in r.cells] for r in best[2].rows[1:]]

    field_idx: dict[str, int] = {}
    for i, h in enumerate(header):
        f = _map_header(h)
        if f and f not in field_idx:
            field_idx[f] = i

    for req in ("platform", "grade"):
        if req not in field_idx:
            raise ValueError(f"战损清单表头缺少必要列：{req}（已识别列：{list(field_idx)}）")

    def cell(row: list[str], field: str) -> str:
        i = field_idx.get(field)
        return str(row[i]).strip() if i is not None and i < len(row) else ""

    seq = 0
    for row in body:
        if not any(str(c).strip() for c in row):
            continue
        platform = cell(row, "platform")
        grade_text = cell(row, "grade")
        # 跳过示例行、合计行、空行：整行任一单元格含"示例"（含"（示例）"标记）即排除
        row_all = " ".join(str(c) for c in row)
        if not platform or "示例" in row_all or "合计" in _norm(platform) or _norm(platform) in ("装备名称", "车型"):
            continue
        if _map_grade(grade_text) in ("",) and not _NUM_RE.search(grade_text):
            # 等级列完全无法识别且无数字，跳过并告警，避免把"合计/说明"当装备
            warnings.append(f"跳过无法判定战损等级的行：{platform[:20]}")
            continue

        seq += 1
        grade = _map_grade(grade_text) or "M"  # 有数字但无别名时按最低可修档 M 保守处理
        if grade not in _GRADE_CODE:
            grade = "M"
        count = _parse_count(cell(row, "count")) or 1
        part_key = _map_part(cell(row, "damage_part"))
        disp = _map_disposition(cell(row, "disposition"))
        if grade == "K":
            disp = "报废"
            part_key = ""
        repair_status = cell(row, "repair_status") or ("不可修" if grade == "K" else "待修")
        est_ticks = {"M": 6, "F": 14, "K": 0, "N": 0}[grade]

        items.append({
            "loss_id": f"W{seq:04d}", "tick": -1,
            "side": "红方", "side_code": 0,
            "unit_id": cell(row, "unit") or "—",
            "platform": platform,
            "category": "manual",  # 人工填报统一归类，避免伪造类别
            "org": cell(row, "unit") or "—",
            "grade": grade, "grade_code": _GRADE_CODE[grade],
            "damage_part": part_key or cell(row, "damage_part"),
            "cause": cell(row, "cause") or "未注明",
            "weapon": "", "disposition": disp,
            "disposition_code": _DISPOSITION_CODE.get(disp, 0),
            "repair_status": repair_status,
            "est_repair_ticks": est_ticks,
            "location": cell(row, "location") or "—",
            "source": "manual_docx",
            "count": count,
        })

    if not items:
        raise ValueError("Word 中没有解析到任何有效战损行（示例行与合计行已排除）")

    # 汇总：roster_size 只有填报人显式给出才采信，否则 0（前端标注"参战基数未填"）
    from .battle_loss import aggregate
    summary = aggregate(items, roster_size=int(roster_size or 0))
    # aggregate 在 roster_size=0 时会用受损数当分母；人工清单未填参战总数时必须保持 0，
    # 避免把"受损台数"当成"参战总数"臆造战损率分母。
    summary["roster_size"] = int(roster_size or 0)
    summary["roster_size_red"] = int(roster_size or 0)
    summary["roster_size_blue"] = 0

    if not roster_size:
        warnings.append("未填报参战总数(roster_size)，战损率分母缺失；方案将只呈现战损绝对数，不臆造战损率")

    return {
        "source": "manual_docx",
        "items": items,
        "n_runs": 0,
        "summary": summary,
        "warnings": warnings,
        "note": note or "由基层单位填报的 Word 战损清单解析生成，数字全部来自填报内容",
        "template_used": MANUAL_DOCX_TEMPLATE,
    }


def save_imported(loss: dict) -> str:
    """落盘复用 battle_loss.save，保证与仿真清单同 store、同结构。"""
    from . import battle_loss
    return battle_loss.save(loss)
