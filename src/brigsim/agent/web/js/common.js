/* C6 大屏公共：工具函数、数据加载、抽屉开关、3D viewer 接入（/api/viewer） */
(function () {
  "use strict";
  const DASH = (window.DASH = window.DASH || {});
  DASH.views = DASH.views || {};
  DASH.data = DASH.data || {};

  DASH.$ = (id) => document.getElementById(id);
  DASH.esc = function (s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  };
  DASH.inline = (s) =>
    DASH.esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // 极简 markdown：表格 / 无序列表 / 段落 + **粗体**（C5 原逻辑）
  DASH.renderMd = function (src) {
    const lines = DASH.esc(src).replace(/\*\*(.+?)\*\*/g, "\u0001$1\u0002").split("\n");
    const out = [];
    let i = 0;
    const bold = (s) => s.replace(/\u0001(.+?)\u0002/g, "<strong>$1</strong>");
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (line.trim().startsWith("|")) {
        const tbl = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) tbl.push(lines[i++]);
        const rows = tbl.filter((r) => !/^\s*\|[\s:|-]+\|?\s*$/.test(r))
          .map((r) => r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim()));
        if (!rows.length) continue;
        out.push("<table><thead><tr>" + rows[0].map((c) => `<th>${bold(c)}</th>`).join("")
          + "</tr></thead><tbody>"
          + rows.slice(1).map((r) => "<tr>" + r.map((c) => `<td>${bold(c)}</td>`).join("") + "</tr>").join("")
          + "</tbody></table>");
        continue;
      }
      if (line.trim().startsWith("- ")) {
        const items = [];
        while (i < lines.length && lines[i].trim().startsWith("- "))
          items.push(`<li>${bold(lines[i++].trim().slice(2))}</li>`);
        out.push("<ul>" + items.join("") + "</ul>");
        continue;
      }
      const buf = [];
      while (i < lines.length && lines[i].trim() &&
             !lines[i].trim().startsWith("|") && !lines[i].trim().startsWith("- "))
        buf.push(lines[i++]);
      out.push("<p>" + buf.map(bold).join("<br>") + "</p>");
    }
    return out.join("");
  };

  DASH.argsText = (tc) =>
    Object.entries(tc.arguments).map(([k, v]) => `${k}=${v}`).join(", ");

  DASH.loadJson = async (name) => {
    const r = await fetch(`/data/${name}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${name} HTTP ${r.status}`);
    return r.json();
  };

  // 百分比/数字格式（null/undefined → 占位）
  DASH.pct = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)
    ? "—" : (v * 100).toFixed(d) + "%");
  DASH.fnum = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)
    ? "—" : Number(v).toFixed(d));
  DASH.pp = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)
    ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(d) + "pp");

  // ---------- 智能参谋抽屉 ----------
  function initDrawer() {
    const drawer = DASH.$("agent-drawer");
    const open = DASH.$("btn-open-agent");
    const close = DASH.$("btn-close-agent");
    if (!drawer || !open) return;
    open.addEventListener("click", () => drawer.classList.add("open"));
    if (close) close.addEventListener("click", () => drawer.classList.remove("open"));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") drawer.classList.remove("open");
    });
  }

  // ---------- 3D viewer：/api/viewer → iframe；未就绪显示降级卡 ----------
  async function initViewer() {
    const frame = DASH.$("replay-frame");
    const fallback = DASH.$("replay-fallback");
    const loading = DASH.$("replay-loading");
    if (!frame) return;
    let st;
    try {
      const r = await fetch("/api/viewer", { cache: "no-store" });
      st = await r.json();
    } catch (e) {
      st = { ready: false, reason: "无法连接回放服务状态接口" };
    }
    if (st.ready && st.viewer_url) {
      frame.addEventListener("load", () => {
        if (loading) loading.style.display = "none";
      }, { once: true });
      frame.src = st.viewer_url;
      // wasm 首次加载较慢，8 秒后无论 load 事件是否触发都撤掉 loading（iframe 内部渲染不冒泡）
      setTimeout(() => { if (loading) loading.style.display = "none"; }, 8000);
      if (fallback) fallback.style.display = "none";
    } else {
      if (loading) loading.style.display = "none";
      frame.style.display = "none";
      if (fallback) {
        fallback.style.display = "flex";
        const reason = DASH.$("replay-fallback-reason");
        if (reason && st.reason) reason.textContent = st.reason;
      }
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    initDrawer();
    initViewer();
  });
})();
