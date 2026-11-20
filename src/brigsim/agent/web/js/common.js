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
    const path = name.endsWith(".json") ? name : name + ".json";
    const r = await fetch(`/data/${path}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path} HTTP ${r.status}`);
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

  // ---------- A2：功能对照弹窗（文档5模块/4域9功能）----------
  function initFuncMap() {
    const modal = DASH.$("funcmap-modal");
    const open = DASH.$("btn-func-map");
    const close = DASH.$("funcmap-close");
    if (!modal || !open) return;
    open.addEventListener("click", () => { modal.hidden = false; });
    if (close) close.addEventListener("click", () => { modal.hidden = true; });
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.hidden = true;
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") modal.hidden = true;
    });
  }

  // ---------- 3D viewer：/api/viewer → iframe（同源代理 / 直连双端口 双模式）----------
  // C6-fix：唯一装载入口。默认「同源」模式：iframe 与 gRPC 都走大屏自身 origin
  // （/rerun-viewer、/proxy 由后端反向代理到 127.0.0.1 回环），不经代理、零外网。
  // C6.1：「直连」回退模式——iframe 指向 rerun viewer 自身端口、gRPC 直连 rerun
  // （原生 CORS），同样全走 127.0.0.1 回环；同源模式在个别浏览器下失败时一键切换，
  // 选择记忆 localStorage。watchdog：load 超时 / 画布未创建 → 显示黑屏自救卡。
  const VIEWER_MODE_KEY = "brigsim.viewer_mode";
  const VIEWER_LOAD_TIMEOUT_MS = 60000;   // iframe load 事件超时
  const VIEWER_CANVAS_GRACE_MS = 25000;   // load 之后等 WebGL 画布出现的宽限期

  function getViewerMode() {
    try {
      return localStorage.getItem(VIEWER_MODE_KEY) === "direct" ? "direct" : "same-origin";
    } catch (e) { return "same-origin"; }
  }
  function setViewerMode(mode) {
    try { localStorage.setItem(VIEWER_MODE_KEY, mode === "direct" ? "direct" : "same-origin"); }
    catch (e) { /* 隐私模式：仅本次会话 */ }
    updateViewerModeButton();
  }
  function updateViewerModeButton() {
    const btn = DASH.$("viewer-mode-btn");
    if (!btn) return;
    const direct = getViewerMode() === "direct";
    btn.textContent = direct ? "直连" : "同源";
    btn.classList.toggle("on", direct);
    btn.title = direct
      ? "3D 数据通道：直连双端口（viewer 端口 / gRPC 端口，本地回环）。点击切回同源代理。"
      : "3D 数据通道：同源反向代理（经大屏 origin 转发到本地回环）。点击切换为直连双端口。";
  }

  function viewerTargetUrl(st) {
    // 直连模式：iframe 指向 rerun viewer 自身端口；gRPC 直连（serve_grpc 已开 CORS）
    if (getViewerMode() === "direct" && st.direct_base && st.direct_grpc) {
      return st.direct_base + "?url=" + encodeURIComponent(st.direct_grpc) + "&notebook=0";
    }
    const origin = window.location.origin;
    const grpcUrl = "rerun+" + origin + (st.grpc_path || "/proxy");
    const embedBase = st.embed_base || "/rerun-viewer/";
    return embedBase + "?url=" + encodeURIComponent(grpcUrl) + "&notebook=0";
  }

  // C7-e：静态 rrd 文件直载 URL（replay 模式专用）——绕开 sink 累积历史
  // （每次切换 relog +6~8MB，新 viewer 连接重下全部历史，60MB≈300s 超时传不完，
  // 表现为"下拉切换无反应"）。文件经 /rerun-file/ 同源提供（直连模式下跨域，
  // 服务端已对该路由开 CORS）。
  function viewerFileTargetUrl(st, file) {
    const fileUrl = window.location.origin + "/rerun-file/" + file;
    const base = (getViewerMode() === "direct" && st.direct_base)
      ? st.direct_base
      : (st.embed_base || "/rerun-viewer/");
    return base + "?url=" + encodeURIComponent(fileUrl) + "&notebook=0";
  }

  // 同源模式下 iframe 可 introspection：检查 WebGL 画布是否真的建出来
  function viewerCanvasReady(frame) {
    try {
      const doc = frame.contentDocument;
      if (!doc) return false;
      const c = doc.querySelector("canvas");
      return !!(c && c.width > 0 && c.height > 0);
    } catch (e) {
      return false;   // 跨域（直连模式）无法探测
    }
  }

  function showViewerError(title, reason, detail, st) {
    const frame = DASH.$("replay-frame");
    const fallback = DASH.$("replay-fallback");
    const loading = DASH.$("replay-loading");
    if (loading) loading.style.display = "none";
    if (frame) frame.style.display = "none";
    if (fallback) {
      fallback.style.display = "flex";
      const t = DASH.$("replay-fallback-title");
      if (t) t.textContent = title || "3D 态势回放加载失败";
      const r = DASH.$("replay-fallback-reason");
      if (r) r.textContent = reason || "";
      const d = DASH.$("replay-fallback-detail");
      if (d) d.textContent = detail || "";
      const hint = DASH.$("replay-fallback-hint");
      if (hint) hint.style.display = "none";   // 黑屏自救场景不显示 dash-build 提示
      const actions = DASH.$("replay-fallback-actions");
      // 仅回放服务就绪（=真黑屏）时才给自救按钮；服务没起时按原降级卡展示
      if (actions) actions.style.display = st && st.ready ? "flex" : "none";
      const sw = DASH.$("fb-switch");
      if (sw) sw.textContent = getViewerMode() === "direct" ? "切回同源模式" : "切换到直连模式";
    }
  }

  function hideViewerError() {
    const frame = DASH.$("replay-frame");
    const fallback = DASH.$("replay-fallback");
    if (fallback) fallback.style.display = "none";
    const hint = DASH.$("replay-fallback-hint");
    if (hint) hint.style.display = "";
    if (frame) { frame.style.display = ""; frame.hidden = false; }
  }

  let viewerWatchdog = null;
  async function ensureViewer(force, fileOverride) {
    const frame = DASH.$("replay-frame");
    const fallback = DASH.$("replay-fallback");
    const loading = DASH.$("replay-loading");
    if (!frame) return;
    if (window.__rerun_iframe_loaded && !force) return;   // 已装载：避免双设 src → 导航中止
    window.__rerun_iframe_loaded = true;
    window.__rerun_viewer_ok = false;
    if (viewerWatchdog) { clearTimeout(viewerWatchdog); viewerWatchdog = null; }
    let st;
    try {
      const r = await fetch("/api/viewer", { cache: "no-store" });
      st = await r.json();
    } catch (e) {
      st = { ready: false, reason: "无法连接回放服务状态接口：" + (e && e.message ? e.message : e) };
    }
    if (!(st.ready && st.viewer_url)) {
      if (loading) loading.style.display = "none";
      frame.style.display = "none";
      if (fallback) {
        fallback.style.display = "flex";
        const reason = DASH.$("replay-fallback-reason");
        if (reason && st.reason) reason.textContent = st.reason;
      }
      return;
    }
    hideViewerError();
    updateViewerModeButton();
    const target = fileOverride ? viewerFileTargetUrl(st, fileOverride) : viewerTargetUrl(st);
    const direct = getViewerMode() === "direct";
    if (loading) loading.style.display = "flex";
    const onLoad = () => {
      if (loading) loading.style.display = "none";
      if (direct) { window.__rerun_viewer_ok = true; return; }   // 跨域无法探测，信任 load
      // 同源模式：再给 wasm 一段宽限期创建 WebGL 画布；建不出来 = 黑屏，走自救卡
      setTimeout(() => {
        if (window.__rerun_viewer_ok) return;
        if (viewerCanvasReady(frame)) { window.__rerun_viewer_ok = true; return; }
        showViewerError(
          "3D 画布未创建（疑似黑屏）",
          "viewer 页面已加载，但 WebGL 画布没有建出来。常见原因：浏览器禁用 WebGL / 硬件加速异常。",
          "可点「切换到直连模式」或「重试加载」；仍不行请强刷页面（排除缓存）后按 F12 查看控制台报错。",
          st
        );
      }, VIEWER_CANVAS_GRACE_MS);
    };
    frame.addEventListener("load", onLoad, { once: true });
    // 整体超时：iframe 迟迟不 fire load（viewer 资源加载失败 / 数据通道不通）
    viewerWatchdog = setTimeout(() => {
      if (window.__rerun_viewer_ok) return;
      showViewerError(
        "3D 引擎加载超时",
        "60 秒内 3D viewer 没有加载完成。",
        "iframe 地址：" + target,
        st
      );
    }, VIEWER_LOAD_TIMEOUT_MS);
    frame.src = target;
  }
  DASH.ensureViewer = ensureViewer;
  DASH.getViewerMode = getViewerMode;
  DASH.setViewerMode = setViewerMode;
  DASH.updateViewerModeButton = updateViewerModeButton;
  // C7-e：replay 模式装载指定静态 rrd 文件（强制重设 iframe src + 重启看门狗）
  DASH.loadViewerFile = function (file) {
    if (!file) return;
    window.__rerun_viewer_static = true;
    ensureViewer(true, file);
  };
  // C7-g：live 模式切工况走静态直载后，启动推演必须切回 gRPC sink 才能看到实时 tick。
  // 仅在确实处于静态文件态时才重载（避免每次启动都无谓刷新、闪烁）。
  DASH.ensureLiveSink = function () {
    if (!window.__rerun_viewer_static) return;
    window.__rerun_viewer_static = false;
    window.__rerun_iframe_loaded = false;
    ensureViewer(true);
  };

  // 黑屏自救卡：重试 / 切换数据通道 / 强刷
  function initViewerFallbackActions() {
    const retry = DASH.$("fb-retry");
    const sw = DASH.$("fb-switch");
    const hard = DASH.$("fb-hardrefresh");
    if (retry) retry.addEventListener("click", () => {
      window.__rerun_iframe_loaded = false;
      ensureViewer(true);
    });
    if (sw) sw.addEventListener("click", () => {
      setViewerMode(getViewerMode() === "direct" ? "same-origin" : "direct");
      window.__rerun_iframe_loaded = false;
      ensureViewer(true);
    });
    if (hard) hard.addEventListener("click", () => {
      try { window.location.reload(true); }   // 绕过缓存整页重载（≈ Ctrl+F5）
      catch (e) { window.location.reload(); }
    });
    updateViewerModeButton();
  }

  window.addEventListener("DOMContentLoaded", () => {
    initDrawer();
    initFuncMap();
    initViewerFallbackActions();
    // C7-e：replay 模式由 main.js boot 流程装载静态 rrd 文件（缓存命中即时切换 /
    // 未命中先构建再切），此处不预载 gRPC——sink 累积历史下载慢（60MB≈300s），
    // 且随后会被静态文件装载打断，纯属浪费
    const _bootMode = new URLSearchParams(window.location.search).get("mode");
    if (_bootMode !== "replay") ensureViewer();
  });
})();
