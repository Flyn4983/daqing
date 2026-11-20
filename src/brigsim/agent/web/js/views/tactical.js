/* 中间实时战术态势：每 tick 的 pos_red/pos_blue 画单位位置 + 移动轨迹 */
(function () {
  "use strict";
  const { $ } = window.DASH;

  const MAX_TRAIL = 40;      // 轨迹保留 tick 数
  const TRAIL_ALPHA = 0.35;  // 轨迹透明度
  const RED = "#ff6450";
  const BLUE = "#36b6f5";

  // 状态
  let canvasEl = null;
  let trailRed = {};   // unit_id → [{x,y}]
  let trailBlue = {};
  let bounds = { w: 10000, h: 10000 };

  function ensureCanvas() {
    if (canvasEl) return canvasEl;
    const iframe = $("replay-frame");
    if (!iframe) return null;

    // 如果 rerun WASM iframe 已经加载了真实 viewer（不是 about:blank），
    // 我们就不 suppress 它了——让 rerun 3D 成为主画面，SVG 层作为轻量 overlay
    const iframeSrc = iframe.getAttribute("src") || "";
    const rerunLoaded = iframeSrc !== "" && iframeSrc !== "about:blank";
    if (rerunLoaded) {
      // 主动恢复 opacity（哪怕之前被 suppress 过）
      iframe.style.opacity = "1";
      iframe.style.display = "block";
    }

    const svgNS = "http://www.w3.org/2000/svg";
    const wrap = document.createElement("div");
    wrap.id = "tactical-wrap";
    wrap.style.cssText =
      "position:absolute;inset:0;pointer-events:none;overflow:hidden;" +
      "z-index:10;background:transparent;";  // rerun 已加载就透明（让 rerun 透出）
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.style.cssText = "width:100%;height:100%;";
    wrap.appendChild(svg);

    const parent = iframe.parentElement;
    const parentPos = getComputedStyle(parent).position;
    if (parentPos === "static") parent.style.position = "relative";

    // 只在 rerun **未**加载时才 suppress iframe / 隐藏 fallback
    if (!rerunLoaded) {
      const fallback = $("replay-fallback");
      const loading = $("replay-loading");
      if (fallback) fallback.style.display = "none";
      if (loading) loading.style.display = "none";
      iframe.style.opacity = "0.3";
      wrap.style.background = "rgba(5,13,27,0.35)";
    }

    parent.insertBefore(wrap, iframe.nextSibling);
    canvasEl = { wrap, svg, ns: svgNS };
    return canvasEl;
  }

  function setBounds(w, h) {
    bounds.w = w || 10000;
    bounds.h = h || 10000;
    if (canvasEl) {
      canvasEl.svg.setAttribute("viewBox", `0 0 ${bounds.w} ${bounds.h}`);
    }
  }

  function toSvgId(unitId, side) {
    return `tac-${side}-${unitId || "x"}`;
  }

  function drawStaticGrid() {
    if (!canvasEl) return;
    const { svg, ns } = canvasEl;
    svg.innerHTML = "";

    // 坐标网格（1000m 间距）
    const step = 1000;
    for (let x = 0; x <= bounds.w; x += step) {
      const l = document.createElementNS(ns, "line");
      l.setAttribute("x1", x); l.setAttribute("y1", 0);
      l.setAttribute("x2", x); l.setAttribute("y2", bounds.h);
      l.setAttribute("stroke", "rgba(54,182,245,0.12)");
      l.setAttribute("stroke-width", 1);
      svg.appendChild(l);
    }
    for (let y = 0; y <= bounds.h; y += step) {
      const l = document.createElementNS(ns, "line");
      l.setAttribute("x1", 0); l.setAttribute("y1", y);
      l.setAttribute("x2", bounds.w); l.setAttribute("y2", y);
      l.setAttribute("stroke", "rgba(54,182,245,0.12)");
      l.setAttribute("stroke-width", 1);
      svg.appendChild(l);
    }

    // 中心十字
    const cx = bounds.w / 2, cy = bounds.h / 2;
    const cross1 = document.createElementNS(ns, "line");
    cross1.setAttribute("x1", 0); cross1.setAttribute("y1", cy);
    cross1.setAttribute("x2", bounds.w); cross1.setAttribute("y2", cy);
    cross1.setAttribute("stroke", "rgba(127,240,210,0.20)");
    cross1.setAttribute("stroke-width", 1.5);
    svg.appendChild(cross1);
    const cross2 = document.createElementNS(ns, "line");
    cross2.setAttribute("x1", cx); cross2.setAttribute("y1", 0);
    cross2.setAttribute("x2", cx); cross2.setAttribute("y2", bounds.h);
    cross2.setAttribute("stroke", "rgba(127,240,210,0.20)");
    cross2.setAttribute("stroke-width", 1.5);
    svg.appendChild(cross2);

    // 坐标角标
    const corner1 = document.createElementNS(ns, "text");
    corner1.setAttribute("x", 20); corner1.setAttribute("y", 30);
    corner1.setAttribute("fill", "rgba(127,240,210,0.6)");
    corner1.setAttribute("font-size", "14");
    corner1.textContent = `0,0`;
    svg.appendChild(corner1);
    const corner2 = document.createElementNS(ns, "text");
    corner2.setAttribute("x", bounds.w - 80); corner2.setAttribute("y", bounds.h - 10);
    corner2.setAttribute("fill", "rgba(127,240,210,0.6)");
    corner2.setAttribute("font-size", "14");
    corner2.textContent = `${Math.round(bounds.w)},${Math.round(bounds.h)}`;
    svg.appendChild(corner2);
  }

  function renderTrails() {
    if (!canvasEl) return;
    const { svg, ns } = canvasEl;

    // 清除旧轨迹 + 单位
    svg.querySelectorAll(".tac-trail,.tac-unit").forEach((n) => n.remove());

    const drawTrail = (tr, color) => {
      Object.entries(tr).forEach(([uid, pts]) => {
        if (pts.length < 2) return;
        // 只画最近 N 个点
        const recent = pts.slice(-MAX_TRAIL);
        let d = `M ${recent[0].x} ${recent[0].y}`;
        for (let i = 1; i < recent.length; i++) d += ` L ${recent[i].x} ${recent[i].y}`;
        const path = document.createElementNS(ns, "path");
        path.setAttribute("d", d);
        path.setAttribute("fill", "none");
        path.setAttribute("stroke", color);
        path.setAttribute("stroke-width", 1.8);
        path.setAttribute("stroke-linecap", "round");
        path.setAttribute("stroke-linejoin", "round");
        path.setAttribute("opacity", TRAIL_ALPHA);
        path.classList.add("tac-trail");
        svg.appendChild(path);
      });
    };
    drawTrail(trailRed, RED);
    drawTrail(trailBlue, BLUE);
  }

  function renderUnits() {
    if (!canvasEl) return;
    const { svg, ns } = canvasEl;

    const drawUnit = (uid, pos, color) => {
      const g = document.createElementNS(ns, "g");
      g.classList.add("tac-unit");
      g.setAttribute("id", toSvgId(uid, color === RED ? "red" : "blue"));
      g.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);

      // 外光环
      const halo = document.createElementNS(ns, "circle");
      halo.setAttribute("r", 14);
      halo.setAttribute("fill", color);
      halo.setAttribute("opacity", 0.18);
      g.appendChild(halo);

      // 主体
      const body = document.createElementNS(ns, "rect");
      body.setAttribute("x", -6); body.setAttribute("y", -6);
      body.setAttribute("width", 12); body.setAttribute("height", 12);
      body.setAttribute("fill", color);
      body.setAttribute("stroke", "#fff");
      body.setAttribute("stroke-width", 1.2);
      body.setAttribute("rx", 2);
      g.appendChild(body);

      // 航向指示（上方小三角）
      const head = document.createElementNS(ns, "polygon");
      head.setAttribute("points", "0,-10 -3,-6 3,-6");
      head.setAttribute("fill", color);
      head.setAttribute("opacity", 0.9);
      g.appendChild(head);

      // 单位 ID
      const label = document.createElementNS(ns, "text");
      label.setAttribute("y", 24);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("fill", "rgba(220,233,245,0.85)");
      label.setAttribute("font-size", "11");
      label.setAttribute("font-family", "Consolas,monospace");
      label.textContent = uid;
      g.appendChild(label);

      svg.appendChild(g);
    };

    Object.entries(trailRed).forEach(([uid, pts]) => {
      if (pts.length) drawUnit(uid, pts[pts.length - 1], RED);
    });
    Object.entries(trailBlue).forEach(([uid, pts]) => {
      if (pts.length) drawUnit(uid, pts[pts.length - 1], BLUE);
    });
  }

  function updateTick(logEntry, totalRed, totalBlue) {
    if (!canvasEl) ensureCanvas();
    if (!canvasEl) return;

    // 每次 render 前都检查 rerun iframe 状态，主动恢复（解决时序竞态）
    const iframe = $("replay-frame");
    if (iframe) {
      const src = iframe.getAttribute("src") || "";
      if (src !== "" && src !== "about:blank") {
        iframe.style.opacity = "1";
        iframe.style.display = "block";
      }
    }

    // 合并 pos_red/pos_blue 到轨迹
    const pr = logEntry.pos_red || [];
    const pb = logEntry.pos_blue || [];
    pr.forEach((u) => {
      if (!trailRed[u.id]) trailRed[u.id] = [];
      trailRed[u.id].push({ x: u.x, y: u.y });
      if (trailRed[u.id].length > MAX_TRAIL + 20) trailRed[u.id] = trailRed[u.id].slice(-MAX_TRAIL);
    });
    pb.forEach((u) => {
      if (!trailBlue[u.id]) trailBlue[u.id] = [];
      trailBlue[u.id].push({ x: u.x, y: u.y });
      if (trailBlue[u.id].length > MAX_TRAIL + 20) trailBlue[u.id] = trailBlue[u.id].slice(-MAX_TRAIL);
    });

    renderTrails();
    renderUnits();
  }

  function init({ size_w, size_h } = {}) {
    ensureCanvas();
    trailRed = {}; trailBlue = {};
    if (size_w || size_h) setBounds(size_w, size_h);
    drawStaticGrid();
  }

  function clear() {
    trailRed = {}; trailBlue = {};
    if (canvasEl) drawStaticGrid();
  }

  function showOverlay(html) {
    // 用 SVG 顶部加一个半透明提示层
    if (!canvasEl) return;
    const { svg, ns } = canvasEl;
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", "50%"); rect.setAttribute("y", "50%");
    rect.setAttribute("transform", "translate(-180,-24)");
    rect.setAttribute("width", 360); rect.setAttribute("height", 48);
    rect.setAttribute("rx", 8);
    rect.setAttribute("fill", "rgba(5,13,27,0.82)");
    rect.setAttribute("stroke", "rgba(54,182,245,0.4)");
    rect.id = "tac-overlay";
    const text = document.createElementNS(ns, "text");
    text.setAttribute("x", "50%"); text.setAttribute("y", "50%");
    text.setAttribute("text-anchor", "middle"); text.setAttribute("dominant-baseline", "middle");
    text.setAttribute("fill", "#bfe9ff"); text.setAttribute("font-size", "15");
    text.setAttribute("letter-spacing", "2");
    text.textContent = html;
    text.id = "tac-overlay-text";
    svg.querySelectorAll("#tac-overlay, #tac-overlay-text").forEach((n) => n.remove());
    svg.appendChild(rect); svg.appendChild(text);
  }
  function hideOverlay() {
    if (!canvasEl) return;
    canvasEl.svg.querySelectorAll("#tac-overlay, #tac-overlay-text").forEach((n) => n.remove());
  }

  window.DASH.views.tactical = { init, clear, updateTick, setBounds, showOverlay, hideOverlay };
})();
