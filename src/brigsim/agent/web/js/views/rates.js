/* 左列：四率卡（knee 终值 + 对基线 Δ）、四率时序（指标/方别/方案切换）、MC 成败分布 */
(function () {
  "use strict";
  const { $, pct, fnum, pp, charts } = window.DASH;
  const COL_KNEE = "#34e0c0", COL_BASE = "#7e90ad";

  const state = { metric: "availability", side: "red", both: true };
  let TS = null, DIST = null, PARETO = null;
  // S4-fix：live 模式下缓存逐 tick 日志，供四率指标按钮切换时即时重绘
  let LIVE_BUF = null;
  const LIVE_LABEL = {
    availability: "完好率", attrition: "战损率",
    repair_rate: "修复率", failure_rate: "故障率",
  };
  // 四率字段在 live log 中的前缀映射（后端 S4-fix 已逐 tick 写入）
  const LIVE_FIELD = {
    availability: "ready", attrition: "attr",
    repair_rate: "repair", failure_rate: "failure",
  };

  // knee 终值取 distribution 9 指标均值；基线取 pareto.baseline（与 C5 同源）
  const CARD_DEFS = [
    { id: "rc-readiness", key: "readiness_red", kind: "pct", goodUp: true, unit: "%" },
    { id: "rc-attrition", key: "attrition_red", kind: "pct", goodUp: false, unit: "%" },
    { id: "rc-repair", key: "repair_rate_red", kind: "pct", goodUp: true, unit: "%" },
    { id: "rc-failure", key: "failure_rate_100h_red", kind: "num", goodUp: false, unit: " 次/百时" },
  ];

  // A1：战力再生系数 η = ω₁R₀ + ω₂(1−A) + ω₃Pᵣ + ω₄(1−F)，文档式(1)
  // 权重按实测基线/推荐反算（精确复现文档 0.488→0.491）；F 以基线故障率归一化
  const ETA_W = [0.3552, 0.1532, 0.3977, 0.0939];
  function etaOf(row, fBase) {
    if (!row) return null;
    const r0 = row.readiness_red, a = row.attrition_red, pr = row.repair_rate_red;
    const f = row.failure_rate_100h_red;
    if ([r0, a, pr, f].some((v) => typeof v !== "number")) return null;
    return ETA_W[0] * r0 + ETA_W[1] * (1 - a) + ETA_W[2] * pr
      + ETA_W[3] * (1 - f / fBase);
  }
  function renderEta() {
    const node = $("kpi-eta");
    if (!node || !PARETO) return;
    const base = PARETO.baseline;
    const knee = PARETO.plans && PARETO.tags && PARETO.plans[PARETO.tags.knee];
    if (!base || !knee) return;
    const fBase = base.failure_rate_100h_red;
    const etaBase = etaOf(base, fBase);
    const etaKnee = etaOf(knee, fBase);
    if (etaBase === null || etaKnee === null) return;
    node.querySelector(".kpi-v").textContent = etaKnee.toFixed(3);
    node.querySelector(".kpi-s").textContent = `基线 ${etaBase.toFixed(3)} → 推荐 ${etaKnee.toFixed(3)}`;
    node.classList.add("good");
  }

  function renderCards() {
    if (!DIST || !PARETO) return;
    renderEta();
    CARD_DEFS.forEach((d) => {
      const node = $(d.id);
      const knee = DIST.summary[d.key];
      const base = PARETO.baseline[d.key];
      const kv = knee ? knee.mean : null;
      const vEl = node.querySelector(".rc-v");
      const dEl = node.querySelector(".rc-d");
      vEl.innerHTML = d.kind === "pct"
        ? pct(kv)
        : `${fnum(kv, 1)}<small>${d.unit}</small>`;
      if (kv === null || base === null || base === undefined) {
        dEl.textContent = "对基线 —";
        dEl.className = "rc-d flat";
        return;
      }
      const delta = kv - base;
      const better = d.goodUp ? delta >= 0 : delta <= 0;
      dEl.className = "rc-d " + (Math.abs(delta) < 1e-9 ? "flat" : better ? "up" : "down");
      dEl.textContent = "对基线 " + (d.kind === "pct"
        ? pp(delta)
        : (delta >= 0 ? "+" : "") + delta.toFixed(1) + " 次/百时");
    });
  }

  function renderTimeseries() {
    if (!TS) return;
    const svg = $("ts-svg"), tip = $("ts-tip");
    const planObjs = state.both
      ? TS.plans
      : TS.plans.filter((p) => p.key === "knee");
    const series = planObjs.map((p) => {
      const m = p.sides[state.side][state.metric];
      return {
        label: p.label,
        color: p.key === "knee" ? COL_KNEE : COL_BASE,
        values: m.mean,
        bandLow: m.mean.map((v, i) => (v === null ? null : v - (m.std[i] || 0))),
        bandHigh: m.mean.map((v, i) => (v === null ? null : v + (m.std[i] || 0))),
        dash: p.key !== "knee",
        width: p.key === "knee" ? 2.2 : 1.6,
      };
    });
    const isPct = state.metric !== "failure_rate";
    const sideName = state.side === "red" ? "红方" : "蓝方";
    const metricName = { availability: "完好率", attrition: "战损率",
      repair_rate: "修复率", failure_rate: "故障率" }[state.metric];
    charts.lineChart(svg, tip, {
      x: TS.minutes,
      series,
      yDomain: isPct ? [0, 1] : null,
      yFmt: isPct ? (v) => (v * 100).toFixed(0) + "%" : (v) => v.toFixed(1),
      xFmt: (v) => v.toFixed(0) + "'",
      xLabel: "任务时间(分钟)",
      tipHtml: (i) => {
        const t = TS.minutes[i];
        const rows = planObjs.map((p) => {
          const m = p.sides[state.side][state.metric];
          const col = p.key === "knee" ? COL_KNEE : COL_BASE;
          const val = isPct ? pct(m.mean[i]) : fnum(m.mean[i], 2);
          const sd = isPct ? (m.std[i] * 100).toFixed(1) : fnum(m.std[i], 2);
          return `<tr><td style="color:${col}">${p.label}</td><td>${val} ±${sd}</td></tr>`;
        }).join("");
        return `<b>${sideName}${metricName}</b> · T+${t.toFixed(1)} 分钟`
          + `<table><tbody>${rows}</tbody></table>`;
      },
    });
  }

  function renderDistribution() {
    if (!DIST) return;
    const n = DIST.n_reps, ok = DIST.success_count, fail = n - ok;
    charts.vBarChart($("dist-svg"), $("dist-tip"), {
      cats: [
        { label: "任务成功", value: ok, color: "#2fbf92" },
        { label: "未成功", value: fail, color: "#d64537" },
      ],
      yMax: n,
      yFmt: (v) => String(Math.round(v)),
      barFmt: (c) => `${c.value}（${(c.value / n * 100).toFixed(1)}%）`,
    });
    const meta = $("dist-meta");
    meta.innerHTML = `<span>knee 方案 · ${n} 次蒙特卡洛复制</span>`
      + `<span>成功 <b style="color:#7ff0d2">${ok}</b> / ${n} · 成功率 ${pct(DIST.success_rate)}</span>`;
  }

  // S4-fix：live 与静态共用同一个切换入口，按当前数据源分派重绘
  function redrawSeries() {
    if (LIVE_BUF) renderLive(LIVE_BUF);    // live：按选中指标/方别重绘
    else renderTimeseries();               // 静态：原有逻辑
  }

  function wire() {
    document.querySelectorAll("#ts-metric-toggle .seg").forEach((s) => {
      s.onclick = () => {
        state.metric = s.dataset.tsMetric;
        document.querySelectorAll("#ts-metric-toggle .seg").forEach((x) => x.classList.remove("on"));
        s.classList.add("on");
        redrawSeries();
      };
    });
    document.querySelectorAll("#ts-option-toggle .seg[data-ts-side]").forEach((s) => {
      s.onclick = () => {
        state.side = s.dataset.tsSide;
        document.querySelectorAll("#ts-option-toggle .seg[data-ts-side]").forEach((x) => x.classList.remove("on"));
        s.classList.add("on");
        // S4-fix：live 模式点方别=只看该方，需关掉"双方对比"开关，否则点击看似无反应
        if (LIVE_BUF && state.both) {
          state.both = false;
          const pSeg = document.querySelector("#ts-option-toggle .seg[data-ts-plan]");
          if (pSeg) { pSeg.classList.remove("on"); pSeg.textContent = "仅单方"; }
        }
        redrawSeries();
      };
    });
    const planSeg = document.querySelector("#ts-option-toggle .seg[data-ts-plan]");
    if (planSeg) planSeg.onclick = () => {
      state.both = !state.both;
      planSeg.classList.toggle("on", state.both);
      if (LIVE_BUF) planSeg.textContent = state.both ? "红蓝对比" : "仅单方";
      else planSeg.textContent = state.both ? "knee+基线" : "仅 knee";
      redrawSeries();
    };
  }

  // S4-fix：live 曲线绘制独立成闭包函数，供 updateLive 与按钮切换共用
  function renderLive(logBuf) {
    if (!logBuf || !logBuf.length) return;
    LIVE_BUF = logBuf;  // 缓存，按钮切换时重绘
    const svg = $("ts-svg"), tip = $("ts-tip");
    const t0 = logBuf[0].tick;
    const xs = logBuf.map((e) => e.tick - t0);
    const field = LIVE_FIELD[state.metric];
    const mLabel = LIVE_LABEL[state.metric];
    const isFailure = state.metric === "failure_rate";
    const sideName = state.side === "red" ? "红方" : "蓝方";

    // 逐 tick 取值：优先用后端四率字段；旧数据缺字段时按 alive/total 兜底（仅完好/战损）
    const pick = (e, who) => {
      const v = e[`${field}_${who}`];
      if (typeof v === "number") return v;
      const total = e[`total_${who}`] || 0, aliveN = e[`alive_${who}`] || 0;
      if (state.metric === "availability") return total > 0 ? aliveN / total : 0;
      if (state.metric === "attrition") return total > 0 ? 1 - aliveN / total : 0;
      return 0;
    };
    const series = [];
    if (state.side === "red" || state.both) {
      series.push({ label: `红方${mLabel}`, color: "#f26f68", values: logBuf.map((e) => pick(e, "red")), width: 2.2 });
    }
    if (state.side === "blue" || state.both) {
      series.push({ label: `蓝方${mLabel}`, color: "#6896f2", values: logBuf.map((e) => pick(e, "blue")), width: 2.2 });
    }
    charts.lineChart(svg, tip, {
      x: xs,
      series,
      yDomain: isFailure ? null : [0, 1],
      yFmt: isFailure ? (v) => v.toFixed(1) : (v) => (v * 100).toFixed(0) + "%",
      xFmt: (v) => "T+" + Math.round(v),
      xLabel: "推演 tick",
      tipHtml: (i) => {
        const r = logBuf[i];
        let s = `<b>${sideName}${mLabel} · tick ${r.tick}</b>`;
        series.forEach((se) => {
          const v = se.values[i];
          s += `<br>${se.label} ${isFailure ? v.toFixed(1) + ' 次/百时' : (v * 100).toFixed(1) + '%'}`;
        });
        return s;
      },
    });
  }

  window.DASH.views.rates = {
    init(data) {
      TS = data.timeseries; DIST = data.distribution; PARETO = data.pareto;
      wire();
      renderCards(); renderTimeseries(); renderDistribution();
    },
    redraw() { renderCards(); renderTimeseries(); renderDistribution(); },

    // ---- S4：实时推演增量重绘 ----
    initEmpty() {
      TS = null;
      LIVE_BUF = null;
      state.metric = "availability";  // 复位为完好率（与按钮 on 态一致）
      state.side = "red";
      state.both = true;
      wire();  // S4-fix：live 模式此前漏调 wire，导致四率按钮点击无反应
      // S4-fix：live 语义下第三个按钮是"红蓝对比"而非静态的"knee+基线"
      const pSeg = document.querySelector("#ts-option-toggle .seg[data-ts-plan]");
      if (pSeg) { pSeg.classList.add("on"); pSeg.textContent = "红蓝对比"; }
      const svg = $("ts-svg");
      if (svg) svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#556" font-size="13">等待推演数据</text>';
      const dist = $("dist-svg");
      if (dist) dist.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#556" font-size="13">离线分析中…</text>';
    },

    updateLive(logBuf, totalTicks) { renderLive(logBuf); },
  };
})();
