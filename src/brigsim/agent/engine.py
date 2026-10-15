"""装备保障智能参谋的推理内核（规则/模板版，接口按真实 LLM 预留）。

链路对齐"大模型函数调用调度仿真"的故事线：
  自然语言 → 意图识别 → 工具选择 → 真实工具执行（代理预测/Pareto/复验数据）
          → 中文参谋语言生成 → 本地推理服务留痕（token/延迟/工具）

C7-a3：旋钮口径升级为 a2 实体化 7 决策（抢修组/抢救车/器材车/携行档/
运行档/波次节拍/每波件数）。原型边界：意图识别为关键词/正则规则，
生成是模板；**工具返回的每一个数字都真实**。接真实国产大模型时替换本类的
route/render 两步，AgentToolkit 与 LocalLLMService 不变。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ..optimize.problem import DECISION_VARS
from ..surrogate.design import FEATURES
from .service import LocalLLMService
from .tools import (
    KEY_METRICS,
    KNOB_LABELS,
    METRIC_META,
    PLAN_TAGS,
    TICK_SECONDS,
    AgentToolkit,
)

_BOUNDS = {name: (lo, hi) for name, lo, hi, _ in FEATURES}
_CN_DIGITS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_D = r"(\d+|[一两二三四五六七八九十]+)"
# 关键词与数字之间的分隔符：连中文数字也排除，避免贪婪量词吞掉"三十"的"三"
_SEP = r"[^。？?0-9一两二三四五六七八九十]"

# 决策旋钮展示单位
_UNITS = {
    "red_repair_teams": "个",
    "red_arv": "辆",
    "red_supply_veh": "辆",
    "carry_level": "档",
    "operating_level": "档",
    "wave_interval_ticks": "tick",
    "wave_qty": "件",
}
# 工具参数名 → 决策旋钮名
_ARG_TO_KNOB = {
    "teams": "red_repair_teams",
    "arv": "red_arv",
    "supply_veh": "red_supply_veh",
    "carry": "carry_level",
    "operating": "operating_level",
    "interval_ticks": "wave_interval_ticks",
    "wave_qty": "wave_qty",
}


@dataclass
class ToolCall:
    name: str
    arguments: dict
    elapsed_ms: float
    summary: str


@dataclass
class AgentReply:
    answer: str
    intent: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def as_dict(self) -> dict:
        return {
            "answer": self.answer,
            "intent": self.intent,
            "tool_calls": [tc.__dict__ for tc in self.tool_calls],
            "latency_ms": round(self.latency_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


def _to_int(token: str) -> int | None:
    token = token.strip()
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        a, _, b = token.partition("十")
        return (_CN_DIGITS.get(a, 1) if a else 1) * 10 + (_CN_DIGITS.get(b, 0) if b else 0)
    return _CN_DIGITS.get(token)


def parse_knobs(text: str) -> dict[str, int]:
    """从中文句子中抽取参谋显式说出的 a2 口径保障旋钮（未提及的键不返回）。"""
    found: dict[str, int] = {}

    def grab(pattern: str, group: int = 1) -> int | None:
        m = re.search(pattern, text)
        return _to_int(m.group(group)) if m else None

    v = (
        grab(rf"{_D}\s*个?\s*(?:前沿)?抢修组")
        or grab(rf"抢修组{_SEP}{{0,8}}{_D}\s*个")
    )
    if v is not None:
        found["red_repair_teams"] = v
    v = (
        grab(rf"{_D}\s*辆?\s*(?:装甲)?抢救车")
        or grab(rf"抢救车{_SEP}{{0,8}}{_D}\s*辆")
    )
    if v is not None:
        found["red_arv"] = v
    # 器材补给车（"弹药补给车"在别处，须排除）
    v = (
        grab(rf"{_D}\s*辆?\s*器材(?:补给)?车")
        or grab(rf"器材(?:补给)?车{_SEP}{{0,8}}{_D}\s*辆")
    )
    if v is not None:
        found["red_supply_veh"] = v
    v = grab(rf"携行档(?:基数)?{_SEP}{{0,6}}{_D}\s*[档级]")
    if v is None:
        v = grab(rf"{_D}\s*[档级]\s*携行")
    if v is not None:
        found["carry_level"] = v
    v = grab(rf"运行档(?:基数)?{_SEP}{{0,6}}{_D}\s*[档级]")
    if v is None:
        v = grab(rf"{_D}\s*[档级]\s*运行")
    if v is not None:
        found["operating_level"] = v
    m = (
        re.search(rf"(?:前送|补给|波次)?\s*(?:周期|间隔|节拍){_SEP}{{0,8}}{_D}\s*(tick|分钟|分|min)",
                  text, re.IGNORECASE)
        or re.search(rf"每\s*{_D}\s*(tick|分钟|分|min)\s*(?:一个波次|前送|补给|波次)",
                     text, re.IGNORECASE)
    )
    if m:
        v = _to_int(m.group(1))
        if v is not None:
            unit = m.group(2).lower()
            found["wave_interval_ticks"] = v * 2 if unit in ("分钟", "分", "min") else v
    v = (
        grab(rf"每波(?:每部件)?{_SEP}{{0,6}}{_D}\s*件")
        or grab(rf"每部件{_SEP}{{0,4}}{_D}\s*件")
        or grab(rf"波次?批量{_SEP}{{0,6}}{_D}\s*件")
    )
    if v is not None:
        found["wave_qty"] = v
    # 截回代理模型可行域并整数化
    return {
        k: int(max(_BOUNDS[k][0], min(_BOUNDS[k][1], float(val))))
        for k, val in found.items()
    }


class AgentEngine:
    """意图识别 → 工具调度 → 答复生成。"""

    def __init__(
        self,
        toolkit: AgentToolkit | None = None,
        service: LocalLLMService | None = None,
        simulate_delay: bool = True,
    ) -> None:
        self.tools = toolkit or AgentToolkit()
        self.service = service or LocalLLMService(simulate_delay=simulate_delay)

    # ---- 对外 ----

    def ask(self, text: str) -> AgentReply:
        t0 = time.perf_counter()
        intent, args = self.route(text)
        answer = self.render(intent, args, text)
        completion_tokens = len(answer)
        self.service.maybe_delay(completion_tokens)
        latency_ms = (time.perf_counter() - t0) * 1000
        rec = self.service.record(
            kind=intent,
            prompt_tokens=len(text),
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            tool_calls=[tc.name for tc in args.get("calls", [])],
        )
        return AgentReply(
            answer=answer,
            intent=intent,
            tool_calls=args.get("calls", []),
            latency_ms=rec.latency_ms,
            prompt_tokens=rec.prompt_tokens,
            completion_tokens=rec.completion_tokens,
        )

    def plan(self, tag: str = "knee") -> AgentReply:
        """Web 一键生成按钮直连（等同对话中说"生成 knee 方案"）。"""
        return self.ask(f"请一键生成{PLAN_TAGS.get(tag, tag)}保障方案")

    # ---- 意图识别 ----

    def route(self, text: str) -> tuple[str, dict]:
        t = text.strip()
        low = t.lower()
        calls: list[ToolCall] = []

        if re.search(r"显存|推理服务|服务状态|部署情况|模型状态|运行状态|gpu", low):
            return "status", {"calls": calls}

        tag = ""
        if re.search(r"最省|省钱|成本最低|资源最少", t):
            tag = "leanest"
        elif re.search(r"最强|成功率最高|保障最强|最强保障|完成率最高", t):
            tag = "strongest"
        elif re.search(r"折中|knee|均衡|性价比", low):
            tag = "knee"

        knobs = parse_knobs(t)
        wants_plan = bool(re.search(r"方案|怎么配置|如何配置|配置建议|一键|推荐|最优", t))
        if wants_plan and tag:
            data = self._call(calls, "generate_support_plan", {"tag": tag})
            return "plan", {"calls": calls, "plan": data}
        whatif_words = "如果|假如|假设|要是|若|改成|调到|预测|预计|多少|几成"
        if knobs and (wants_plan or re.search(whatif_words, t)):
            data = self._call(calls, "compare_with_baseline", self._knob_args(knobs))
            return "whatif", {"calls": calls, "comparison": data, "knobs": knobs}

        # Pareto 方案集查询要先于通用"方案"关键词（"前沿有哪些方案"不是要生成方案）
        if re.search(r"pareto|帕累托|前沿|非支配|方案集|哪些方案|几套方案", low):
            data = self._call(calls, "query_pareto_front", {"tag": tag, "top_n": 8})
            return "pareto", {"calls": calls, "front": data}

        if wants_plan:
            data = self._call(calls, "generate_support_plan", {"tag": "knee"})
            return "plan", {"calls": calls, "plan": data}

        if re.search(r"四率|完好率|故障率|修复率|战损率", t) and re.search(
            r"什么是|定义|口径|含义|怎么算|如何计算|解释|指的是", t
        ):
            data = self._call(calls, "get_rate_definitions", {})
            return "rates_def", {"calls": calls, "defs": data}

        if re.search(r"对比|比较|相比", t) and re.search(r"基线|基准|现行|现有|v4|v3", low):
            data = self._call(calls, "generate_support_plan", {"tag": "knee"})
            return "compare", {"calls": calls, "plan": data}

        if re.search(r"你好|您好|hi|hello", low):
            return "greeting", {"calls": calls}
        if re.search(r"能做什么|帮助|功能|你会|怎么用|会什么", t):
            return "help", {"calls": calls}
        return "fallback", {"calls": calls}

    # ---- 答复生成 ----

    def render(self, intent: str, args: dict, text: str) -> str:
        if intent == "plan":
            return self._render_plan(args["plan"], full=True)
        if intent == "compare":
            return self._render_plan(args["plan"], full=False)
        if intent == "whatif":
            return self._render_whatif(args["comparison"], args["knobs"])
        if intent == "pareto":
            return self._render_pareto(args["front"])
        if intent == "rates_def":
            return self._render_defs(args["defs"])
        if intent == "status":
            return self._render_status()
        if intent in ("help", "greeting"):
            return self._render_help(greeting=intent == "greeting")
        return self._render_fallback(text)

    # ---- 各意图模板 ----

    def _config_lines(self, d: dict, b: dict) -> list[str]:
        return [
            f"- {KNOB_LABELS['red_repair_teams']}：{d['red_repair_teams']}"
            f"（基线 {b['red_repair_teams']}）",
            f"- {KNOB_LABELS['red_arv']}：{d['red_arv']}"
            f"（基线 {b['red_arv']}）",
            f"- {KNOB_LABELS['red_supply_veh']}：{d['red_supply_veh']}"
            f"（基线 {b['red_supply_veh']}）",
            f"- {KNOB_LABELS['carry_level']}：{d['carry_level']}"
            f"（基线 {b['carry_level']}，档级越高携行器材越多）",
            f"- {KNOB_LABELS['operating_level']}：{d['operating_level']}"
            f"（基线 {b['operating_level']}，营抢修点常备器材）",
        ]

    def _render_plan(self, p: dict, full: bool) -> str:
        d, b, dm = p["decisions"], p["baseline_decisions"], p["delta"]
        s = p["schedule"]
        head = {
            "knee": "已生成折中推荐保障方案（knee：归一化目标空间中距乌托邦点最近的最大折中点）",
            "strongest": "已生成保障最强方案（任务完成率最高的 Pareto 端点，代价是资源成本最高）",
            "leanest": "已生成资源最省方案（成本最低的 Pareto 端点，适合资源极度受限场景）",
        }[p["tag"]]
        lines = [
            head,
            f"（NSGA-II 在 {p['n_pareto_plans']} 个 Pareto 非支配方案中选出；"
            f"想定 {p['scenario']}，任务时长 {p['duration_minutes']:.1f} 分钟，"
            f"1 tick={TICK_SECONDS}s）",
            "",
            "**一、保障力量配置（参谋可直接执行）**",
            *self._config_lines(d, b),
            f"- 前送波次：每 {s['interval_minutes']:.1f} 分钟一个波次，每波每部件"
            f" {d['wave_qty']} 件（共 {s['per_wave_parts']} 件）；"
            f"任务内预计 {s['estimated_trips']} 个波次、共前送 "
            f"{s['estimated_resupply_qty']} 件"
            f"（基线：{b['wave_interval_ticks'] * TICK_SECONDS / 60:.1f} 分钟一波、"
            f"每部件 {b['wave_qty']} 件）",
            "",
            "**二、预期效能（代理模型秒级预测 vs v4 基线）**",
            "| 指标 | 推荐方案 | 基线 | 变化 |",
            "| --- | --- | --- | --- |",
        ]
        lines += self._metric_rows(
            p["metrics"], p["baseline_metrics"], dm, include_cost=True, cost=p["cost"]
        )
        if full:
            lines += [
                "",
                "**三、方案逻辑**",
                self._plan_logic(p),
                "",
                "**四、校准与风险（诚实声明）**",
                self._validation_notes(p),
                "- 全部输入参数为开源文献整编的原型值（置信度标注见参数目录），"
                "真实保障数据按 data-interface v0.1 接入并重训代理模型后，"
                "方案可直接刷新；方法不变、可推广。",
            ]
        return "\n".join(lines)

    def _plan_logic(self, p: dict) -> str:
        d, b = p["decisions"], p["baseline_decisions"]
        bits = []
        if d["red_repair_teams"] != b["red_repair_teams"]:
            arrow = "增" if d["red_repair_teams"] > b["red_repair_teams"] else "压减"
            bits.append(f"抢修组 {arrow}至 {d['red_repair_teams']} 个")
        if d["red_arv"] != b["red_arv"]:
            arrow = "增配" if d["red_arv"] > b["red_arv"] else "减配"
            bits.append(f"装甲抢救车{arrow} {d['red_arv']} 辆（拖救 M 级失动装备）")
        if d["red_supply_veh"] != b["red_supply_veh"]:
            bits.append(f"器材补给车调整为 {d['red_supply_veh']} 辆")
        if d["carry_level"] != b["carry_level"]:
            bits.append(f"携行档基数调到 {d['carry_level']} 档")
        if d["operating_level"] != b["operating_level"]:
            bits.append(f"运行档基数调到 {d['operating_level']} 档")
        if d["wave_interval_ticks"] != b["wave_interval_ticks"] or d["wave_qty"] != b["wave_qty"]:
            bits.append(
                f"波次节拍 {d['wave_interval_ticks']}tick、每波每部件 {d['wave_qty']} 件"
            )
        body = "；".join(bits) if bits else "力量配置与基线持平"
        finding = f"前沿扫描结论：{p['front_finding']}。"
        tail = {
            "knee": "knee 点在任务完成率、红方战损率与资源成本三者间最大折中，为默认推荐。",
            "strongest": "该端点不计成本堆足保障，适用于关键方向、任务必须完成的底线场景。",
            "leanest": "该端点资源投入最低，任务完成率也明显偏低，仅适用于低威胁、牵制方向。",
        }[p["tag"]]
        return f"- {body}。{tail}\n- {finding}"

    def _validation_notes(self, p: dict) -> str:
        rows = {r["metric"]: r for r in p["validation"]}
        notes = ["- 代表方案已回蒙特卡洛仿真复验（非仅代理预测）："]
        for m in ("success", "attrition_red", "readiness_red"):
            r = rows.get(m)
            if not r:
                continue
            label = METRIC_META[m][0]
            notes.append(
                f"  - {label}：代理 {self._fmt(m, r['surrogate'])} / "
                f"仿真 {self._fmt(m, r['simulation'])}±{r['sim_std']:.2f}"
            )
        worst = max(p["validation"], key=lambda r: r["abs_error"], default=None)
        if worst and worst["abs_error"] > 0.1:
            notes.append(
                f"- 注意：{METRIC_META.get(worst['metric'], (worst['metric'],))[0]}存在"
                f"{worst['abs_error']:.2f} 的端点偏差（二值稀有事件、复制数有限），"
                "方案排序可信、绝对值按区间理解。"
            )
        return "\n".join(notes)

    def _render_whatif(self, cmp_: dict, knobs: dict) -> str:
        cur, base, dm = cmp_["plan"], cmp_["baseline"], cmp_["delta"]
        d = cur["decisions"]
        defaults = self.tools.context_info()["baseline_decisions"]
        given = "、".join(f"{KNOB_LABELS[k]} {v}{_UNITS[k]}" for k, v in knobs.items())
        unspecified = [k for k in DECISION_VARS if k not in knobs]
        if unspecified:
            fill = "、".join(
                f"{KNOB_LABELS[k]} {defaults[k]}{_UNITS[k]}" for k in unspecified
            )
            head = f"按你给定的（{given}）做四率代理预测；未说明的 {fill} 按基线取值。"
        else:
            head = f"按你给定的（{given}）做四率代理预测，7 个旋钮均已指定。"
        config = "；".join(f"{KNOB_LABELS[k]} {d[k]}{_UNITS[k]}" for k in DECISION_VARS)
        lines = [
            head,
            "",
            f"完整配置：{config}",
            "| 指标 | 本方案 | v4 基线 | 变化 |",
            "| --- | --- | --- | --- |",
        ]
        lines += self._metric_rows(
            {m: cur[m] for m in METRIC_META}, base, dm, include_cost=True, cost=cur["cost"]
        )
        lines.append("")
        if cmp_["on_pareto_front"]:
            tag = cmp_["rank_tag"]
            lines.append(
                "该配置就在 Pareto 前沿上"
                + (f"（标签：{PLAN_TAGS.get(tag, tag)}），" if tag else "，")
                + "不存在三目标同时更优的方案，可按此执行。"
            )
        elif cmp_["dominated_by_count"] > 0:
            lines.append(
                f"该配置被前沿中 {cmp_['dominated_by_count']} 个方案支配"
                "（成功率不更低、红战损不更高、成本不更高且至少一项更优）——"
                "建议让我\"一键生成 knee 保障方案\"获取推荐配置。"
            )
        else:
            lines.append("该配置虽不在当前 Pareto 前沿上，但也不被支配，属于前沿之外的可行折中。")
        return "\n".join(lines)

    def _render_pareto(self, front: dict) -> str:
        lines = [
            f"NSGA-II 共得到 {front['n_plans']} 个 Pareto 非支配保障方案"
            f"（{'标签：' + PLAN_TAGS[front['tag']] if front['tag'] else '按资源成本从低到高'}）：",
            "",
            "| 抢修组 | 抢救车 | 器材车 | 携行档 | 运行档 | 间隔(tick) | 每波(件) |"
            " 完成率 | 红战损 | 红完好 | 成本 | 标签 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in front["plans"]:
            lines.append(
                f"| {r['red_repair_teams']} | {r['red_arv']} | {r['red_supply_veh']} | "
                f"{r['carry_level']} | {r['operating_level']} | "
                f"{r['wave_interval_ticks']} | {r['wave_qty']} | "
                f"{r['success'] * 100:.1f}% | {r['attrition_red'] * 100:.1f}% | "
                f"{r['readiness_red'] * 100:.1f}% | {r['cost']:.3f} | "
                f"{PLAN_TAGS.get(r['rank_tag'], '')} |"
            )
        lines += [
            "",
            f"解读：{front['front_finding']}；三个目标相互冲突，没有绝对最优，"
            "knee=最大折中（默认推荐），leanest=资源最省，strongest=完成率最高。",
            "需要可执行方案的话，直接说\"一键生成 knee 保障方案\"。",
        ]
        return "\n".join(lines)

    def _render_defs(self, defs: dict) -> str:
        lines = ["**GJB 口径四率与任务完成率（代码与单测同一口径，C1 已对齐）：**", ""]
        for k in ("完好率", "故障率", "修复率", "战损率", "任务完成率"):
            lines.append(f"- **{k}**：{defs[k]}")
        lines += ["", f"- **适用边界**：{defs['适用边界']}"]
        return "\n".join(lines)

    def _render_status(self) -> str:
        s = self.service.status()
        return "\n".join([
            "**本地大模型推理服务状态**",
            f"- 状态：{s['state']}（{s['backend']}）",
            f"- 模型：{s['model']}（{s['quantization']} 量化，{s['version']}）",
            f"- 设备：{s['device']}",
            f"- 显存：{s['vram_used_gb']:.2f}/{s['vram_total_gb']:.1f} GB"
            f"（{s['vram_percent']:.0f}%）；GPU 利用率 {s['gpu_util_percent']:.0f}%",
            f"- 上下文：{s['context_tokens']}/{s['context_limit']} tokens",
            f"- 累计服务请求 {s['requests_total']} 次、函数调用 {s['tools_served']} 次；"
            f"平均推理延迟 {s['avg_latency_ms']:.0f} ms",
        ])

    def _render_help(self, greeting: bool = False) -> str:
        head = (
            "您好，我是部署在本地的合成旅装备保障智能参谋。"
            if greeting
            else "我可以基于仿真数据为保障参谋做四件事："
        )
        return "\n".join([
            head,
            "- **四率问答**：如\"完好率和战损率是什么口径？\"",
            "- **秒级预测**：如\"如果派 5 个抢修组、4 辆抢救车、携行档 3 档，预测四率？\"",
            "- **方案对比**：如\"每 10 分钟一个波次、每波每部件 1 件，和基线比怎么样？\"",
            "- **一键生成保障方案**：如\"生成 knee 保障方案\"、"
            "\"资源最省的方案\"、\"保障最强的方案\"",
            "- **Pareto 方案集查询**：如\"NSGA-II 一共给出多少套方案？\"",
            "- **本地服务状态**：如\"推理服务现在什么状态？显存占了多少？\"",
            "",
            "所有数字均来自代理模型对蒙特卡洛仿真结果的秒级预测，以及代表方案的仿真复验，不凭空生成。",
        ])

    def _render_fallback(self, text: str) -> str:
        return (
            f"我还不能直接回答这类问题（原型只接了装备保障相关工具）。你问的是：\"{text[:40]}\"。\n"
            "可以问我：四率口径、自定义保障方案预测（给抢修组/抢救车/器材车/基数档/波次参数）、"
            "Pareto 方案集，或直接\"一键生成 knee 保障方案\"。"
        )

    # ---- 工具与格式化辅助 ----

    def _call(self, calls: list[ToolCall], name: str, arguments: dict) -> dict:
        t0 = time.perf_counter()
        result = getattr(self.tools, name)(**arguments)
        elapsed = (time.perf_counter() - t0) * 1000
        calls.append(ToolCall(name, arguments, elapsed, self._tool_summary(name, result)))
        return result

    @staticmethod
    def _tool_summary(name: str, result: dict) -> str:
        if name == "predict_four_rates":
            return (
                f"完成率 {result['success'] * 100:.1f}% / "
                f"红战损 {result['attrition_red'] * 100:.1f}%"
            )
        if name == "generate_support_plan":
            return f"{PLAN_TAGS[result['tag']]}：{result['n_pareto_plans']} 个前沿方案中选定"
        if name == "query_pareto_front":
            return f"{result['n_plans']} 个 Pareto 非支配方案"
        if name == "compare_with_baseline":
            return f"已对比基线；在前沿上={result['on_pareto_front']}"
        if name == "get_rate_definitions":
            return "返回 GJB 口径四率定义 5 条"
        return ""

    @staticmethod
    def _knob_args(knobs: dict) -> dict:
        """决策旋钮名 → 工具参数名；未提及的参数缺省 None（工具端按基线补齐）。"""
        return {
            arg: (knobs[knob] if knob in knobs else None)
            for arg, knob in _ARG_TO_KNOB.items()
        }

    def _metric_rows(
        self,
        metrics: dict,
        baseline: dict,
        delta: dict,
        include_cost: bool,
        cost: float | None = None,
    ) -> list[str]:
        rows = []
        for m in KEY_METRICS + ["failure_rate_100h_red"]:
            rows.append(
                f"| {METRIC_META[m][0]} | {self._fmt(m, metrics[m])} | "
                f"{self._fmt(m, baseline[m])} | {self._delta(m, delta[m])} |"
            )
        if include_cost:
            base_cost = baseline.get("cost", float("nan"))
            rows.append(
                f"| 保障资源成本(归一化) | {cost:.3f} | {base_cost:.3f} | "
                f"{delta['cost']:+.3f} |"
            )
        return rows

    @staticmethod
    def _fmt(metric: str, v: float) -> str:
        return f"{v * 100:.1f}%" if METRIC_META[metric][1] == "pct" else f"{v:.2f}"

    @staticmethod
    def _delta(metric: str, d: float) -> str:
        return f"{d * 100:+.1f}pp" if METRIC_META[metric][1] == "pct" else f"{d:+.2f}"
