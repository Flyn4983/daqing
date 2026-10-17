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

    // C7-c：boot 完成后再启动工况选择器（DOM + 数据都就绪）
    initScenarioSwitch();
  }

  // ---- C7-c：实时推演（顶栏工况选择 + 轮询保底） ----
  function initScenarioSwitch() {
    const sel = $("#scenario-select");
    const btnRun = $("#btn-run");
    const btnStop = $("#btn-run-stop");
    const progressEl = $("#run-progress");
    if (!sel || !btnRun) return;  // 顶栏 HTML 未加载则跳过（如 dash-build 纯数据模式）

    let activeRun = null;        // { run_id, scenario, stopTick, pollTimer }
    let lastTick = -1;

    async function fillOptions() {
      try {
        const r = await fetch("/api/scenarios", { cache: "no-store" });
        const j = await r.json();
        const arr = j.scenarios || [];
        arr.forEach((s) => {
          const pre = s.terrain_preset || "geotiff";
          const opt = document.createElement("option");
          opt.value = s.file;
          opt.textContent = `${s.name} · ${pre} · ${s.duration_ticks}tick · vis=${s.visibility_factor}`;
          sel.appendChild(opt);
        });
        // 默认 medium_brigade（最具 demo 故事性：快攻碾压 → 雾攻雷达制胜 → 坚固防御被扛）
        const fav = arr.find((s) => s.file === "medium_brigade");
        if (fav) sel.value = fav.file;
      } catch (_) {
        // 后端没启动时静态降级（select 里有占位，不阻断）
      }
    }

    function setProgress(text, cls) {
      progressEl.textContent = text;
      progressEl.className = "run-progress " + (cls || "");
    }

    function updateFourRatesFromRun(snapshot) {
      if (!snapshot || !snapshot.log) return;
      const last = snapshot.log[snapshot.log.length - 1];
      const nr = last.total_red, nb = last.total_blue;
      const ar = last.alive_red, ab = last.alive_blue;
      const attrRed = nr > 0 ? 1 - ar / nr : 0;
      const readyRed = nr > 0 ? ar / nr : 0;
      const kpiAttrRed = $("#kpi-attr-red");
      const kpiReadyRed = $("#kpi-ready-red");
      if (kpiAttrRed) kpiAttrRed.querySelector(".kpi-v").textContent = (attrRed * 100).toFixed(1) + "%";
      if (kpiReadyRed) kpiReadyRed.querySelector(".kpi-v").textContent = (readyRed * 100).toFixed(1) + "%";
      const kpiRepair = $("#kpi-repair-red");
      if (kpiRepair) {
        kpiRepair.querySelector(".kpi-v").textContent = "推演中";
        kpiRepair.querySelector(".kpi-s").textContent = `${ar}/${nr} 红存活`;
      }
    }

    function poll(runId) {
      fetch(`/api/run?run_id=${runId}`, { cache: "no-store" })
        .then((r) => r.json())
        .then((s) => {
          if (s.error) { setProgress("错误", "error"); btnRun.hidden = false; btnStop.hidden = true; activeRun = null; return; }
          const pct = (s.progress * 100).toFixed(0);
          setProgress(`tick ${s.tick}/${s.total_ticks} ${pct}%`, "running");
          updateFourRatesFromRun(s);
          if (s.done) {
            const last2 = s.log && s.log.length ? s.log[s.log.length - 1] : null;
            const ar = last2 ? last2.alive_red : "—";
            const ab = last2 ? last2.alive_blue : "—";
            setProgress(`终局 红${ar}/蓝${ab}`, "done");
            btnRun.hidden = false; btnStop.hidden = true; activeRun = null;
            return;
          }
          activeRun.pollTimer = setTimeout(() => poll(runId), 1500);
        })
        .catch(() => {
          setProgress("连接中断", "error");
          btnRun.hidden = false; btnStop.hidden = true; activeRun = null;
        });
    }

    btnRun.addEventListener("click", async () => {
      const scenario = sel.value;
      if (!scenario) return;
      btnRun.hidden = true; btnStop.hidden = false; setProgress("启动…", "running");
      try {
        const r = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scenario, seed: 42 }),
        });
        const j = await r.json();
        if (!j.run_id) { setProgress(j.error || "启动失败", "error"); btnRun.hidden = false; btnStop.hidden = true; return; }
        activeRun = { run_id: j.run_id, scenario, pollTimer: null };
        poll(j.run_id);
      } catch (e) {
        setProgress("无法连接后端", "error");
        btnRun.hidden = false; btnStop.hidden = true;
      }
    });

    btnStop.addEventListener("click", async () => {
      if (!activeRun) return;
      if (activeRun.pollTimer) clearTimeout(activeRun.pollTimer);
      try {
        await fetch("/api/run-stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_id: activeRun.run_id }),
        });
      } catch (_) {}
      setProgress("已停止", "");
      btnRun.hidden = false; btnStop.hidden = true; activeRun = null;
    });

    fillOptions();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
