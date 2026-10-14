/* 右列下：代理模型精度/加速比统计 + 留出集 parity 散点（代理预测 vs 仿真真值，y=x 参考） */
(function () {
  "use strict";
  const { $, fnum, pct, charts } = window.DASH;
  let D = null;
  let target = "success";

  function speedText(v) {
    if (v === null || v === undefined) return "—";
    return v >= 10000 ? (v / 10000).toFixed(1) + " 万×" : Math.round(v) + "×";
  }

  function renderStats() {
    const r2 = D.targets.map((t) => t.r2).filter((v) => v !== null);
    const mae = D.targets.map((t) => t.mae).filter((v) => v !== null);
    const meanR2 = r2.reduce((a, b) => a + b, 0) / r2.length;
    // MAE 混合 pct/num 量纲，只对 pct 类求平均展示
    const pctMae = D.targets.filter((t) => t.kind === "pct").map((t) => t.mae);
    const meanMae = pctMae.reduce((a, b) => a + b, 0) / pctMae.length;
    $("sur-r2").textContent = fnum(meanR2, 3);
    $("sur-mae").textContent = (meanMae * 100).toFixed(1) + "%";
    $("sur-speed").textContent = speedText(D.benchmark.speedup);
    $("sur-size").textContent = `${D.n_train}/${D.n_holdout}`;
  }

  function renderParity() {
    const t = D.targets.find((x) => x.key === target) || D.targets[0];
    const isPct = t.kind === "pct";
    const vals = t.points.flat();
    const lo = Math.min(...vals), hi = Math.max(...vals);
    charts.scatterChart($("parity-svg"), $("parity-tip"), {
      points: t.points.map(([pred, trueV]) => ({ x: trueV, y: pred })),
      xDomain: [lo, hi], yDomain: [lo, hi],
      diagonal: true,
      r: 3.2,
      xLabel: "仿真真值 →",
      xFmt: isPct ? (v) => (v * 100).toFixed(0) + "%" : (v) => v.toFixed(1),
      yFmt: isPct ? (v) => (v * 100).toFixed(0) + "%" : (v) => v.toFixed(1),
      tipHtml: (p) => `<b>${t.label}</b><br>仿真真值：${isPct ? pct(p.x) : fnum(p.x, 2)}
        <br>代理预测：${isPct ? pct(p.y) : fnum(p.y, 2)}`,
    });
    // R² 角标
    const svg = $("parity-svg");
    const NS = "http://www.w3.org/2000/svg";
    const g = document.createElementNS(NS, "g");
    g.innerHTML = `<text x="14" y="22" fill="#7ff0d2" font-size="12" font-family="monospace">
      R²=${fnum(t.r2, 3)} · MAE=${isPct ? (t.mae * 100).toFixed(1) + "%" : fnum(t.mae, 3)} · n=${t.points.length}</text>`;
    svg.appendChild(g);
  }

  window.DASH.views.surrogate = {
    init(data) {
      D = data.surrogate;
      if (!D) return;
      renderStats();
      // 只启用数据中存在的目标切换
      const have = new Set(D.targets.map((t) => t.key));
      document.querySelectorAll("#parity-target-toggle .seg").forEach((s) => {
        if (!have.has(s.dataset.parityTarget)) s.style.display = "none";
        s.onclick = () => {
          target = s.dataset.parityTarget;
          document.querySelectorAll("#parity-target-toggle .seg").forEach((x) => x.classList.remove("on"));
          s.classList.add("on");
          renderParity();
        };
      });
      renderParity();
    },
    redraw() { renderParity(); },
  };
})();
