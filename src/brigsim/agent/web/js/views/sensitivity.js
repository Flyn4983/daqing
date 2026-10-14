/* 右列中：Sobol 灵敏度 Top 因子横向条形（5 指标切换，ST 总效应排序，top3 高亮） */
(function () {
  "use strict";
  const { $, fnum, charts } = window.DASH;
  let D = null;
  let metric = "success";

  function render() {
    if (!D) return;
    const m = D.metrics.find((x) => x.key === metric) || D.metrics[0];
    const top3 = new Set(m.top3.map((t) => t.param));
    charts.hBarChart($("sens-svg"), $("sens-tip"), {
      items: m.rows.map((r) => ({
        label: r.label,
        value: Math.max(0, r.ST || 0),
        color: top3.has(r.param) ? "#34e0c0" : "#36b6f5",
        dim: !top3.has(r.param),
        raw: r,
      })),
      xDomain: [0, Math.max(0.05, ...m.rows.map((r) => r.ST || 0)) * 1.15],
      fmt: (v) => fnum(v, 2),
      tipHtml: (it) => {
        const r = it.raw;
        return `<b>${it.label}</b>（${r.param}）<br>`
          + `ST 总效应：<b>${fnum(r.ST, 3)}</b> ± ${fnum(r.ST_conf, 3)}<br>`
          + `S1 主效应：${fnum(r.S1, 3)} ± ${fnum(r.S1_conf, 3)}<br>`
          + `<span style="color:#7e90ad">ST 含交互作用的总方差贡献，越大越关键</span>`;
      },
    });
  }

  window.DASH.views.sensitivity = {
    init(data) {
      D = data.sensitivity;
      if (!D) return;
      document.querySelectorAll("#sens-metric-toggle .seg").forEach((s) => {
        s.onclick = () => {
          metric = s.dataset.sensMetric;
          document.querySelectorAll("#sens-metric-toggle .seg").forEach((x) => x.classList.remove("on"));
          s.classList.add("on");
          render();
        };
      });
      render();
    },
    redraw() { render(); },
  };
})();
