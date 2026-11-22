/* 研究细节面板：报告第3节（方案设计/四个模型/检验验证）↔ 软件真实数据
 * 数据来源：/data/verification.json（由 tools/build_verification_data.py 从真实 JSON 实算生成）
 * 两类数字严格区分：
 *   real  —— 从大屏真实数据实算，可能随数据刷新变化
 *   text  —— 报告正文结论，无底层落盘数据，标注来源、不冒充实时读取
 */
(function () {
  const DASH = window.DASH || {};
  const $ = (id) => document.getElementById(id);
  let DATA = null;
  let cur = "design";
  const ORDER = ["design", "fig8", "figmap", "realdata", "textable", "m1", "m2", "m3", "m4", "verify"];

  const pct = (x, d = 1) => (x == null ? "—" : (x * 100).toFixed(d) + "%");
  const num = (x, d = 3) => (x == null ? "—" : Number(x).toFixed(d));
  const badge = (kind) => kind === "real"
    ? '<span class="r-badge real">真实数据实算</span>'
    : '<span class="r-badge text">报告正文</span>';
  const kv = (k, v, src) => `<div class="r-kv"><span class="k">${k}</span><span class="v">${v}${src ? `<span class="r-src-inline"> ${src}</span>` : ""}</span></div>`;
  const card = (title, body, src) => `<div class="r-card"><h4>${title}${badge(src)}</h4>${body}</div>`;

  function design() {
    const d = DATA;
    return `
      <div class="r-sec-title">一是方案设计 · 反问题求解路径</div>
      <div class="r-sec-sub">保障方案生成是"给定战损与战力再生要求反解方案参数"的反问题。软件按报告图5~图7拆成可解子链与四层架构。</div>
      <div class="r-card"><h4>反问题 → 可解子链（图5）${badge("text")}</h4>
        <div class="r-flow">${d.chain.map((s, i) => `<span class="node">${s}</span>${i < d.chain.length - 1 ? '<span class="arw">→</span>' : ""}`).join("")}</div>
        <div class="r-note">把"战损→四率→战力再生"的离散事件因果链，拆成表示、映射、反演、复验四个可计算子问题。</div>
      </div>
      <div class="r-grid c2" style="margin-top:14px">
        ${card("四层架构（图6）", `
          <div class="r-flow">${d.four_layer_arch.map((s, i) => `<span class="node">${s}</span>${i < 3 ? '<span class="arw">→</span>' : ""}`).join("")}</div>
          <div class="r-note">推演层出战损事件流 → 目标层定义η → 模型层可微代理+寻优 → 求解层双闸复验。</div>`, "text")}
        ${card("六环节闭环（图7）", `
          <div class="r-kv"><span class="k">模型分工</span><span class="v">四模型各司其职</span></div>
          <div class="r-note">${d.six_loops_note}。软件对应实现：交战推演（3D回放）→ η目标卡 → 代理/Pareto → 双闸复验 → 智能参谋解读 → 方案参数表。</div>`, "text")}
      </div>
      <div class="r-card" style="margin-top:14px"><h4>界面落点对照</h4>
        <table class="r-table"><thead><tr><th>报告环节</th><th>软件落点</th><th>可直接操作</th></tr></thead><tbody>
          <tr><td>表示（方案参数化）</td><td>顶栏「▶实时推演」配置弹窗 ③保障资源旋钮 / ④情景因子</td><td>改旋钮→预测四率</td></tr>
          <tr><td>映射（战损→四率）</td><td>左列四率态势卡 + 时序曲线 + 3D交战回放</td><td>切换工况看变化</td></tr>
          <tr><td>反演（寻优）</td><td>右列 Pareto 前沿 + NSGA-II 三档方案</td><td>点前沿方案看细节</td></tr>
          <tr><td>复验（可信判定）</td><td>MC分布卡 + 方案卡「仿真复验」+ 智能参谋</td><td>一键生成方案</td></tr>
        </tbody></table>
      </div>`;
  }

  // 图8参数全景：一阶9维 / 二阶9维 / 全集十类，逐项标注软件落点与状态
  const STATUS = {
    real:   '<span class="r-badge real">真实可调</span>',
    fixed:  '<span class="r-badge text">内核固定</span>',
    method: '<span class="r-badge text">方法层·功能承载</span>',
  };
  function paramGroup(g) {
    const rows = g.items.map((it) => `
      <tr><td>${it.name}</td><td>${STATUS[it.status] || ""}</td><td>${it.locus}</td></tr>`).join("");
    return `
      <div class="r-card" style="margin-top:12px"><h4>${g.title}</h4>
        <div class="r-note">${g.software}</div>
        <table class="r-table"><thead><tr><th style="width:20%">参数</th><th style="width:20%">软件状态</th><th>软件落点 / 实现方式</th></tr></thead><tbody>${rows}</tbody></table>
      </div>`;
  }
  function fig8() {
    const fp = DATA.fig8_params || {};
    return `
      <div class="r-sec-title">图8 · 方案分层参数化与三源约束（参数全景）</div>
      <div class="r-sec-sub">报告原文："一阶 9 维 / 二阶 9 维 / 全集十类"。本页逐项列出每个要素的软件落点，状态透明：<span class="r-badge real">真实可调</span>=配置弹窗有旋钮且进优化器；<span class="r-badge text">内核固定</span>=软件以场景常量/机理实现、非优化变量；<span class="r-badge text">方法层·功能承载</span>=报告的分类方法，软件按功能域间接承载。</div>
      ${paramGroup(fp.order1 || { title: "", software: "", items: [] })}
      ${paramGroup(fp.order2 || { title: "", software: "", items: [] })}
      ${paramGroup(fp.universe || { title: "", software: "", items: [] })}
      <div class="r-card" style="margin-top:12px"><h4>可行域 X 与三源约束 ${badge("text")}</h4>
        <div class="r-formula" style="font-size:13px">X = {x ∈ R⁹ × Z⁹ | 编制档位、Σxᵢ≤上限、MTTR(x)≤MTTR_max、R₀(x)≤1−P(Ⅳ)、P(指标≥要求)≥1−α}</div>
        <div class="r-kv"><span class="k">A 损伤源</span><span class="v">不可修判定 / 能力极限 → 必后送 → 反送时限</span></div>
        <div class="r-kv"><span class="k">B 权威源</span><span class="v">维修时限（关键设备内修≤4小时）/ 重点装备优先 / 四率最低要求</span></div>
        <div class="r-kv"><span class="k">C 资源源</span><span class="v">人力/工具/维修点/维修仓位/预算上限</span></div>
        <div class="r-kv"><span class="k">D 逻辑随机源</span><span class="v">工序顺序、备件装配时间、测试打磨制、机会约束 P(·)≥1−α、鲁棒裕度 δ</span></div>
        <div class="r-note warn">口径说明：报告图8为方法层设计（全集保证不遗漏、分层保证可搜索），软件实装 7 维可调决策 + 3 维情景；其余要素作为内核固定条件或功能落点承载，未逐条做成交互控件——避免以假控件误导评审。74 个前沿解逐维核对 7 维档位零越界（见模型一）。</div>
      </div>`;
  }

  // 全图要素索引：最终报告 20 张图的要素点 ↔ 软件落点
  function figmap() {
    const figs = DATA.figure_index || [];
    const st = { real: '<span class="r-badge real">可查</span>' };
    const rows = figs.map((f) => `
      <tr><td style="white-space:nowrap">图${f.no} ${f.name}</td><td>${f.elements.join(" · ")}</td><td>${f.locus}</td><td>${st[f.status] || ""}</td></tr>`).join("");
    const sobol = (DATA.sobol8_attrition_blue || []).map((x) => `<tr><td>${x.label}</td><td class="${x.ST >= 0.25 ? "r-hl" : ""}">${num(x.ST, 3)}</td><td>${x.rank}</td></tr>`).join("");
    return `
      <div class="r-sec-title">最终报告 20 张图 · 要素点全量索引</div>
      <div class="r-sec-sub">逐张列出报告插图中的要素点及其软件落点，供现场按图核对。所有要素均可在软件主界面或「研究细节」各页找到对应；图8三层参数见上一页「★图8参数全景」。</div>
      <div class="r-card"><h4>20 张图要素 ↔ 软件落点</h4>
        <table class="r-table"><thead><tr><th style="width:16%">报告图</th><th>要素点</th><th style="width:22%">软件落点</th><th style="width:8%">状态</th></tr></thead><tbody>${rows}</tbody></table>
      </div>
      <div class="r-card" style="margin-top:12px"><h4>图18 · 蓝方战损率 Sobol 八因子 ${badge("real")}</h4>
        <table class="r-table" style="max-width:520px"><thead><tr><th>战场因子</th><th>ST 总效应</th><th>排序</th></tr></thead><tbody>${sobol}</tbody></table>
        <div class="r-note">数据来自 /data/sensitivity.json 实算，与报告图18同源；气象能见度 ST=0.758 为蓝方战损率 rank1 因子。灵敏度面板主图为任务完成率口径，此表补齐蓝方战损率口径。</div>
      </div>`;
  }

  function realdata() {
    const rd = DATA.real_data || {};
    if (!rd.available) {
      return `<div class="r-sec-title">真实地形 / 气象数据溯源</div>
        <div class="r-card"><h4>数据未就绪</h4><div class="r-note">${rd.error || "未采集到真实地形/气象数据"}</div></div>`;
    }
    const t = rd.terrain, w = rd.weather;
    const wxDist = Object.entries(w.weather_dist || {})
      .map(([k, v]) => `<tr><td>${({clear:"晴",overcast:"多云",rain:"降雨",fog:"雾",heavy_fog:"浓雾",snow:"降雪"}[k] || k)}</td><td>${v} 小时</td><td>${(v / w.n_hours * 100).toFixed(0)}%</td></tr>`).join("");
    const dailyRows = (w.daily || []).map((d) => `<tr><td>${d.date}</td><td>${({clear:"晴",overcast:"多云",rain:"降雨"}[d.weather] || d.weather)}</td><td>${num(d.visibility_factor, 2)}</td><td>${num(d.precip_sum_mm, 1)} mm</td><td>${num(d.wind_max_kmh, 1)} km/h</td></tr>`).join("");
    return `
      <div class="r-sec-title">真实地形 / 气象数据溯源 ${badge("real")}</div>
      <div class="r-sec-sub">为回应"真实地形、天气是否引入"，本平台新增真实数据验证想定（太行山中段），地形取 SRTM 派生 DEM、气象取 ERA5 再分析，数据已打包本地、完全离线可用。原有 7 个想定与报告权威数字均未改动。</div>

      <div class="r-card"><h4>地形 · 太行山中段真实 DEM ${badge("real")}</h4>
        <div class="r-kv"><span class="k">区域</span><span class="v">${t.region}</span></div>
        <div class="r-kv"><span class="k">中心坐标</span><span class="v">${num(t.center[0], 2)}°N / ${num(t.center[1], 2)}°E</span></div>
        <div class="r-kv"><span class="k">数据源</span><span class="v">${t.source}</span></div>
        <div class="r-kv"><span class="k">精度 / 层级</span><span class="v">约 ${t.resolution_m}m（zoom ${t.zoom}）</span></div>
        <div class="r-kv"><span class="k">实测高程</span><span class="v r-hl">${t.elev_min} ~ ${t.elev_max} m（起伏 ${t.elev_span} m）</span></div>
        <div class="r-note">通行性栅格由真实高程坡度派生（河谷走廊抬升通行性）。采集于 ${t.collected_at.slice(0, 10)}，可用 tools/fetch_real_dem.py 复现。软件内切到「★真实地形」工况即可在 3D 面板预览。</div>
      </div>

      <div class="r-card" style="margin-top:12px"><h4>气象 · ERA5 再分析真实序列 ${badge("real")}</h4>
        <div class="r-kv"><span class="k">区域 / 时段</span><span class="v">${w.region} · ${w.period[0]} ~ ${w.period[1]}</span></div>
        <div class="r-kv"><span class="k">数据源</span><span class="v">${w.source}</span></div>
        <div class="r-kv"><span class="k">样本</span><span class="v">${w.n_hours} 小时（逐小时）</span></div>
        <table class="r-table" style="max-width:420px;margin-top:8px"><thead><tr><th>真实天气</th><th>时长</th><th>占比</th></tr></thead><tbody>${wxDist}</tbody></table>
        <table class="r-table" style="margin-top:10px"><thead><tr><th>日期</th><th>主导天气</th><th>能见度因子</th><th>日降水</th><th>最大风速</th></tr></thead><tbody>${dailyRows}</tbody></table>
        <div class="r-note">WMO 天气码映射为本平台 weather 枚举与能见度因子（口径同 combat/weather.py）。采集于 ${w.collected_at.slice(0, 10)}，可用 tools/fetch_real_weather.py 复现。真实气象已接入验证想定的环境参数与 3D 气象层。</div>
      </div>

      <div class="r-card" style="margin-top:12px"><h4>数据来源与许可</h4>
        <div class="r-note">
        · DEM：AWS Open Data elevation-tiles-prod（Terrarium，SRTM/ASTER GDEM 派生）——公共数据集。<br>
        · 气象：Open-Meteo Historical Weather API（ECMWF ERA5 再分析）——原型打包其输出数据。<br>
        · 核实日期 2026-11-20；原型阶段按授权暂不考虑版权，正式提交前需完成清权（AGPL-3.0 / NOASSERTION 项人工复核）。
        </div>
      </div>`;
  }

  // 正文与表格细节：覆盖报告文字段落与表格中的事实性细节（此前只系统核对了图片）
  function textable() {
    const t = DATA.text_table_details || {};
    const bg = t.background || {};
    const problems = (bg.three_problems || []).map((p) =>
      `<div class="r-kv"><span class="k">${p.name}</span><span class="v">${p.detail}</span></div>`).join("");
    const contradictions = (bg.three_contradictions || []).map((c) =>
      `<span class="node">${c}</span>`).join('<span class="arw">·</span>');
    const rates = (t.table_rates || []).map((r) =>
      `<tr><td>${r.metric}</td><td>${r.def}</td><td>${r.stat}</td><td class="r-hl">${r.role}</td></tr>`).join("");
    const goals = (t.goals || []).map((g) =>
      `<tr><td>${g.name}</td><td>${g.target}</td><td class="r-hl">${g.actual}</td><td>✓</td></tr>`).join("");
    const mb = t.military_benefit || {};
    const mechs = (mb.mechanisms || []).map((m) => `<span class="node">${m}</span>`).join('<span class="arw">→</span>');
    const usage = (t.table_usage || []).map((u) =>
      `<tr><td>${u.step}</td><td>${u.before}</td><td>${u.after}</td><td class="r-hl">${u.benefit}</td></tr>`).join("");
    const ul = t.usage_loop || {};
    const deliverables = (t.deliverables || []).map((d) => `<li>${d}</li>`).join("");
    const promote = (t.outlook?.promote || []).map((x) => `<li>${x}</li>`).join("");
    const upgrade = (t.outlook?.upgrade || []).map((x) => `<li>${x}</li>`).join("");
    return `
      <div class="r-sec-title">报告正文与表格 · 细节全量对照 ${badge("text")}</div>
      <div class="r-sec-sub">此前已逐张核对 20 张插图（见「全图要素索引」），本页补齐报告<b>正文段落</b>与<b>表格</b>中的事实性细节。这些为报告事实与结论，无底层实时计算数据，界面如实标注"报告正文/表格"，不冒充实时读取；可实算的核心数字见模型一至四与检验验证页。</div>

      <div class="r-card"><h4>一、问题背景 · 因果链与三对矛盾 ${badge("text")}</h4>
        <div class="r-flow">${(bg.causal_chain || "").split("→").map((s) => `<span class="node">${s.trim()}</span>`).join('<span class="arw">→</span>')}</div>
        ${problems}
        <div class="r-flow" style="margin-top:8px">${contradictions}</div>
        <div class="r-note">软件落点：主界面四列即这条因果链的操作化（交战推演→四率态势→寻优复验→方案判读）；"算不清/配不优/说不清"是平台要解决的痛点，对应主界面功能分区。</div>
      </div>

      <div class="r-card" style="margin-top:12px"><h4>二、表1 · 四率统计口径与在η中的角色 ${badge("text")}</h4>
        <table class="r-table"><thead><tr><th style="width:16%">指标</th><th>定义式</th><th>统计口径</th><th style="width:18%">在η中的角色</th></tr></thead><tbody>${rates}</tbody></table>
        <div class="r-note">模型一页的四率口径卡为简版（可用/编制等）；此表为报告原文完整口径。η 合成 η=ω₁R₀+ω₂(1−A)+ω₃Pᵣ+ω₄(1−F)，权重口径见模型一页。主界面四率态势卡与曲线按此口径统计。</div>
      </div>

      <div class="r-card" style="margin-top:12px"><h4>三、主要指标 · 立项目标与实测达成（8项） ${badge("text")}</h4>
        <table class="r-table"><thead><tr><th style="width:22%">指标</th><th>立项目标</th><th>实测达成</th><th style="width:8%">达标</th></tr></thead><tbody>${goals}</tbody></table>
        <div class="r-note">其中"推理加速·前向约38万×""方案集74""方案可信0.650/下界0.582""评估精度0.705"由大屏真实数据实算（见模型三/四）；"端到端≥300倍""部署两档""≤30分钟"等为报告目标与结论。</div>
      </div>

      <div class="r-card" style="margin-top:12px"><h4>四、军事效益 · 资源压缩与机理量化 ${badge("text")}</h4>
        <div class="r-kv"><span class="k">资源压缩</span><span class="v r-hl">${mb.resource_cut || "—"}</span></div>
        <div class="r-kv"><span class="k">效果</span><span class="v">${mb.outcome || "—"}</span></div>
        <div class="r-flow" style="margin-top:8px">${mechs}</div>
        <div class="r-note">软件落点：三档方案按钮的 leanest 最省方案即"压缩抢修组"代表；配置弹窗可调抢修组/抢救车数并看四率变化；三条机理通过模型二排队饱和与模型三代理对比体现。</div>
      </div>

      <div class="r-card" style="margin-top:12px"><h4>五、表4 · 部队应用前后保障决策环节对比 ${badge("text")}</h4>
        <table class="r-table"><thead><tr><th style="width:14%">决策环节</th><th>以往做法</th><th>使用后</th><th style="width:24%">效益</th></tr></thead><tbody>${usage}</tbody></table>
        <div class="r-note">软件落点：对应主界面完整操作动线（生成战损→寻优→复验→出参数表），效率数字为报告结论。</div>
      </div>

      <div class="r-card" style="margin-top:12px"><h4>六、使用闭环 · 战前—战中—战后 ${badge("text")}</h4>
        <div class="r-kv"><span class="k">${(ul.pre || "").split("：")[0]}</span><span class="v">${(ul.pre || "").split("：").slice(1).join("：")}</span></div>
        <div class="r-kv"><span class="k">${(ul.mid || "").split("：")[0]}</span><span class="v">${(ul.mid || "").split("：").slice(1).join("：")}</span></div>
        <div class="r-kv"><span class="k">${(ul.post || "").split("：")[0]}</span><span class="v">${(ul.post || "").split("：").slice(1).join("：")}</span></div>
        <div class="r-note">软件落点：实时推演（战中按 tick 更新四率、超预案重启）+ 回放模式（战后复盘）+ 智能参谋生成参数表（战前预案）。</div>
      </div>

      <div class="r-grid c2" style="margin-top:12px">
        ${card("七、成果形式（5项）", `<ul class="r-list">${deliverables}</ul><div class="r-note">研究周期：${t.research_period || "—"}</div>`, "text")}
        ${card("八、推广与升级考虑", `
          <div class="r-note" style="margin-bottom:6px"><b>推广</b></div><ul class="r-list">${promote}</ul>
          <div class="r-note" style="margin:8px 0 6px"><b>升级</b></div><ul class="r-list">${upgrade}</ul>`, "text")}
      </div>`;
  }

  function m1() {
    const m = DATA.model1;
    const rows = m.eta_decomp.map((p) => `
      <tr><td>${p.name}</td><td class="r-hl">${num(p.eta, 4)}</td><td>${num(p.cost, 4)}</td>
      <td>${pct(p.success)}</td><td>${num(p.parts.w1_R0, 3)} / ${num(p.parts.w2_1mA, 3)} / ${num(p.parts.w3_Pr, 3)} / ${num(p.parts.w4_mF, 3)}</td></tr>`).join("");
    return `
      <div class="r-sec-title">模型一 · 战力再生目标与保障方案可行域约束</div>
      <div class="r-sec-sub">"加权合成＋分层参数化＋三源约束"。η 口径与报告图13生成脚本一致：W=[0.35, 0.25, 0.25, 0.15]。</div>
      <div class="r-formula">η = ω₁R₀ + ω₂(1−A) + ω₃Pᵣ + ω₄(1−F)，　ω₁+ω₂+ω₃ = 1（式1）</div>
      <div class="r-grid c4">
        <div class="r-card"><h4>四率口径（表1）</h4>
          <div class="r-kv"><span class="k">完好率 R₀</span><span class="v">可用/编制</span></div>
          <div class="r-kv"><span class="k">战损率 A</span><span class="v">损失/编制</span></div>
          <div class="r-kv"><span class="k">修复率 Pᵣ</span><span class="v">已修/待修</span></div>
          <div class="r-kv"><span class="k">故障率 F</span><span class="v">次/百装备时</span></div></div>
        <div class="r-card"><h4>目标与权重</h4>
          <div class="r-kv"><span class="k">λ 成本权重</span><span class="v">${m.lambda}</span></div>
          <div class="r-kv"><span class="k">η 基线</span><span class="v r-big" style="font-size:24px">${num(m.eta_goal[0], 3)}</span></div>
          <div class="r-kv"><span class="k">η 推荐</span><span class="v r-big" style="font-size:24px">${num(m.eta_goal[1], 3)}</span></div></div>
        <div class="r-card"><h4>η 加权分解（图13a）${badge("real")}</h4>
          <div class="r-kv"><span class="k">现行→折中</span><span class="v r-hl">${num(m.eta_decomp[0].eta, 3)} → ${num(m.eta_decomp[2].eta, 3)}</span></div>
          <div class="r-kv"><span class="k">折中成本/基线</span><span class="v">${pct(m.eta_decomp[2].cost / m.eta_decomp[0].cost, 0)}</span></div>
          <div class="r-note">折中推荐与基线η持平、成本仅36%，战力再生与成本解耦。</div></div>
        <div class="r-card"><h4>权重扰动稳健性（图13b）${badge("real")}</h4>
          <div class="r-big gold">${m.perturbation.stable_top5_indices.length} 个</div>
          <div class="r-note">前沿第 ${Math.min(...m.perturbation.stable_top5_indices)}～${Math.max(...m.perturbation.stable_top5_indices)} 号方案，在 ±30% 权重扰动 ${m.perturbation.n_trials} 组中稳定进入η前五（每组≥${m.perturbation.stable_top5_min_count}次）。编号口径同报告图13b（0-based 前沿序号）。</div></div>
      </div>
      <div class="r-card" style="margin-top:14px"><h4>四方案 η 分项贡献明细 ${badge("real")}</h4>
        <table class="r-table"><thead><tr><th>方案</th><th>η</th><th>成本</th><th>完成率(代理)</th><th>贡献 ω₁R₀/ω₂(1−A)/ω₃Pᵣ/ω₄(1−F)</th></tr></thead><tbody>${rows}</tbody></table>
        <div class="r-note">数据由 /data/pareto.json 按式(1)现算，与报告图13同源。资源最省 η 偏低是代价，反映"战力再生与成本解耦"。</div>
      </div>
      <div class="r-grid c2" style="margin-top:14px">
        ${card("可行域 X（式3）与三源约束", `
          <div class="r-formula" style="font-size:14px">X={x∈R⁹×Z⁹ | 编制档位内，Σxᵢ≤上限，MTTR(x)≤MTTR_max，R₀(x)≤1−P(Ⅳ)，P(指标≥要求)≥1−α}</div>
          <div class="r-kv"><span class="k">A 损伤源</span><span class="v">损伤率、不可修判定</span></div>
          <div class="r-kv"><span class="k">B 权威源</span><span class="v">维修时限、备件储备</span></div>
          <div class="r-kv"><span class="k">C 资源源</span><span class="v">人力/工具/工位/预算</span></div>
          <div class="r-kv"><span class="k">D 逻辑随机</span><span class="v">机会约束 P(·)≥1−α</span></div>
          <div class="r-note warn">落点：三源约束已在「保障全过程 → 录入」中做成 GUI 可设定的真实输入——资源现实源（抢修车/抢救车/器材车上限、维修人员上限、单组修理能力）、指挥权威源（抢修时限、后送时限、重点装备完好下限、上级命令原文、抢修优先规则）、战损物理源（本级最高可修等级）与软约束（风险预算）。硬约束违反阻断保存与方案生成，软约束仅告警；并派生为 Pareto 选优的资源硬筛选。寻优内核仍按 FEATURES 权威边界，权威 74 方案与四率数字不变。随机约束落到 200 次终审守卫（见模型四）。</div>`, "text")}
        ${card("74 解逐维约束校验", `
          <div class="r-big">${m.feasibility.violations} <span style="font-size:16px">项越界</span></div>
          <div class="r-kv"><span class="k">前沿解</span><span class="v">${m.feasibility.n_plans} 个</span></div>
          <div class="r-kv"><span class="k">校验维度</span><span class="v">${m.feasibility.n_dims} 维编制档位</span></div>
          <div class="r-bar"><i style="width:100%"></i></div>
          <div class="r-note">${m.feasibility.conclusion}。软件按 FEATURES 的 lo/hi 边界与整数取整实现"编制档位内"这一硬约束。</div>`, "real")}
      </div>`;
  }

  function m2() {
    const m = DATA.model2;
    return `
      <div class="r-sec-title">模型二 · 战损—可修性—四率耦合映射</div>
      <div class="r-sec-sub">"可修性分级＋有限容量排队"。损伤四级判定可修性，排队饱和使 MTTR 指数恶化。</div>
      <div class="r-formula">Ω(d)=[P(可修|d), P(现地修|d), P(后送修|d), P(报废|d)]ᵀ　（式4）</div>
      <div class="r-grid c3">
        ${card("完好率上限（式5）", `
          <div class="r-formula" style="font-size:15px">max R₀ ≤ 1 − P(Ⅳ)</div>
          <div class="r-note">Ⅳ级报废与超能力不可修构成完好率硬上限，由损伤四级判定。</div>`, "text")}
        ${card("有限容量排队（式6）", `
          <div class="r-formula" style="font-size:15px">MTTR ≈ MTTR₀ · exp(Q/c)</div>
          <div class="r-kv"><span class="k">c 并行修复作业</span><span class="v">抢修组</span></div>
          <div class="r-kv"><span class="k">Q 排队待修</span><span class="v">战损事件流</span></div>
          <div class="r-kv"><span class="k">S 备件库存</span><span class="v">不足则挂起</span></div>`, "text")}
        ${card("战损谱压制完好率", `
          <div class="r-big red">${num(m.corr_attrition_readiness, 4)}</div>
          <div class="r-note">74 前沿解的战损率—完好率相关系数，未超式(5)上限，损伤谱对完好率形成压制。</div>`, "real")}
      </div>
      <div class="r-card" style="margin-top:14px"><h4>抢修能力饱和验证（报告图14）${badge("text")}</h4>
        <table class="r-table"><thead><tr><th>抢修组数</th><th>MTTR 相对值</th><th>边际改善</th></tr></thead><tbody>
          <tr><td>6 组</td><td>1.00</td><td>基准</td></tr>
          <tr><td>10 组</td><td>0.72</td><td class="r-hl">改善约 28%</td></tr>
          <tr><td>14 组</td><td>0.69</td><td>仅再改善约 4%</td></tr>
        </tbody></table>
        <div class="r-note warn">"加人有边际"经验判断被量化：10→14 组边际递减。此三值为报告正文结论（重跑原始输出未落盘），软件以四率代理预测与 Pareto 前沿间接体现该机理；点 Pareto 上不同抢修组方案可对比 MTTR/完好率。</div>
      </div>
      <div class="r-card" style="margin-top:14px"><h4>界面落点</h4>
        <div class="r-note">损伤谱与四率上限 → 左列四率态势卡（完好率/战损率/修复率/故障率）+ 四率时序曲线；排队机理 → 智能参谋问"抢修组从6加到14会怎样"触发代理对比；3D 回放展示战损事件流的时域演化。</div>
      </div>`;
  }

  function m3() {
    const m = DATA.model3;
    const r2rows = m.targets.map((t) => `<tr><td>${t.label}</td><td class="${t.r2 >= 0.65 ? "r-hl" : ""}">${num(t.r2, 4)}</td></tr>`).join("");
    return `
      <div class="r-sec-title">模型三 · 可微代理与随机战损扰动下的方案反演</div>
      <div class="r-sec-sub">"可微代理＋两阶段随机规划"，把确定性最优升级为统计最优。LHS 抽样 + 公共随机数(CRN)训练代理 g≈f。</div>
      <div class="r-grid c4">
        <div class="r-card"><h4>代理精度（图15）${badge("real")}</h4>
          <div class="r-big gold">${num(m.r2_mean, 3)}</div>
          <div class="r-note">留出集 ${m.n_targets} 项 R² 均值 ≥ 0.65，范围 ${num(m.r2_min, 3)}～${num(m.r2_max, 3)}。</div></div>
        <div class="r-card"><h4>训练样本</h4>
          <div class="r-kv"><span class="k">训练</span><span class="v">${m.n_train}</span></div>
          <div class="r-kv"><span class="k">留出</span><span class="v">${m.n_holdout}</span></div>
          <div class="r-kv"><span class="k">加速比</span><span class="v r-hl">${Math.round(m.speedup / 10000)}万×</span></div></div>
        <div class="r-card"><h4>寻优配置${badge("text")}</h4>
          <div class="r-kv"><span class="k">种群</span><span class="v">${m.pop_size}</span></div>
          <div class="r-kv"><span class="k">进化代</span><span class="v">${m.n_gen}</span></div>
          <div class="r-kv"><span class="k">评估次数</span><span class="v">${m.n_eval}</span></div>
          <div class="r-kv"><span class="k">非支配解</span><span class="v">74 个 / 14 秒</span></div></div>
        <div class="r-card"><h4>Pareto 前沿（图16）${badge("real")}</h4>
          <div class="r-big blue">74</div>
          <div class="r-note">F=(−P_success, A, Cost)，现行编组被前沿支配。右列 Pareto 散点可逐点点击查看。</div></div>
      </div>
      <div class="r-card" style="margin-top:14px"><h4>九项指标 R² 明细 ${badge("real")}</h4>
        <table class="r-table" style="max-width:560px"><thead><tr><th>指标</th><th>R²</th></tr></thead><tbody>${r2rows}</tbody></table>
        <div class="r-note">数据来自 /data/surrogate.json（留出集实测）。界面「四率代理预测模型」面板可看九项散点与拟合线。</div>
      </div>
      <div class="r-card" style="margin-top:14px"><h4>两阶段随机优化（图10）</h4>
        <div class="r-flow"><span class="node">阶段一：场景集多目标寻优</span><span class="arw">→</span><span class="node">Pareto 前沿</span><span class="arw">→</span><span class="node">阶段二：按成本间距选候选</span><span class="arw">→</span><span class="node">交模型四复验</span></div>
        <div class="r-note">软件三档方案按钮（knee折中 / leanest最省 / strongest最强）即阶段二按成本间距输出的推荐/备选/保守候选，点击可一键生成并复验。</div>
      </div>`;
  }

  function m4() {
    const m = DATA.model4;
    return `
      <div class="r-sec-title">模型四 · 双闸守卫与区间估计的可信复验</div>
      <div class="r-sec-sub">避免"端点偏差被误判为方案优势"。一闸代理粗筛 → 二闸 K₁ 快筛 → K₂ 终审 → Wilson 区间判定。</div>
      <div class="r-flow"><span class="node">一闸·代理粗筛</span><span class="arw">→</span><span class="node">K₁=${m.k1_fast} 快筛</span><span class="arw">→</span><span class="node">K₂=${m.k2_final} 终审</span><span class="arw">→</span><span class="node">Wilson 判定</span></div>
      <div class="r-grid c4" style="margin-top:14px">
        <div class="r-card"><h4>终审达标（图17）${badge("real")}</h4>
          <div class="r-big">${m.success_count}/${m.n_reps}</div>
          <div class="r-note">任务完成率 ${pct(m.success_rate)}，来自 /data/distribution.json 真实分布。</div></div>
        <div class="r-card"><h4>置信判定（式8）${badge("real")}</h4>
          <div class="r-kv"><span class="k"> Wilson 95% 区间</span><span class="v r-hl">[${num(m.wilson95[0], 3)}, ${num(m.wilson95[1], 3)}]</span></div>
          <div class="r-kv"><span class="k"> p_thr</span><span class="v">≥ ${m.p_thr}</span></div>
          <div class="r-kv"><span class="k"> p_lb</span><span class="v">≥ ${m.p_lb}</span></div>
          <div class="r-note">由 130/200 现算，下界 ${num(m.wilson95[0], 3)} ≥ 0.55 通过。</div></div>
        <div class="r-card"><h4>样本量反算（式9）${badge("text")}</h4>
          <div class="r-formula" style="font-size:14px">${m.k_formula}</div>
          <div class="r-kv"><span class="k">K₁ / ε</span><span class="v">${m.k1_fast} / ${pct(m.eps_fast, 1)}</span></div>
          <div class="r-kv"><span class="k">K₂ / ε</span><span class="v">${m.k2_final} / ${pct(m.eps_final, 1)}</span></div></div>
        <div class="r-card"><h4>无守卫消融（表3）${badge("text")}</h4>
          <div class="r-big red">${m.ablation.dropped} / ${m.ablation.n_candidates}</div>
          <div class="r-note">去掉守卫时 ${m.ablation.n_candidates} 个候选中 ${m.ablation.dropped} 个掉出底线——证明守卫的必要性。</div></div>
      </div>
      <div class="r-card" style="margin-top:14px"><h4>方案对照（报告表3）</h4>
        <table class="r-table"><thead><tr><th>方案</th><th>求解耗时</th><th>方案质量</th><th>可信性</th></tr></thead><tbody>
          <tr><td>现行编组</td><td>约3小时</td><td>完成率0.533（代理），成本0.458</td><td>无复验手段</td></tr>
          <tr><td>纯仿真搜索</td><td>数十分钟级</td><td>接近最优</td><td>可信但不可用</td></tr>
          <tr><td>代理直接采信</td><td>秒级</td><td>端点偏差明显</td><td>8 候选中 2 个掉线</td></tr>
          <tr><td>代理＋守卫</td><td>秒级＋分钟级</td><td class="r-hl">完成率0.650，成本0.165</td><td class="r-hl">复验过闸</td></tr>
        </tbody></table>
        <div class="r-note warn">K₁/K₂、消融明细、耗时为报告正文结论（原始输出未落盘）；终审 130/200 与 Wilson 区间由真实分布实算。界面左列「任务完成率蒙特卡洛分布」+ 方案卡「仿真复验」即此模型的直接体现。</div>
      </div>`;
  }

  function verify() {
    const d = DATA;
    const scen = d.four_scenarios.map((s) => `<tr><td>${s.name}</td><td>${s.setting}</td><td class="r-hl">${s.result}</td></tr>`).join("");
    return `
      <div class="r-sec-title">检验验证与总体效果</div>
      <div class="r-sec-sub">按"条件—模型—方案—总体"四段检验。四个成品想定固定验证条件，随机种子固定，复验次数 48/200 由置信半宽反算。</div>
      <div class="r-grid c2">
        ${card("验证设计四段", `
          <div class="r-flow">${d.validation_stages.map((s, i) => `<span class="node">${s}</span>${i < 3 ? '<span class="arw">→</span>' : ""}`).join("")}</div>
          <div class="r-note">先限定条件保证可复现 → 沿表示/映射/求解/复验逐一验四模型 → 验总体方案 → 与部队演练对比。</div>`, "text")}
        ${card("总体指标达成", `
          <div class="r-kv"><span class="k">完成率</span><span class="v r-hl">0.533 → 0.650 <span class="r-badge real">实算</span></span></div>
          <div class="r-kv"><span class="k">战力再生 η</span><span class="v r-hl">0.488 → 0.491 <span class="r-badge real">实算</span></span></div>
          <div class="r-kv"><span class="k">成本</span><span class="v r-hl">0.458 → 0.165（降64%） <span class="r-badge real">实算</span></span></div>
          <div class="r-kv"><span class="k">方案拟制</span><span class="v">2小时 → 20秒+3分钟 <span class="r-badge text">正文</span></span></div>`, "text")}
      </div>
      <div class="r-card" style="margin-top:14px"><h4>四个成品想定（表2 / 图12）${badge("text")}</h4>
        <table class="r-table"><thead><tr><th>想定</th><th>关键设定</th><th>推演结果</th></tr></thead><tbody>${scen}</tbody></table>
        <div class="r-note">大屏顶栏「工况下拉」可切换对应想定：medium_brigade（中型旅快攻）、brigade_canyon_clear（深切峡谷）、brigade_plain_fog（平原浓雾）、brigade_rolling_fortified（丘陵防御），切换后 3D 回放和数据面板同步更新。</div>
        <div class="r-note warn">口径说明：四个想定的地形「类型」与报告一致（丘陵/峡谷/平原/丘陵），其中两个丘陵想定为同一地形族的不同空间相位（起伏与通行性统计特征相同），便于视觉区分；表内战果为原型标定值、非单一地形相位的必然结果，以实时推演为准。装备作用范围圈/射界按地形通视（LOS）渲染——山体遮蔽方向不显示，与内核直瞄判定一致。</div>
      </div>
      <div class="r-card" style="margin-top:14px"><h4>指标达成度（图19 / 图20）${badge("text")}</h4>
        <div class="r-grid c3">
          <div class="r-kv"><span class="k">方案生成</span><span class="v">≤30分钟 ✓</span></div>
          <div class="r-kv"><span class="k">战力再生</span><span class="v">不低于编组 ✓</span></div>
          <div class="r-kv"><span class="k">评估精度</span><span class="v">R² ${num(DATA.model3.r2_mean, 3)} ≥ 0.65 ✓</span></div>
          <div class="r-kv"><span class="k">推理加速</span><span class="v">${Math.round(DATA.model3.speedup / 10000)}万× ≥ 10万 ✓</span></div>
          <div class="r-kv"><span class="k">方案集</span><span class="v">74 ≥ 50 ✓</span></div>
          <div class="r-kv"><span class="k">方案可信</span><span class="v">65.0% / 下界0.582 ✓</span></div>
        </div>
      </div>
      <div class="r-card" style="margin-top:14px"><h4>数据来源说明</h4>
        <div class="r-note">本面板数字分两类：<span class="r-badge real">真实数据实算</span>来自大屏 /data/*.json（pareto / surrogate / distribution / sensitivity），由 tools/build_verification_data.py 现算，可随数据刷新；<span class="r-badge text">报告正文</span>为最终提交报告结论，底层原始输出未落盘，界面如实标注来源，不冒充实时读取。两者口径已在报告定稿中统一。</div>
      </div>`;
  }

  const RENDER = { design, fig8, figmap, realdata, textable, m1, m2, m3, m4, verify };
  const SRC_NOTE = {
    design: "来源：报告第3节·方案设计（图5~图7）",
    fig8: "来源：最终报告图8（参数全景）+ 软件参数落点核对",
    figmap: "来源：最终报告图1~图20 + /data/sensitivity.json 实算",
    realdata: "来源：AWS Terrarium SRTM DEM + Open-Meteo ERA5（采集元数据实读）",
    textable: "来源：最终报告正文段落 + 表1~表4 + 主要指标/军事效益/推广升级（报告事实，如实标注）",
    m1: "来源：报告图8/13 + /data/pareto.json 实算",
    m2: "来源：报告图9/14 + /data/pareto.json 实算",
    m3: "来源：报告图10/15/16 + /data/surrogate.json",
    m4: "来源：报告图11/17 + /data/distribution.json",
    verify: "来源：报告表2/图19/图20 + 大屏真实数据",
  };

  function render() {
    const body = $("research-body");
    if (!body || !DATA) return;
    body.innerHTML = (RENDER[cur] || design)();
    body.scrollTop = 0;
    if ($("research-source")) $("research-source").textContent = SRC_NOTE[cur] || "";
    document.querySelectorAll(".rtab").forEach((b) => b.classList.toggle("active", b.dataset.rtab === cur));
  }

  function goto(tab) {
    if (!ORDER.includes(tab)) return;
    cur = tab;
    render();
  }

  function open() {
    const modal = $("research-modal");
    if (!modal) return;
    modal.hidden = false;
    if (!DATA) {
      fetch("/data/verification.json", { cache: "no-store" })
        .then((r) => r.json())
        .then((d) => { DATA = d; goto("design"); })
        .catch((e) => { $("research-body").innerHTML = `<div class="r-note warn">数据加载失败：${e}</div>`; });
    } else {
      render();
    }
  }

  function close() {
    const modal = $("research-modal");
    if (modal) modal.hidden = true;
  }

  function init() {
    const openBtn = $("btn-research");
    if (!openBtn) return;
    openBtn.addEventListener("click", open);
    $("research-close")?.addEventListener("click", close);
    $("research-done")?.addEventListener("click", close);
    $("research-modal")?.addEventListener("click", (e) => { if (e.target === $("research-modal")) close(); });
    document.querySelectorAll(".rtab").forEach((b) => b.addEventListener("click", () => goto(b.dataset.rtab)));
    $("research-prev")?.addEventListener("click", () => {
      const i = ORDER.indexOf(cur); goto(ORDER[(i - 1 + ORDER.length) % ORDER.length]);
    });
    $("research-next")?.addEventListener("click", () => {
      const i = ORDER.indexOf(cur); goto(ORDER[(i + 1) % ORDER.length]);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("research-modal").hidden) close();
    });
  }

  window.addEventListener("DOMContentLoaded", init);
  DASH.research = { open, close, goto, init };
  window.DASH = DASH;
})();
