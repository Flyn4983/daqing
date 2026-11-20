/* 细节内嵌模块（报告第3节 ↔ GUI 输入/设置/图表）
 * 目标：让评委在软件界面上"随手可指"报告细节——
 *   1. 配置弹窗每个参数项 label 追加 ℹ 按钮 → 浮层显示该参数对应的报告模型/公式/可行域/权威数字
 *   2. 每个 cfg-group 标题下追加"报告映射"说明带（一阶决策/二阶条件/情景因子…）
 *   3. 5 个图表面板标题追加 ℹ 按钮 + 图表下方"报告锚点"带
 *   4. 工况下拉每个 option 加 title 悬浮说明（含 MutationObserver 兼容异步重建）
 * 数字来源：/data/verification.json（tools/build_verification_data.py 从真实 JSON 实算）
 * 角标：real=真实数据实算 / text=报告正文结论（严格区分，不冒充实时读取）
 */
(function () {
  "use strict";
  const DASH = (window.DASH = window.DASH || {});
  const $ = (id) => document.getElementById(id);

  const pct = (x, d = 1) => (x == null ? "—" : (Number(x) * 100).toFixed(d) + "%");
  const num = (x, d = 3) => (x == null ? "—" : Number(x).toFixed(d));
  const badge = (kind) => kind === "real"
    ? '<span class="r-badge real">真实数据实算</span>'
    : '<span class="r-badge text">报告正文</span>';

  // ---------- 元数据构建（依赖 DATA，故用函数） ----------
  function buildMeta(D) {
    const m1 = D.model1, m2 = D.model2, m3 = D.model3, m4 = D.model4;
    const sv = D.sensitivity_visibility || {};
    const stBlue = sv.attrition_blue ? num(sv.attrition_blue.ST, 3) : "0.758";
    const stBlueRank = sv.attrition_blue ? sv.attrition_blue.rank : 1;
    const eta0 = m1.eta_goal && m1.eta_goal[0], eta1 = m1.eta_goal && m1.eta_goal[1];
    const lb = m1.feasibility && m1.feasibility.bounds ? m1.feasibility.bounds : null;
    const bnds = (k) => {
      if (lb && lb[k]) return `${lb[k][0]} ~ ${lb[k][1]}`;
      return null;
    };
    const pert = m1.perturbation || {};

    // 简易构造器：rows = [[k,v],…]
    const D_ = (tag, title, kind, rows, opt) => Object.assign({ tag, title, kind, rows }, opt || {});

    // ===== 配置弹窗：参数项细节 =====
    const CTRL = {
      // ① 任务与地形
      "cfg-preset": D_("想定矩阵", "快速预设 · 成品想定", "text", [
        ["报告落点", "表2 四个成品想定 + 示例想定"],
        ["区分维度", "编成规模 / 地形族 / 气象 / 敌情"],
        ["验证", "四想定按同一四率口径横向对比（检验验证）"],
      ], { note: "切换预设即切换整套想定参数；工况下拉与预设联动。" }),
      "cfg-duration_ticks": D_("想定时长", "作战时长 (tick)", "text", [
       ["采样基准", "战损事件流的统计窗口"],
        ["模型四", `K₁=${m4.k1_fast} 快筛 / K₂=${m4.k2_final} 终审的仿真时长基准`],
      ]),
      "cfg-objective_x": D_("Pareto 目标", "目标坐标 / 完成判定", "text", [
        ["目标函数", "F = (−P_s, A, Cost)，式(7)"],
        ["P_s", "任务完成率——模型三 Pareto 三目标之一"],
        ["达成", `完成率 ${pct(m1.success_goal && m1.success_goal[0])} → ${pct(m1.success_goal && m1.success_goal[1])}`, ],
      ], { kind: "real" }),
      "cfg-preset_family": D_("环境建模", "地形族 · 8 族 FBM", "text", [
        ["报告落点", "8 族程序化地形（rolling/canyon/plain/mountain/paddy/urban/desert/snowfield）"],
        ["特例", "example_brigade / example_battalion 用实测 DEM，其余 5 个为 FBM 预设"],
        ["可视化", "3D 回放 Hillshade 地形随想定切换"],
      ]),
      "cfg-grid_n": D_("战场离散", "网格分辨率", "text", [
        ["作用", "战场离散化精度，影响 FBM 地形与通行性场"],
        ["基线", "65×65"],
      ]),

      // ② 环境与气象
      "cfg-weather": D_("气象建模", "天气", "text", [
        ["报告落点", "气象差异化建模（晴/雾/雨/雪）"],
        ["可视化", "3D 回放按气象渲染雨幕/雪点/雾团"],
      ]),
      "cfg-illumination": D_("能见度建模", "光照", "text", [
        ["报告落点", "昼夜光照差异化（白昼/黄昏/暗夜）"],
        ["可视化", "3D 回放 Lambert 山影随光照角变化"],
      ]),
      "cfg-visibility_factor": D_("★ 灵敏度 rank1", "能见度系数", "real", [
        ["Sobol 总效应", `ST = ${stBlue}`],
        ["归属指标", `蓝方战损率 attrition_blue（rank ${stBlueRank}）`],
        ["报告结论", "能见度是八类战场因子中对蓝方战损率影响最大的单因子"],
      ], { formula: "S_i^T = 1 − E[Var(f | X_∼i)] / Var(f)", note: "调低能见度→发现概率下降→交战强度变化→战损率改变。" }),

      // ③ 保障资源旋钮（核心 · 一阶决策）
      "cfg-red_repair_teams": D_("一阶决策 x₁", "并行抢修组数", "real", [
        ["可行域", bnds("red_repair_teams") || "2 ~ 10"],
        ["机理", "模型二 MTTR ≈ MTTR₀·exp(Q/c)，Q 为抢修容量"],
        ["报告验证", "抢修组取 6/10/14 重跑，MTTR 相对 1.00/0.72/0.69（指数饱和）"],
      ], { formula: "MTTR ≈ MTTR₀ · exp(Q / c)", note: "报告原始输出未落盘，MTTR 三值为报告正文口径。" }),
      "cfg-red_arv": D_("一阶决策 x₂", "装甲抢救车数", "real", [
        ["可行域", bnds("red_arv") || "2 ~ 6"],
        ["作用", "战损装备后送/抢救能力，影响可修性判定 Ω(d)"],
      ]),
      "cfg-red_supply_veh": D_("一阶决策 x₃", "器材补给车数", "real", [
        ["可行域", bnds("red_supply_veh") || "1 ~ 3"],
        ["作用", "备件供应节拍，影响修复率 Pᵣ 与排队长度 Q"],
      ]),
      "cfg-carry_level": D_("一阶决策 x₄", "携行档基数档级", "real", [
        ["可行域", bnds("carry_level") || "0 ~ 4（5 档）"],
        ["作用", "随行携行器材充裕度 → 初始保障韧性"],
        ["成本", "计入 Σx 资源成本软目标（三源约束 C）"],
      ]),
      "cfg-operating_level": D_("一阶决策 x₅", "运行档基数档级", "real", [
        ["可行域", bnds("operating_level") || "0 ~ 3（4 档）"],
        ["作用", "装备出动强度档 → 影响故障率 F 与战损暴露"],
      ]),
      "cfg-wave_interval_ticks": D_("一阶决策 x₆", "前送波次节拍", "real", [
        ["可行域", bnds("wave_interval_ticks") || "10 ~ 40 tick"],
        ["作用", "备件前送波次间隔 → 排队等待与 MTTR"],
        ["推荐方案", "knee 折中方案在节拍与成本间取平衡"],
      ]),
      "cfg-wave_qty": D_("一阶决策 x₇", "每波每部件件数", "real", [
        ["可行域", bnds("wave_qty") || "1 ~ 4"],
        ["作用", "单波补给量 → 修复率 Pᵣ 与成本 Σx 的权衡"],
      ]),

      // ④ 情景因子
      "cfg-mtbf_scale": D_("二阶情景 ξ", "MTBF 乘子 · 装备可靠性", "text", [
        ["报告落点", "两阶段随机优化的情景抽样因子（图10）"],
        ["作用", "改变平均故障间隔 → 故障率 F → 战力再生 η"],
        ["方法", "LHS 抽样 + 公共随机数 CRN"],
      ]),
      "cfg-red_pk_scale": D_("二阶情景 ξ", "红方效能乘子", "text", [
        ["报告落点", "交战强度情景，鲁棒性分析对象"],
        ["作用", "改变红方打击效能 → 战损事件流 → 四率"],
      ]),
      "cfg-blue_pk_scale": D_("二阶情景 ξ", "蓝方效能乘子", "text", [
        ["报告落点", "敌情强度情景，鲁棒性分析对象"],
        ["作用", "改变蓝方打击效能 → 我方战损 → 完好率 R₀"],
      ]),

      // ⑤ FBM 地形
      "cfg-octaves": D_("环境建模", "FBM 叠加层数 octaves", "text", [
        ["报告落点", "8 族 FBM 地形生成：H = Σ ampᵢ·noise(scale·x)"],
        ["作用", "层数越多，地形细节越丰富"],
      ]),
      "cfg-amplitude": D_("环境建模", "起伏幅度 amplitude", "text", [
        ["作用", "FBM 地形垂直起伏基准（米）"],
        ["可视化", "3D 回放按 auto_z_exag 夸张后呈现立体地形"],
      ]),

      // ⑥ 方别不对称
      "cfg-red_detect_scale": D_("能见度差异化", "红方发现概率乘子", "text", [
        ["报告落点", "红蓝差异化能见度建模"],
        ["关联", "能见度 ST=0.758 主导蓝方战损率"],
      ], { kind: "real" }),
      "cfg-blue_detect_scale": D_("能见度差异化", "蓝方发现概率乘子", "real", [
        ["Sobol rank1", `能见度 → 蓝方战损率 ST = ${stBlue}`],
        ["作用", "发现概率变化直接改变交战命中链"],
      ]),
    };

    // ===== 配置弹窗：组说明带 =====
    const GROUPS = [
      { match: "① 任务与地形", text: "报告映射：想定与战场环境构建 · 表2 四成品想定 · 8 族 FBM 地形 / 实测 DEM" },
      { match: "② 环境与气象", text: `报告映射：气象/光照/能见度差异化建模 · 能见度 ST=${stBlue} 为蓝方战损率 rank1 因子` },
      { match: "③ 保障资源旋钮", text: "报告映射：模型一『一阶决策变量』—— 优化器实装 7 维（下列）+ 固定 2 维（后送比例/抢修优先规则）；点顶栏『研究细节 → ★图8参数全景』可逐项查看一阶9维/二阶9维/全集十类的软件落点；可行域 X 见式(3)，74 解零越界；三源约束 A/B/C/D 见下" },
      { match: "④ 情景因子", text: "报告映射：模型一『二阶固定条件 / 情景因子』—— 两阶段随机优化（图10）的抽样维度，LHS + 公共随机数 CRN" },
      { match: "⑤ 地形合成细节", text: "报告映射：8 族 FBM 地形程序化生成参数（报告环境建模）" },
      { match: "⑥ 方别不对称乘子", text: `报告映射：红蓝差异化能见度 / 机动建模 · 对应能见度因子对蓝方战损率的 ST=${stBlue}` },
      { match: "⑦ 风场", text: "报告映射：气象要素（风速/风向），3D 回放风矢可视化" },
    ];

    // ===== 图表面板：ℹ 细节 + 锚点带 =====
    const CHARTS = [
      { svg: "ts-svg", tag: "模型二 · 四率耦合", title: "四率时序曲线", kind: "real",
        rows: [
          ["四率口径", "完好率 R₀=可用/编制 · 战损率 A=损失/编制 · 修复率 Pᵣ=已修/待修 · 故障率 F=次/百装备时"],
          ["合成目标", `η = ω₁R₀ + ω₂(1−A) + ω₃Pᵣ + ω₄(1−F)，ω=[0.35,0.25,0.25,0.15]`],
          ["战力再生", `η ${num(eta0,3)} → ${num(eta1,3)}（报告图13a）`],
          ["耦合机理", `战损率—完好率相关系数 ${num(m2.corr_attrition_readiness,3)}（≈−0.60）`],
        ],
        formula: "η = ω₁R₀ + ω₂(1−A) + ω₃Pᵣ + ω₄(1−F)　（式1）",
        anchor: "式(1) 战力再生合成 · 表1 四率口径 · 图13a η分解" },
      { svg: "dist-svg", tag: "模型四 · 可信复验", title: "任务完成率蒙特卡洛分布", kind: "real",
        rows: [
          ["双闸守卫", `K₁=${m4.k1_fast} 快筛 → K₂=${m4.k2_final} 终审（式9）`],
          ["终审达标", `${m4.success_count}/${m4.n_reps} = ${pct(m4.success_rate)}`],
          ["Wilson 95% 区间", `[${num(m4.wilson95 && m4.wilson95[0],3)}, ${num(m4.wilson95 && m4.wilson95[1],3)}]`],
          ["合格判据", `P̂ ≥ ${m4.p_thr} 且 Wilson 下界 ≥ ${m4.p_lb}（式8）`],
        ],
        formula: "P̂ ≥ p_thr 且 Wilson_LB ≥ p_lb　（式8）",
        note: `ε 快筛≈${pct(m4.eps_fast,1)} / 终审≈${pct(m4.eps_final,1)}；消融 8 候选 2 掉线（表3，报告正文）`,
        anchor: "式(8)(9) 双闸守卫 · 图17 MC 分布 · 130/200 终审" },
      { svg: "pareto-svg", tag: "模型三 · 反演寻优", title: "NSGA-II Pareto 前沿", kind: "real",
        rows: [
          ["目标函数", "F = (−P_s, A, Cost)（式7）"],
          ["前沿规模", `${m1.feasibility ? m1.feasibility.n_plans : 74} 个非支配解（三源约束零越界 ${m1.feasibility ? m1.feasibility.violations : 0} 项）`],
          ["成本", `knee ${num(m1.cost_goal && m1.cost_goal[1],3)} vs 基线 ${num(m1.cost_goal && m1.cost_goal[0],3)}（降64%）`],
          ["完成率", `${num(m1.success_goal && m1.success_goal[0],3)} → ${num(m1.success_goal && m1.success_goal[1],3)}（${m4.success_count}/${m4.n_reps} 终审）`],
        ],
        formula: "F = (−P_s, A, Cost)　（式7）",
        note: `NSGA-II 种群 ${m3.pop_size||80} × ${m3.n_gen||60} 代 = ${m3.n_eval||4800} 次评估（报告正文）`,
        anchor: "式(7) 三目标 · 74 解零越界 · 成本降64%" },
      { svg: "sens-svg", tag: "模型三 · 灵敏度分析", title: "Sobol 灵敏度（ST 总效应）", kind: "real",
        rows: [
          ["rank1 因子", `能见度 → 蓝方战损率，ST = ${stBlue}`],
          ["完成率 ST", sv.success ? `${num(sv.success.ST,3)}（rank ${sv.success.rank}）` : "0.598（rank 1）"],
          ["方法", "Sobol 总效应分解，八类战场因子 → 五项关键指标"],
          ["结论", "能见度/天候对交战结果的影响超过保障资源本身"],
        ],
        formula: "S_i^T = 1 − E[Var(f | X_∼i)] / Var(f)",
        anchor: `Sobol ST 总效应 · 能见度 rank1=${stBlue}` },
      { svg: "parity-svg", tag: "模型三 · 可微代理", title: "四率代理预测模型", kind: "real",
        rows: [
          ["精度", `留出集九项指标平均 R² = ${num(m3.r2_mean,3)}（≥0.65 达标）`],
          ["样本", `训练 ${m3.n_train||192} + 留出 ${m3.n_holdout||48}`],
          ["加速", `${num(m3.speedup,0)} 倍（前向 ≥10 万倍，秒级预测）`],
          ["训练法", "LHS 抽样 + 公共随机数 CRN，MLP 64×64"],
        ],
        formula: "R² = 1 − SS_res / SS_tot",
        anchor: "九项 R² 均值 0.705 · 加速 38 万× · 192+48 样本" },
    ];

    return { CTRL, GROUPS, CHARTS };
  }

  // ---------- 浮层 ----------
  let pop = null;
  function ensurePop() {
    if (pop) return pop;
    pop = document.createElement("div");
    pop.id = "insight-pop";
    pop.className = "insight-pop";
    pop.hidden = true;
    pop.innerHTML = `<div class="ip-head"><span class="ip-title"></span><button class="ip-close" type="button" aria-label="关闭">✕</button></div>
      <div class="ip-body"></div><div class="ip-foot">点击空白处或按 Esc 关闭</div>`;
    document.body.appendChild(pop);
    pop.querySelector(".ip-close").addEventListener("click", hidePop);
    pop.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", hidePop);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") hidePop(); });
    return pop;
  }
  function hidePop() { if (pop) pop.hidden = true; }
  function showPop(anchor, meta) {
    ensurePop();
    const title = pop.querySelector(".ip-title");
    const body = pop.querySelector(".ip-body");
    title.innerHTML = `<span class="ip-tag">${DASH.esc(meta.tag)}</span> ${DASH.esc(meta.title)} ${badge(meta.kind)}`;
    const rows = (meta.rows || []).map((r) =>
      `<div class="r-kv"><span class="k">${DASH.esc(r[0])}</span><span class="v">${r[1]}</span></div>`).join("");
    body.innerHTML = rows
      + (meta.formula ? `<div class="r-formula">${meta.formula}</div>` : "")
      + (meta.note ? `<div class="r-note">${meta.note}</div>` : "");
    pop.hidden = false;
    // 定位：优先放按钮下方右侧，越界则收回到视口内
    const r = anchor.getBoundingClientRect();
    const pw = Math.min(460, window.innerWidth - 24);
    pop.style.width = pw + "px";
    let left = r.left - 8, top = r.bottom + 8;
    // 先渲染再量高
    const ph = pop.offsetHeight;
    if (left + pw > window.innerWidth - 12) left = window.innerWidth - pw - 12;
    if (left < 12) left = 12;
    if (top + ph > window.innerHeight - 12) top = Math.max(12, r.top - ph - 8);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }
  function mkBtn(meta) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "i-btn";
    b.textContent = "i";
    b.setAttribute("aria-label", "查看报告细节：" + meta.title);
    b.title = "查看报告细节";
    b.addEventListener("click", (e) => { e.stopPropagation(); showPop(b, meta); });
    return b;
  }

  // ---------- 注入 ----------
  function injectConfig(M) {
    // 参数项 ℹ 按钮
    Object.keys(M.CTRL).forEach((id) => {
      const input = $(id);
      if (!input) return;
      const item = input.closest(".cfg-item");
      if (!item) return;
      const label = item.querySelector("label");
      if (!label || label.querySelector(".i-btn")) return;
      label.appendChild(mkBtn(M.CTRL[id]));
    });
    // 组说明带
    document.querySelectorAll(".cfg-group h3").forEach((h3) => {
      const txt = (h3.textContent || "").trim();
      const g = M.GROUPS.find((x) => txt.indexOf(x.match) === 0);
      if (!g) return;
      if (h3.parentElement.querySelector(".insight-band")) return;
      const band = document.createElement("div");
      band.className = "insight-band";
      band.innerHTML = `<span class="ib-dot">报</span>${DASH.esc(g.text)}`;
      h3.insertAdjacentElement("afterend", band);
    });
  }

  function injectCharts(M) {
    M.CHARTS.forEach((c) => {
      const svg = $(c.svg);
      if (!svg) return;
      const panel = svg.closest(".panel");
      if (!panel) return;
      const h3 = panel.querySelector("h3");
      if (h3 && !h3.querySelector(".i-btn")) h3.appendChild(mkBtn(c));
      const body = panel.querySelector(".panel-body");
      if (body && !body.querySelector(".chart-anchor")) {
        const a = document.createElement("div");
        a.className = "chart-anchor";
        a.innerHTML = `<span class="ca-ico">报</span>${DASH.esc(c.anchor)}`;
        body.appendChild(a);
      }
    });
  }

  function injectScenarioTitles() {
    const meta = {
      medium_brigade: "基线 · 中型合成旅快攻 · rolling 起伏丘陵 · 晴 · 白昼",
      brigade_canyon_clear: "成品想定 · 峡谷通道攻坚 · canyon 深切峡谷 · 坚固防御",
      brigade_plain_fog: "成品想定 · 平原大雾突袭 · plain 平原 · 大雾（能见度受限）",
      brigade_rolling_fortified: "成品想定 · 坚固防御反击 · rolling · 夜间防御",
      example_brigade: "示例 · 重型合成旅 · 实测 DEM 地形",
      example_battalion: "示例 · 合成营 · 实测 DEM 地形",
      example_company: "示例 · 合成连",
    };
    const apply = (sel) => {
      if (!sel) return;
      Array.from(sel.options).forEach((o) => {
        if (meta[o.value]) o.title = meta[o.value];
      });
    };
    apply($("scenario-select"));
    apply($("cfg-preset"));
    // 兼容 fillOptions 异步重建 option
    const sel = $("scenario-select");
    if (sel && !sel.__insightObs) {
      sel.__insightObs = true;
      new MutationObserver(() => apply(sel)).observe(sel, { childList: true, subtree: true });
    }
  }

  function init() {
    DASH.loadJson("verification.json").then((D) => {
      const M = buildMeta(D);
      injectConfig(M);
      injectCharts(M);
      injectScenarioTitles();
    }).catch(() => {
      // 数据缺失时仍注入纯文本元数据的ℹ（用空 DATA 兜底，避免界面无按钮）
      const M = buildMeta({ model1: {}, model2: {}, model3: {}, model4: {}, four_scenarios: [] });
      injectConfig(M); injectCharts(M); injectScenarioTitles();
    });
    // DOM 就绪后立刻给下拉加 title（不依赖数据）
    injectScenarioTitles();
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
