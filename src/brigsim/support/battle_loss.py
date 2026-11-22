"""战损清单生成器（P0）——从仿真事件流派生逐件清单，并让四率由清单派生。

设计要点：
1. **不改内核**：只读 `kernel.log` 的 snapshots/engagements（或 parquet），
   从 `damage_grade / part / disposition` 列生成逐件战损记录。
2. **清单是唯一战损事实源**：`rates_from_loss_list()` 按 §4.3 口径 A 派生
   战损率（仅 K 级）、战斗损伤率（K+M+F）、完好率、修复率、故障率，
   并与 `experiment/four_rates.py` 的口径对账（容差内一致）。
3. 无事件数据时提供 `from_metrics()` 兜底：由 MC 汇总的均值 + 编制数生成
   "按类别的汇总清单"（明确标注 source=estimate，不伪造逐件明细）。

落盘 `outputs/support/loss_<id>.json` / `.csv`。
"""

from __future__ import annotations

import csv
import json
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .catalog import CATALOG

_STORE = Path(__file__).resolve().parents[3] / "outputs" / "support" / "loss"
_STORE.mkdir(parents=True, exist_ok=True)

GRADES = {0: "N", 1: "M", 2: "F", 3: "K"}
DISPOSITIONS = {
    0: "未处置", 1: "现地处置/抢修", 2: "拖救", 3: "自行后送", 4: "后送", 5: "报废",
}
REPAIR_PARTS = {"mobility", "powerpack", "fire_control", "weapon"}
SIDE_NAMES = {0: "红方", 1: "蓝方"}


def _unit_meta(scenario=None) -> dict[str, dict]:
    """unit_id → {platform, category, org}；从想定 forces 读取，不改内核。"""
    meta: dict[str, dict] = {}
    cat_by_key = {e["key"]: e["category"] for e in CATALOG["equipment"]}
    if scenario is None:
        return meta
    for side, units in (getattr(scenario, "forces", None) or {}).items():
        for u in units or []:
            meta[str(u.unit_id)] = {
                "platform": str(u.type),
                # 想定里的平台类型可能不是catalog的26个键：保留原始值，便于清单可读
                "category": cat_by_key.get(str(u.type), str(u.type)),
                "org": str(getattr(u, "org", "") or ""),
            }
    return meta


def from_event_frames(
    snap_df,
    eng_df=None,
    scenario=None,
    *,
    n_runs: int = 1,
    source: str = "event",
) -> dict:
    """从 EventLog.to_frames() 的快照表生成战损清单。

    取每个 unit_id 的**最后一条**快照作为终态；grade>0 或 alive=False 记账。
    致损武器从 engagements 的 target 列反查（最后一击）。
    """
    import pandas as pd  # 延迟导入，ARM CPU 启动更快

    if snap_df is None or getattr(snap_df, "empty", True):
        return {"source": "empty", "items": [], "summary": {}, "n_runs": n_runs}
    df = snap_df.copy()
    # 取终态：按 unit_id 分组取最后 tick 行
    if "tick" in df.columns:
        df = df.sort_values("tick").groupby("unit_id", as_index=False).tail(1)
    meta = _unit_meta(scenario)
    weapon_by_target: dict[str, str] = {}
    if eng_df is not None and not getattr(eng_df, "empty", True):
        for _, r in eng_df.iterrows():
            if bool(r.get("killed")) or bool(r.get("damaged")):
                weapon_by_target[str(r.get("target", ""))] = str(r.get("weapon", "") or "")

    items: list[dict] = []
    for _, row in df.iterrows():
        uid = str(row.get("unit_id", ""))
        grade = int(row.get("damage_grade", 0) or 0)
        alive = bool(row.get("alive", True))
        damaged = bool(row.get("damaged", False))
        under_repair = bool(row.get("under_repair", False))
        under = int(row.get("under_repair", False) or 0)
        disp = int(row.get("disposition", 0) or 0)
        # K 级以 alive=False 为准（对齐内核口径）； grade=3 也算 K
        if not alive:
            grade = 3
        if grade == 0 and not damaged and not under_repair:
            continue
        side = int(row.get("side", 0) or 0)
        m = meta.get(uid, {})
        items.append({
            "loss_id": "",  # 汇总后统一编号
            "tick": int(row.get("tick", 0) or 0),
            "side": SIDE_NAMES.get(side, str(side)),
            "side_code": side,
            "unit_id": uid,
            "platform": m.get("platform", "unknown"),
            "category": m.get("category", "unknown"),
            "org": m.get("org", ""),
            "grade": GRADES.get(grade, str(grade)),
            "grade_code": grade,
            "damage_part": str(row.get("part", "") or ""),
            "cause": "combat" if weapon_by_target.get(uid) else
                     ("failure_natural" if grade in (1, 2) else "combat"),
            "weapon": weapon_by_target.get(uid, ""),
            "disposition": DISPOSITIONS.get(disp, str(disp)),
            "disposition_code": disp,
            "repair_status": "在修" if under else ("已修复" if disp == 0 and grade in (1, 2) else
                             ("不可修" if grade == 3 else "待修")),
            "est_repair_ticks": 6 if grade == 1 else (14 if grade == 2 else 0),
            "location": f"({float(row.get('x', 0)):.0f},{float(row.get('y', 0)):.0f})",
            "source": source,
        })
    items.sort(key=lambda x: (x["side_code"], x["grade_code"], x["unit_id"]))
    for i, it in enumerate(items, 1):
        it["loss_id"] = f"L{i:04d}"
    summary = aggregate(items, roster_size=_roster_size(snap_df, 0))
    summary["roster_size_red"] = _roster_size(snap_df, 0)
    summary["roster_size_blue"] = _roster_size(snap_df, 1)
    return {
        "source": source, "items": items,
        "summary": summary,
        "n_runs": n_runs,
    }


