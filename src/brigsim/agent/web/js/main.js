/* C6 大屏入口：预载 6 个 JSON（任一缺失降级为空图，不阻断）→ 渲染四视图 + 顶栏 KPI。 */
(function () {
  "use strict";
  const { $, pct, fnum, loadJson, charts, views } = window.DASH;

  const FILES = ["timeseries", "distribution", "sensitivity", "surrogate", "pareto", "manifest"];

  function speedText(v) {
    if (v === null || v === undefined) return "—";
    return v >= 10000 ? (v / 10000).toFixed(1) + "万×" : Math.round(v) + "×";
  }

  function fillKpis(data) {
    const dist = data.distribution, sur = data.surrogate;
    const set = (id, v, sub) => {
      const node = $(id);
      node.querySelector(".kpi-v").textContent = v;
      if (sub) node.querySelector(".kpi-s").textContent = sub;
    };
    if (dist) {
      set("kpi-success", pct(dist.success_rate), `MC ${dist.n_reps} 复制`);
      const s = dist.summary;
      if (s.attrition_red) set("kpi-attr-red", pct(s.attrition_red.mean));
      if (s.readiness_red) set("kpi-ready-red", pct(s.readiness_red.mean));
      if (s.repair_rate_red) set("kpi-repair-red", pct(s.repair_rate_red.mean));
    }
    if (sur) {
      const r2 = sur.targets.map((t) => t.r2).filter((v) => v !== null);
      if (r2.length) set("kpi-r2", fnum(r2.reduce((a, b) => a + b, 0) / r2.length, 3));
      set("kpi-speedup", speedText(sur.benchmark.speedup));
    }
  }

  function markEmpty(svgId, name) {
    const svg = $(svgId);
    if (svg) charts.emptyChart(svg, `${name}数据未构建（brigsim dash-build）`);
  }

  async function boot() {
    const data = {};
    const results = await Promise.allSettled(FILES.map((f) =>
      loadJson(`${f}.json`).then((j) => [f, j])));
    results.forEach((r) => {
      if (r.status === "fulfilled") data[r.value[0]] = r.value[1];
    });
    window.DASH.data = data;

    fillKpis(data);

    // 四率卡依赖 distribution + pareto；缺失任一则置占位
    if (data.timeseries && data.distribution) {
      views.rates.init(data);
    } else {
      ["ts-svg", "dist-svg"].forEach((id) => markEmpty(id, ""));
    }
    if (data.pareto) views.pareto.init(data); else markEmpty("pareto-svg", "Pareto ");
    if (data.sensitivity) views.sensitivity.init(data); else markEmpty("sens-svg", "灵敏度");
    if (data.surrogate) views.surrogate.init(data); else markEmpty("parity-svg", "代理模型");
    if (!data.pareto || !data.distribution) {
      ["rc-readiness", "rc-attrition", "rc-repair", "rc-failure"].forEach((id) => {
        const d = $(id).querySelector(".rc-d");
        d.textContent = "数据未构建"; d.className = "rc-d flat";
      });
    }

    // 容器尺寸变化（窗口缩放）重绘当前视图
    const redrawMap = [
      ["ts-svg", "rates"], ["dist-svg", "rates"],
      ["pareto-svg", "pareto"], ["sens-svg", "sensitivity"], ["parity-svg", "surrogate"],
    ];
    redrawMap.forEach(([svgId, view]) => {
      const box = $(svgId).parentElement;
      let t = null;
      new ResizeObserver(() => {
        clearTimeout(t);
        t = setTimeout(() => { if (views[view]) views[view].redraw(); }, 120);
      }).observe(box);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
