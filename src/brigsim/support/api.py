"""作战保障全过程 HTTP API 编排层。

只编排已有模块（mission_input / battle_loss / plan）与现有推演器，不修改仿真内核。
server.py 只在本文件暴露的函数上做路由分派，保持整体架构不变。

新增端点：
- GET  /api/support/catalog        标准资源目录 + 8 节点流程 + 槽位字典
- GET  /api/support/mission         指定/最近一次录入
- POST /api/support/mission         校验并保存录入，返回映射后的推演配置
- POST /api/support/deduce          启动 N 次推演（后台线程）→ 逐件战损清单
- GET  /api/support/deduce          轮询推演进度
- GET  /api/support/loss            战损清单 + 清单派生四率 + 内核对账
- POST /api/support/plan            生成方案（模板槽位 + LLM 叙述 + 数字复核）
- GET  /api/support/plan             读取已生成方案
- GET  /api/support/templates        模板列表
- POST /api/support/template         上传/替换模板
- GET  /api/support/export           导出战损清单 / 方案文件路径
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import battle_loss, force_config, loss_import, mission_input, optimize_pareto, plan as plan_mod, repair_plan, constraints
from .catalog import (CATALOG, PROCESS_NODES, SLOT_DICT, METRIC_GLOSSARY, LLM_PARAM_SPEC,
                      LOSS_FIELD_DICT, COST_WEIGHT_SPEC, REVIEW_STATES, REVIEW_ACTIONS)

_REPO = Path(__file__).resolve().parents[3]
_SCENARIOS = _REPO / "scenarios"
_SUPPORT_DIR = _REPO / "outputs" / "support"
_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)

_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_MAX_JOBS = 8


# ---------------- 目录 / 流程 ----------------

def templates() -> dict:
    return {"templates": plan_mod.list_templates()}


def catalog() -> dict:
    return {
        "catalog": CATALOG,
        "process_nodes": PROCESS_NODES,
        "slot_dict": SLOT_DICT,
        "metric_glossary": METRIC_GLOSSARY,   # P1-7 指标口径 tooltip
        "llm_params": LLM_PARAM_SPEC,          # P1-3 LLM 生成参数规格
        "data_dictionary": LOSS_FIELD_DICT,    # P2-4 战损清单字段字典
        "cost_weight_spec": COST_WEIGHT_SPEC,  # P2-5 成本权重规格
        "review_states": REVIEW_STATES,        # P2-3 方案签批状态机
        "review_actions": REVIEW_ACTIONS,
        "constraint_spec": constraints.CONSTRAINT_SPEC,  # P3 真实业务约束规格（三源）
        "constraint_sources": constraints.SOURCE_NAMES,
        "templates": plan_mod.list_templates(),
        "support_dir": str(_SUPPORT_DIR.relative_to(_REPO)).replace("\\", "/"),
        "force_config": {
            "sides": [
                {"id": "red", "name": "红方（我方）", "color": "#f87171"},
                {"id": "blue", "name": "蓝方（敌方）", "color": "#60a5fa"},
            ],
            "multipliers": [
                {"key": "pk_scale", "name": "交火效能", "default": 1.0,
                 "hint": "单次交火毁伤概率乘子"},
                {"key": "detect_scale", "name": "侦察效能", "default": 1.0,
                 "hint": "发现概率乘子"},
                {"key": "speed_scale", "name": "机动速度", "default": 1.0,
                 "hint": "机动速度乘子"},
            ],
            "ground_keys": [e["key"] for e in CATALOG["equipment"]
                            if e["key"] not in ("uav", "loitering", "helo")],
            "air_kinds": ["uav", "loitering", "helo"],
        },
    }


def forces_default(scenario_name: str | None = None) -> dict:
    """读取指定想定（或最近录入使用的基础想定）的红蓝双方默认编成，供 GUI 一键载入。"""
    name = str(scenario_name or "")
    if not name:
        latest = mission_input.latest()
        name = (latest or {}).get("base_scenario", "example_brigade")
    try:
        return force_config.defaults(name)
    except Exception as exc:
        return {"error": f"读取想定默认编成失败：{type(exc).__name__}: {exc}"}


# ---------------- 录入 ----------------

def mission_get(mission_id: str | None = None) -> dict:
    m = mission_input.load(mission_id) if mission_id else mission_input.latest()
    if m is None:
        return {"mission": None, "message": "尚无录入记录"}
    return {"mission": m, "run_config": mission_input.to_run_config(m)}


def mission_upsert(payload: dict) -> dict:
    try:
        p, warnings = mission_input.validate(payload)
    except ValueError as exc:
        # 硬约束违反等校验失败：结构化返回（HTTP 200 + error 字段），前端可清晰展示
        msg = str(exc)
        return {"error": msg, "error_type": "validation",
                "constraint_violation": "硬约束" in msg,
                "hint": "请按提示调整录入值后重试；硬约束来自上级命令与资源现实。"}
    mid = mission_input.save(p)
    p["mission_id"] = mid
    return {"mission": p, "warnings": warnings,
            "run_config": mission_input.to_run_config(p), "mission_id": mid}


# ---------------- P3：真实业务约束校核 ----------------

def constraints_check(payload: dict | None = None) -> dict:
    """按当前录入（或指定 mission/loss）校核三源约束，返回逐条 pass/warn/fail。

    纯派生、毫秒级；不修改内核/权威数字。前端在录入页实时调用展示红黄绿。
    """
    payload = payload or {}
    mid = str(payload.get("mission_id") or "").strip()
    mission = mission_input.load(mid) if mid else mission_input.latest()
    if mission is None:
        mission = {}
    lid = str(payload.get("loss_id") or "").strip()
    loss = battle_loss.load(lid) if lid else battle_loss.latest()
    if not payload.get("with_loss"):
        loss = None  # 默认只做录入侧校核；显式 with_loss 才纳入战损清单
    result = constraints.check_feasibility(
        mission, mission.get("constraints") or payload.get("constraints"), loss=loss)
    result["constraints_configured"] = bool(mission.get("constraints_configured"))
    return result


# ---------------- 多次推演 → 战损清单 ----------------

def _scenario_info(name: str) -> dict:
    """只读 yaml 的地形/气象文本描述（不加载 HeightField，ARM 上毫秒级）。"""
    f = _SCENARIOS / f"{name}.yaml"
    if not f.is_file():
        return {}
    try:
        import yaml
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    env = d.get("environment") or {}
    ter = d.get("terrain") or {}
    weather = {
        "clear": "晴空", "cloudy": "多云", "overcast": "阴天", "fog": "浓雾",
        "rain": "降雨", "snow": "降雪", "smoke": "烟幕", "dust": "扬尘",
    }
    presets = {
        "mountain": "山地", "hills": "丘陵", "plain": "平原", "canyon": "峡谷",
        "rolling": "起丘陵地", "urban": "城市居民地", "desert": "荒漠",
        "real_dem": "真实 DEM 地形",
    }
    w = str(env.get("weather", ""))
    t = str(ter.get("preset_family", ""))
    return {
        "terrain": presets.get(t, t or "按想定地形"),
        "weather": weather.get(w, w or "按想定天候"),
        "visibility": f"能见度系数 {env.get('visibility_factor', 1.0)}",
    }


def _mean(rows: list[dict], key: str) -> float:
    vals = [float(r[key]) for r in rows if key in r and r[key] is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _deduce_worker(job_id: str, mission: dict, cfg: dict, n_runs: int) -> None:
    def upd(**kw):
        with _JOBS_LOCK:
            _JOBS[job_id].update(kw)

    try:
        upd(status="running", stage="加载想定与保障配置", progress=0.05)
        from ..data.provider import YamlFileProvider
        from ..surrogate.design import apply_features, FEATURE_NAMES
        import numpy as np

        sc = YamlFileProvider().load_scenario(mission["base_scenario"])
        # 双方实力注入：GUI 录入的红蓝编成/空中架次/方别乘子覆盖想定默认。
        # 放在 apply_features 之前，使情景乘子(red_pk_scale 等)在用户设定基础上继续累乘。
        sc, forces_report = force_config.inject(sc, mission.get("forces") or {})
        if forces_report.get("injected"):
            upd(stage="双方实力配置已注入", progress=0.09)
        feat_vals: list[float] = []
        for name in FEATURE_NAMES:
            if name in cfg:
                feat_vals.append(float(cfg[name]))
            else:
                feat_vals.append(float(getattr(sc, name, 1.0 if name in
                                  ("mtbf_scale", "red_pk_scale", "blue_pk_scale") else 0)))
        try:
            sc = apply_features(sc, np.asarray(feat_vals, dtype=float))
        except Exception as exc:  # 配置叠加失败不阻断推演
            print(f"[support] apply_features 失败，使用原想定：{exc}", flush=True)

        upd(stage=f"蒙特卡洛推演 {n_runs} 次复制", progress=0.15)
        from ..experiment.runner import run_replications

        rows, frames = run_replications(
            sc, n_runs=n_runs, base_seed=int(mission.get("seed", 20261013)),
            keep_events_of=0,
        )
        upd(stage="生成逐件战损清单", progress=0.65)

        snap_df, eng_df = frames if frames else (None, None)
        loss = battle_loss.from_event_frames(snap_df, eng_df, scenario=sc, n_runs=n_runs)
        metrics = {
            "n_reps": len(rows),
            "attrition_red": _mean(rows, "attrition_red"),
            "readiness_red": _mean(rows, "readiness_red"),
            "attrition_blue": _mean(rows, "attrition_blue"),
            "success": sum(1 for r in rows if r.get("success")) / len(rows) if rows else 0.0,
        }
        # ⚠️ 对账基准必须取「清单所属的那次复制」（keep_events_of=0 → rows[0]），
        # 不能用 N 次均值——清单是单次的逐件事实，均值是跨次统计，两者口径不同。
        core = {"attrition_red": rows[0].get("attrition_red", 0.0) if rows else 0.0,
                "readiness_red": rows[0].get("readiness_red", 0.0) if rows else 0.0}
        # 事件流无损伤记录时，用 MC 汇总按类别估算（明确标注 source=estimate）
        if not loss.get("items"):
            loss = battle_loss.from_metrics({"summary": metrics, "n_reps": len(rows)}, mission)
        rates = battle_loss.rates_both_doctrines(loss, core_rates=core)
        loss["rates"] = rates
        loss["metrics"] = metrics
        # P0-2：逐次复制明细 + 95%CI（来自同一批 rows，纯派生）
        loss["replication"] = battle_loss.replication_ci(rows)
        # P0-5：业务环节状态时间线（从保留的事件帧派生，纯展示不影响四率）
        try:
            loss["timeline"] = battle_loss.business_timeline(snap_df)
        except Exception as _te:  # 派生失败不阻断主链路
            loss["timeline"] = {"stages": [], "tracks": [], "source": "error",
                                "note": f"环节时间线派生失败：{type(_te).__name__}: {_te}"}
        loss["mission_id"] = mission.get("mission_id", "")
        loss["task_name"] = mission.get("task_name", "")
        loss["scenario"] = mission["base_scenario"]
        loss["base_seed"] = int(mission.get("seed", 20261013))  # P1-6 复现记录
        loss["scenario_info"] = _scenario_info(mission["base_scenario"])
        loss["forces_report"] = forces_report
        lid = battle_loss.save(loss)
        upd(status="done", stage="完成", progress=1.0,
            loss_id=lid, n_items=len(loss.get("items") or []),
            checklist_rate=rates.get("reconciliation", {}).get("passed"))
        print(f"[support] 推演完成 job={job_id} loss={lid} items={len(loss.get('items') or [])}",
              flush=True)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        upd(status="error", stage="失败", progress=1.0, error=f"{type(exc).__name__}: {exc}")


def deduce_start(payload: dict) -> dict:
    mid = str(payload.get("mission_id") or "").strip()
    mission = mission_input.load(mid) if mid else mission_input.latest()
    if mission is None:
        return {"error": "请先录入任务与资源（POST /api/support/mission）"}
    n_runs = int(payload.get("n_runs") or mission.get("n_runs", 12))
    n_runs = max(1, min(n_runs, 60))
    # P1-6：base_seed 显式覆盖并回写录入，保证多次推演可复现
    base_seed = payload.get("base_seed")
    if base_seed not in (None, ""):
        try:
            seed_val = int(base_seed)
            if 0 <= seed_val <= 2**31 - 1:
                mission["seed"] = seed_val
                mission_input.save(mission)  # 回写，档案与复现记录一致
            else:
                return {"error": "base_seed 必须在 [0, 2147483647]"}
        except (TypeError, ValueError):
            return {"error": "base_seed 必须是整数"}
    job_id = uuid.uuid4().hex[:10]
    with _JOBS_LOCK:
        if len(_JOBS) >= _MAX_JOBS:
            for k in list(_JOBS)[:_MAX_JOBS // 2]:
                if _JOBS[k].get("status") in ("done", "error"):
                    del _JOBS[k]
        _JOBS[job_id] = {"job_id": job_id, "status": "queued", "stage": "排队中",
                         "progress": 0.0, "mission_id": mission.get("mission_id", ""),
                         "n_runs": n_runs, "base_seed": int(mission.get("seed", 20261013)),
                         "created": time.strftime("%H:%M:%S")}
    cfg = mission_input.to_run_config(mission)
    threading.Thread(target=_deduce_worker, args=(job_id, mission, cfg, n_runs),
                     daemon=True).start()
    print(f"[support] 启动多次推演 job={job_id} n_runs={n_runs} scenario={mission['base_scenario']}",
          flush=True)
    return {"job_id": job_id, "status": "queued", "n_runs": n_runs}


def deduce_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = dict(_JOBS.get(job_id) or {})
    return job or {"error": "job_id 不存在"}


def deduce_list() -> dict:
    with _JOBS_LOCK:
        return {"jobs": sorted(_JOBS.values(), key=lambda x: x.get("created", ""), reverse=True)}


def loss_get(loss_id: str | None = None, job_id: str | None = None) -> dict:
    if job_id:
        with _JOBS_LOCK:
            job = dict(_JOBS.get(job_id) or {})
        loss_id = job.get("loss_id") or loss_id
    loss = battle_loss.load(loss_id) if loss_id else battle_loss.latest()
    if loss is None:
        return {"loss": None, "message": "尚无战损清单，请先执行多次推演"}
    return {"loss": loss, "rates": loss.get("rates") or
            battle_loss.rates_from_loss_list(loss)}


# ---------------- 方案 ----------------

def plan_generate(payload: dict) -> dict:
    mid = str(payload.get("mission_id") or "").strip()
    lid = str(payload.get("loss_id") or "").strip()
    mission = mission_input.load(mid) if mid else mission_input.latest()
    loss = battle_loss.load(lid) if lid else battle_loss.latest()
    if mission is None or loss is None:
        return {"error": "需要已录入的任务与战损清单"}
    # P3 硬阻断：仅当 GUI 显式录入约束时，硬约束违反即拒绝生成方案
    if mission.get("constraints_configured"):
        _cc = constraints.check_feasibility(mission, mission.get("constraints"), loss=loss)
        if _cc["n_fail"]:
            return {"error": "硬约束校核未通过，方案生成已阻断",
                    "constraint_check": _cc,
                    "blocking": _cc["blocking"]}
    use_llm = bool(payload.get("use_llm", True))
    template = str(payload.get("template") or plan_mod.DEFAULT_TEMPLATE)
    llm_params = payload.get("llm_params") or {}  # P1-3
    if not isinstance(llm_params, dict):
        llm_params = {}
    result = plan_mod.generate(
        mission, loss,
        rates=loss.get("rates"), metrics=loss.get("metrics"),
        scenario_info=loss.get("scenario_info") or _scenario_info(mission["base_scenario"]),
        template=template, use_llm=use_llm, llm_params=llm_params,
    )
    result["plan_id"] = plan_mod.save(result)
    return {"plan": result}


def plan_override(payload: dict) -> dict:
    """P1-2：人工覆写 LLM 叙述槽位并锁定。

    只接受 source=llm 的槽位；数字槽位一律拒绝覆写（数字权威来自清单/录入）。
    覆写后重新填模板、重新做数字一致性复核与溯源，另存为新方案（不覆盖原稿）。
    """
    pid = str(payload.get("plan_id") or "").strip()
    plan = plan_mod.load(pid) if pid else plan_mod.latest()
    if not plan:
        return {"error": "无可用方案"}
    overrides = payload.get("overrides") or {}
    if not isinstance(overrides, dict):
        return {"error": "overrides 必须是对象"}
    try:
        updated = plan_mod.apply_overrides(plan, overrides,
                                           lock=bool(payload.get("lock", True)))
    except ValueError as exc:
        return {"error": str(exc)}
    updated["based_on"] = plan.get("plan_id", "")
    new_id = plan_mod.save(updated)
    updated["plan_id"] = new_id
    return {"plan": updated}


def archive_list() -> dict:
    """P1-8：作业档案列表（录入/战损/方案三类落盘产物的索引）。"""
    def scan(folder: Path, prefix: str, key: str) -> list[dict]:
        out = []
        for f in sorted(folder.glob(f"{prefix}_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            saved = (d.get("saved_at") or d.get("generated_at") or
                     time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)))
            out.append({
                "id": d.get(key, f.stem),
                "task_name": d.get("task_name") or d.get("plan_name") or "",
                "scenario": d.get("scenario") or d.get("base_scenario", ""),
                "saved_at": saved,
                "file": f.name,
            })
        return out

    missions = scan(_SUPPORT_DIR / "missions", "mission", "mission_id")
    losses = scan(_SUPPORT_DIR / "loss", "loss", "loss_id")
    plans = scan(_SUPPORT_DIR / "plans", "plan", "plan_id")
    # 给战损/方案档案补关键指标，便于横向对比
    for p in plans:
        pf = _SUPPORT_DIR / "plans" / f"plan_{p['id']}.json"
        lid = ""
        if pf.is_file():
            try:
                pd = json.loads(pf.read_text(encoding="utf-8"))
                lid = pd.get("loss_id", "")
            except Exception:
                pass
        l = battle_loss.load(lid) if lid else None
        rates = (l or {}).get("rates") or {}
        p["attrition"] = rates.get("attrition")
        p["validation_passed"] = None
        pf = _SUPPORT_DIR / "plans" / f"plan_{p['id']}.json"
        if pf.is_file():
            try:
                p["validation_passed"] = json.loads(pf.read_text(encoding="utf-8")).get("validation", {}).get("passed")
            except Exception:
                pass
    for l in losses:
        full = battle_loss.load(l["id"]) or {}
        rates = full.get("rates") or {}
        l["attrition"] = rates.get("attrition")
        l["n_items"] = len(full.get("items") or [])
        l["base_seed"] = full.get("base_seed")
    return {"missions": missions, "losses": losses, "plans": plans,
            "counts": {"missions": len(missions), "losses": len(losses), "plans": len(plans)}}


def plan_get(plan_id: str | None = None) -> dict:
    p = plan_mod.load(plan_id) if plan_id else plan_mod.latest()
    return {"plan": p} if p else {"plan": None, "message": "尚无生成方案"}


def template_upsert(payload: dict) -> dict:
    name = str(payload.get("name") or "custom_support_plan.md")
    text = str(payload.get("text") or "")
    if not text:
        return {"error": "模板正文不能为空"}
    try:
        saved = plan_mod.save_template(name, text)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"name": saved, "templates": plan_mod.list_templates()}


def template_detail(name: str | None = None) -> dict:
    """P1-1：模板正文 + 槽位映射可视化（已使用/字典中存在/未知槽位）。"""
    import re
    name = str(name or plan_mod.DEFAULT_TEMPLATE)
    try:
        text = plan_mod.read_template(name)
    except Exception as exc:
        return {"error": f"读取模板失败：{type(exc).__name__}: {exc}"}
    used = sorted(set(re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", text)))
    known = set(SLOT_DICT)
    return {
        "name": name, "text": text,
        "slots": [{"key": k, "used": k in used,
                   "label": SLOT_DICT.get(k, {}).get("label", ""),
                   "source": SLOT_DICT.get(k, {}).get("source", "")}
                  for k in sorted(known)],
        "used_slots": used,
        "unknown_slots": [k for k in used if k not in known],
        "unused_slots": sorted(known - set(used)),
        "count_used": len(used), "count_total": len(known),
    }


def export(payload: dict) -> dict:
    """返回可下载的产物路径（前端用 /data 或 /static 不方便，这里直接回传文本）。"""
    what = str(payload.get("what") or "loss")
    if what == "loss":
        loss = battle_loss.load(str(payload.get("loss_id") or "")) if payload.get("loss_id") else battle_loss.latest()
        if not loss:
            return {"error": "无战损清单"}
        import csv
        import io
        buf = io.StringIO()
        cols = ["loss_id", "tick", "side", "unit_id", "platform", "category", "org",
                "grade", "damage_part", "cause", "weapon", "disposition",
                "repair_status", "est_repair_ticks", "location", "source"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for it in loss.get("items") or []:
            w.writerow(it)
        return {"filename": f"战损清单_{loss.get('loss_id', '')}.csv",
                "content": buf.getvalue(), "format": "csv"}
    if what == "plan":
        p = plan_mod.load(str(payload.get("plan_id") or "")) if payload.get("plan_id") else plan_mod.latest()
        if not p:
            return {"error": "无保障方案"}
        return {"filename": f"保障方案_{p.get('plan_id', '')}.md",
                "content": p.get("text", ""), "format": "md"}
    if what == "plan_docx":
        # P0-7：公文式 Word 导出，base64 回传（前端用 Blob 下载，不改服务静态路由）
        import base64
        p = plan_mod.load(str(payload.get("plan_id") or "")) if payload.get("plan_id") else plan_mod.latest()
        if not p:
            return {"error": "无保障方案"}
        data = plan_mod.to_docx_bytes(p)
        return {"filename": f"保障方案_{p.get('plan_id', '')}.docx",
                "content_b64": base64.b64encode(data).decode("ascii"),
                "format": "docx", "size": len(data)}


# ================= P2 进阶能力 =================

def compare_scenarios() -> dict:
    """P2-1：批量工况对比。

    纯档案聚合：扫描已落盘的战损清单，按 scenario 取每个工况最近一次推演的
    战损率/完好率/条目数，并补齐地形气象文本。**不重新推演**，不改变权威数字。
    同时列出目录中尚无推演档案的工况，避免"未推演被误读为零战损"。
    """
    rows: dict[str, dict] = {}
    ldir = _SUPPORT_DIR / "loss"
    for f in sorted(ldir.glob("loss_*.json"), key=lambda x: x.stat().st_mtime):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        sc = str(d.get("scenario") or "")
        if not sc:
            continue
        rates = d.get("rates") or {}
        info = d.get("scenario_info") or _scenario_info(sc)
        rows[sc] = {
            "scenario": sc,
            "loss_id": d.get("loss_id", f.stem[5:]),
            "attrition": rates.get("attrition"),
            "damage_rate": rates.get("damage_rate"),
            "readiness": rates.get("readiness"),
            "n_items": len(d.get("items") or []),
            "n_runs": d.get("n_runs"),
            "base_seed": d.get("base_seed"),
            "terrain": info.get("terrain", ""),
            "weather": info.get("weather", ""),
            "visibility": info.get("visibility", ""),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
            "source": "archive",
        }
    compared = sorted(rows.values(), key=lambda x: (x.get("attrition") is None,
                                                     -(x.get("attrition") or 0)))
    all_sc = set(CATALOG.get("scenarios") or [])
    compared_sc = set(rows)
    return {
        "scenarios": compared,
        "not_deduced": sorted(all_sc - compared_sc),
        "count": len(compared),
        "note": "对比来自已归档推演（source=archive），未重新推演；'not_deduced' 工况没有档案，不代表零战损。",
    }


def plan_compare(payload: dict) -> dict:
    """P2-2：方案版本对比（最多 4 份）。槽位差异 + 文本行级 diff。"""
    import difflib
    ids = [str(x).strip() for x in (payload.get("plan_ids") or []) if str(x).strip()][:4]
    if len(ids) < 2:
        return {"error": "至少选择 2 份方案进行对比"}
    plans = []
    for pid in ids:
        p = plan_mod.load(pid)
        if not p:
            return {"error": f"方案不存在：{pid}"}
        plans.append(p)
    base = plans[0]
    base_slots = base.get("slots") or {}
    slot_diffs: list[dict] = []
    for key in sorted(base_slots):
        vals = [str((p.get("slots") or {}).get(key, "")) for p in plans]
        if len(set(vals)) > 1:
            slot_diffs.append({"key": key,
                               "label": SLOT_DICT.get(key, {}).get("label", key),
                               "values": vals})
    base_lines = str(base.get("text", "")).splitlines()
    text_diffs: list[dict] = []
    for p in plans[1:]:
        diff = list(difflib.unified_diff(base_lines, str(p.get("text", "")).splitlines(),
                                         fromfile=base.get("plan_id", "A"),
                                         tofile=p.get("plan_id", "B"), lineterm=""))
        text_diffs.append({"against": p.get("plan_id", ""), "lines": diff[:400]})
    return {
        "plans": [{"plan_id": p.get("plan_id", ""), "plan_name": p.get("plan_name") or
                   (p.get("slots") or {}).get("plan_name", ""),
                   "generated_at": p.get("generated_at", ""),
                   "validation_passed": (p.get("validation") or {}).get("passed"),
                   "review_status": p.get("review_status", "draft"),
                   "narrative_locked": bool(p.get("narrative_locked"))} for p in plans],
        "slot_diffs": slot_diffs,
        "text_diffs": text_diffs,
        "count": len(plans),
    }


def plan_review(payload: dict) -> dict:
    """P2-3：方案签批工作流。状态机校验 + 流转日志，落盘方案。"""
    pid = str(payload.get("plan_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    plan = plan_mod.load(pid) if pid else plan_mod.latest()
    if not plan:
        return {"error": "无可用方案"}
    rule = REVIEW_ACTIONS.get(action)
    if not rule:
        return {"error": f"未知签批动作：{action}；可选 {sorted(REVIEW_ACTIONS)}"}
    cur = plan.get("review_status") or "draft"
    if cur not in rule["from"]:
        return {"error": f"当前状态 {cur} 不允许动作 {action}（允许来源 {rule['from']}）"}
    log = list(plan.get("review_log") or [])
    log.append({"action": action, "name": rule["name"], "from": cur, "to": rule["to"],
                "note": str(payload.get("note") or ""),
                "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    plan["review_status"] = rule["to"]
    plan["review_log"] = log
    plan_mod.save(plan)
    return {"plan": plan, "transition": f"{cur} → {rule['to']}", "action_name": rule["name"]}


def recompute_from(payload: dict) -> dict:
    """P2-7：环节级回退重算——用已有 mission/loss 重算下游，不重跑仿真。

    stage ∈ {rates, plan, review}：
      - rates：从战损清单重派生四率并落新清单；
      - plan：从现有任务/清单重新生成方案（数字槽位确定性重填）；
      - review：对现有方案重新做数字一致性复核与溯源。
    前 3 个环节（input/validate/deduce/loss）需重跑仿真，不在本端点内，前端引导走推演。
    """
    stage = str(payload.get("stage") or "").strip()
    lid = str(payload.get("loss_id") or "").strip()
    mid = str(payload.get("mission_id") or "").strip()
    pid = str(payload.get("plan_id") or "").strip()
    if stage == "rates":
        loss = battle_loss.load(lid) if lid else battle_loss.latest()
        if not loss:
            return {"error": "无战损清单可重算"}
        core_rates = (loss.get("rates") or {}).get("reconciliation", {}).get("core") or None
        rates = battle_loss.rates_both_doctrines(loss, core_rates=core_rates)
        loss["rates"] = rates
        new_id = battle_loss.save(loss)
        return {"stage": "rates", "loss_id": new_id, "rates": rates,
                "note": "四率已从清单重派生；下游方案需重新生成"}
    if stage == "plan":
        mission = mission_input.load(mid) if mid else mission_input.latest()
        loss = battle_loss.load(lid) if lid else battle_loss.latest()
        if not mission or not loss:
            return {"error": "需要已录入的任务与战损清单"}
        result = plan_mod.generate(
            mission, loss, rates=loss.get("rates"), metrics=loss.get("metrics"),
            scenario_info=loss.get("scenario_info") or _scenario_info(mission["base_scenario"]),
            template=str(payload.get("template") or plan_mod.DEFAULT_TEMPLATE),
            use_llm=bool(payload.get("use_llm", False)),
            llm_params=payload.get("llm_params") or {},
        )
        new_id = plan_mod.save(result)
        result["plan_id"] = new_id
        return {"stage": "plan", "plan_id": new_id,
                "validation_passed": (result.get("validation") or {}).get("passed"),
                "note": "方案已从当前任务/清单重新生成（数字槽位确定性重填）"}
    if stage == "review":
        plan = plan_mod.load(pid) if pid else plan_mod.latest()
        if not plan:
            return {"error": "无可用方案"}
        slots = plan.get("slots") or {}
        text = plan.get("text", "")
        plan["validation"] = plan_mod.validate_document(text, slots)
        plan["provenance"] = plan_mod.build_provenance(slots, text)
        plan["rechecked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        plan_mod.save(plan)
        return {"stage": "review", "plan_id": plan.get("plan_id", ""),
                "validation": plan["validation"], "note": "数字一致性复核与溯源已重跑"}
    return {"error": f"环节 {stage} 需重跑仿真，请走'多次推演'；本端点仅支持 rates/plan/review"}


# ---------------- P2 边界补齐：成本约束 Pareto 选优 ----------------

def pareto_recommend(payload: dict) -> dict:
    """按录入的三维权重在 74 方案 Pareto 前沿内加权选优（只读派生，不重跑仿真）。

    权重优先取 payload.weights；未传则取最近一次录入的 mission.cost_weights；
    再缺省为等权。cost_ceiling 与方案 cost 同量纲（0-1）；budget_limit(万元)不直接参与。
    """
    payload = payload or {}
    weights = payload.get("weights")
    scenario = str(payload.get("scenario") or "").strip() or None
    mid = str(payload.get("mission_id") or "").strip()
    mission = mission_input.load(mid) if mid else mission_input.latest()
    if not weights:
        if mission and mission.get("cost_weights"):
            weights = mission["cost_weights"]
    try:
        ceiling = payload.get("cost_ceiling")
        ceiling = None if ceiling in (None, "") else float(ceiling)
    except (TypeError, ValueError):
        ceiling = None
    try:
        top_n = int(payload.get("top_n", 5))
    except (TypeError, ValueError):
        top_n = 5
    # P3：仅当 GUI 显式录入约束时才启用硬筛选（缺省零漂移）
    cons = None
    if mission and mission.get("constraints_configured"):
        cons = mission.get("constraints")
    if payload.get("constraints") is not None:
        cons = payload.get("constraints")
    return optimize_pareto.recommend(weights, cost_ceiling=ceiling,
                                     scenario=scenario, top_n=top_n, constraints=cons)


def pareto_region(payload: dict) -> dict:
    """二维可行域投影数据：导出 74 方案原维度 + GUI 约束可行性（只读派生）。

    约束来源与 pareto_recommend 一致：优先 payload.constraints，其次最近录入且
    constraints_configured 的 mission。缺省无约束 → 全部可行（零漂移）。
    """
    payload = payload or {}
    scenario = str(payload.get("scenario") or "").strip() or None
    mid = str(payload.get("mission_id") or "").strip()
    mission = mission_input.load(mid) if mid else mission_input.latest()
    cons = None
    if mission and mission.get("constraints_configured"):
        cons = mission.get("constraints")
    if payload.get("constraints") is not None:
        cons = payload.get("constraints")
    try:
        ceiling = payload.get("cost_ceiling")
        ceiling = None if ceiling in (None, "") else float(ceiling)
    except (TypeError, ValueError):
        ceiling = None
    return optimize_pareto.feasible_region(scenario=scenario, constraints=cons,
                                           cost_ceiling=ceiling)


def pareto_apply(payload: dict) -> dict:
    """一键采用选优方案：把 Pareto 推荐参数写入新方案的附节字段（另存，不覆盖原稿）。

    只在方案 JSON 增加 pareto_selection 结构化字段（7维决策+得分+锚点对照），
    不触碰数字槽位/权威指标，不重跑仿真。正文附节由前端渲染展示，不写死进模板正文，
    以保持"数字槽位确定性"不被污染。
    """
    pid = str(payload.get("plan_id") or "").strip()
    plan = plan_mod.load(pid) if pid else plan_mod.latest()
    if not plan:
        return {"error": "无可用方案，请先生成方案"}
    selection = payload.get("selection")
    if not isinstance(selection, dict) or not selection:
        return {"error": "selection 必须是选中的方案对象"}
    rec = optimize_pareto.recommend(
        payload.get("weights"),
        cost_ceiling=payload.get("cost_ceiling"),
        scenario=payload.get("scenario") or None,
        top_n=5,
    )
    clean = {k: v for k, v in selection.items() if not k.startswith("_")}
    plan["pareto_selection"] = {
        "decision": {k: clean.get(k) for k in
                     ("red_repair_teams", "red_arv", "red_supply_veh", "carry_level",
                      "operating_level", "wave_interval_ticks", "wave_qty")},
        "score": clean.get("_score"),
        "dimension_scores": clean.get("_scores") or {},
        "effective_weights": rec.get("effective_weights"),
        "cost_ceiling": rec.get("cost_ceiling"),
        "anchors": rec.get("anchors"),
        "source": rec.get("source"),
        "selected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "成本约束 Pareto 选优结论（既有前沿内按偏好排序，非新权威数字）",
    }
    plan["based_on"] = plan.get("plan_id", "")
    new_id = plan_mod.save(plan)
    plan["plan_id"] = new_id
    return {"plan_id": new_id, "pareto_selection": plan["pareto_selection"],
            "note": "选优参数已作为附节写入新方案（另存），数字槽位与权威指标未改"}


# ================= 抢修方案 / 人工 Word 战损清单导入 =================

def loss_template_docx() -> dict:
    """返回基层填报用《战损清单上报模板.docx》的 base64（前端 Blob 下载）。"""
    import base64
    data = loss_import.build_report_template_docx()
    return {
        "filename": loss_import.MANUAL_DOCX_TEMPLATE,
        "content_b64": base64.b64encode(data).decode("ascii"),
        "format": "docx",
        "size": len(data),
        "note": "按表头填写真实战损（示例行请删除），上传后自动生成抢修方案",
    }


def loss_import_docx(payload: dict) -> dict:
    """解析人工上传的 Word 战损清单 → 落盘为内部清单（source=manual_docx）。

    payload:
        content_b64 / content: Word base64（或 data URL）；
        roster_size: 参战总数（可选，缺省 0 → 不臆造战损率分母）；
        note: 填报说明。
    """
    raw = payload.get("content_b64") or payload.get("content") or ""
    if not raw:
        return {"error": "缺少 Word 内容（content_b64）"}
    roster_size = 0
    try:
        roster_size = int(payload.get("roster_size", 0) or 0)
    except (TypeError, ValueError):
        return {"error": "roster_size 必须是整数"}
    try:
        loss = loss_import.parse_loss_docx(
            raw, roster_size=roster_size, note=str(payload.get("note") or ""))
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc)}
    lid = loss_import.save_imported(loss)
    loss["loss_id"] = lid
    return {
        "loss_id": lid,
        "source": loss["source"],
        "n_items": len(loss["items"]),
        "summary": loss["summary"],
        "warnings": loss.get("warnings", []),
        "items": loss["items"],
        "note": loss["note"],
    }


def repair_plan_generate(payload: dict) -> dict:
    """抢修方案生成：仿真清单 / 人工 Word 清单同构接入，LLM 只写叙述。"""
    mid = str(payload.get("mission_id") or "").strip()
    lid = str(payload.get("loss_id") or "").strip()
    mission = mission_input.load(mid) if mid else mission_input.latest()
    loss = battle_loss.load(lid) if lid else battle_loss.latest()
    if mission is None or loss is None:
        return {"error": "需要已录入的任务与战损清单（仿真或人工导入均可）"}
    llm_params = payload.get("llm_params") or {}
    if not isinstance(llm_params, dict):
        llm_params = {}
    result = repair_plan.generate(
        mission, loss,
        rates=loss.get("rates"), metrics=loss.get("metrics"),
        scenario_info=loss.get("scenario_info") or _scenario_info(mission.get("base_scenario", "")),
        use_llm=bool(payload.get("use_llm", True)),
        llm_params=llm_params,
        template=str(payload.get("template") or repair_plan.DEFAULT_REPAIR_TEMPLATE),
    )
    result["plan_id"] = plan_mod.save(result)
    return {"plan": result, "loss_source": result["loss_source"]}