def _roster_size(snap_df, side: int = 0) -> int:
    """按阵营统计参战单位数。

    ⚠️ 内核四率（metrics.per_run_metrics）的分母是 state.side==0 的红方单位数，
    不含蓝方。若用 snapshot 全体（红+蓝）做分母，清单派生战损率会与内核
    对账失败（实测 10/23=0.435 vs 内核 2/14=0.143）。故这里按 side 过滤。
    """
    try:
        df = snap_df if side is None else snap_df[snap_df["side"] == side]
        return int(df["unit_id"].nunique())
    except Exception:
        return 0


def from_metrics(metrics: dict, mission: dict | None = None) -> dict:
    """兜底：只有 MC 汇总指标时，按类别生成"汇总清单"（不伪造逐件明细）。

    明确 `source=estimate`，前端会标注"按类别汇总 · 非逐件明细"，
    避免与真实事件流清单混淆。
    """
    summary_metrics = metrics or {}
    est = summary_metrics.get("summary") or summary_metrics
    items: list[dict] = []
    roster_total = 0
    if mission:
        from .mission_input import summary as mission_summary
        roster_total = mission_summary(mission)["roster_total"]

    def mv(key: str, default: float = 0.0) -> float:
        v = est.get(key, default)
        if isinstance(v, dict):
            v = v.get("mean", default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    attr_red = mv("attrition_red")
    ready_red = mv("readiness_red")
    n = roster_total or 100
    k = int(round(attr_red * n))
    damaged = max(0, n - k - int(round(ready_red * n)))
    m = int(round(damaged * 0.65))
    f = damaged - m
    if k:
        items.append({
            "loss_id": "E0001", "tick": -1, "side": "红方", "side_code": 0, "unit_id": "—",
            "platform": "按类别汇总", "category": "aggregate", "org": "全旅",
            "grade": "K", "grade_code": 3, "damage_part": "", "cause": "combat",
            "weapon": "", "disposition": "报废", "disposition_code": 5,
            "repair_status": "不可修", "est_repair_ticks": 0, "location": "—",
            "source": "estimate", "count": k,
        })
    for grade, cnt, disp in (("M", m, "现地处置/抢修"), ("F", f, "后送")):
        if cnt:
            items.append({
                "loss_id": f"E{len(items)+1:04d}", "tick": -1, "side": "红方", "side_code": 0,
                "unit_id": "—", "platform": "按类别汇总", "category": "aggregate",
                "org": "全旅", "grade": grade,
                "grade_code": 1 if grade == "M" else 2, "damage_part": "", "cause": "combat",
                "weapon": "", "disposition": disp, "disposition_code": 1 if grade == "M" else 4,
                "repair_status": "待修", "est_repair_ticks": 6 if grade == "M" else 14,
                "location": "—", "source": "estimate", "count": cnt,
            })
    summary = aggregate(items, roster_size=n)
    summary["roster_size_red"] = n
    summary["roster_size_blue"] = 0
    return {
        "source": "estimate", "items": items, "n_runs": summary_metrics.get("n_reps", 0),
        "summary": summary,
        "note": "由 MC 汇总指标按类别反推，非逐件明细；运行一次实时推演可生成真实逐件清单。",
    }


def aggregate(items: list[dict], roster_size: int = 0) -> dict:
    """多维汇总：grade / disposition / category / platform。"""
    by_grade = Counter()
    by_disp = Counter()
    by_cat = Counter()
    by_plat = Counter()
    by_side = Counter()
    for it in items:
        cnt = int(it.get("count", 1))
        by_grade[it["grade"]] += cnt
        by_disp[it["disposition"]] += cnt
        by_cat[it["category"]] += cnt
        by_plat[it["platform"]] += cnt
        by_side[it["side"]] += cnt
    n = roster_size or sum(int(i.get("count", 1)) for i in items)
    def pct(v: int) -> float:
        return round(v / n, 6) if n else 0.0
    return {
        "roster_size": n,
        "total_losses": sum(int(i.get("count", 1)) for i in items),
        "by_grade": dict(by_grade), "by_disposition": dict(by_disp),
        "by_category": dict(by_cat), "by_platform": dict(by_plat),
        "by_side": dict(by_side),
        "pct_of_roster": {k: pct(v) for k, v in by_grade.items()},
    }


def aggregate_side(items: list[dict], side_code: int = 0) -> dict:
    """只汇总指定阵营（默认红方=我方）的清单条目，供四率派生使用。"""
    return aggregate([it for it in items if int(it.get("side_code", 0)) == side_code],
                     roster_size=0)


def rates_from_loss_list(loss: dict, *, core_rates: dict | None = None) -> dict:
    """由清单派生四率（口径 A），并与内核四率对账。

    ⚠️ 只统计我方（红方 side_code=0）：内核 attrition_red / readiness_red 的分母
    都是红方参战单位数；蓝方战损属于"战果"，不能混入我方战损率。
    """
    s = loss.get("summary") or {}
    n = int(s.get("roster_size_red", 0) or s.get("roster_size", 0) or 0)
    own = aggregate_side(loss.get("items") or [], 0)
    g = own.get("by_grade") or {}
    k = int(g.get("K", 0)); m = int(g.get("M", 0)); f = int(g.get("F", 0))
    d = own.get("by_disposition") or {}
    repaired = sum(v for key, v in d.items() if "抢修" in key or "修复" in key)
    in_repair = 0
    for it in loss.get("items") or []:
        if int(it.get("side_code", 0)) == 0 and it.get("repair_status") == "在修":
            in_repair += int(it.get("count", 1))

    def rate(v: int) -> float:
        return round(v / n, 6) if n else 0.0

    derived = {
        "attrition": rate(k),                       # K 级战毁 / 参战总数（对齐内核）
        "damage_rate": rate(k + m + f),             # 战斗损伤率（新增，单列）
        "readiness": rate(max(0, n - k - m - f - in_repair)),
        "repair_rate": round(repaired / (repaired + in_repair), 6) if (repaired + in_repair) else 1.0,
        "grade_counts": {"K": k, "M": m, "F": f},
        "n_roster": n,
    }
    # 对账：内核四率来自 four_rates_summary（若提供）
    recon = {"checked": False, "passed": None, "items": []}
    if core_rates:
        recon["checked"] = True
        ok = True
        pairs = [
            ("attrition", "attrition_red", 1e-6),
            ("readiness", "readiness_red", 1e-3),
        ]
        for dk, ck, tol in pairs:
            if ck in core_rates:
                cv = core_rates[ck]
                if isinstance(cv, dict):
                    cv = cv.get("mean", 0)
                diff = abs(derived[dk] - float(cv))
                passed = diff <= tol
                ok &= passed
                recon["items"].append({"metric": dk, "from_list": derived[dk],
                                       "from_kernel": float(cv), "diff": round(diff, 6),
                                       "tol": tol, "passed": passed})
        recon["passed"] = bool(ok)
    derived["reconciliation"] = recon
    return derived


def rates_both_doctrines(loss: dict, *, core_rates: dict | None = None) -> dict:
    """口径 A / B 双列派生（P0-4）。

    - 口径 A（权威，与内核对账）：战损率只认 K 级 → 等价于 `1 - alive/n`（内核口径）。
    - 口径 B（辅助，单列展示）：战斗损伤率 = K+M+F，反映"含可修损伤"的广义受损比例。
    两者都基于同一份清单事实，绝不互相替代；输出里明确标注哪列对账。
    """
    base = rates_from_loss_list(loss, core_rates=core_rates)
    base["doctrines"] = [
        {"id": "A", "name": "战损率（K 级战毁）",
         "desc": "只计 K 级战毁 / 我方参战数；与内核 attrition_red 等价，可核账",
         "value": base["attrition"], "authoritative": True, "reconcilable": True},
        {"id": "B", "name": "战斗损伤率（K+M+F）",
         "desc": "计全部战斗损伤（含可修 M/F）/ 我方参战数；反映广义受损，不对账内核战损率",
         "value": base["damage_rate"], "authoritative": False, "reconcilable": False},
    ]
    base["rate_basis_default"] = "A"
    return base


def replication_ci(rows: list[dict], keys: tuple[str, ...] = (
    "attrition_red", "readiness_red", "attrition_blue", "readiness_blue",
)) -> dict:
    """逐次复制明细 + 均值±95%CI（P0-2）。

    直接复用 `experiment/metrics.summarize` 的 z=1.96 正态近似口径，
    不改内核；输入是 `run_replications` 返回的 rows（每次复制的指标 dict）。
    success 为非数值布尔，单独按"成功复制数/总复制数"统计。
    """
    if not rows:
        return {"rows": [], "summary": {}, "n": 0}
    import math

    detail = []
    for i, r in enumerate(rows, 1):
        detail.append({
            "rep": i,
            "seed_offset": i - 1,
            **{k: (float(r[k]) if r.get(k) is not None else None) for k in keys if k in r},
            "success": bool(r.get("success", False)),
        })
    summary: dict[str, dict] = {}
    n = len(rows)
    for k in keys:
        vals = [float(r[k]) for r in rows if r.get(k) is not None]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0.0
        ci95 = 1.96 * sd / math.sqrt(len(vals)) if len(vals) > 1 else 0.0
        summary[k] = {"mean": round(mean, 6), "std": round(sd, 6),
                      "ci95": round(ci95, 6),
                      "lo": round(mean - ci95, 6), "hi": round(mean + ci95, 6)}
    succ_n = sum(1 for r in rows if r.get("success"))
    summary["success"] = {"mean": round(succ_n / n, 6), "count": succ_n,
                          "n": n, "ci95": None}
    return {"rows": detail, "summary": summary, "n": n,
            "note": "95%CI 用正态近似 z=1.96·sd/√n，与 experiment/metrics.summarize 口径一致"}


def business_timeline(snap_df) -> dict:
    """业务环节状态时间线（P0-5）——逐单位从事件快照派生环节流转。

    这是与"软件8节点流程条"并列的**第二条主线**：真实保障业务环节
    发现战损→损伤评估→分级判定→抢拖救/后送→修复归队的状态变化。
    只读事件帧，不写内核；纯展示派生，不影响四率。

    每个单位按 tick 提取状态变化点，环节映射：
      - 受损发现：damage_grade 0→>0 或 damaged False→True
      - 损伤评估：part 非空
      - 分级判定：grade 落定为 M/F/K
      - 抢救抢修：under_repair True
      - 后送处置：disposition∈{2拖救,3自后送,4后送}
      - 修复归队：末态 grade==0 且 alive
      - 报废补充：grade==K 或 disposition==5
    """
    if snap_df is None or getattr(snap_df, "empty", True):
        return {"stages": [], "tracks": [], "summary": {}, "source": "empty"}
    STAGES = ["受损发现", "损伤评估", "分级判定", "抢救抢修", "后送处置", "修复归队", "报废补充"]
    GRADE = {0: "N", 1: "M", 2: "F", 3: "K"}
    DISP = {0: "未处置", 1: "现地抢修", 2: "拖救", 3: "自后送", 4: "后送", 5: "报废"}
    df = snap_df.sort_values("tick") if "tick" in snap_df.columns else snap_df
    tracks: list[dict] = []
    for uid, g in df.groupby("unit_id", sort=False):
        evts: list[dict] = []
        prev = None
        for _, r in g.iterrows():
            grade = int(r.get("damage_grade", 0) or 0)
            alive = bool(r.get("alive", True))
            damaged = bool(r.get("damaged", False))
            under = bool(r.get("under_repair", False))
            disp = int(r.get("disposition", 0) or 0)
            part = str(r.get("part", "") or "")
            if not alive:
                grade = 3
            state = {"grade": GRADE.get(grade, str(grade)), "under": under,
                     "disp": DISP.get(disp, str(disp)), "part": part}
            if prev is None:
                # 首个 tick 只记基线（无损则跳过，避免健康单位也画一条空轨迹）
                if grade == 0 and not damaged and not under:
                    prev = state
                    continue
            changed = prev is None or any(state[k] != prev[k] for k in ("grade", "under", "disp", "part"))
            if changed:
                stage = None
                if grade > 0 and (prev is None or prev["grade"] == "N"):
                    stage = "受损发现"
                elif part and (prev is None or prev["part"] != part):
                    stage = "损伤评估"
                elif grade in (1, 2, 3) and (prev is None or prev["grade"] == "N"):
                    stage = "分级判定"
                elif under and (prev is None or not prev["under"]):
                    stage = "抢救抢修"
                elif disp in (2, 3, 4) and (prev is None or prev["disp"] != state["disp"]):
                    stage = "后送处置"
                elif grade == 3 or disp == 5:
                    stage = "报废补充"
                elif grade == 0 and alive and prev is not None and prev["grade"] != "N":
                    stage = "修复归队"
                evts.append({"tick": int(r.get("tick", 0) or 0),
                             "stage": stage or "状态更新",
                             "grade": state["grade"], "disp": state["disp"],
                             "part": part, "under": under,
                             "side": SIDE_NAMES.get(int(r.get("side", 0) or 0), "")})
            prev = state
        if evts:
            tracks.append({
                "unit_id": str(uid),
                "platform": _unit_meta().get(str(uid), {}).get("platform", ""),
                "events": evts,
            })
    stage_count = {s: 0 for s in STAGES}
    for t in tracks:
        for e in t["events"]:
            if e["stage"] in stage_count:
                stage_count[e["stage"]] += 1
    return {
        "stages": STAGES,
        "tracks": tracks,
        "summary": {"n_tracks": len(tracks), "stage_events": stage_count,
                    "n_repaired": sum(1 for t in tracks if any(e["stage"] == "修复归队" for e in t["events"])),
                    "n_scrapped": sum(1 for t in tracks if any(e["stage"] == "报废补充" for e in t["events"]))},
        "source": "event",
        "note": "从事件快照逐 tick 派生的业务环节流转；与四率无关，仅用于全过程可视化",
    }


def save(loss: dict) -> str:
    lid = loss.get("loss_id") or uuid.uuid4().hex[:10]
    loss["loss_id"] = lid
    loss["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _STORE.mkdir(parents=True, exist_ok=True)
    (_STORE / f"loss_{lid}.json").write_text(
        json.dumps(loss, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # CSV（人可读可上报）
    with (_STORE / f"loss_{lid}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        cols = ["loss_id", "tick", "side", "unit_id", "platform", "category", "org",
                "grade", "damage_part", "cause", "weapon", "disposition",
                "repair_status", "est_repair_ticks", "location", "source"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for it in loss.get("items") or []:
            w.writerow(it)
    return lid


def load(lid: str) -> dict | None:
    f = _STORE / f"loss_{lid}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def latest() -> dict | None:
    """最近一次**仿真推演**清单。

    人工填报 Word 清单（source=manual_docx）虽落在同一 store，但不代表"最近推演"，
    跳过它们以免污染按 latest() 取仿真结果的既有链路（如 P1 对账明细）。
    人工清单始终可通过 loss_id 精确读取（load()）。
    """
    files = sorted(_STORE.glob("loss_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("source") == "manual_docx":
            continue
        return d
    return None


def parts_needed(loss: dict) -> dict[str, int]:
    """从清单的 damage_part 汇总器材需求（仅 4 类可修部件）。"""
    out = defaultdict(int)
    for it in loss.get("items") or []:
        p = it.get("damage_part", "")
        if p in REPAIR_PARTS and it.get("grade") in ("M", "F"):
            out[p] += int(it.get("count", 1))
    return dict(out)
