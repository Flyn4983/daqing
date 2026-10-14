/* 零依赖手写 SVG 图表：折线（多系列+置信带+hover）、横向条形、散点（点击/标记）、纵向类别柱。
   所有图表按容器实际像素渲染（ResizeObserver 触发重绘），深色指挥风配色。 */
(function () {
  "use strict";
  const SVGNS = "http://www.w3.org/2000/svg";
  const C = {
    grid: "#16233b", axis: "#6680a5", text: "#8fa6c8",
    accent: "#36b6f5", accent2: "#34e0c0", muted: "#4e6488",
    red: "#d64537", amber: "#efaa17", purple: "#a855f7",
  };

  function el(name, attrs) {
    const node = document.createElementNS(SVGNS, name);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    return node;
  }

  /** 清空 svg 并按容器尺寸建立画布；返回绘图区几何与标尺。 */
  function prep(svg, m) {
    m = m || { l: 40, r: 12, t: 12, b: 24 };
    const box = svg.parentElement.getBoundingClientRect();
    const W = Math.max(120, box.width), H = Math.max(80, box.height);
    svg.innerHTML = "";
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    return {
      svg, W, H, m, iw, ih,
      x: (v, d) => m.l + (v - d[0]) / (d[1] - d[0] || 1) * iw,
      y: (v, d) => m.t + ih - (v - d[0]) / (d[1] - d[0] || 1) * ih,
    };
  }

  function extent(values, pad) {
    let lo = Infinity, hi = -Infinity;
    values.forEach((v) => {
      if (v === null || v === undefined || Number.isNaN(v)) return;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    });
    if (!isFinite(lo)) return [0, 1];
    if (lo === hi) { lo -= 0.5; hi += 0.5; }
    if (pad) { const s = (hi - lo) * pad; lo -= s; hi += s; }
    return [lo, hi];
  }

  function niceDomain(lo, hi, ticks) {
    // 线性等距刻度（不做 1-2-5 美化，数据域本身规整）
    const out = [];
    for (let i = 0; i <= ticks; i++) out.push(lo + (hi - lo) * i / ticks);
    return out;
  }

  function gridAndAxes(g, xd, yd, opts) {
    opts = opts || {};
    const yt = niceDomain(yd[0], yd[1], opts.yTicks || 4);
    yt.forEach((v) => {
      g.svg.appendChild(el("line", {
        x1: g.m.l, x2: g.m.l + g.iw, y1: g.y(v, yd), y2: g.y(v, yd),
        stroke: C.grid, "stroke-width": 1,
      }));
      const t = el("text", { x: g.m.l - 5, y: g.y(v, yd) + 3, "text-anchor": "end", class: "ax-text" });
      t.textContent = opts.yFmt ? opts.yFmt(v) : String(+v.toFixed(2));
      g.svg.appendChild(t);
    });
    (opts.xTicks || []).forEach(({ v, label }) => {
      const t = el("text", {
        x: g.x(v, xd), y: g.H - 7, "text-anchor": "middle", class: "ax-text",
      });
      t.textContent = label;
      g.svg.appendChild(t);
    });
    if (opts.xLabel) {
      const t = el("text", {
        x: g.m.l + g.iw, y: g.H - 7, "text-anchor": "end", class: "ax-text",
      });
      t.textContent = opts.xLabel;
      g.svg.appendChild(t);
    }
  }

  function path(g, xs, ys, xd, yd) {
    return xs.map((v, i) =>
      `${i ? "L" : "M"}${g.x(v, xd).toFixed(1)},${g.y(ys[i], yd).toFixed(1)}`).join("");
  }

  function bindHover(g, xs, xd, onMove, onLeave, tipEl) {
    const hit = el("rect", {
      x: g.m.l, y: g.m.t, width: g.iw, height: g.ih, fill: "transparent",
    });
    g.svg.appendChild(hit);
    hit.addEventListener("mousemove", (e) => {
      const r = g.svg.getBoundingClientRect();
      const px = (e.clientX - r.left) * g.W / r.width - g.m.l;
      const frac = Math.max(0, Math.min(1, px / g.iw));
      const val = xd[0] + frac * (xd[1] - xd[0]);
      let idx = 0, best = Infinity;
      xs.forEach((v, i) => { const d = Math.abs(v - val); if (d < best) { best = d; idx = i; } });
      const cx = g.x(xs[idx], xd);
      onMove(idx, cx);
    });
    hit.addEventListener("mouseleave", () => {
      if (onLeave) onLeave();
      if (tipEl) tipEl.style.display = "none";
    });
  }

  // ---------------- 折线（多系列 + 置信带） ----------------
  function lineChart(svg, tipEl, opts) {
    const xs = opts.x;
    const xd = [xs[0], xs[xs.length - 1]];
    let yd = opts.yDomain;
    if (!yd) {
      const all = [];
      opts.series.forEach((s) => {
        all.push(...s.values);
        if (s.bandLow) all.push(...s.bandLow, ...s.bandHigh);
      });
      yd = extent(all, 0.08);
    }
    const g = prep(svg, opts.margin);
    const xTicks = [];
    const step = Math.max(1, Math.ceil(xs.length / 6));
    xs.forEach((v, i) => { if (i % step === 0 || i === xs.length - 1)
      xTicks.push({ v, label: opts.xFmt ? opts.xFmt(v, i) : String(v) }); });
    gridAndAxes(g, xd, yd, { xTicks, yFmt: opts.yFmt, xLabel: opts.xLabel });

    opts.series.forEach((s) => {
      if (s.bandLow && s.bandHigh) {
        const pts = [];
        xs.forEach((v, i) => pts.push(`${g.x(v, xd)},${g.y(s.bandHigh[i], yd)}`));
        for (let i = xs.length - 1; i >= 0; i--)
          pts.push(`${g.x(xs[i], xd)},${g.y(s.bandLow[i], yd)}`);
        g.svg.appendChild(el("polygon", {
          points: pts.join(" "), fill: s.color, opacity: 0.12,
        }));
      }
      g.svg.appendChild(el("path", {
        d: path(g, xs, s.values, xd, yd), fill: "none",
        stroke: s.color, "stroke-width": s.width || 2,
        "stroke-dasharray": s.dash ? "5 4" : null, opacity: s.opacity || 1,
      }));
    });

    // hover 十字线 + 点 + tooltip
    const cross = el("line", { y1: g.m.t, y2: g.m.t + g.ih, stroke: C.accent,
      "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0.6 });
    cross.style.display = "none";
    g.svg.appendChild(cross);
    const dots = opts.series.map((s) => {
      const c = el("circle", { r: 3.5, fill: s.color, stroke: "#050a14", "stroke-width": 1.5 });
      c.style.display = "none";
      g.svg.appendChild(c);
      return c;
    });
    bindHover(g, xs, xd, (idx, cx) => {
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx);
      cross.style.display = "";
      dots.forEach((d, si) => {
        d.setAttribute("cx", cx);
        d.setAttribute("cy", g.y(opts.series[si].values[idx], yd));
        d.style.display = "";
      });
      if (tipEl && opts.tipHtml) {
        tipEl.innerHTML = opts.tipHtml(idx);
        tipEl.style.display = "block";
        const box = g.svg.parentElement.getBoundingClientRect();
        const left = cx / g.W * box.width + 12;
        tipEl.style.left = Math.min(left, box.width - tipEl.offsetWidth - 8) + "px";
        tipEl.style.top = "8px";
      }
    }, () => { cross.style.display = "none"; dots.forEach((d) => (d.style.display = "none")); }, tipEl);
  }

  // ---------------- 横向条形（固定行序） ----------------
  function hBarChart(svg, tipEl, opts) {
    const items = opts.items;
    const m = { l: 96, r: 46, t: 6, b: 6 };
    const g = prep(svg, m);
    const n = items.length;
    const gap = 6, bh = Math.min(22, (g.ih - gap * (n - 1)) / n);
    const xd = opts.xDomain || extent(items.map((i) => i.value), 0.12);
    xd[0] = 0;
    items.forEach((it, i) => {
      const y = g.m.t + i * (bh + gap);
      const w = g.x(it.value, xd) - g.m.l;
      g.svg.appendChild(el("rect", { x: g.m.l, y, width: g.iw, height: bh, fill: "#0a1426", rx: 4 }));
      g.svg.appendChild(el("rect", {
        x: g.m.l, y, width: Math.max(2, w), height: bh, rx: 4,
        fill: it.color || C.accent, opacity: it.dim ? 0.45 : 1,
      }));
      const name = el("text", { x: g.m.l - 7, y: y + bh / 2 + 4, "text-anchor": "end", class: "ax-text" });
      name.textContent = it.label;
      g.svg.appendChild(name);
      const val = el("text", {
        x: g.m.l + Math.max(2, w) + 5, y: y + bh / 2 + 4, class: "ax-text",
        fill: "#cfe0f7",
      });
      val.textContent = opts.fmt ? opts.fmt(it.value, it) : it.value.toFixed(2);
      g.svg.appendChild(val);
    });
    const hit = el("rect", { x: 0, y: 0, width: g.W, height: g.H, fill: "transparent" });
    g.svg.appendChild(hit);
    if (tipEl && opts.tipHtml) {
      hit.addEventListener("mousemove", (e) => {
        const r = g.svg.getBoundingClientRect();
        const py = (e.clientY - r.top) * g.H / r.height - g.m.t;
        const idx = Math.floor(py / (bh + gap));
        if (idx < 0 || idx >= n) return;
        tipEl.innerHTML = opts.tipHtml(items[idx], idx);
        tipEl.style.display = "block";
        tipEl.style.left = (e.clientX - r.left + 12) + "px";
        tipEl.style.top = (e.clientY - r.top + 12) + "px";
      });
      hit.addEventListener("mouseleave", () => (tipEl.style.display = "none"));
    }
  }

  // ---------------- 散点（颜色/点击/特殊标记/对角线） ----------------
  function scatterChart(svg, tipEl, opts) {
    const xd = opts.xDomain || extent(opts.points.map((p) => p.x), 0.08);
    const yd = opts.yDomain || extent(opts.points.map((p) => p.y), 0.08);
    const g = prep(svg, opts.margin);
    gridAndAxes(g, xd, yd, {
      yFmt: opts.yFmt, xLabel: opts.xLabel,
      xTicks: [{ v: xd[0], label: opts.xFmt ? opts.xFmt(xd[0]) : xd[0].toFixed(2) },
               { v: xd[1], label: opts.xFmt ? opts.xFmt(xd[1]) : xd[1].toFixed(2) }],
    });
    if (opts.diagonal) {
      const lo = Math.max(xd[0], yd[0]), hi = Math.min(xd[1], yd[1]);
      if (hi > lo) g.svg.appendChild(el("line", {
        x1: g.x(lo, xd), y1: g.y(lo, yd), x2: g.x(hi, xd), y2: g.y(hi, yd),
        stroke: C.muted, "stroke-dasharray": "4 4", "stroke-width": 1,
      }));
    }
    opts.points.forEach((p, i) => {
      const c = el("circle", {
        cx: g.x(p.x, xd), cy: g.y(p.y, yd), r: p.r || 4,
        fill: p.color || C.accent, opacity: p.opacity || 0.85,
        stroke: "#050a14", "stroke-width": 0.8, style: "cursor:pointer",
      });
      c.addEventListener("click", () => opts.onSelect && opts.onSelect(p, i));
      g.svg.appendChild(c);
    });
    (opts.markers || []).forEach((mk) => {
      const cx = g.x(mk.x, xd), cy = g.y(mk.y, yd);
      const sym = el("path", {
        d: starPath(cx, cy, mk.r || 7), fill: mk.color || C.accent2,
        stroke: "#050a14", "stroke-width": 1, style: "cursor:pointer",
      });
      sym.addEventListener("click", () => opts.onSelect && opts.onSelect(mk.data ? mk : mk, mk.idx));
      g.svg.appendChild(sym);
      const t = el("text", { x: cx + 8, y: cy - 7, class: "ax-text", fill: mk.color || C.accent2 });
      t.textContent = mk.label || "";
      g.svg.appendChild(t);
    });
    if (tipEl && opts.tipHtml) {
      const hit = el("rect", { x: 0, y: 0, width: g.W, height: g.H, fill: "transparent" });
      g.svg.appendChild(hit);
      hit.addEventListener("mousemove", (e) => {
        const r = g.svg.getBoundingClientRect();
        const mx = (e.clientX - r.left) * g.W / r.width, my = (e.clientY - r.top) * g.H / r.height;
        let best = null, bd = Infinity;
        opts.points.forEach((p, i) => {
          const d = (g.x(p.x, xd) - mx) ** 2 + (g.y(p.y, yd) - my) ** 2;
          if (d < bd) { bd = d; best = { p, i }; }
        });
        if (!best || bd > 400) { tipEl.style.display = "none"; return; }
        tipEl.innerHTML = opts.tipHtml(best.p, best.i);
        tipEl.style.display = "block";
        tipEl.style.left = (e.clientX - r.left + 12) + "px";
        tipEl.style.top = (e.clientY - r.top + 12) + "px";
      });
      hit.addEventListener("mouseleave", () => (tipEl.style.display = "none"));
    }
  }

  function starPath(cx, cy, r) {
    const out = [];
    for (let i = 0; i < 10; i++) {
      const ang = -Math.PI / 2 + i * Math.PI / 5;
      const rr = i % 2 ? r * 0.45 : r;
      out.push(`${i ? "L" : "M"}${(cx + rr * Math.cos(ang)).toFixed(1)},${(cy + rr * Math.sin(ang)).toFixed(1)}`);
    }
    return out.join("") + "Z";
  }

  // ---------------- 纵向类别柱（MC 成败分布等） ----------------
  function vBarChart(svg, tipEl, opts) {
    const cats = opts.cats;
    const g = prep(svg, { l: 40, r: 12, t: 14, b: 30 });
    const yd = [0, opts.yMax || Math.max(1, ...cats.map((c) => c.value)) * 1.15];
    gridAndAxes(g, [0, 1], yd, { yFmt: opts.yFmt, yTicks: 4 });
    const bw = Math.min(110, g.iw / cats.length * 0.55);
    cats.forEach((c, i) => {
      const cx = g.m.l + g.iw * (i + 0.5) / cats.length;
      const h = g.y(c.value, yd) - g.y(yd[0], yd);
      g.svg.appendChild(el("rect", {
        x: cx - bw / 2, y: g.y(c.value, yd), width: bw, height: Math.max(0, h),
        fill: c.color, rx: 5, opacity: 0.9,
      }));
      const vt = el("text", { x: cx, y: g.y(c.value, yd) - 5, "text-anchor": "middle",
        fill: "#e8f1ff", class: "ax-text" });
      vt.textContent = opts.barFmt ? opts.barFmt(c) : String(c.value);
      g.svg.appendChild(vt);
      const lt = el("text", { x: cx, y: g.H - 9, "text-anchor": "middle", class: "ax-text" });
      lt.textContent = c.label;
      g.svg.appendChild(lt);
    });
  }

  function emptyChart(svg, msg) {
    const g = prep(svg, { l: 8, r: 8, t: 8, b: 8 });
    const t = el("text", { x: g.W / 2, y: g.H / 2, "text-anchor": "middle",
      fill: "#556b8d", "font-size": 12 });
    t.textContent = msg || "暂无数据（运行 brigsim dash-build 生成）";
    g.svg.appendChild(t);
  }

  window.DASH.charts = { lineChart, hBarChart, scatterChart, vBarChart, emptyChart, C };
})();
