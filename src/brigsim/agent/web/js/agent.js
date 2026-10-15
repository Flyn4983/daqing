/* C5 智能参谋全部交互逻辑（迁入抽屉；DOM id 与 C5 单页完全一致，零行为回归） */
(function () {
  "use strict";
  const { $, esc, inline, renderMd, argsText } = window.DASH;

  const INTENT_LABEL = { plan: "方案生成", compare: "基线对比", whatif: "方案测算",
    pareto: "前沿查询", rates_def: "四率问答", status: "状态查询", help: "能力说明",
    greeting: "问候", fallback: "对话" };
  const CTX_LABEL = { scenario: "想定", duration_minutes: "任务时长(分钟)", tick_seconds: "tick 换算(秒)",
    mtbf_scale: "装备 MTBF 乘子", red_pk_scale: "红方交战乘子", blue_pk_scale: "蓝方交战乘子(敌情)" };

  function addTrace(tc) {
    const el = $("trace");
    if (el.querySelector(".empty")) el.innerHTML = "";
    const div = document.createElement("div");
    div.className = "trace-item";
    div.innerHTML = `<div class="t-fn">${esc(tc.name)}</div>
      <div class="t-args">${esc(argsText(tc))}</div>
      <div class="t-foot"><span>${esc(tc.summary || "")}</span><span>${tc.elapsed_ms.toFixed(1)} ms</span></div>`;
    el.prepend(div);
  }

  function addMsg(role, html) {
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.innerHTML = `<div class="avatar">${role === "user" ? "我" : "参"}</div>
      <div class="bubble"><div class="who">${role === "user" ? "保障参谋（我）" : "本地保障智能参谋"}</div>
      <div class="body">${html}</div></div>`;
    $("msgs").appendChild(div);
    $("msgs").scrollTop = $("msgs").scrollHeight;
    return div.querySelector(".body");
  }

  function toolChips(calls) {
    if (!calls.length) return "";
    return `<div class="tools">${calls.map((tc) =>
      `<div class="tool">函数调用 <span class="fn">${esc(tc.name)}</span>(${esc(argsText(tc))})<span class="ms">${tc.elapsed_ms.toFixed(1)} ms</span></div>`).join("")}</div>`;
  }

  async function sendText(text) {
    text = text.trim();
    if (!text) return;
    addMsg("user", inline(text));
    const body = addMsg("bot", '<span class="typing">本地推理中<i></i><i></i><i></i></span>');
    $("send").disabled = true;
    try {
      const r = await fetch("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await r.json();
      if (!r.ok) {
        body.innerHTML = `<span style="color:var(--bad)">${esc(data.error || "请求失败")}</span>`;
        return;
      }
      body.innerHTML = renderMd(data.answer)
        + toolChips(data.tool_calls)
        + `<div class="meta">${esc(INTENT_LABEL[data.intent] || data.intent)} · 本地推理 ${data.latency_ms.toFixed(1)} ms · 入参 ${data.prompt_tokens} tok / 生成 ${data.completion_tokens} tok</div>`;
      data.tool_calls.forEach(addTrace);
      $("msgs").scrollTop = $("msgs").scrollHeight;
    } catch (e) {
      body.innerHTML = '<span style="color:var(--bad)">推理服务无响应，请检查本地服务进程。</span>';
    } finally {
      $("send").disabled = false;
    }
  }

  async function genPlan(tag) {
    const body = addMsg("bot", '<span class="typing">正在调度 NSGA-II 方案库与代理模型核验<i></i><i></i><i></i></span>');
    document.querySelectorAll(".plan-btns button").forEach((b) => (b.disabled = true));
    try {
      const r = await fetch("/api/plan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag }),
      });
      const data = await r.json();
      if (!r.ok) {
        body.innerHTML = `<span style="color:var(--bad)">${esc(data.error || "请求失败")}</span>`;
        return;
      }
      body.innerHTML = renderMd(data.answer)
        + toolChips(data.tool_calls)
        + `<div class="meta">方案生成 · 本地推理 ${data.latency_ms.toFixed(1)} ms · 生成 ${data.completion_tokens} tok</div>`;
      data.tool_calls.forEach(addTrace);
      $("msgs").scrollTop = $("msgs").scrollHeight;
    } finally {
      document.querySelectorAll(".plan-btns button").forEach((b) => (b.disabled = false));
    }
  }

  async function pollStatus() {
    try {
      const r = await fetch("/api/status");
      const d = await r.json();
      const s = d.service, c = d.context;
      $("svc-dot").className = "dot" + (s.busy ? " busy" : "");
      $("svc-state").textContent = s.busy ? "推理中" : "运行中";
      $("st-model").textContent = s.model;
      $("st-device").textContent = s.device;
      $("st-vram").textContent = `${s.vram_used_gb.toFixed(2)} / ${s.vram_total_gb.toFixed(1)} GB`;
      $("bar-vram").style.width = s.vram_percent + "%";
      $("st-util").textContent = s.gpu_util_percent + "%";
      $("bar-util").style.width = s.gpu_util_percent + "%";
      $("st-ctx").textContent = `${s.context_tokens} / ${s.context_limit} tok`;
      $("bar-ctx").style.width = (100 * s.context_tokens / s.context_limit) + "%";
      $("st-req").innerHTML = s.requests_total;
      $("st-tools").innerHTML = s.tools_served;
      $("st-lat").innerHTML = s.avg_latency_ms.toFixed(0) + "<small> ms</small>";
      $("st-up").innerHTML = s.uptime_s.toFixed(0) + "<small> s</small>";
      const rows = [["scenario", c.scenario], ["duration_minutes", c.duration_minutes.toFixed(1)],
        ["tick_seconds", c.tick_seconds], ...Object.entries(c.context)];
      $("ctx-kv").innerHTML = rows.map(([k, v]) =>
        `<span class="k">${esc(CTX_LABEL[k] || k)}</span><span class="v">${esc(v)}</span>`).join("");

      // 顶栏迷你徽标（元素不存在时静默——C5 旧页无此槽）
      const topDot = document.getElementById("top-dot");
      if (topDot) {
        topDot.className = "dot" + (s.busy ? " busy" : "");
        document.getElementById("top-state").textContent = s.busy ? "推理中" : "本地模型在线";
        document.getElementById("top-vram").textContent = `显存 ${s.vram_percent.toFixed(0)}%`;
        document.getElementById("top-lat").textContent = `延迟 ${s.avg_latency_ms.toFixed(0)}ms`;
      }
    } catch (e) { /* 轮询静默失败 */ }
  }

  window.addEventListener("DOMContentLoaded", () => {
    $("send").onclick = () => { sendText($("input").value); $("input").value = ""; };
    $("input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("send").click(); }
    });
    document.querySelectorAll(".chip").forEach((c) => (c.onclick = () => sendText(c.dataset.q)));
    document.querySelectorAll(".plan-btns button").forEach((b) => (b.onclick = () => genPlan(b.dataset.tag)));
    pollStatus();
    setInterval(pollStatus, 2000);
    addMsg("bot", "<p>本地保障智能参谋已就绪。可直接提问，或在右侧一键生成 NSGA-II 保障方案。"
      + "所有回答数字均来自代理模型对蒙特卡洛仿真的秒级预测与代表方案仿真复验。</p>");
  });
})();
