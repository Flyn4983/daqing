/* 左列：四率卡（knee 终值 + 对基线 Δ）、四率时序（指标/方别/方案切换）、MC 成败分布 */
(function () {
  "use strict";
  const { $, pct, fnum, pp, charts } = window.DASH;
  const COL_KNEE = "#34e0c0", COL_BASE = "#7e90ad";

  const state = { metric: "availability", side: "red", both: true };
  let TS = null, DIST = null, PARETO = null;

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

  function wire() {
    document.querySelectorAll("#ts-metric-toggle .seg").forEach((s) => {
      s.onclick = () => {
        state.metric = s.dataset.tsMetric;
        document.querySelectorAll("#ts-metric-toggle .seg").forEach((x) => x.classList.remove("on"));
        s.classList.add("on");
        renderTimeseries();
      };
    });
    document.querySelectorAll("#ts-option-toggle .seg[data-ts-side]").forEach((s) => {
      s.onclick = () => {
        state.side = s.dataset.tsSide;
        document.querySelectorAll("#ts-option-toggle .seg[data-ts-side]").forEach((x) => x.classList.remove("on"));
        s.classList.add("on");
        renderTimeseries();
      };
    });
    const planSeg = document.querySelector("#ts-option-toggle .seg[data-ts-plan]");
    planSeg.onclick = () => {
      state.both = !state.both;
      planSeg.classList.toggle("on", state.both);
      planSeg.textContent = state.both ? "knee+基线" : "仅 knee";
      renderTimeseries();
    };
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
      const svg = $("ts-svg");
      if (svg) svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#556" font-size="13">等待推演数据</text>';
      const dist = $("dist-svg");
      if (dist) dist.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#556" font-size="13">离线分析中…</text>';
    },

    updateLive(logBuf, totalTicks) {
      if (!logBuf || !logBuf.length) return;
      const svg = $("ts-svg"), tip = $("ts-tip");
      const t0 = logBuf[0].tick;
      const xs = logBuf.map((e) => e.tick - t0);
      const yReadyRed = logBuf.map((e) => e.total_red > 0 ? e.alive_red / e.total_red : 0);
      const yReadyBlue = logBuf.map((e) => e.total_blue > 0 ? e.alive_blue / e.total_blue : 0);

      charts.lineChart(svg, tip, {
        x: xs,
        series: [
          { label: "红方完好率", color: "#f26f68", values: yReadyRed, width: 2.2 },
          { label: "蓝方完好率", color: "#6896f2", values: yReadyBlue, width: 2.2 },
        ],
        yDomain: [0, 1],
        yFmt: (v) => (v * 100).toFixed(0) + "%",
        xFmt: (v) => "T+" + Math.round(v),
        xLabel: "推演 tick",
        tipHtml: (i) => {
          const r = logBuf[i];
          const rr = r.total_red > 0 ? (r.alive_red / r.total_red * 100).toFixed(1) : 0;
          const bb = r.total_blue > 0 ? (r.alive_blue / r.total_blue * 100).toFixed(1) : 0;
          return `<b>tick ${r.tick}</b><br>红 ${r.alive_red}/${r.total_red} (${rr}%)<br>蓝 ${r.alive_blue}/${r.total_blue} (${bb}%)`;
        },
      });
    },
  };
})();
