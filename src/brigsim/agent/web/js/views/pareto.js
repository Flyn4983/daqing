/* 右列上：Pareto 前沿散点（成本×完成率，色=红战损），三标签星标 + 基线，点击看方案细节与复验 */
(function () {
  "use strict";
  const { $, pct, fnum, esc, charts } = window.DASH;
  let D = null;

  // 红战损率连续色标：低（绿）→ 中（琥珀）→ 高（红）
  function heat(v) {
    const stops = [[0.0, [52, 211, 153]], [0.35, [251, 191, 36]], [0.7, [248, 113, 113]]];
    for (let i = 0; i < stops.length - 1; i++) {
      const [a, ca] = stops[i], [b, cb] = stops[i + 1];
      if (v <= b) {
        const t = Math.max(0, Math.min(1, (v - a) / (b - a)));
        const c = ca.map((x, j) => Math.round(x + (cb[j] - x) * t));
        return `rgb(${c[0]},${c[1]},${c[2]})`;
      }
    }
    return "rgb(248,113,113)";
  }

  function tagOf(idx) {
    const e = Object.entries(D.tags).find(([, v]) => v === idx);
    return e ? e[0] : null;
  }

  function detail(idx) {
    const p = D.plans[idx];
    if (!p) return;
    const tag = tagOf(idx);
    const knobs = Object.entries(D.knob_labels).map(([k, label]) =>
      `<span class="k">${esc(label)}</span><span class="v">${p[k]}</span>`).join("");
    let valHtml = "";
    if (tag) {
      const rows = D.validation.filter((r) => r.plan === tag);
      if (rows.length) {
        valHtml = `<div style="margin-top:6px"><b>仿真复验（代理 vs 蒙特卡洛）</b>
          <table style="width:100%;border-collapse:collapse;margin-top:3px;font-size:10.5px">
          <tr><th>指标</th><th>代理</th><th>仿真</th><th>|误差|</th></tr>`
          + rows.map((r) => `<tr><td style="text-align:left">${esc(r.metric_label)}</td>
            <td>${pctLike(r.surrogate, r.metric)}</td>
            <td>${pctLike(r.simulation, r.metric)} ±${fnum(r.sim_std, 2)}</td>
            <td>${fnum(r.abs_error, 3)}</td></tr>`).join("")
          + `</table></div>`;
      }
    }
    const tagLine = tag
      ? `<span class="pd-tag">${esc(D.tag_labels[tag])}（${tag}）</span>　`
      : `方案 #${idx + 1}　`;
    $("pareto-detail").innerHTML =
      `${tagLine}<b>完成率 ${pct(p.success)}</b> · 红战损 ${pct(p.attrition_red)}
       · 红完好 ${pct(p.readiness_red)} · 成本 ${fnum(p.cost, 3)}
       <div class="kv" style="grid-template-columns:1fr auto;margin-top:4px">${knobs}</div>
       ${valHtml}`;
  }

  function pctLike(v, metricKey) {
    // 复验长表只有 metric 名；failure_rate_100h_* 为数值，其余按百分比
    return typeof v === "number"
      ? (metricKey && metricKey.startsWith("failure") ? fnum(v, 1) : pct(v))
      : "—";
  }

  function render() {
    if (!D) return;
    const markers = Object.entries(D.tags).map(([tag, idx]) => {
      const p = D.plans[idx];
      const color = tag === "knee" ? "#34e0c0" : tag === "strongest" ? "#fbbf24" : "#9fb4d8";
      return { x: p.cost, y: p.success, r: 8, color, label: tag, idx, data: p };
    });
    if (D.baseline) {
      markers.push({
        x: D.baseline.cost, y: D.baseline.success, r: 7,
        color: "#ffffff", label: "基线", idx: -1, data: null,
      });
    }
    charts.scatterChart($("pareto-svg"), $("pareto-tip"), {
      points: D.plans.map((p, i) => ({
        x: p.cost, y: p.success, color: heat(p.attrition_red), r: tagOf(i) ? 0.1 : 4.2,
        opacity: tagOf(i) ? 0 : 0.85, dataIdx: i,
      })),
      markers,
      xLabel: "保障资源成本（归一化）→",
      yFmt: (v) => (v * 100).toFixed(0) + "%",
      tipHtml: (p, i) => {
        const idx = p.dataIdx !== undefined ? p.dataIdx : i;
        const pl = D.plans[idx];
        const tag = tagOf(idx);
        return `<b>${tag ? esc(D.tag_labels[tag]) + " · " + tag : "方案 #" + (idx + 1)}</b><br>`
          + `成本 ${fnum(pl.cost, 3)}<br>完成率 ${pct(pl.success)}<br>红战损 ${pct(pl.attrition_red)}`;
      },
      onSelect: (p) => {
        if (p.dataIdx !== undefined) detail(p.dataIdx);
        else if (p.idx !== undefined) (p.idx === -1 ? baselineDetail() : detail(p.idx));
      },
    });
    drawLegend();
    detail(D.tags.knee || 0);
    const sub = document.getElementById("replay-knee-sub");
    if (sub) {
      const k = D.plans[D.tags.knee || 0];
      sub.textContent = Object.entries(D.knob_labels)
        .map(([key, label]) => `${label.replace(/\(.*\)/, "")}${k[key]}`).join(" / ");
    }
  }

  function baselineDetail() {
    const b = D.baseline;
    const knobs = Object.entries(D.knob_labels).map(([k, label]) =>
      `<span class="k">${esc(label)}</span><span class="v">${b[k]}</span>`).join("");
    $("pareto-detail").innerHTML =
      `<span class="pd-tag">现状保障基线</span>　<b>完成率 ${pct(b.success)}</b> · 红战损 ${pct(b.attrition_red)}
       · 红完好 ${pct(b.readiness_red)} · 成本 ${fnum(b.cost, 3)}
       <div class="kv" style="grid-template-columns:1fr auto;margin-top:4px">${knobs}</div>
       <div style="margin-top:4px;color:#7e90ad;font-size:10.5px">
       v4 标定现行配置（NSGA-II 优化前），7 个保障旋钮均可调</div>`;
  }

  function drawLegend() {
    const svg = $("pareto-svg");
    const NS = "http://www.w3.org/2000/svg";
    const box = svg.parentElement.getBoundingClientRect();
    const grad = document.createElementNS(NS, "defs");
    grad.innerHTML = `<linearGradient id="attrHeat" x1="0" x2="1">
      <stop offset="0" stop-color="rgb(52,211,153)"/><stop offset="0.55" stop-color="rgb(251,191,36)"/>
      <stop offset="1" stop-color="rgb(248,113,113)"/></linearGradient>`;
    svg.appendChild(grad);
    const g = document.createElementNS(NS, "g");
    g.innerHTML =
      `<rect x="14" y="${box.height - 40}" width="110" height="8" fill="url(#attrHeat)" rx="2"/>
       <text x="14" y="${box.height - 44}" fill="#6680a5" font-size="9.5" font-family="monospace">红战损率 低→高</text>
       <text x="14" y="${box.height - 22}" fill="#6680a5" font-size="9" font-family="monospace">0%</text>
       <text x="124" y="${box.height - 22}" fill="#6680a5" font-size="9" font-family="monospace" text-anchor="end">70%+</text>`;
    svg.appendChild(g);
  }

  window.DASH.views.pareto = {
    init(data) { D = data.pareto; render(); },
    redraw() { render(); },
  };
})();
