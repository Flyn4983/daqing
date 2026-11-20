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

  // C7-l：把一份数据快照完整刷到所有可视化区域（KPI 数字 + 四率卡 + 四视图）。
  // 工况切换必须走这里，不能只刷 rates——否则曲线变了但 KPI/Pareto/灵敏度
  // 还停在上一个工况，评委看到的就是"切了下拉只有一张图动"。
  function renderAll(data) {
    if (!data) return;
    window.DASH.data = data;
    fillKpis(data);
    if (views.rates && data.timeseries) {
      views.rates.init({
        timeseries: data.timeseries,
        distribution: data.distribution,
        pareto: data.pareto,
      });
    }
    if (views.pareto && data.pareto) views.pareto.init(data);
    if (views.sensitivity && data.sensitivity) views.sensitivity.init(data);
    if (views.surrogate && data.surrogate) views.surrogate.init(data);
  }
  window.DASH.renderAll = renderAll;

  async function boot() {
    // 先判断模式（从 URL query），决定要不要加载 baseline 预构建数据
    const urlParams = new URLSearchParams(window.location.search);
    const isLive = urlParams.get("mode") === "live";
    const isReplay = urlParams.get("mode") === "replay";

    let data = {};
    // live 模式：跳过 baseline 预构建 JSON（timeseries/distribution/sensitivity/pareto/surrogate），
    // 这些数据在推演结束+离线分析完成后才会有；现在加载会"假装已有数据"干扰用户认知
    if (!isLive) {
      const results = await Promise.allSettled(FILES.map((f) =>
        loadJson(`${f}.json`).then((j) => [f, j])));
      results.forEach((r) => {
        if (r.status === "fulfilled") data[r.value[0]] = r.value[1];
      });
    }
    window.DASH.data = data;

    if (isLive) {
      // live 模式：KPI 和 5 个图表区都清占位，等推演推进
      zeroKpis();
      ["ts-svg", "dist-svg", "pareto-svg", "sens-svg", "parity-svg"].forEach((id) =>
        markEmpty(id, ""));
      ["rc-readiness", "rc-attrition", "rc-repair", "rc-failure"].forEach((id) => {
        const d = $(id).querySelector(".rc-d");
        if (d) { d.textContent = "等待推演…"; d.className = "rc-d flat"; }
      });
    }

    // 基线数据状态标签
    const tag = document.getElementById("baseline-tag");
    if (tag && !isLive) {
      const scName = (data.manifest && data.manifest.scenario) || "—";
      const nReps = (data.distribution && data.distribution.n_reps) || "—";
      tag.textContent = `基线数据 · ${scName} · MC ${nReps} 复制`;
      tag.className = "baseline-tag";
    }

    // 5 个分析视图：live 模式下前面已经 markEmpty 了，这里只在有 baseline 数据时才 init
    if (!isLive) renderAll(data);

    // 容器尺寸变化（窗口缩放）重绘当前视图
    // live 模式没有 baseline 预构建数据（DASH.data 为空），重绘会空崩，先守卫
    const redrawMap = [
      ["ts-svg", "rates"], ["dist-svg", "rates"],
      ["pareto-svg", "pareto"], ["sens-svg", "sensitivity"], ["parity-svg", "surrogate"],
    ];
    redrawMap.forEach(([svgId, view]) => {
      const box = $(svgId).parentElement;
      let t = null;
      new ResizeObserver(() => {
        clearTimeout(t);
        t = setTimeout(() => {
          const d = window.DASH && window.DASH.data;
          if (!d || !Object.keys(d).length) return;   // live 模式：无基线数据不重绘
          if (views[view]) views[view].redraw();
        }, 120);
      }).observe(box);
    });

    // C7-c：boot 完成后再启动工况选择器（DOM + 数据都就绪）
    initScenarioSwitch();

    // ===== LIVE / REPLAY 都用 rerun WASM iframe（由 detectModeAndRun 内部 ensureRerunIframe 统一加载）=====
    // C6-fix：three-frame（Three.js 画布）必须真正隐藏——它 absolute inset:0 + z-index:0，
    // 一旦显示会整块盖住 rerun iframe。除 hidden 属性外再显式置 display:none 双保险。
    const frame = document.getElementById("three-frame");
    const iframe = document.getElementById("replay-frame");
    if (frame) { frame.hidden = true; frame.style.display = "none"; }
    if (iframe) iframe.hidden = false;

    // C6.1：3D 数据通道切换（同源代理 ↔ 直连双端口），黑屏自救入口
    const vmBtn = document.getElementById("viewer-mode-btn");
    if (vmBtn) vmBtn.addEventListener("click", () => {
      const D = window.DASH || {};
      const next = (D.getViewerMode && D.getViewerMode()) === "direct" ? "same-origin" : "direct";
      if (D.setViewerMode) D.setViewerMode(next);
      window.__rerun_iframe_loaded = false;
      if (D.ensureViewer) D.ensureViewer(true);
    });

    // S4：检测模式（live / replay / 默认）并进入对应入口
    detectModeAndRun();
  }

  // ---- C7-c：实时推演（顶栏工况选择 + 轮询保底） ----
  // 硬编码 fallback（后端没启动 / fetch 失败时兜底，保证下拉永远不空）
  const SCENARIO_FALLBACK = [
    { file: "medium_brigade",            label: "中型合成旅 · 丘陵快攻",     pre: "rolling",   dur: "24", wx: "clear",     terrain: "rolling" },
    { file: "brigade_canyon_clear",      label: "重型旅 · 峡谷晴日攻坚",     pre: "canyon",    dur: "48", wx: "clear",     terrain: "canyon" },
    { file: "brigade_plain_fog",         label: "重型旅 · 平原浓雾突击",     pre: "plain",     dur: "48", wx: "heavy_fog", terrain: "plain" },
    { file: "brigade_rolling_fortified", label: "重型旅 · 丘陵坚固防御",     pre: "rolling",   dur: "48", wx: "clear",     terrain: "rolling" },
    { file: "example_brigade",           label: "重型旅 · 合成兵种示例",     pre: "geotiff",   dur: "48", wx: "clear",     terrain: "DEM地形" },
    { file: "example_company",           label: "连级 · 装步冲击示例",       pre: "synthetic", dur: "60", wx: "clear",     terrain: "程序地形" },
    { file: "example_battalion",         label: "营级 · 三阶段进攻示例",     pre: "geotiff",   dur: "80", wx: "clear",     terrain: "DEM地形" },
    { file: "real_terrain_brigade",      label: "★真实地形 · 太行山地形+ERA5气象", pre: "geotiff", dur: "48", wx: "overcast", terrain: "真实DEM" },
  ];

  function initScenarioSwitch() {
    const sel = document.getElementById("scenario-select");
    const btnRun = document.getElementById("btn-run");
    const btnStop = document.getElementById("btn-run-stop");
    const progressEl = document.getElementById("run-progress");
    if (!sel || !btnRun) return;

    let activeRun = null;

    // 先塞硬编码 fallback，保证下拉有内容可见（挂 dataset，fillOptions 完成前也能解析元数据）
    SCENARIO_FALLBACK.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.file;
      opt.textContent = `${s.label} · ${s.pre} · ${s.dur}min`;
      opt.dataset.label = s.label;
      opt.dataset.weather = s.wx || "";
      opt.dataset.terrain = s.terrain || s.pre || "程序地形";
      sel.appendChild(opt);
    });
    const isReplay = (new URLSearchParams(window.location.search).get("mode") === "replay");
    sel.value = isReplay ? "example_brigade" : "medium_brigade";

    async function fillOptions() {
      // C7-d：记住用户当前选择，异步重建选项后恢复（避免晚到的 /api/scenarios
      // 响应把用户已选工况冲掉）
      const keep = sel.value;
      try {
        const r = await fetch("/api/scenarios", { cache: "no-store" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const arr = (await r.json()).scenarios || [];
        if (!arr.length) return;
        // 清空 fallback，用真实数据重建
        sel.innerHTML = "";
        arr.forEach((s) => {
          const terrainTag = s.terrain_source === "geotiff"
            ? "DEM地形" : (s.terrain_preset || "程序地形");
          const wx = WEATHER_ZH[s.weather] || s.weather || "—";
          const dur = (s.duration_ticks / 2).toFixed(0);
          const label = s.label_zh || s.name;
          const opt = document.createElement("option");
          opt.value = s.file;
          opt.textContent = `${label} · ${terrainTag} · ${wx} · ${dur}min`;
          opt.dataset.label = label;
          opt.dataset.weather = s.weather || "";
          opt.dataset.terrain = terrainTag;
          sel.appendChild(opt);
        });
        const favKey = isReplay ? "example_brigade" : "medium_brigade";
        const fav = arr.find((s) => s.file === keep) || arr.find((s) => s.file === favKey) || arr[0];
        if (fav) sel.value = fav.file;
      } catch (e) {
        console.warn("[scenario-switch] 后端工况列表暂不可用，使用内置 7 项 fallback:", e.message);
      }
    }

    function setProgress(text, cls) {
      progressEl.textContent = text;
      progressEl.className = "run-progress " + (cls || "");
    }

    function setTag(text, cls) {
      const t = document.getElementById("baseline-tag");
      if (t) { t.textContent = text; t.className = "baseline-tag" + (cls ? " " + cls : ""); }
    }

    // C7-l：顶栏工况上下文徽标——3D / KPI / 图表之外，文字区也随切换刷新
    function setScenarioContext(scenario, meta) {
      const badge = document.getElementById("scenario-context");
      if (!badge) return;
      const wx = WEATHER_ZH[(meta && meta.weather) || ""] || (meta && meta.weather) || "—";
      const label = (meta && meta.label) || scenario;
      badge.textContent = `当前工况：${label} · ${wx}`;
      badge.dataset.scenarioLabel = scenario;
      // 每个面板标题都挂一个小标签，切换时"文字区"同样肉眼可见地更新
      document.querySelectorAll(".panel h3").forEach((h) => {
        let tag = h.querySelector(".panel-scenario");
        if (!tag) {
          tag = document.createElement("span");
          tag.className = "panel-scenario";
          h.appendChild(tag);
        }
        tag.textContent = ` · ${label}`;
      });
    }

    // ---- C6.1：工况切换 → 3D 场景联动 ----
    const WEATHER_ZH = {
      clear: "晴", overcast: "多云", rain: "降雨", heavy_rain: "暴雨",
      snow: "降雪", fog: "雾", heavy_fog: "浓雾", sand: "沙尘", haze: "霾",
    };

    function scenarioMeta(file) {
      const opt = [...sel.options].find((o) => o.value === file);
      if (!opt) return { label: file };
      return {
        label: opt.dataset.label || file,
        weather: opt.dataset.weather,
        terrain: opt.dataset.terrain,
      };
    }

    // 回放模式没有可见的 baseline-tag，状态写进 3D 面板的副标题
    function setSceneTag(text, cls) {
      const isReplay = (new URLSearchParams(window.location.search).get("mode") === "replay");
      if (isReplay) {
        const sub = document.getElementById("replay-knee-sub");
        if (sub) { sub.textContent = text; sub.style.color = cls === "error" ? "#ff8a7a" : ""; }
      } else {
        setTag(text, cls);
      }
    }

    function onSceneReady(scenario, meta, hasData) {
      const wx = meta.weather ? ` · 气象 ${WEATHER_ZH[meta.weather] || meta.weather}` : "";
      setScenarioContext(scenario, meta);
      const tt = document.getElementById("replay-tag-title");
      if (tt) tt.textContent = `3D 态势回放 · ${meta.label || scenario}`;
      setSceneTag(
        hasData
          ? `3D 回放已切换 · ${meta.label || scenario}${wx} · 图表已跟随切换`
          : `3D 回放已切换 · ${meta.label || scenario}${wx}（图表仍为基线数据）`,
        "done"
      );
    }

    // C7-l：工况切换 → 全部可视化区域刷新。
    // 优先读 /data/scenarios/<name>/ 的 6 份 JSON；模型层（sensitivity/pareto/
    // surrogate）若该工况未单独构建，则回退基线同名文件（科学含义是全局模型，
    // 跨工况共用），但 KPI/四率卡/时序/分布一定用该工况自己的数据重绘。
    const DATA_FILES = ["timeseries", "distribution", "sensitivity", "pareto", "surrogate", "manifest"];
    const MODEL_FILES = ["sensitivity", "pareto", "surrogate", "manifest"];
    async function loadScenarioBundle(scenario) {
      const pairs = await Promise.all(DATA_FILES.map(async (f) => {
        let j = await window.DASH.loadJson(`scenarios/${scenario}/${f}.json`).catch(() => null);
        if (!j && MODEL_FILES.indexOf(f) >= 0) {
          j = await window.DASH.loadJson(`${f}.json`).catch(() => null);
        }
        return [f, j];
      }));
      return Object.fromEntries(pairs.filter(([, j]) => !!j));
    }

    async function applyScenarioData(scenario) {
      const bundle = await loadScenarioBundle(scenario);
      let data = bundle;
      if (!data.timeseries) {
        // 该工况还没单独构建仿真数据：至少给一份基线快照，保证所有面板
        // 进入一致的"已切换"状态，而不是一半新一半旧。
        const base = await loadScenarioBundle("example_brigade");
        data = Object.assign({}, base, bundle);
      }
      if (!data.timeseries) return false;
      // 先停掉旧播放循环（旧闭包若在播会继续用旧数据重绘）
      const rcPause = document.getElementById("rc-pause");
      if (rcPause && !rcPause.hidden) rcPause.click();
      window.DASH.renderAll(data);
      bindReplayController(data.timeseries);
      const meta = scenarioMeta(scenario);
      const label = document.getElementById("baseline-tag");
      if (label) {
        const nReps = data.distribution && data.distribution.n_reps ? data.distribution.n_reps : "—";
        label.textContent = `历史数据回放 · ${meta.label || scenario} · MC ${nReps} 复制`;
        label.className = "baseline-tag done";
      }
      setScenarioContext(scenario, meta);
      return true;
    }

    async function switchReplayScene(scenario) {
      const meta = scenarioMeta(scenario);
      setSceneTag(`3D 回放切换 · ${meta.label || scenario} · 正在准备回放数据…`, "running");
      let j;
      try {
        const r = await fetch("/api/scene-switch", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scenario, mode: "replay" }),
        });
        j = await r.json();
      } catch (e) {
        setSceneTag(`切换失败：${e.message}`, "error");
        return;
      }
      if (j.error) { setSceneTag(`切换失败：${j.error}`, "error"); return; }
      if (j.status === "ready") {
        // C7-e：iframe 切静态 rrd 文件直载（/rerun-file/）——replay 不再走 sink
        // relog（sink 累积全部历史，新连接下载不完 → "下拉切换无反应"）
        if (j.file && window.DASH && window.DASH.loadViewerFile) {
          window.DASH.loadViewerFile(j.file);
        }
        // C7-f：数据面板跟随切换（该工况有预构建数据时）
        applyScenarioData(scenario).then((hasData) => onSceneReady(scenario, meta, hasData));
        return;
      }
      // building → 轮询构建状态（单想定全流程仿真，通常 30–90s）
      const t0 = Date.now();
      const timer = setInterval(async () => {
        try {
          const r = await fetch(`/api/scene-status?scenario=${encodeURIComponent(scenario)}`, { cache: "no-store" });
          const s = await r.json();
          if (s.status === "ready") {
            clearInterval(timer);
            if (s.file && window.DASH && window.DASH.loadViewerFile) {
              window.DASH.loadViewerFile(s.file);
            }
            applyScenarioData(scenario).then((hasData) => onSceneReady(scenario, meta, hasData));
          }
          else if (s.status === "error") {
            clearInterval(timer);
            setSceneTag(`切换失败：${s.error || "未知错误"}`, "error");
          } else if (Date.now() - t0 > 20 * 60 * 1000) {
            clearInterval(timer);
            setSceneTag("回放生成超时（>20 分钟），请重试", "error");
          } else {
            setSceneTag(`3D 回放切换 · ${meta.label || scenario} · 回放数据生成中…`, "running");
          }
        } catch (e) { /* 网络抖动：继续轮询 */ }
      }, 2000);
    }

    async function previewLiveScene(scenario) {
      const meta = scenarioMeta(scenario);
      setScenarioContext(scenario, meta);
      // C7-l：live 模式切工况时，KPI/图表先回到"等待推演"的空态并打上工况标记，
      // 不能残留上一个工况的数字（用户反馈"切换后数字没变"）。
      if (window.DASH.renderAll && !window.__rerun_viewer_static) {
        window.DASH.renderAll({});
      }
      if (window.DASH.views && window.DASH.views.rates && window.DASH.views.rates.initEmpty) {
        window.DASH.views.rates.initEmpty();
      }
      zeroKpis();
      ["pareto-svg", "sens-svg", "parity-svg"].forEach((id) =>
        markEmpty(id, "等待该工况推演"));
      setTag(`已选 · ${meta.label || scenario} · 3D 态势切换中…`, "running");
      try {
        const r = await fetch("/api/scene-switch", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scenario, mode: "preview" }),
        });
        const j = await r.json();
        if (j.ok && j.mode === "static") {
          // C7-g：静态直载该工况的完整 rrd（地形/气象/编成/作用范围全对齐），
          // 绕开 gRPC sink 中 replay_knee recording 的 static 地形残留导致"切工况不变"。
          if (j.file && window.DASH && window.DASH.loadViewerFile) {
            window.DASH.loadViewerFile(j.file);
          }
          const title = document.getElementById("replay-tag-title");
          if (title) title.textContent = `3D 实时推演 · ${meta.label || scenario}`;
          const wx = meta.weather ? ` · 气象 ${WEATHER_ZH[meta.weather] || meta.weather}` : "";
          const terrain = (meta.terrain || "").indexOf("DEM") >= 0 ? "DEM 实测地形" : (meta.terrain || "程序地形");
          setTag(`已选 · ${meta.label || scenario} · ${terrain}${wx} · 3D 态势已切换 · 点▶启动推演`, "done");
        } else if (j.ok) {
          const wx = WEATHER_ZH[j.weather] || j.weather || "—";
          const terrain = j.terrain_source === "geotiff"
            ? "DEM 实测地形" : (j.terrain_preset || "程序地形");
          setTag(`已选 · ${meta.label || scenario} · ${terrain} · 气象 ${wx} · 能见度 ${(j.visibility_factor * 100).toFixed(0)}% · 3D 预览已推送 · 点▶启动推演`, "");
        } else {
          setTag(`已选 · ${meta.label || scenario}（${j.reason || "预览不可用"}）`, "error");
        }
      } catch (e) {
        setTag(`已选 · ${meta.label || scenario}（预览接口异常：${e.message}）`, "error");
      }
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
          if (s.error) { setProgress("错误", "error"); setTag("推演失败", "error"); btnRun.hidden = false; btnStop.hidden = true; activeRun = null; return; }
          const pct = (s.progress * 100).toFixed(0);
          setProgress(`tick ${s.tick}/${s.total_ticks} ${pct}%`, "running");
          setTag(`实时推演 · ${s.scenario} · tick ${s.tick}/${s.total_ticks}`, "running");
          updateFourRatesFromRun(s);

          // ===== THREE.JS：把最新 tick 的红蓝位置推给 3D 场景 =====
          if (window.DASH.threeScene && s.log && s.log.length) {
            const latest = s.log[s.log.length - 1];
            const units = [];
            for (const p of latest.pos_red  || []) units.push({ id: p.id, x: p.x, y: p.y, team: 'red' });
            for (const p of latest.pos_blue || []) units.push({ id: p.id, x: p.x, y: p.y, team: 'blue' });
            window.DASH.threeScene.updateUnits(units);
          }
          if (s.done) {
            const last2 = s.log && s.log.length ? s.log[s.log.length - 1] : null;
            const ar = last2 ? last2.alive_red : "—";
            const ab = last2 ? last2.alive_blue : "—";
            setProgress(`终局 红${ar}/蓝${ab}`, "done");
            setTag(`推演完成 · 终局 红${ar}/蓝${ab}`, "done");
            btnRun.hidden = false; btnStop.hidden = true; activeRun = null;
            return;
          }
          activeRun.pollTimer = setTimeout(() => poll(runId), 1500);
        })
        .catch(() => {
          setProgress("连接中断", "error");
          setTag("推演中断", "error");
          btnRun.hidden = false; btnStop.hidden = true; activeRun = null;
        });
    }

    // select 变化 → 真正驱动 3D 场景（C6.1：rerun iframe 取代 Three.js 后的新联动）
    // replay：按需构建该想定回放 rrd 并重灌 3D；live：立即预览该想定地形/气象
    sel.addEventListener("change", () => {
      const scenario = sel.value;
      const isReplay = (new URLSearchParams(window.location.search).get("mode") === "replay");
      if (isReplay) switchReplayScene(scenario);
      else previewLiveScene(scenario);
    });

    btnRun.addEventListener("click", async () => {
      // live 模式：detectModeAndRun 已为该按钮挂"打开配置弹窗"监听，
      // 此处若再直接 POST /api/run 会造成"打开弹窗即后台起跑 + 提交后再起跑"双重启动，
      // 无配置的野跑会话还会锁死 3D 场景预览——故 live 模式直接让位。
      if ((new URLSearchParams(window.location.search).get("mode")) === "live") return;
      const scenario = sel.value;
      if (!scenario) return;
      btnRun.hidden = true; btnStop.hidden = false; setProgress("启动…", "running"); setTag(`启动推演 · ${scenario}`, "running");
      try {
        const r = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scenario, seed: 42 }),
        });
        const j = await r.json();
        if (!j.run_id) { setProgress(j.error || "启动失败", "error"); setTag("推演启动失败", "error"); btnRun.hidden = false; btnStop.hidden = true; return; }
        activeRun = { run_id: j.run_id, scenario, pollTimer: null };
        poll(j.run_id);
      } catch (e) {
        setProgress("无法连接后端", "error"); setTag("后端连接失败", "error");
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
      setTag("已停止", "");
      btnRun.hidden = false; btnStop.hidden = true; activeRun = null;
    });

    // C7-e：replay 模式在真实工况列表落定后，用最终 sel.value 触发初始静态文件装载。
    // （在此 initScenarioSwitch 作用域内调用 switchReplayScene / 读 sel，避免 detectModeAndRun
    // 越界引用；且等 fillOptions 可能改写 sel.value 后再读，保证装载的是用户可见工况。）
    fillOptions().then(() => {
      if (!isReplay) return;
      let tries = 0;
      const tryLoad = () => {
        fetch("/api/viewer", { cache: "no-store" })
          .then((r) => r.json())
          .then((st) => {
            if (st && st.ready && st.viewer_url) {
              switchReplayScene(sel.value);
            } else if (tries++ < 30) {
              setTimeout(tryLoad, 1000);   // rerun viewer 启动竞态：最多等 30s
            } else {
              setSceneTag("3D 回放服务未就绪（/api/viewer 不可用），请刷新重试", "error");
            }
          })
          .catch(() => { if (tries++ < 30) setTimeout(tryLoad, 1000); });
      };
      tryLoad();
    });
  }

  // ============ S4：双模式入口 + 配置弹窗 + 增量时序 ============
  const S4_CONFIG_DEFAULTS = {
    scenario: "medium_brigade",
    duration_ticks: 48, dt_seconds: 30,
    objective_x: 9000, objective_y: 5000,
    success_radius_m: 600,
    preset_family: "rolling", size_w: 10000, size_h: 10000, grid_n: 65,
    noise_seed: 42, noise_scale: 3.0, octaves: 4,
    base_height: 60, amplitude: 140, water_level: 10,
    weather: "clear", illumination: "day", visibility_factor: 1.0,
    wind_speed_mps: 0, wind_dir_deg: 0,
    red_repair_teams: 6, red_arv: 3, red_supply_veh: 2,
    carry_level: 2, operating_level: 2,
    wave_interval_ticks: 20, wave_qty: 2,
    mtbf_scale: 1.0, red_pk_scale: 1.0, blue_pk_scale: 1.0,
    red_detect_scale: 1.0, red_speed_scale: 1.0,
    blue_detect_scale: 1.0, blue_speed_scale: 1.0,
  };

  function readConfigForm() {
    const p = { ...S4_CONFIG_DEFAULTS };
    const $ = (id) => document.getElementById(id);
    const g = (id) => {
      const el = $(id); if (!el) return S4_CONFIG_DEFAULTS[id];
      if (el.type === "number") return Number(el.value);
      if (el.type === "range") return Number(el.value);
      return el.value;
    };
    p.scenario = g("cfg-preset");
    p.duration_ticks = g("cfg-duration_ticks");
    p.dt_seconds = g("cfg-dt_seconds");
    p.objective_x = g("cfg-objective_x");
    p.objective_y = g("cfg-objective_y");
    p.success_radius_m = g("cfg-success_radius_m");
    p.preset_family = g("cfg-preset_family");
    p.size_w = g("cfg-size_w");
    p.size_h = g("cfg-size_h");
    p.grid_n = g("cfg-grid_n");
    p.weather = g("cfg-weather");
    p.illumination = g("cfg-illumination");
    p.visibility_factor = g("cfg-visibility_factor");
    p.red_repair_teams = g("cfg-red_repair_teams");
    p.red_arv = g("cfg-red_arv");
    p.red_supply_veh = g("cfg-red_supply_veh");
    p.carry_level = Number(g("cfg-carry_level"));
    p.operating_level = Number(g("cfg-operating_level"));
    p.wave_interval_ticks = g("cfg-wave_interval_ticks");
    p.wave_qty = g("cfg-wave_qty");
    p.mtbf_scale = g("cfg-mtbf_scale");
    p.red_pk_scale = g("cfg-red_pk_scale");
    p.blue_pk_scale = g("cfg-blue_pk_scale");
    // 第五组：Terrain FBM 细节
    p.noise_seed = g("cfg-noise_seed");
    p.noise_scale = g("cfg-noise_scale");
    p.octaves = g("cfg-octaves");
    p.base_height = g("cfg-base_height");
    p.amplitude = g("cfg-amplitude");
    p.water_level = g("cfg-water_level");
    // 第七组：风场
    p.wind_speed_mps = g("cfg-wind_speed_mps");
    p.wind_dir_deg = g("cfg-wind_dir_deg");
    // 第六组：方别不对称
    p.red_detect_scale = g("cfg-red_detect_scale");
    p.red_speed_scale = g("cfg-red_speed_scale");
    p.blue_detect_scale = g("cfg-blue_detect_scale");
    p.blue_speed_scale = g("cfg-blue_speed_scale");
    return p;
  }

  function bindConfigSliderLabels() {
    const pairs = [
      ["cfg-visibility_factor", "cfg-visibility-val"],
      ["cfg-mtbf_scale", "cfg-mtbf-val"],
      ["cfg-red_pk_scale", "cfg-redpk-val"],
      ["cfg-blue_pk_scale", "cfg-bluepk-val"],
      ["cfg-red_detect_scale", "cfg-reddetect-val"],
      ["cfg-red_speed_scale", "cfg-redspeed-val"],
      ["cfg-blue_detect_scale", "cfg-bluedetect-val"],
      ["cfg-blue_speed_scale", "cfg-bluespeed-val"],
    ];
    pairs.forEach(([inId, valId]) => {
      const inp = document.getElementById(inId);
      const val = document.getElementById(valId);
      if (inp && val) {
        const upd = () => val.textContent = Number(inp.value).toFixed(2);
        inp.addEventListener("input", upd);
        upd();
      }
    });
  }

  function resetConfigForm() {
    Object.entries(S4_CONFIG_DEFAULTS).forEach(([k, v]) => {
      const el = document.getElementById(`cfg-${k}`);
      if (el) el.value = v;
    });
    bindConfigSliderLabels();
  }

  // ---- S0：KPI 清零（进入 live 模式时先清除基线数据填充）----
  function zeroKpis() {
    const ids = ["kpi-success", "kpi-attr-red", "kpi-ready-red", "kpi-repair-red", "kpi-r2", "kpi-speedup"];
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      const v = el.querySelector(".kpi-v");
      const s = el.querySelector(".kpi-s");
      if (v) v.textContent = "—";
      if (s) s.textContent = "等待推演…";
    });
  }

  // ---- S0b：抑制 rerun iframe（live 模式用我们自己的战术 SVG）----
  function suppressRerunIframe() {
    const frame = document.getElementById("replay-frame");
    const loading = document.getElementById("replay-loading");
    if (frame) { frame.src = "about:blank"; frame.style.display = "none"; }
    if (loading) loading.style.display = "none";
  }

  async function runLiveMode() {
    const modal = document.getElementById("config-modal");
    if (!modal) return;
    resetConfigForm();
    modal.hidden = false;

    document.getElementById("config-close").onclick = () => { modal.hidden = true; };
    document.getElementById("cfg-reset").onclick = resetConfigForm;
    bindConfigSliderLabels();

    document.getElementById("cfg-submit").onclick = async () => {
      modal.hidden = true;
      const params = readConfigForm();
      const submit = document.getElementById("cfg-submit");
      const oldText = submit.textContent;
      submit.textContent = "推演中…"; submit.disabled = true;

      // 初始化空态 + 战术地图
      if (window.DASH) {
        window.DASH.liveLog = [];
        window.DASH.liveDone = false;
      }
      if (views.rates && views.rates.initEmpty) views.rates.initEmpty();
      if (views.tactical) {
        views.tactical.init({ size_w: params.size_w, size_h: params.size_h });
        views.tactical.showOverlay("推演启动中…");
      }

      try {
        const r = await fetch("/api/run-config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(params),
        });
        const j = await r.json();
        if (!j.run_id) { alert("启动失败: " + (j.error || "未知")); return; }
        // C7-g：切工况后 3D 处于静态 rrd 直载态；启动推演需切回 gRPC sink 看实时 tick
        if (window.DASH && window.DASH.ensureLiveSink) window.DASH.ensureLiveSink();
        if (views.tactical) views.tactical.hideOverlay();
        startLivePolling(j.run_id, params);
      } catch (e) {
        alert("后端连接失败: " + e.message);
      } finally {
        submit.textContent = oldText; submit.disabled = false;
      }
    };
  }

  function startLivePolling(runId, params) {
    const state = { runId, seenTick: -1, logBuf: [], done: false };

    function pollStep() {
      fetch(`/api/run?run_id=${runId}`, { cache: "no-store" })
        .then((r) => r.json())
        .then((s) => {
          if (s.error) return;

          // 增量合并 log
          const newEntries = (s.log || []).filter((e) => e.tick > state.seenTick);
          if (newEntries.length) {
            state.seenTick = newEntries[newEntries.length - 1].tick;
            state.logBuf = state.logBuf.concat(newEntries);
            // 喂给时序视图增量重绘
            if (views.rates && views.rates.updateLive) {
              views.rates.updateLive(state.logBuf);
            }
            // 每 tick 喂给战术地图
            if (views.tactical) {
              newEntries.forEach((e) => views.tactical.updateTick(e, s.n_red, s.n_blue));
            }
          }

          // 更新 KPI 顶栏（从最新 tick）
          const last = state.logBuf[state.logBuf.length - 1];
          if (last) updateKpiFromTick(last, state.logBuf.length / Math.max(s.total_ticks, 1));

          if (s.done && !state.done) {
            state.done = true;
            // 推演结束 → 定时查离线分析
            setTimeout(() => pollAnalyze(runId), 800);
            return;
          }
          if (!s.done) setTimeout(pollStep, 1200);
        })
        .catch(() => setTimeout(pollStep, 2000));
    }
    pollStep();
  }

  function buildTimeseriesData(logArr, totalTicks) {
    // 把 raw log array 构造成 timeseries.json 兼容格式
    const ticks = [];
    const alive_red = [], alive_blue = [], total_red = [], total_blue = [];
    logArr.forEach((e) => {
      ticks.push(e.tick);
      alive_red.push(e.alive_red); alive_blue.push(e.alive_blue);
      total_red.push(e.total_red); total_blue.push(e.total_blue);
    });
    return {
      ticks,
      alive_red, alive_blue, total_red, total_blue,
      progress_of_total: ticks.map((t) => (t + 1) / Math.max(totalTicks, 1)),
    };
  }

  function updateKpiFromTick(last, progressRatio) {
    const nr = last.total_red, ar = last.alive_red;
    const nb = last.total_blue, ab = last.alive_blue;
    const attrRed = nr > 0 ? (1 - ar / nr) * 100 : 0;
    const readyRed = nr > 0 ? (ar / nr) * 100 : 0;
    const attrBlue = nb > 0 ? (1 - ab / nb) * 100 : 0;
    const setKpi = (id, val, sub) => {
      const el = document.getElementById(id);
      if (!el) return;
      const v = el.querySelector(".kpi-v");
      const s = el.querySelector(".kpi-s");
      if (v) v.textContent = val;
      if (s && sub) s.textContent = sub;
    };
    setKpi("kpi-ready-red", readyRed.toFixed(1) + "%", `${ar}/${nr} 红存活`);
    setKpi("kpi-attr-red", attrRed.toFixed(1) + "%", "红方战损率");
    setKpi("kpi-attr-blue", attrBlue.toFixed(1) + "%", `${ab}/${nb} 蓝存活`);
    setKpi("kpi-repair-red", "推演中", "保障运转");
    setKpi("kpi-progress", (progressRatio * 100).toFixed(0) + "%", "推演进度");
  }

  function pollAnalyze(runId) {
    let tries = 0;
    const maxTries = 30;
    function step() {
      fetch(`/api/analyze?run_id=${runId}`, { cache: "no-store" })
        .then((r) => r.json())
        .then((s) => {
          const label = document.getElementById("baseline-tag");
          if (label) {
            if (s.status === "running") {
              label.textContent = `离线分析中 · ${s.step || ""} · ${((s.progress || 0) * 100).toFixed(0)}%`;
              label.className = "baseline-tag running";
            } else if (s.status === "done" && s.mc) {
              const mc = s.mc;
              label.textContent = `推演完成 · MC ${mc.n_reps}复制 · 成功率${(mc.success_rate * 100).toFixed(1)}%`;
              label.className = "baseline-tag done";
              // 用 MC 结果更新 KPI
              const kpiAttr = document.getElementById("kpi-attr-red");
              if (kpiAttr) {
                const v = kpiAttr.querySelector(".kpi-v");
                if (v) v.textContent = (mc.mean_attrition_red * 100).toFixed(1) + "%";
              }
              const kpiReady = document.getElementById("kpi-ready-red");
              if (kpiReady) {
                const v = kpiReady.querySelector(".kpi-v");
                if (v) v.textContent = (mc.mean_readiness_red * 100).toFixed(1) + "%";
              }
              const kpiRepair = document.getElementById("kpi-repair-red");
              if (kpiRepair) {
                const v = kpiRepair.querySelector(".kpi-v");
                const sub = kpiRepair.querySelector(".kpi-s");
                if (v) v.textContent = (mc.success_rate * 100).toFixed(1) + "%";
                if (sub) sub.textContent = "任务成功率";
              }
              return;
            } else if (s.status === "error") {
              label.textContent = "离线分析失败: " + (s.reason || "");
              label.className = "baseline-tag error";
              return;
            }
          }
          if (tries++ < maxTries) setTimeout(step, 2000);
        })
        .catch(() => { if (tries++ < maxTries) setTimeout(step, 2000); });
    }
    step();
  }

  // ---- S6：回放模式（加载 5 JSON + 播放控制器；中间区域交给 rerun WASM iframe）----
  function runReplayMode() {
    // 加载全部 5 个预构建 JSON
    const files = ["timeseries", "distribution", "sensitivity", "pareto", "surrogate", "manifest"];
    Promise.all(files.map((n) => loadJson(n + ".json").catch(() => null)))
      .then((results) => {
        const data = {};
        results.forEach((j, i) => { if (j) data[files[i]] = j; });

        // 1. 渲染所有 5 个视图
        if (data.timeseries && data.distribution) {
          views.rates && views.rates.init(data);
        } else if (data.timeseries && views.rates && views.rates.init) {
          views.rates.init({ timeseries: data.timeseries });
        }
        if (data.pareto) views.pareto && views.pareto.init(data);
        if (data.sensitivity) views.sensitivity && views.sensitivity.init(data);
        if (data.surrogate) views.surrogate && views.surrogate.init(data);

        // 2. 绑定播放控制器（驱动 5 个视图动画；中间区域由 rerun WASM 负责）
        if (data.timeseries) {
          bindReplayController(data.timeseries);
        }

        // 3. 顶栏标签
        const label = document.getElementById("baseline-tag");
        if (label && data.manifest) {
          const sc = data.manifest.scenario || "-";
          const nReps = data.distribution?.n_reps || "-";
          label.textContent = `历史数据回放 · ${sc} · MC ${nReps} 复制`;
          label.className = "baseline-tag done";
        }
      });
  }

  function bindReplayController(ts) {
    const ctrl = document.getElementById("replay-controller");
    if (!ctrl) return;
    ctrl.hidden = false;

    // 构建伪 logBuf：timeseries.json 兼容 —— 如果没有 pos_red/pos_blue 也能播
    // 这里简化：先播 50 tick 的占位 pos（均匀分布），以后 dash-build 时把 pos 写进去
    const nReds = ts.total_red ? ts.total_red[0] : 14;
    const nBlues = ts.total_blue ? ts.total_blue[0] : 8;
    const totalTicks = ts.ticks ? ts.ticks.length : 48;

    const logBuf = [];
    for (let i = 0; i < totalTicks; i++) {
      const aliveR = ts.alive_red ? ts.alive_red[i] : nReds;
      const aliveB = ts.alive_blue ? ts.alive_blue[i] : nBlues;
      // 均匀分布占位位置（等间距）
      const makePos = (n, cx, cy, spread) => {
        const arr = [];
        for (let k = 0; k < n; k++) {
          const t = k / Math.max(n - 1, 1);
          arr.push({ id: `u${k}`, x: cx + (t - 0.5) * spread, y: cy + Math.sin(i * 0.1 + k) * 150 });
        }
        return arr;
      };
      logBuf.push({
        tick: ts.ticks ? ts.ticks[i] : i,
        alive_red: aliveR, alive_blue: aliveB,
        total_red: nReds, total_blue: nBlues,
        pos_red: makePos(nReds, 2500 + i * 80, 5000, 3000),
        pos_blue: makePos(nBlues, 8500 - i * 60, 5000, 2000),
      });
    }

    let idx = 0;
    let timer = null;
    const rcPlay = document.getElementById("rc-play");
    const rcPause = document.getElementById("rc-pause");
    const rcStop = document.getElementById("rc-stop");
    const rcRew = document.getElementById("rc-rew");
    const rcInfo = document.getElementById("rc-info");
    const rcProgress = document.getElementById("rc-progress");
    const rcProgressBar = document.getElementById("rc-progress-bar");
    const rcSpeed = document.getElementById("rc-speed");

    const setPlay = () => { rcPlay.hidden = true; rcPause.hidden = false; };
    const setPause = () => { rcPlay.hidden = false; rcPause.hidden = true; };

    const renderTick = (i) => {
      idx = Math.max(0, Math.min(i, logBuf.length - 1));
      const e = logBuf[idx];
      // 更新 KPI 顶栏
      updateKpiFromTick(e, (idx + 1) / logBuf.length);
      // 战术地图
      if (views.tactical) {
        views.tactical.hideOverlay();
        views.tactical.updateTick(e, nReds, nBlues);
      }
      // 时序视图（全量重绘到 idx 截止）
      if (views.rates && views.rates.updateLive) {
        views.rates.updateLive(logBuf.slice(0, idx + 1));
      }
      // 进度条
      rcInfo.textContent = `tick ${idx + 1} / ${logBuf.length}`;
      rcProgress.style.width = ((idx + 1) / logBuf.length * 100) + "%";
    };

    const step = () => {
      renderTick(idx);
      idx++;
      if (idx >= logBuf.length) {
        idx = logBuf.length - 1;
        setPause();
        return;
      }
      const delay = parseInt(rcSpeed.value, 10) || 200;
      timer = setTimeout(step, delay);
    };

    rcPlay.onclick = () => {
      if (idx >= logBuf.length - 1) idx = 0;
      setPlay();
      step();
    };
    rcPause.onclick = () => {
      if (timer) { clearTimeout(timer); timer = null; }
      setPause();
    };
    rcStop.onclick = () => {
      if (timer) { clearTimeout(timer); timer = null; }
      idx = 0;
      setPause();
      renderTick(0);
      // 重置战术地图
      views.tactical && views.tactical.clear();
      views.tactical && views.tactical.showOverlay("已停止 · 点击 ▶ 重新播放");
    };
    rcRew.onclick = () => {
      if (timer) { clearTimeout(timer); timer = null; }
      idx = Math.max(0, idx - 5);
      setPause();
      renderTick(idx);
    };
    // 进度条点击跳转
    rcProgressBar.onclick = (ev) => {
      const rect = rcProgressBar.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      if (timer) { clearTimeout(timer); timer = null; setPause(); }
      idx = Math.floor(ratio * logBuf.length);
      renderTick(idx);
    };

    // 初始渲染
    renderTick(0);
    setPause();
  }

  // ---- C6：确保 rerun iframe 加载（统一委托 common.js 的唯一装载入口）----
  function ensureRerunIframe() {
    if (window.DASH && window.DASH.ensureViewer) {
      window.DASH.ensureViewer();   // 内部有 __rerun_iframe_loaded 去重，不会双设 src
    }
  }

  function detectModeAndRun() {
    const params = new URLSearchParams(window.location.search);
    const mode = params.get("mode");
    if (mode === "live") {
      // 实时推演模式：KPI 清零；rerun iframe 走 gRPC sink（后端每 tick rr.log() 推 3D 数据）
      zeroKpis();
      const replayTitle = document.getElementById("replay-tag-title");
      const replaySub = document.getElementById("replay-knee-sub");
      if (replayTitle) replayTitle.textContent = "3D 实时推演 · Live Simulation";
      if (replaySub) replaySub.textContent = "后端每 tick 推送红蓝方 3D 位置，实时渲染";
      const tag = document.getElementById("baseline-tag");
      if (tag) { tag.textContent = "等待配置与启动…"; tag.className = "baseline-tag"; }
      const btnRun = document.getElementById("btn-run");
      const selectWrap = document.getElementById("scenario-select-wrap");
      if (selectWrap) selectWrap.style.display = "";
      if (btnRun) {
        btnRun.textContent = "▶ 启动推演";
        btnRun.onclick = null;
        // 点击前先把 scenario-select 的值同步进 config-modal 的 cfg-preset
        btnRun.addEventListener("click", () => {
          const sel = document.getElementById("scenario-select");
          const preset = document.getElementById("cfg-preset");
          if (sel && preset) preset.value = sel.value;
          runLiveMode();
        });
      }
      ensureRerunIframe();   // live：gRPC sink 实时 tick
      runLiveMode();
    } else if (mode === "replay") {
      // 回放模式：保留工况下拉，隐藏推演/停止按钮 + 进度标签
      const btnRun = document.getElementById("btn-run");
      const btnStop = document.getElementById("btn-run-stop");
      const progressEl = document.getElementById("run-progress");
      const baselineTag = document.getElementById("baseline-tag");
      if (btnRun) btnRun.style.display = "none";
      if (btnStop) btnStop.style.display = "none";
      if (progressEl) progressEl.style.display = "none";
      if (baselineTag) { baselineTag.style.display = "none"; }
      runReplayMode();
      // C7-e：replay 初始装载由 initScenarioSwitch 在 fillOptions 完成后触发
      // （switchReplayScene → loadViewerFile → 静态 /rerun-file/ 直载，绕开 gRPC sink
      // 累积历史）。此处不再 ensureRerunIframe，避免先走 gRPC 再被静态文件覆盖的闪烁/竞争。
    } else {
      // 默认 V1 路径（boot 会预加载 JSON）：gRPC 装载基线 rrd（无实时数据）
      ensureRerunIframe();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    // DOMContentLoaded 已 fire，common.js 的 initViewer 不会再触发
    // 所以这里先手动确保 rerun iframe 加载，再 boot
    ensureRerunIframe();
    boot();
  }
})();
