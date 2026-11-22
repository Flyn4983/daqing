/* 作战保障全过程抽屉 —— 8 节点流程状态机 + 三栏联动
   仅新增，不改任何既有 JS；通过 /api/support/* 与后端交互。 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var CAT = null;
  var FORCES_DEFAULT = null;   // 想定默认双方编成（懒加载）
  var FORCE_SIDE = 'red';      // 当前编辑的方别 tab
  var ST = { missionId: null, lossId: null, planId: null, jobId: null, poll: null, nodes: {},
             loss: null, lossTab: 'items', lossFilter: {}, lossSort: null, lossPage: 0,
             lossPageSize: 50, provision: null,
             /* P2 */ compareRows: null, selectedPlans: [], review: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }
  function api(path, opts) {
    return fetch(path, opts).then(function (r) { return r.json(); }).catch(function (e) {
      return { error: '网络错误：' + e.message };
    });
  }
  function post(path, body) {
    return api(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  /* ---------------- 抽屉开关 ---------------- */
  function open() {
    $('sf-drawer').classList.add('open');
    $('sf-drawer').setAttribute('aria-hidden', 'false');
    if (!CAT) boot();
    else refreshLlmBadge();
  }
  function close() {
    $('sf-drawer').classList.remove('open');
    $('sf-drawer').setAttribute('aria-hidden', 'true');
    if (ST.poll) { clearInterval(ST.poll); ST.poll = null; }
  }

  /* ---------------- 节点流程条 ---------------- */
  var NODE_COL = { input: 'sf-col-input', validate: 'sf-col-input', deduce: 'sf-col-loss',
                   loss: 'sf-col-loss', rates: 'sf-col-loss', plan: 'sf-col-plan',
                   review: 'sf-col-plan', export: 'sf-col-plan' };
  function renderPipeline(nodes) {
    var html = '';
    nodes.forEach(function (n, i) {
      var st = ST.nodes[n.id] || 'idle';
      var label = { idle: '未开始', ready: '就绪', running: '进行中', done: '完成', error: '异常' }[st] || st;
      html += '<div class="sf-node ' + st + (ST.activeNode === n.id ? ' active' : '') +
              '" data-node="' + esc(n.id) + '" title="' + esc(n.desc) + '">' +
              '<span class="n-idx">' + n.idx + '</span>' +
              '<div class="n-name">' + esc(n.name) + '</div>' +
              '<div class="n-desc">' + esc(n.desc) + '</div>' +
              '<div class="n-status">' + label + '</div></div>';
      if (i < nodes.length - 1) html += '<div class="sf-arrow">›</div>';
    });
    $('sf-pipeline').innerHTML = html;
    Array.prototype.forEach.call($('sf-pipeline').querySelectorAll('.sf-node'), function (el) {
      el.addEventListener('click', function () {
        var id = el.getAttribute('data-node');
        ST.activeNode = id;
        renderPipeline(nodes);
        var col = $(NODE_COL[id]);
        if (col && col.scrollIntoView) col.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
      });
    });
  }
  function setNode(id, status) {
    ST.nodes[id] = status;
    if (CAT) renderPipeline(CAT.process_nodes);
  }
  function resetNodes() {
    ST.nodes = {};
    setNode('input', 'ready');
  }

  /* ---------------- 录入表单 ---------------- */
  var DEFAULT_COUNTS = { tank: 24, ifv: 36, howitzer: 12, rocket: 6, hq17: 8,
                         c2_veh: 6, recon_veh: 8, arv: 3, repair_veh: 6,
                         supply_veh: 2, ammo_veh: 3, uav: 4 };
  function numField(group, key, field, val, label) {
    var input = '<input type="number" min="0" value="' + esc(val) +
                '" data-group="' + esc(group) + '" data-key="' + esc(key) +
                '" data-field="' + esc(field) + '">';
    return label ? '<label>' + esc(label) + '</label>' + input : input;
  }
  // 双方实力专用字段：data-group=forces，带 side 与 cat（equipment/air/modifiers）
  function forceField(side, cat, key, val, step) {
    return '<input type="number" min="0" ' + (step ? 'step="' + step + '"' : '') +
           ' value="' + esc(val) + '" data-group="forces" data-side="' + side +
           '" data-cat="' + cat + '" data-key="' + esc(key) + '">';
  }
  function renderForm(cat) {
    var h = '';
    // 任务参数
    h += '<details class="sf-group" open><summary>任务与推演参数</summary><div class="sf-group-body">';
    h += '<div class="sf-row simple">' + numField('meta', 'task', 'duration_ticks', 96, '推演时长 tick') + '</div>';
    h += '<div class="sf-row simple">' + numField('meta', 'task', 'n_runs', 8, 'MC 推演次数') + '</div>';
    h += '<div class="sf-row simple">' + numField('meta', 'task', 'seed', 20261013, '随机种子') + '</div>';
    h += '<div class="sf-row simple"><label>任务名称</label><input type="text" value="合成旅进攻装备保障任务" data-group="meta" data-key="task" data-field="task_name"></div>';
    h += '<div class="sf-row simple"><label>任务类型</label><select data-group="meta" data-key="task" data-field="mission_type">' +
         cat.catalog.mission_types.map(function (x) { return '<option>' + esc(x) + '</option>'; }).join('') + '</select></div>';
    h += '<div class="sf-row simple"><label>基础工况</label><select data-group="meta" data-key="task" data-field="base_scenario">' +
         cat.catalog.scenarios.map(function (x) { return '<option>' + esc(x) + '</option>'; }).join('') + '</select></div>';
    h += '<div class="sf-subtitle">保障目标阈值（用于方案§二，可留空用默认 90/85/80%）</div>';
    h += '<div class="sf-row simple">' + numField('targets', 'targets', 'readiness', 90, '完好率目标 %') + '</div>';
    h += '<div class="sf-row simple">' + numField('targets', 'targets', 'success', 85, '完成率目标 %') + '</div>';
    h += '<div class="sf-row simple">' + numField('targets', 'targets', 'repair_timeliness', 80, '抢修及时率目标 %') + '</div>';
    h += '</div></details>';

    // P2-5：成本权重与约束（仅方案层/对比展示，不进内核旋钮）
    var cwSpec = cat.cost_weight_spec || [];
    if (cwSpec.length) {
      h += '<details class="sf-group"><summary>成本权重与约束（P2-5，方案层）</summary><div class="sf-group-body">';
      cwSpec.forEach(function (cw) {
        h += '<div class="sf-row simple"><label title="' + esc(cw.hint) + '">' + esc(cw.name) + '</label>' +
             '<input type="number" min="' + cw.min + '" max="' + cw.max + '" step="' + cw.step +
             '" value="' + cw.default + '" data-group="cost_weights" data-key="cost" data-field="' +
             esc(cw.key) + '"></div>';
      });
      h += '<div class="sf-note">三项权重自动归一到 1；成本上限 0=不设限。仅用于方案评估展示，不进入仿真内核，四率权威数字不变。</div>';
      h += '</div></details>';
    }

    // 对战双方实力（红蓝对称，默认不启用=零漂移）
    var fc = cat.force_config || { sides: [{id:'red',name:'红方（我方）',color:'#f87171'},{id:'blue',name:'蓝方（敌方）',color:'#60a5fa'}],
      multipliers: [{key:'pk_scale',name:'交火效能',default:1},{key:'detect_scale',name:'侦察效能',default:1},{key:'speed_scale',name:'机动速度',default:1}],
      ground_keys: [], air_kinds: ['uav','loitering','helo'] };
    h += '<details class="sf-group sf-forces" open><summary>对战双方实力设定 <span id="sf-forces-badge" class="sf-forces-badge">未启用</span></summary><div class="sf-group-body">';
    h += '<label class="sf-forces-toggle"><input type="checkbox" id="sf-forces-enable"> 自定义双方编成（不勾选则沿用想定默认，数字不变）</label>';
    h += '<div class="sf-forces-tabs">' + fc.sides.map(function (s) {
      return '<button type="button" class="sf-side-tab' + (s.id === FORCE_SIDE ? ' active' : '') +
             '" data-side="' + s.id + '" style="--side-color:' + s.color + '">' + esc(s.name) + '</button>';
    }).join('') + '</div>';
    h += '<div class="sf-forces-summary" id="sf-forces-summary"></div>';
    // 两个方别的字段区（同时渲染，靠 CSS 控制显示）
    fc.sides.forEach(function (sd) {
      h += '<div class="sf-side-pane' + (sd.id === FORCE_SIDE ? ' active' : '') + '" data-pane="' + sd.id + '">';
      h += '<div class="sf-force-sub">地面装备数量（台）</div>';
      h += '<div class="sf-force-grid">';
      fc.ground_keys.forEach(function (k) {
        var meta = (cat.catalog.equipment || []).filter(function (e) { return e.key === k; })[0] || {};
        h += '<label title="' + esc(meta.name || k) + '"><span>' + esc(meta.name || k) + '</span>' +
             forceField(sd.id, 'equipment', k, 0) + '</label>';
      });
      h += '</div>';
      h += '<div class="sf-force-sub">空中架次（架）</div>';
      h += '<div class="sf-force-grid air">';
      fc.air_kinds.forEach(function (k) {
        var nm = {uav:'无人机', loitering:'巡飞弹', helo:'武装直升机'}[k] || k;
        h += '<label><span>' + nm + '</span>' + forceField(sd.id, 'air', k, 0) + '</label>';
      });
      h += '</div>';
      h += '<div class="sf-force-sub">方别效能乘子（相对基准 1.0）</div>';
      h += '<div class="sf-force-grid mods">';
      fc.multipliers.forEach(function (mo) {
        h += '<label title="' + esc(mo.hint) + '"><span>' + esc(mo.name) + '</span>' +
             forceField(sd.id, 'modifiers', mo.key, mo.default, '0.05') + '</label>';
      });
      h += '</div>';
      h += '<div class="sf-force-grid ratios">' +
           '<label title="带病参战比例 0~1"><span>带病比例</span>' + forceField(sd.id, 'ratio', 'worn_ratio', 0, '0.05') + '</label>' +
           '<label title="既设工事比例 0~1"><span>工事比例</span>' + forceField(sd.id, 'ratio', 'dug_ratio', 0, '0.05') + '</label></div>';
      h += '</div>';
    });
    h += '<div class="sf-forces-actions"><button type="button" class="sf-btn" id="sf-forces-load">载入当前想定默认编成</button>' +
         '<button type="button" class="sf-btn" id="sf-forces-clear">清空</button></div>';
    h += '<div class="sf-note">载入默认后可逐项修改；数值会真正覆盖想定编成进入推演（四率分母随之变化）。</div>';
    h += '</div></details>';

    // 装备编成（按 group 归类）
    var groups = {};
    cat.catalog.equipment.forEach(function (e) { (groups[e.group] = groups[e.group] || []).push(e); });
    Object.keys(groups).forEach(function (gname, gi) {
      h += '<details class="sf-group"' + (gi < 2 ? ' open' : '') + '><summary>' + esc(gname) + '</summary><div class="sf-group-body">';
      h += '<div class="sf-row sf-row-head"><label>装备类型</label><label>数量</label><label>初始磨损</label><label>弹药基数</label></div>';
      groups[gname].forEach(function (e) {
        h += '<div class="sf-row"><label title="' + esc(e.name) + '">' + esc(e.name) + '</label>' +
             numField('equipment', e.key, 'count', DEFAULT_COUNTS[e.key] || 0, '') +
             numField('equipment', e.key, 'initial_worn', 0, '') +
             numField('equipment', e.key, 'ammo_basic_load', 0, '') + '</div>';
      });
      h += '</div></details>';
    });

    // 弹药
    h += '<details class="sf-group"><summary>弹药储备</summary><div class="sf-group-body">';
    h += '<div class="sf-row sf-row-head"><label>弹种</label><label>基数</label><label>储备</label></div>';
    cat.catalog.ammo.forEach(function (a) {
      h += '<div class="sf-row"><label title="' + esc(a['for']) + '">' + esc(a.name) + '</label>' +
           numField('ammo', a['class'], 'basic_load', 0, '') +
           numField('ammo', a['class'], 'reserve', 0, '') + '<span></span></div>';
    });
    h += '</div></details>';

    // 油料
    h += '<details class="sf-group"><summary>油料与油脂（方案层）</summary><div class="sf-group-body">';
    h += '<div class="sf-row sf-row-head"><label>品类</label><label>储备</label><label>前送</label></div>';
    cat.catalog.fuel.forEach(function (f) {
      h += '<div class="sf-row"><label>' + esc(f.grade) + '(' + esc(f.unit) + ')</label>' +
           numField('fuel', f.grade, 'stock_l', 0, '') +
           numField('fuel', f.grade, 'resupply_l', 0, '') + '<span></span></div>';
    });
    h += '<div class="sf-note">油料不进入仿真内核，只用于方案测算；四率权威数字不受影响。</div>';
    h += '</div></details>';

    // 保障车辆
    h += '<details class="sf-group" open><summary>保障车辆（映射推演旋钮）</summary><div class="sf-group-body">';
    h += '<div class="sf-row sf-row-head"><label>车型</label><label>数量</label></div>';
    cat.catalog.support_vehicles.forEach(function (v) {
      h += '<div class="sf-row simple"><label>' + esc(v.name) + '</label>' +
           numField('support_vehicles', v.key, 'count', DEFAULT_COUNTS[v.key] || 0, '') + '</div>';
    });
    h += '</div></details>';

    // 其他资源
    h += '<details class="sf-group"><summary>器材与通用消耗品（三级储备）</summary><div class="sf-group-body">';
    h += '<div class="sf-row sf-row-head"><label>品名</label><label>携行</label><label>运行</label><label>基地</label></div>';
    cat.catalog.resources.forEach(function (r) {
      h += '<div class="sf-row"><label>' + esc(r.name) + '</label>' +
           numField('resources', r.key, 'carry', 0, '') +
           numField('resources', r.key, 'operating', 0, '') +
           numField('resources', r.key, 'depot', 0, '') + '</div>';
    });
    h += '</div></details>';

    // 日常业务
    h += '<details class="sf-group"><summary>日常业务要素（方案层）</summary><div class="sf-group-body">';
    cat.catalog.daily_ops.forEach(function (d) {
      h += '<div class="sf-row simple"><label>' + esc(d.name) + '</label><select data-group="daily_ops" data-key="' +
           esc(d.key) + '" data-field="value">' +
           d.options.map(function (o) { return '<option>' + esc(o) + '</option>'; }).join('') + '</select></div>';
    });
    h += '</div></details>';
    $('sf-form').innerHTML = h;
  }

  /* ---- 双方实力：tab 切换 / 载入默认 / 汇总 ---- */
  function switchForceSide(side) {
    FORCE_SIDE = side;
    Array.prototype.forEach.call($('sf-form').querySelectorAll('.sf-side-tab'), function (b) {
      b.classList.toggle('active', b.getAttribute('data-side') === side);
    });
    Array.prototype.forEach.call($('sf-form').querySelectorAll('.sf-side-pane'), function (p) {
      p.classList.toggle('active', p.getAttribute('data-pane') === side);
    });
  }
  function loadForcesDefault() {
    var scen = ($('sf-form').querySelector('[data-group="meta"][data-field="base_scenario"]') || {}).value || 'example_brigade';
    api('/api/support/forces?scenario=' + encodeURIComponent(scen)).then(function (r) {
      if (r.error || !r.sides) { alert('载入默认失败：' + (r.error || '无数据')); return; }
      FORCES_DEFAULT = r;
      var eqNames = r.equipment_names || {};
      ['red', 'blue'].forEach(function (side) {
        var d = r.sides[side] || {};
        // 先清零：避免上一个想定的字段值残留（不同想定平台类型不同）
        resetForceVals(side);
        ensureExtraEquipFields(side, d.equipment || {}, eqNames);
        setForceVals(side, 'equipment', d.equipment || {});
        setForceVals(side, 'air', d.air || {});
        setForceVals(side, 'modifiers', d.modifiers || {});
        setForceVals(side, 'ratio', { worn_ratio: 0, dug_ratio: 0 });
      });
      $('sf-forces-enable').checked = true;
      updateForcesSummary();
      $('sf-forces-summary').title = '已载入 ' + scen + ' 的默认编成，可逐项修改';
    });
  }
  // 想定可能含标准目录外的专属平台类型（如 wheel_assault），动态补录入框
  function ensureExtraEquipFields(side, equip, names) {
    var pane = $('sf-form').querySelector('.sf-side-pane[data-pane="' + side + '"]');
    if (!pane) return;
    var grid = pane.querySelector('.sf-force-grid');
    if (!grid) return;
    Object.keys(equip).forEach(function (k) {
      var exists = grid.querySelector('[data-group="forces"][data-cat="equipment"][data-key="' + k + '"]');
      if (exists) return;
      var label = document.createElement('label');
      label.title = k;
      var span = document.createElement('span');
      span.textContent = names[k] || k;
      label.appendChild(span);
      var input = document.createElement('input');
      input.type = 'number'; input.min = '0'; input.value = '0';
      input.setAttribute('data-group', 'forces');
      input.setAttribute('data-side', side);
      input.setAttribute('data-cat', 'equipment');
      input.setAttribute('data-key', k);
      label.appendChild(input);
      grid.appendChild(label);
    });
  }
  function setForceVals(side, cat, obj) {
    Object.keys(obj).forEach(function (k) {
      var el = $('sf-form').querySelector('[data-group="forces"][data-side="' + side +
               '"][data-cat="' + cat + '"][data-key="' + k + '"]');
      if (el) el.value = obj[k];
    });
  }
  function resetForceVals(side) {
    var sel = '[data-group="forces"]' + (side ? '[data-side="' + side + '"]' : '');
    Array.prototype.forEach.call($('sf-form').querySelectorAll(sel), function (el) {
      var cat = el.getAttribute('data-cat');
      el.value = (cat === 'modifiers') ? '1' : '0';
    });
  }
  function clearForces() {
    resetForceVals(null);
    $('sf-forces-enable').checked = false;
    updateForcesSummary();
  }
  function collectForces() {
    var enabled = $('sf-forces-enable') && $('sf-forces-enable').checked;
    updateForcesBadge(enabled);
    if (!enabled) return {};
    var out = { red: {}, blue: {} };
    ['red', 'blue'].forEach(function (side) {
      var node = { equipment: {}, air: {}, modifiers: {}, worn_ratio: 0, dug_ratio: 0 };
      Array.prototype.forEach.call($('sf-form').querySelectorAll(
        '[data-group="forces"][data-side="' + side + '"]'), function (el) {
        var cat = el.getAttribute('data-cat'), k = el.getAttribute('data-key');
        var v = Number(el.value) || 0;
        if (cat === 'equipment' || cat === 'air') {
          if (v > 0) node[cat][k] = v;
        } else if (cat === 'modifiers') {
          node.modifiers[k] = v;
        } else if (cat === 'ratio') {
          node[k] = v;
        }
      });
      out[side] = node;
    });
    return out;
  }
  function updateForcesBadge(enabled) {
    var el = $('sf-forces-badge');
    if (!el) return;
    el.textContent = enabled ? '已启用' : '未启用';
    el.className = 'sf-forces-badge' + (enabled ? ' on' : '');
  }
  function updateForcesSummary() {
    var el = $('sf-forces-summary');
    if (!el || !$('sf-forces-enable')) return;
    var totals = { red: 0, blue: 0 }, airs = { red: 0, blue: 0 };
    ['red', 'blue'].forEach(function (side) {
      Array.prototype.forEach.call($('sf-form').querySelectorAll(
        '[data-group="forces"][data-side="' + side + '"][data-cat="equipment"]'), function (i) {
        totals[side] += Number(i.value) || 0;
      });
      Array.prototype.forEach.call($('sf-form').querySelectorAll(
        '[data-group="forces"][data-side="' + side + '"][data-cat="air"]'), function (i) {
        airs[side] += Number(i.value) || 0;
      });
    });
    var on = $('sf-forces-enable').checked;
    updateForcesBadge(on);
    el.innerHTML = '<span class="sf-balance"><b style="color:#f87171">红 ' + totals.red + '</b> 台 / ' + airs.red +
                   ' 架　vs　<b style="color:#60a5fa">蓝 ' + totals.blue + '</b> 台 / ' + airs.blue + ' 架</span>' +
                   (on ? '' : '<span class="sf-balance-off">（沿用想定默认）</span>');
  }

  function collectMission() {
    var p = { equipment: [], ammo: [], fuel: [], support_vehicles: [], resources: [], daily_ops: {} };
    p.forces = collectForces();
    p.targets = {};
    p.cost_weights = {};
    var meta = {};
    Array.prototype.forEach.call($('sf-form').querySelectorAll('[data-group]'), function (el) {
      var g = el.getAttribute('data-group'), k = el.getAttribute('data-key'),
          f = el.getAttribute('data-field'), v = el.value;
      if (g === 'meta') { meta[f] = v; return; }
      if (g === 'targets') { p.targets[f] = v; return; }
      if (g === 'cost_weights') { p.cost_weights[f] = Number(v); return; }
      if (g === 'daily_ops') { p.daily_ops[k] = v; return; }
      if (['equipment', 'ammo', 'fuel', 'support_vehicles', 'resources'].indexOf(g) < 0) return;
      var arr = p[g], row = null;
      for (var i = 0; i < arr.length; i++) {
        if ((arr[i].key || arr[i]['class'] || arr[i].grade) === k) { row = arr[i]; break; }
      }
      if (!row) {
        // 后端契约：ammo 用 class、fuel 用 grade、其余用 key —— 必须一一对应
        row = (g === 'ammo') ? { 'class': k } : (g === 'fuel') ? { grade: k } : { key: k };
        if (g === 'resources') row.qty = {};
        arr.push(row);
      }
      if (g === 'resources') row.qty[f] = Number(v) || 0;
      else if (f === 'count') row.count = Number(v) || 0;
      else row[f] = Number(v) || 0;
    });
    p.task_name = meta.task_name; p.mission_type = meta.mission_type;
    p.base_scenario = meta.base_scenario;
    p.duration_ticks = Number(meta.duration_ticks) || 96;
    p.n_runs = Number(meta.n_runs) || 8;
    p.seed = Number(meta.seed) || 20261013;
    // 过滤全 0 行，避免无效录入
    ['equipment', 'ammo', 'fuel', 'support_vehicles', 'resources'].forEach(function (g) {
      p[g] = p[g].filter(function (r) {
        if (g === 'resources') return Object.keys(r.qty).some(function (k) { return r.qty[k] > 0; });
        if (g === 'ammo') return (r.basic_load || 0) > 0 || (r.reserve || 0) > 0;
        if (g === 'fuel') return (r.stock_l || 0) > 0 || (r.resupply_l || 0) > 0;
        return (r.count || 0) > 0;
      });
    });
    return p;
  }

  /* ---------------- 保存 / 推演 / 方案 ---------------- */
  function saveMission() {
    var p = collectMission();
    setNode('input', 'running');
    post('/api/support/mission', p).then(function (r) {
      if (r.error) { setNode('input', 'error'); alert('保存失败：' + r.error); return; }
      ST.missionId = r.mission_id;
      setNode('input', 'done'); setNode('validate', 'done');
      setNode('deduce', 'ready');
      $('sf-job-hint').textContent = '录入已保存 · ' + r.mission_id;
      if (r.warnings && r.warnings.length) $('sf-job-hint').title = r.warnings.join('\n');
    });
  }

  function loadMission() {
    api('/api/support/mission').then(function (r) {
      if (!r.mission) { alert('尚无录入记录'); return; }
      fillForm(r.mission);
      ST.missionId = r.mission.mission_id;
      setNode('input', 'done'); setNode('validate', 'done'); setNode('deduce', 'ready');
      $('sf-job-hint').textContent = '已读取 · ' + ST.missionId;
    });
  }
  function fillForm(m) {
    function setVal(g, k, f, v) {
      var el = $('sf-form').querySelector('[data-group="' + g + '"][data-key="' + k + '"][data-field="' + f + '"]');
      if (el) el.value = v;
    }
    setVal('meta', 'task', 'task_name', m.task_name);
    setVal('meta', 'task', 'mission_type', m.mission_type);
    setVal('meta', 'task', 'base_scenario', m.base_scenario);
    setVal('meta', 'task', 'duration_ticks', m.duration_ticks);
    setVal('meta', 'task', 'n_runs', m.n_runs);
    setVal('meta', 'task', 'seed', m.seed);
    ['readiness', 'success', 'repair_timeliness'].forEach(function (f) {
      setVal('targets', 'targets', f, String((m.targets || {})[f] || '').replace('%', ''));
    });
    // P2-5：成本权重回填
    Object.keys(m.cost_weights || {}).forEach(function (f) {
      setVal('cost_weights', 'cost', f, m.cost_weights[f]);
    });
    ['equipment', 'ammo', 'fuel', 'support_vehicles', 'resources'].forEach(function (g) {
      (m[g] || []).forEach(function (r) {
        var k = r.key || r['class'] || r.grade;
        Object.keys(r).forEach(function (f) {
          if (f === 'key' || f === 'class' || f === 'grade' || f === 'name' || f === 'qty') return;
          if (g === 'resources' && f === 'qty') {
            Object.keys(r.qty).forEach(function (e) { setVal(g, k, e, r.qty[e]); });
          } else setVal(g, k, f, r[f]);
        });
      });
    });
    Object.keys(m.daily_ops || {}).forEach(function (k) { setVal('daily_ops', k, 'value', m.daily_ops[k]); });
    // forces 回填：数据存在即自动勾选启用
    var f = m.forces || {};
    if (f.red || f.blue) {
      var eqNames = (FORCES_DEFAULT && FORCES_DEFAULT.equipment_names) || {};
      ['red', 'blue'].forEach(function (side) {
        var s = f[side] || {};
        ensureExtraEquipFields(side, s.equipment || {}, eqNames);
        setForceVals(side, 'equipment', s.equipment || {});
        setForceVals(side, 'air', s.air || {});
        setForceVals(side, 'modifiers', s.modifiers || {});
        setForceVals(side, 'ratio', { worn_ratio: s.worn_ratio || 0, dug_ratio: s.dug_ratio || 0 });
      });
      if ($('sf-forces-enable')) $('sf-forces-enable').checked = true;
    }
    updateForcesSummary();
  }

  function deduce() {
    if (!ST.missionId) {
      alert('请先保存任务与资源录入'); return;
    }
    var n = Number($('sf-nruns').value) || 8;
    var seedEl = $('sf-seed');
    var body = { mission_id: ST.missionId, n_runs: n };
    if (seedEl && seedEl.value !== '') body.base_seed = Number(seedEl.value);  // P1-6
    setNode('deduce', 'running'); setNode('loss', 'idle'); setNode('rates', 'idle');
    $('sf-job-hint').textContent = '正在启动推演…';
    $('sf-progress-bar').style.width = '4%';
    post('/api/support/deduce', body).then(function (r) {
      if (r.error) { setNode('deduce', 'error'); alert(r.error); return; }
      ST.jobId = r.job_id;
      $('sf-job-hint').textContent = '推演任务 ' + r.job_id + ' · ' + n + ' 次复制';
      if (ST.poll) clearInterval(ST.poll);
      ST.poll = setInterval(function () {
        api('/api/support/deduce?job_id=' + encodeURIComponent(ST.jobId)).then(function (s) {
          $('sf-progress-bar').style.width = Math.round((s.progress || 0) * 100) + '%';
          $('sf-job-hint').textContent = (s.stage || '运行中') + ' · ' +
            Math.round((s.progress || 0) * 100) + '%';
          if (s.status === 'done') {
            clearInterval(ST.poll); ST.poll = null;
            setNode('deduce', 'done'); setNode('loss', 'done');
            setNode('rates', s.checklist_rate ? 'done' : 'error');
            ST.lossId = s.loss_id;
            loadLoss(s.loss_id);
          } else if (s.status === 'error') {
            clearInterval(ST.poll); ST.poll = null;
            setNode('deduce', 'error');
            $('sf-job-hint').textContent = '失败：' + (s.error || '未知错误');
          }
        });
      }, 1200);
    });
  }

  function loadLoss(lid) {
    api('/api/support/loss' + (lid ? '?loss_id=' + encodeURIComponent(lid) : '')).then(function (r) {
      if (!r.loss) { $('sf-loss-table').innerHTML = '<div class="sf-empty">无战损清单</div>'; return; }
      ST.loss = r.loss; ST.lossPage = 0; ST.lossFilter = {};
      if (r.loss.base_seed != null && $('sf-seed')) $('sf-seed').value = r.loss.base_seed;
      renderMetrics();
      renderLossTab();
      setNode('plan', 'ready');
      $('sf-plan-hint').textContent = '清单已就绪 · ' + (r.loss.loss_id || '');
    });
  }

  /* ---- 指标卡 + 口径切换（P0-4） ---- */
  function renderMetrics() {
    var loss = ST.loss, rates = loss.rates || {}, s = loss.summary || {};
    var recon = rates.reconciliation || {};
    if (!ST.rateDoctrine) ST.rateDoctrine = (rates.rate_basis_default || 'A');
    var doctrine = ST.rateDoctrine;
    var dA = (rates.doctrines || []).filter(function (d) { return d.id === 'A'; })[0] || {};
    var dB = (rates.doctrines || []).filter(function (d) { return d.id === 'B'; })[0] || {};
    var shown = doctrine === 'B' ? dB : dA;
    function metric(v, k, cls, tip, glossKey) {
      var g = (window.DASH_GLOS || {})[glossKey || ''];
      var q = g ? '<button type="button" class="sf-gloss-q" data-gloss="' + esc(glossKey) +
                   '" title="查看指标口径">?</button>' : '';
      return '<div class="sf-metric ' + (cls || '') + '"' + (tip && !g ? ' title="' + esc(tip) + '"' : '') +
             '><div class="v">' + esc(v) + '</div><div class="k">' + esc(k) + q + '</div></div>';
    }
    var mh = '<div class="sf-doctrine"><span class="dl">战损率口径</span>' +
      ['A', 'B'].map(function (id) {
        var d = id === 'A' ? dA : dB;
        return '<button type="button" class="dbtn' + (doctrine === id ? ' on' : '') + '" data-doctrine="' + id +
          '" title="' + esc(d.desc || '') + '">' + id + '·' + esc((d.name || '').split('（')[0]) + '</button>';
      }).join('') + '</div>';
    mh += metric(((shown.value != null ? shown.value : rates.attrition) * 100 || 0).toFixed(1) + '%',
                 '战损率（口径' + doctrine + '）', 'bad',
                 (shown.desc || 'K 级战毁/参战数'), 'attrition');
    mh += metric((rates.damage_rate * 100 || 0).toFixed(1) + '%', '战斗损伤率(K+M+F)', 'warn',
                 '', 'damage_rate');
    mh += metric((rates.readiness * 100 || 0).toFixed(1) + '%', '完好率', 'good', '', 'readiness');
    mh += metric(((rates.repair_rate || 0) * 100).toFixed(1) + '%', '修复率', 'good', '', 'repair_rate');
    mh += metric(s.total_losses || 0, '清单条目数', '');
    mh += metric(loss.source === 'event' ? '逐件' : '按类别估算', '清单性质',
                 loss.source === 'event' ? 'good' : 'warn');
    mh += '<div class="sf-recon ' + (recon.passed ? 'ok' : 'no') + '">' +
          (recon.checked ? (recon.passed ? '✓ 与内核四率对账通过（差异 ≤ 容差）' :
            '✗ 与内核四率对账未通过：清单与报告数字不一致，请检查') :
            '未启用内核对账') +
          (recon.checked ? ' <button type="button" class="sf-recon-toggle" id="sf-recon-toggle">明细 ▾</button>' : '') +
          '</div>';
    $('sf-metrics').innerHTML = mh;
    Array.prototype.forEach.call($('sf-metrics').querySelectorAll('.dbtn'), function (b) {
      b.addEventListener('click', function () {
        ST.rateDoctrine = b.getAttribute('data-doctrine'); renderMetrics();
      });
    });
    // P1-7：指标口径 tooltip
    Array.prototype.forEach.call($('sf-metrics').querySelectorAll('[data-gloss]'), function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        var g = (window.DASH_GLOS || {})[b.getAttribute('data-gloss')] || {};
        var pop = $('sf-prov-pop');
        pop.innerHTML = '<div class="sf-gloss-title">' + esc(g.name || '指标') +
          (g.authoritative ? ' <span class="sf-tag K">权威可对账</span>' : '') + '</div>' +
          '<div class="sf-focus-row"><span>公式</span><code>' + esc(g.formula || '—') + '</code></div>' +
          '<div class="sf-focus-row"><span>分子</span><span>' + esc(g.numerator || '—') + '</span></div>' +
          '<div class="sf-focus-row"><span>分母</span><span>' + esc(g.denominator || '—') + '</span></div>' +
          '<div class="sf-focus-note">' + esc(g.note || '') + '</div>';
        var r = b.getBoundingClientRect(), host = $('sf-drawer').getBoundingClientRect();
        pop.style.left = Math.min(r.left - host.left, host.width - 340) + 'px';
        pop.style.top = (r.bottom - host.top + 6) + 'px';
        pop.hidden = false;
      });
    });
    // P1-5：对账差异明细展开
    var rt = $('sf-recon-toggle');
    if (rt) rt.addEventListener('click', function () {
      var box = $('sf-recon-detail');
      if (!box.hidden) { box.hidden = true; return; }
      var items = recon.items || [];
      var h = '<table class="sf-table sf-recon-table"><thead><tr><th>指标</th><th>清单派生</th><th>内核值</th>' +
              '<th>差异</th><th>容差</th><th>结论</th></tr></thead><tbody>';
      items.forEach(function (x) {
        var nm = (window.DASH_GLOS && DASH_GLOS[x.metric] && DASH_GLOS[x.metric].name) || x.metric;
        h += '<tr><td>' + esc(nm) + '</td><td>' + esc(x.from_list) + '</td><td>' +
             esc(Number(x.from_kernel).toFixed(6)) + '</td><td>' + esc(x.diff) + '</td><td>' +
             esc(x.tol) + '</td><td>' + (x.passed ? '<span class="sf-tag K">通过</span>' :
             '<span class="sf-tag F">未通过</span>') + '</td></tr>';
      });
      h += '</tbody></table>';
      box.innerHTML = h || '<div class="sf-empty">无对账项</div>';
      box.hidden = false;
    });
  }

  /* ---- 子 tab 切换 ---- */
  function renderLossTab() {
    Array.prototype.forEach.call($('sf-loss-tabs').querySelectorAll('.sftab'), function (t) {
      t.classList.toggle('on', t.getAttribute('data-tab') === ST.lossTab);
    });
    ['items', 'summary', 'ci', 'timeline'].forEach(function (name) {
      var p = $('sf-panel-' + name); if (p) p.hidden = (name !== ST.lossTab);
    });
    if (ST.lossTab === 'items') renderItems();
    else if (ST.lossTab === 'summary') renderSummary();
    else if (ST.lossTab === 'ci') renderCI();
    else if (ST.lossTab === 'timeline') renderTimeline();
  }

  /* ---- P0-1：清单筛选 + 排序 + 分页 ---- */
  function renderItems() {
    var loss = ST.loss, items = (loss.items || []).slice();
    var f = ST.lossFilter;
    var filtered = items.filter(function (it) {
      if (f.grade && it.grade !== f.grade) return false;
      if (f.side && it.side !== f.side) return false;
      if (f.category && (it.category || it.platform) !== f.category) return false;
      if (f.disposition && it.disposition !== f.disposition) return false;
      if (f.q) {
        var q = f.q.toLowerCase();
        if (String(it.unit_id).toLowerCase().indexOf(q) < 0 &&
            String(it.platform).toLowerCase().indexOf(q) < 0 &&
            String(it.loss_id).toLowerCase().indexOf(q) < 0) return false;
      }
      return true;
    });
    if (ST.lossSort) {
      var key = ST.lossSort.key, dir = ST.lossSort.dir === 'desc' ? -1 : 1;
      filtered.sort(function (a, b) {
        var va = a[key], vb = b[key];
        if (va == null) va = ''; if (vb == null) vb = '';
        if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
        return String(va).localeCompare(String(vb), 'zh') * dir;
      });
    }
    // 筛选条
    function uniqVals(field) {
      var out = {}; items.forEach(function (it) { var v = it[field]; if (v != null && v !== '') out[v] = 1; });
      return Object.keys(out).sort();
    }
    function sel(label, field, options) {
      return '<label>' + label + '<select data-filter="' + field + '"><option value="">全部</option>' +
        options.map(function (o) {
          return '<option value="' + esc(o) + '"' + (f[field] === o ? ' selected' : '') + '>' + esc(o) + '</option>';
        }).join('') + '</select></label>';
    }
    var fb = '<div class="sf-filter-row">' +
      sel('等级', 'grade', ['K', 'F', 'M', 'N']) +
      sel('阵营', 'side', uniqVals('side')) +
      sel('类别', 'category', uniqVals('category')) +
      sel('处置', 'disposition', uniqVals('disposition')) +
      '<label>搜索<input type="text" data-filter="q" placeholder="编号/单位/类型" value="' + esc(f.q || '') + '"></label>' +
      '<button type="button" class="sf-btn sf-filter-clear">清除</button></div>' +
      '<div class="sf-filter-count">匹配 ' + filtered.length + ' / ' + items.length + ' 条</div>';
    $('sf-filter-bar').innerHTML = fb;
    // 表格
    var cols = [['loss_id', '编号'], ['side', '阵营'], ['unit_id', '单位'],
                ['category', '装备/类型'], ['grade', '等级'], ['damage_part', '损伤部位'],
                ['disposition', '处置'], ['repair_status', '状态'], ['location', '位置'], ['', '3D']];
    var totalPages = Math.max(1, Math.ceil(filtered.length / ST.lossPageSize));
    if (ST.lossPage >= totalPages) ST.lossPage = totalPages - 1;
    var pageRows = filtered.slice(ST.lossPage * ST.lossPageSize, (ST.lossPage + 1) * ST.lossPageSize);
    var t = '<table class="sf-table"><thead><tr>' + cols.map(function (c) {
      var mark = ST.lossSort && ST.lossSort.key === c[0] ? (ST.lossSort.dir === 'desc' ? ' ▾' : ' ▴') : '';
      return '<th class="sf-sortable" data-sort="' + c[0] + '">' + c[1] + mark + '</th>';
    }).join('') + '</tr></thead><tbody>';
    pageRows.forEach(function (it) {
      var canLocate = !!it.unit_id;
      t += '<tr><td>' + esc(it.loss_id) + '</td><td>' + esc(it.side) + '</td><td>' + esc(it.unit_id) + '</td>' +
           '<td title="' + esc(it.platform) + '">' + esc(it.category || it.platform) + '</td>' +
           '<td><span class="sf-tag ' + esc(it.grade) + '">' + esc(it.grade) + '</span>' +
           (it.source === 'estimate' ? ' <span class="sf-tag est">估</span>' : '') + '</td>' +
           '<td>' + esc(it.damage_part || '—') + '</td><td>' + esc(it.disposition) + '</td>' +
           '<td>' + esc(it.repair_status) + '</td><td>' + esc(it.location) + '</td>' +
           '<td>' + (canLocate
             ? '<button type="button" class="sf-locate" data-unit="' + esc(it.unit_id) +
               '" title="P1-4：在 3D 态势中定位该单位">◎</button>' : '—') + '</td></tr>';
    });
    if (!pageRows.length) t += '<tr><td colspan="10" class="sf-empty-cell">无匹配记录</td></tr>';
    t += '</tbody></table>';
    $('sf-loss-table').innerHTML = t;
    $('sf-pager').innerHTML = '<button type="button" class="sf-btn" data-page="prev"' + (ST.lossPage <= 0 ? ' disabled' : '') + '>‹ 上页</button>' +
      '<span>第 ' + (ST.lossPage + 1) + ' / ' + totalPages + ' 页</span>' +
      '<button type="button" class="sf-btn" data-page="next"' + (ST.lossPage >= totalPages - 1 ? ' disabled' : '') + '>下页 ›</button>';
    // 绑定
    Array.prototype.forEach.call($('sf-filter-bar').querySelectorAll('[data-filter]'), function (el) {
      var ev = el.tagName === 'SELECT' ? 'change' : 'input';
      el.addEventListener(ev, function () {
        ST.lossFilter[el.getAttribute('data-filter')] = el.value; ST.lossPage = 0; renderItems();
      });
    });
    var clr = $('sf-filter-bar').querySelector('.sf-filter-clear');
    if (clr) clr.addEventListener('click', function () { ST.lossFilter = {}; ST.lossPage = 0; renderItems(); });
    Array.prototype.forEach.call($('sf-loss-table').querySelectorAll('[data-sort]'), function (th) {
      th.addEventListener('click', function () {
        var k = th.getAttribute('data-sort');
        if (ST.lossSort && ST.lossSort.key === k) ST.lossSort = { key: k, dir: ST.lossSort.dir === 'asc' ? 'desc' : 'asc' };
        else ST.lossSort = { key: k, dir: 'asc' };
        renderItems();
      });
    });
    Array.prototype.forEach.call($('sf-pager').querySelectorAll('[data-page]'), function (b) {
      b.addEventListener('click', function () {
        if (b.getAttribute('data-page') === 'prev') ST.lossPage--; else ST.lossPage++;
        renderItems();
      });
    });
    Array.prototype.forEach.call($('sf-loss-table').querySelectorAll('[data-unit]'), function (b) {
      b.addEventListener('click', function () { focusUnit(b.getAttribute('data-unit')); });
    });
  }

  /* ---- P1-4：清单 → 3D 定位（广播事件 + 定位卡，低耦合不侵入 3D 视图） ---- */
  function focusUnit(uid) {
    var it = ((ST.loss && ST.loss.items) || []).filter(function (x) { return String(x.unit_id) === String(uid); })[0] || {};
    var ent = 'world/units/' + uid;
    // 广播给可选监听者（3D 视图可订阅 brigsim:focus-unit 做镜头联动）
    try {
      window.dispatchEvent(new CustomEvent('brigsim:focus-unit', {
        detail: { unit_id: uid, entity_path: ent, grade: it.grade || '',
                  location: it.location || '', platform: it.platform || '', tick: it.tick || null }
      }));
    } catch (e) {}
    var card = $('sf-focus-card');
    card.innerHTML = '<div class="sf-focus-title">3D 定位 · ' + esc(it.unit_id || uid) + '</div>' +
      '<div class="sf-focus-row"><span>实体路径</span><code>' + esc(ent) + '</code></div>' +
      '<div class="sf-focus-row"><span>装备</span><span>' + esc(it.platform || it.category || '—') + '</span></div>' +
      '<div class="sf-focus-row"><span>等级/处置</span><span>' + esc(it.grade || '—') + ' / ' + esc(it.disposition || '—') + '</span></div>' +
      '<div class="sf-focus-row"><span>位置/tick</span><span>' + esc(it.location || '—') + ' / ' + esc(it.tick == null ? '—' : it.tick) + '</span></div>' +
      '<div class="sf-focus-note">已广播定位事件；3D 回放中对应实体为 <code>' + esc(ent) + '</code>（回放按 tick 逐帧推演）。</div>' +
      '<button type="button" class="sf-btn mini sf-focus-close">关闭</button>';
    card.hidden = false;
    card.querySelector('.sf-focus-close').addEventListener('click', function () { card.hidden = true; });
  }

  /* ---- P1-3：LLM 生成参数面板 ---- */
  function renderLlmParams() {
    var box = $('sf-adv-body');
    if (!box || !CAT || !CAT.llm_params) return;
    var h = '<div class="sf-adv-grid">';
    CAT.llm_params.forEach(function (p) {
      h += '<label title="' + esc(p.hint) + '">' + esc(p.name) +
           '<input type="number" id="sf-llmp-' + esc(p.key) + '" value="' + esc(p.default) +
           '" min="' + p.min + '" max="' + p.max + '" step="' + p.step + '"></label>';
    });
    h += '</div><div class="sf-adv-hint">27B CPU 部署：段数越多单次内存压力越小；温度低更稳定。</div>';
    box.innerHTML = h;
  }
  function collectLlmParams() {
    var out = {};
    (CAT && CAT.llm_params || []).forEach(function (p) {
      var el = $('sf-llmp-' + p.key);
      if (el) out[p.key] = Number(el.value);
    });
    return out;
  }

  /* ---- P1-2：叙述人工覆写 + 锁定 ---- */
  function openNarrEditor() {
    if (!ST.planId) { alert('请先生成保障方案'); return; }
    var box = $('sf-narr-editor');
    if (!box.hidden) { box.hidden = true; return; }
    var plan = ST.plan || {};
    var slots = plan.slots || {};
    var sd = (CAT && CAT.slot_dict) || {};
    var keys = Object.keys(sd).filter(function (k) { return sd[k].source === 'llm'; });
    var h = '<div class="sf-narr-head"><b>覆写 LLM 叙述槽位</b><span>仅文字可改；数字由清单派生，不可编辑</span></div>';
    keys.forEach(function (k) {
      h += '<label><span>' + esc(sd[k].label || k) + '</span><textarea rows="1" data-narr="' + esc(k) + '">' +
           esc(slots[k] || '') + '</textarea></label>';
    });
    h += '<div class="sf-narr-foot"><button type="button" class="sf-btn primary" id="sf-narr-save">保存覆写并锁定</button>' +
         '<button type="button" class="sf-btn" id="sf-narr-cancel">取消</button></div>';
    box.innerHTML = h; box.hidden = false;
    $('sf-narr-save').addEventListener('click', function () {
      var ov = {};
      Array.prototype.forEach.call(box.querySelectorAll('[data-narr]'), function (t) {
        ov[t.getAttribute('data-narr')] = t.value.trim();
      });
      $('sf-plan-hint').textContent = '正在覆写叙述…';
      post('/api/support/plan/override', { plan_id: ST.planId, overrides: ov, lock: true })
        .then(function (r) {
          if (r.error) { alert(r.error); return; }
          applyPlan(r.plan); box.hidden = true;
        });
    });
    $('sf-narr-cancel').addEventListener('click', function () { box.hidden = true; });
  }
  function applyPlan(p) {
    ST.planId = p.plan_id; ST.plan = p; ST.provision = p.provenance || null;
    var v = p.validation || {};
    setNode('plan', 'done'); setNode('review', v.passed ? 'done' : 'error'); setNode('export', 'done');
    $('sf-check').className = 'sf-check ' + (v.passed ? 'ok' : 'no');
    $('sf-check').innerHTML = (v.passed
      ? '✓ 数字一致性复核通过：' + v.checked_numeric_slots + ' 个数值槽位来自清单/录入' +
        (p.narrative_locked ? ' · 叙述已人工覆写锁定' : '')
      : '✗ 复核未通过');
    $('sf-plan-doc').innerHTML = md(p.text || '') + annotateProv(p.text || '', ST.provision) +
      '<div class="sf-llm-note">叙述来源：' + esc((p.llm && p.llm.source) || 'fallback') +
      ' · 成功段 ' + ((p.llm && p.llm.groups_ok) || 0) +
      ' · 降级段 ' + ((p.llm && p.llm.groups_failed) || 0) +
      (p.narrative_locked ? ' · 人工覆写锁定' : '') + '</div>';
    annotateNumbersIn($('sf-plan-doc'));
    $('sf-plan-hint').textContent = '方案 ' + p.plan_id;
    if (typeof renderReview === 'function') renderReview(p);  // P2-3 签批状态条
  }

  /* ---- P1-1：模板槽位映射可视化 ---- */
  function openTemplateMap() {
    var box = $('sf-modal-template');
    box.hidden = false;
    $('sf-modal-template-body').innerHTML = '<div class="sf-empty">加载模板中…</div>';
    var tpl = $('sf-template').value;
    api('/api/support/template-detail?name=' + encodeURIComponent(tpl)).then(function (r) {
      if (r.error) { $('sf-modal-template-body').innerHTML = '<div class="sf-empty">' + esc(r.error) + '</div>'; return; }
      var h = '<div class="sf-tpl-stat">模板 <b>' + esc(r.name) + '</b> · 已用槽位 <b>' + r.count_used + '</b> / ' +
              r.count_total + (r.unknown_slots.length ? ' · <span class="bad">未知槽位 ' + r.unknown_slots.join(',') + '</span>' : '') + '</div>';
      h += '<table class="sf-table sf-map-table"><thead><tr><th>槽位 ID</th><th>名称</th><th>来源</th><th>模板中使用</th></tr></thead><tbody>';
      r.slots.forEach(function (s) {
        h += '<tr><td><code>' + esc(s.key) + '</code></td><td>' + esc(s.label) + '</td><td>' + esc(s.source) +
             '</td><td>' + (s.used ? '<span class="sf-tag K">✓</span>' : '<span class="sf-mute">—</span>') + '</td></tr>';
      });
      h += '</tbody></table>';
      h += '<div class="sf-tpl-upload"><b>替换正式模板</b><div class="sf-note">粘贴制式模板正文，用 <code>{{槽位ID}}</code> 标记；' +
           '槽位 ID 需在上表字典内。保存后选择该模板重新生成方案。</div>' +
           '<input type="text" id="sf-tpl-name" placeholder="模板文件名，如 brigade_official.md" value="custom_support_plan.md">' +
           '<textarea id="sf-tpl-text" rows="6" placeholder="在此粘贴模板 Markdown 正文…"></textarea>' +
           '<button type="button" class="sf-btn primary" id="sf-tpl-save">保存模板</button></div>';
      $('sf-modal-template-body').innerHTML = h;
      $('sf-tpl-save').addEventListener('click', function () {
        var name = $('sf-tpl-name').value.trim(), text = $('sf-tpl-text').value;
        if (!text) { alert('模板正文不能为空'); return; }
        post('/api/support/template', { name: name, text: text }).then(function (rr) {
          if (rr.error) { alert(rr.error); return; }
          refreshTemplates(rr.name);
          openTemplateMap();
        });
      });
    });
  }

  /* ---- P1-8：作业档案 ---- */
  /* ================= P2 进阶能力 ================= */

  /* ---- P2-1：批量工况对比（档案聚合，不重跑推演）---- */
  function openCompare() {
    var body = $('sf-modal-compare-body'), arch = $('sf-modal-archive-body');
    if (!body) return;
    arch.hidden = true; body.hidden = false;
    body.innerHTML = '<div class="sf-empty">加载工况对比数据…</div>';
    api('/api/support/compare').then(function (r) {
      if (r.error) { body.innerHTML = '<div class="sf-empty">' + esc(r.error) + '</div>'; return; }
      var rows = r.scenarios || [];
      var h = '<div class="sf-note">' + esc(r.note) + '</div>';
      if (!rows.length) {
        h += '<div class="sf-empty">尚无推演档案，请先执行多次推演。</div>';
      } else {
        h += '<div class="sf-p2-sub">跨工况战损率 / 完好率（来自归档）</div>';
        h += '<table class="sf-table sf-compare-table"><thead><tr><th>工况</th><th>地形</th><th>天候</th>' +
             '<th>战损率</th><th>完好率</th><th>条目</th><th>次数</th><th>种子</th></tr></thead><tbody>';
        rows.forEach(function (x) {
          function pct(v) { return v == null ? '—' : (Number(v) * 100).toFixed(1) + '%'; }
          h += '<tr><td title="' + esc(x.scenario) + '">' + esc(x.scenario) + '</td>' +
               '<td>' + esc(x.terrain || '—') + '</td><td>' + esc(x.weather || '—') + '</td>' +
               '<td class="p2-neg">' + pct(x.attrition) + '</td>' +
               '<td class="p2-pos">' + pct(x.readiness) + '</td>' +
               '<td>' + esc(x.n_items) + '</td><td>' + esc(x.n_runs == null ? '—' : x.n_runs) + '</td>' +
               '<td>' + esc(x.base_seed == null ? '—' : x.base_seed) + '</td></tr>';
        });
        h += '</tbody></table>';
        // 战损率横向条形（CSS，红=战损）
        var max = Math.max.apply(null, rows.map(function (x) { return Number(x.attrition) || 0; }).concat([0.0001]));
        h += '<div class="sf-p2-sub">战损率横向对比</div>';
        rows.forEach(function (x) {
          var w = Math.round((Number(x.attrition) || 0) / max * 100);
          h += '<div class="sf-bar-row"><span class="sf-bar-lb">' + esc(x.scenario) + '</span>' +
               '<span class="sf-bar-track"><i class="sf-bar-fill neg" style="width:' + w + '%"></i></span>' +
               '<span class="sf-bar-val">' + ((Number(x.attrition) || 0) * 100).toFixed(1) + '%</span></div>';
        });
      }
      if ((r.not_deduced || []).length) {
        h += '<div class="sf-note">尚未推演的工况（' + r.not_deduced.length + '）：' +
             esc(r.not_deduced.join('、')) + '。这些工况没有档案，不代表零战损。</div>';
      }
      body.innerHTML = h;
    });
  }
  function backToArchive() {
    var body = $('sf-modal-compare-body'), arch = $('sf-modal-archive-body');
    if (body) body.hidden = true;
    if (arch) arch.hidden = false;
  }

  /* ---- P2-2：方案版本对比 ---- */
  function openPlanCompare() {
    var box = $('sf-modal-plancompare');
    box.hidden = false;
    var body = $('sf-modal-plancompare-body');
    body.innerHTML = '<div class="sf-empty">加载方案列表…</div><div id="sf-pc-result"></div>';
    api('/api/support/archive').then(function (r) {
      var plans = r.plans || [];
      if (plans.length < 2) {
        body.innerHTML = '<div class="sf-empty">至少需要 2 份已生成方案才能对比（当前 ' +
                         plans.length + ' 份）。请先多次生成方案。</div>';
        return;
      }
      var selector = '<div class="sf-pc-select"><div class="sf-p2-sub">选择 2~4 份方案（勾选后点“开始对比”）</div>';
      plans.forEach(function (p) {
        selector += '<label class="sf-pc-item"><input type="checkbox" class="sf-pc-cb" value="' +
                    esc(p.id) + '"> <span>' + esc(p.saved_at || '') + ' · ' + esc(p.task_name || p.id) +
                    ' · 战损率 ' + (p.attrition == null ? '—' : (Number(p.attrition) * 100).toFixed(1) + '%') +
                    '</span></label>';
      });
      selector += '<button class="sf-btn primary" id="sf-pc-run">开始对比</button></div>';
      body.innerHTML = selector + '<div id="sf-pc-result"></div>';
      var run = $('sf-pc-run');
      if (run) run.addEventListener('click', function () {
        var ids = Array.prototype.map.call(body.querySelectorAll('.sf-pc-cb:checked'), function (c) { return c.value; });
        if (ids.length < 2) { alert('请至少勾选 2 份方案'); return; }
        var out = $('sf-pc-result');
        out.innerHTML = '<div class="sf-empty">对比中…</div>';
        post('/api/support/plan/compare', { plan_ids: ids }).then(function (d) {
          if (d.error) { out.innerHTML = '<div class="sf-empty">' + esc(d.error) + '</div>'; return; }
          var h = '<div class="sf-p2-sub">方案概览</div><table class="sf-table"><thead><tr><th>方案</th>' +
                  '<th>生成时间</th><th>复核</th><th>签批状态</th><th>叙述锁定</th></tr></thead><tbody>';
          d.plans.forEach(function (p) {
            h += '<tr><td>' + esc(p.plan_id) + '</td><td>' + esc(p.generated_at) + '</td><td>' +
                 (p.validation_passed ? '通过' : '未通过') + '</td><td>' + esc(p.review_status) +
                 '</td><td>' + (p.narrative_locked ? '已锁定' : '否') + '</td></tr>';
          });
          h += '</tbody></table>';
          h += '<div class="sf-p2-sub">数值槽位差异（' + d.slot_diffs.length + ' 项）</div>';
          if (!d.slot_diffs.length) h += '<div class="sf-empty">所选方案数值槽位完全一致</div>';
          else {
            h += '<table class="sf-table"><thead><tr><th>槽位</th>' +
                 d.plans.map(function (p) { return '<th>' + esc(p.plan_id) + '</th>'; }).join('') + '</tr></thead><tbody>';
            d.slot_diffs.forEach(function (s) {
              h += '<tr><td title="' + esc(s.key) + '">' + esc(s.label || s.key) + '</td>' +
                   s.values.map(function (v) { return '<td>' + esc(v) + '</td>'; }).join('') + '</tr>';
            });
            h += '</tbody></table>';
          }
          (d.text_diffs || []).forEach(function (td, i) {
            h += '<div class="sf-p2-sub">正文差异：基准 ↔ ' + esc(td.against) + '</div><pre class="sf-pc-diff">' +
                 esc(td.lines.join('\n') || '（无差异）') + '</pre>';
          });
          out.innerHTML = h;
        });
      });
    });
  }

  /* ---- P2-3：方案签批工作流 ---- */
  function renderReview(plan) {
    var bar = $('sf-review-bar');
    if (!bar) return;
    var st = plan.review_status || 'draft';
    var names = { draft: '草案', reviewing: '复核中', approved: '已定稿' };
    var acts = (CAT && CAT.review_actions) || {};
    var allowed = Object.keys(acts).filter(function (a) {
      return (acts[a].from || []).indexOf(st) >= 0;
    });
    bar.hidden = false;
    var h = '<span class="sf-rv-label">签批状态</span><span class="sf-rv-state st-' + esc(st) + '">' +
            esc(names[st] || st) + '</span>';
    allowed.forEach(function (a) {
      h += '<button class="sf-btn mini sf-rv-btn" data-act="' + esc(a) + '">' + esc(acts[a].name) + '</button>';
    });
    var log = plan.review_log || [];
    if (log.length) {
      h += '<span class="sf-rv-log" title="' + esc(log.map(function (x) {
        return x.at + ' ' + x.name + '：' + x.from + '→' + x.to + (x.note ? '（' + x.note + '）' : '');
      }).join('\n')) + '">流转 ' + log.length + ' 次</span>';
    }
    bar.innerHTML = h;
    Array.prototype.forEach.call(bar.querySelectorAll('.sf-rv-btn'), function (b) {
      b.addEventListener('click', function () {
        var act = b.getAttribute('data-act');
        var note = act === 'reject' ? (prompt('退回原因：') || '') : '';
        post('/api/support/plan/review', { plan_id: ST.planId, action: act, note: note }).then(function (r) {
          if (r.error) { alert('流转失败：' + r.error); return; }
          ST.plan = r.plan;
          renderReview(r.plan);
        });
      });
    });
  }

  /* ---- P2-4：数据字典 ---- */
  function openDataDict() {
    open();  // 模态在抽屉内，先确保抽屉可见（顶栏入口未开抽屉时也能用）
    var box = $('sf-modal-dict');
    box.hidden = false;
    var body = $('sf-modal-dict-body');
    body.innerHTML = '<div class="sf-empty">加载数据字典…</div>';
    function renderDict(cat) {
    function table(cols, rows) {
      var h = '<table class="sf-table"><thead><tr>' +
              cols.map(function (c) { return '<th>' + esc(c[1]) + '</th>'; }).join('') + '</tr></thead><tbody>';
      rows.forEach(function (r) {
        h += '<tr>' + cols.map(function (c) { return '<td>' + esc(r[c[0]] == null ? '' : r[c[0]]) + '</td>'; }).join('') + '</tr>';
      });
      return h + '</tbody></table>';
    }
    var h = '';
    h += '<div class="sf-p2-sub">① 指标口径（权威数字定义，P1-7）</div>';
    var gloss = cat.metric_glossary || {};
    h += table([['name', '指标'], ['formula', '公式'], ['numerator', '分子'], ['denominator', '分母'], ['note', '说明']],
               Object.keys(gloss).map(function (k) { return gloss[k]; }));
    h += '<div class="sf-p2-sub">② 战损清单字段（P2-4）</div>';
    h += table([['key', '字段'], ['name', '名称'], ['type', '类型'], ['source', '来源'], ['note', '说明']],
               cat.data_dictionary || []);
    h += '<div class="sf-p2-sub">③ 方案槽位字典（77 槽位，数字由清单派生 / 叙述由 LLM 生成）</div>';
    var slots = cat.slot_dict || {};
    h += table([['key', '槽位 ID'], ['label', '含义'], ['source', '来源'], ['section', '章节']],
               Object.keys(slots).map(function (k) {
                 var v = slots[k]; return { key: k, label: v.label, source: v.source, section: v.section || '' };
               }));
    body.innerHTML = h;
    }
    if (CAT) renderDict(CAT);
    else api('/api/support/catalog').then(function (r) {
      if (!r.error) { CAT = r; window.DASH_GLOS = r.metric_glossary || {}; }
      renderDict(r.error ? {} : r);
    });
  }

  /* ---- P2-6：保障力量编组部署示意（纯 SVG 展示，不改 3D）---- */
  function openDeployView() {
    var box = $('sf-modal-deploy');
    box.hidden = false;
    var body = $('sf-modal-deploy-body');
    var plan = ST.plan || {};
    var slots = plan.slots || {};
    var svg = '<svg viewBox="0 0 760 420" class="sf-deploy-svg" xmlns="http://www.w3.org/2000/svg">';
    svg += '<rect x="0" y="0" width="760" height="420" fill="var(--sf-bg2,#0f1622)"/>';
    // 标题与说明
    svg += '<text x="24" y="30" fill="#e6edf6" font-size="15" font-weight="bold">保障群编组与部署示意（依据当前方案数字槽位）</text>';
    // 前线（红方）
    svg += '<rect x="40" y="70" width="680" height="40" rx="6" fill="#3a1f24" stroke="#f87171"/>';
    svg += '<text x="380" y="95" fill="#f87171" font-size="13" text-anchor="middle">前方作战部队（红方）· 战损装备产生地</text>';
    // 中间保障群节点
    var nodes = [
      { x: 60, y: 160, w: 190, t: '保障指挥所', d: (slots.cp_composition || '按方案编成'), c: '#7dd3fc' },
      { x: 285, y: 160, w: 190, t: '抢修组（' + (slots.repair_teams || '—') + '）', d: 'M 级现地抢修', c: '#86efac' },
      { x: 510, y: 160, w: 190, t: '抢救组（' + (slots.arv_count || '—') + ' 台）', d: 'F 级拖救后送', c: '#fca5a5' },
      { x: 60, y: 275, w: 190, t: '器材补给组（' + (slots.supply_veh || '—') + ' 台）', d: '三级器材前送', c: '#fcd34d' },
      { x: 285, y: 275, w: 190, t: '弹药/油料保障组', d: '油罐车 ' + (slots.tanker_count || '—') + ' 台', c: '#c4b5fd' },
      { x: 510, y: 275, w: 190, t: '后方修理 / 后送', d: (slots.evac_assets || '按方案后送'), c: '#fdba74' },
    ];
    nodes.forEach(function (n) {
      svg += '<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="78" rx="8" fill="#1b2536" stroke="' + n.c + '"/>';
      svg += '<text x="' + (n.x + n.w / 2) + '" y="' + (n.y + 30) + '" fill="' + n.c +
             '" font-size="13" font-weight="bold" text-anchor="middle">' + esc(n.t) + '</text>';
      svg += '<text x="' + (n.x + n.w / 2) + '" y="' + (n.y + 52) + '" fill="#93a4bc" font-size="10" text-anchor="middle">' + esc(String(n.d).slice(0, 18)) + '</text>';
    });
    // 连线：前线 → 抢修/抢救；指挥所 → 各保障组
    function line(x1, y1, x2, y2, c) {
      svg += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
             '" stroke="' + c + '" stroke-width="1.5" stroke-dasharray="4 3"/>';
    }
    line(380, 110, 380, 160, '#f87171');
    line(155, 238, 380, 275, '#7dd3fc');
    line(380, 238, 380, 275, '#7dd3fc');
    line(605, 238, 605, 275, '#7dd3fc');
    line(380, 110, 605, 160, '#f87171');
    svg += '<text x="380" y="400" fill="#64748b" font-size="10" text-anchor="middle">' +
           '示意图依据方案数字槽位自动绘制；真实地形与单位位置见 3D 态势回放（P1-4 清单定位）</text>';
    svg += '</svg>';
    if (!plan.plan_id) {
      body.innerHTML = '<div class="sf-empty">尚未生成方案，示意图将显示空槽位。</div>' + svg;
    } else {
      body.innerHTML = svg;
    }
  }

  /* ---- P2-7：环节级回退重算 ---- */
  function recomputeStage(stage) {
    var body = (stage === 'rates') ? { stage: 'rates', loss_id: ST.lossId }
             : stage === 'plan' ? { stage: 'plan', mission_id: ST.missionId, loss_id: ST.lossId,
                                    use_llm: !!($('sf-use-llm') && $('sf-use-llm').checked) }
             : { stage: 'review', plan_id: ST.planId };
    post('/api/support/recompute', body).then(function (r) {
      if (r.error) { alert('回退重算失败：' + r.error); return; }
      if (stage === 'rates' && r.rates) {
        ST.loss.rates = r.rates;
        renderMetrics();
        setNode('rates', 'done');
      } else if (stage === 'plan' && r.plan_id) {
        ST.planId = r.plan_id;
        setNode('plan', 'done');
        api('/api/support/plan?plan_id=' + encodeURIComponent(r.plan_id)).then(function (d) {
          if (d.plan) { applyPlan(d.plan); renderReview(d.plan); }
        });
      } else if (stage === 'review') {
        setNode('review', 'done');
        alert('数字一致性复核已重跑：' + (r.validation && r.validation.passed ? '通过' : '未通过'));
      }
    });
  }

  function openArchive() {
    var box = $('sf-modal-archive');
    box.hidden = false;
    $('sf-modal-archive-body').innerHTML = '<div class="sf-empty">加载档案中…</div>';
    api('/api/support/archive').then(function (r) {
      function section(title, rows, cols) {
        var h = '<div class="sf-arch-sec"><b>' + title + '（' + rows.length + '）</b>';
        if (!rows.length) return h + '<div class="sf-empty">无记录</div></div>';
        h += '<table class="sf-table"><thead><tr>' + cols.map(function (c) { return '<th>' + c[1] + '</th>'; }).join('') + '</tr></thead><tbody>';
        rows.forEach(function (row) {
          h += '<tr>' + cols.map(function (c) {
            var v = row[c[0]];
            if (c[0] === 'attrition') v = (v == null ? '—' : (Number(v) * 100).toFixed(1) + '%');
            if (c[0] === 'validation_passed') v = (v == null ? '—' : (v ? '通过' : '未通过'));
            return '<td title="' + esc(row.id) + '">' + esc(v == null ? '' : v) + '</td>';
          }).join('') + '</tr>';
        });
        return h + '</tbody></table></div>';
      }
      $('sf-modal-archive-body').innerHTML =
        section('任务录入', r.missions || [], [['saved_at', '时间'], ['task_name', '任务'], ['scenario', '想定'], ['id', 'ID']]) +
        section('战损清单', r.losses || [], [['saved_at', '时间'], ['task_name', '任务'], ['n_items', '条目'], ['attrition', '战损率'], ['base_seed', '种子']]) +
        section('保障方案', r.plans || [], [['saved_at', '时间'], ['task_name', '任务'], ['attrition', '战损率'], ['validation_passed', '复核']]);
    });
  }

  function refreshTemplates(selected) {
    api('/api/support/templates').then(function (r) {
      var sel = $('sf-template');
      if (!sel || !r.templates) return;
      sel.innerHTML = r.templates.map(function (t) {
        return '<option value="' + esc(t.id) + '"' + (t.id === selected ? ' selected' : '') + '>' + esc(t.name) + '</option>';
      }).join('');
    });
  }

  /* ---- P0-3：多维汇总图（纯 CSS 横向条形） ---- */
  function renderSummary() {
    var s = ST.loss.summary || {};
    function block(title, obj) {
      var keys = Object.keys(obj || {});
      if (!keys.length) return '<div class="sf-empty">无数据</div>';
      var max = Math.max.apply(null, keys.map(function (k) { return obj[k]; })) || 1;
      return '<div class="sf-chart-block"><div class="sf-chart-title">' + esc(title) + '</div>' +
        keys.map(function (k) {
          var w = Math.max(2, Math.round(obj[k] / max * 100));
          return '<div class="sf-bar-row"><span class="bl">' + esc(k) + '</span>' +
                 '<span class="bt"><i style="width:' + w + '%"></i></span><span class="bv">' + esc(obj[k]) + '</span></div>';
        }).join('') + '</div>';
    }
    $('sf-panel-summary').innerHTML =
      block('按战损等级', s.by_grade) + block('按处置方式', s.by_disposition) +
      block('按装备类别', s.by_category) + block('按阵营', s.by_side) +
      '<div class="sf-note">汇总来自后端 aggregate()；口径 A 战损率只认 K，战斗损伤率含 M/F。</div>';
  }

  /* ---- P0-2：逐次复制明细 + 95%CI ---- */
  function renderCI() {
    var rep = ST.loss.replication || {};
    var rows = rep.rows || [], sum = rep.summary || {};
    var keys = ['attrition_red', 'readiness_red', 'attrition_blue', 'readiness_blue'];
    var label = { attrition_red: '红方战损率', readiness_red: '红方完好率',
                  attrition_blue: '蓝方战损率', readiness_blue: '蓝方完好率' };
    var h = '<div class="sf-ci-sum">' + keys.map(function (k) {
      var v = sum[k]; if (!v) return '';
      return '<div class="sf-ci-card"><div class="ci-k">' + label[k] + '</div>' +
        '<div class="ci-v">' + (v.mean * 100).toFixed(1) + '%</div>' +
        '<div class="ci-range">95%CI [' + (v.lo * 100).toFixed(1) + '%, ' + (v.hi * 100).toFixed(1) + '%]</div>' +
        '<div class="ci-sd">sd ' + (v.std * 100).toFixed(1) + ' · ±' + (v.ci95 * 100).toFixed(1) + '</div></div>';
    }).join('') + (sum.success ? '<div class="sf-ci-card"><div class="ci-k">任务完成率</div>' +
        '<div class="ci-v">' + (sum.success.mean * 100).toFixed(1) + '%</div>' +
        '<div class="ci-range">' + sum.success.count + ' / ' + sum.success.n + ' 次成功</div></div>' : '') + '</div>';
    h += '<table class="sf-table sf-ci-table"><thead><tr><th>复制</th><th>种子偏移</th>' +
         keys.map(function (k) { return '<th>' + label[k] + '</th>'; }).join('') + '<th>成功</th></tr></thead><tbody>';
    rows.forEach(function (r) {
      h += '<tr><td>' + r.rep + '</td><td>+' + r.seed_offset + '</td>' +
        keys.map(function (k) { return '<td>' + (r[k] != null ? (r[k] * 100).toFixed(1) + '%' : '—') + '</td>'; }).join('') +
        '<td>' + (r.success ? '✓' : '—') + '</td></tr>';
    });
    h += '</tbody></table>';
    if (rep.note) h += '<div class="sf-note">' + esc(rep.note) + '</div>';
    $('sf-panel-ci').innerHTML = h;
  }

  /* ---- P0-5：业务环节状态时间线 ---- */
  function renderTimeline() {
    var tl = ST.loss.timeline || {};
    var tracks = tl.tracks || [], sm = tl.summary || {};
    var stageSummary = sm.stage_events || {};
    var h = '<div class="sf-tl-legend"><b>业务环节流转</b>（与软件 8 节点流程并列的第二条主线）：' +
      (tl.stages || []).map(function (s) {
        var n = stageSummary[s] || 0;
        return '<span class="sf-tl-stage' + (n ? '' : ' zero') + '">' + esc(s) + ' ' + n + '</span>';
      }).join('') + '</div>';
    h += '<div class="sf-tl-sum">受损单位 ' + (sm.n_tracks || 0) +
         ' · 修复归队 ' + (sm.n_repaired || 0) + ' · 报废 ' + (sm.n_scrapped || 0) + '</div>';
    if (!tracks.length) {
      h += '<div class="sf-empty">事件流未产出台账级环节记录（可运行一次实时推演并保留事件帧后重试）。</div>';
    } else {
      h += '<div class="sf-tl-track-wrap">';
      tracks.slice(0, 60).forEach(function (t) {
        h += '<div class="sf-tl-track"><div class="tt-name" title="' + esc(t.unit_id) + '">' + esc(t.unit_id) + '</div>' +
             '<div class="tt-flow">' + t.events.map(function (e) {
               return '<span class="tt-ev s-' + esc(e.stage) + '" title="tick ' + e.tick + ' · ' + esc(e.disp) +
                      (e.part ? ' · ' + esc(e.part) : '') + '">' + esc(e.stage) + '<small>@' + e.tick + '</small></span>';
             }).join('<span class="tt-arrow">›</span>') + '</div></div>';
      });
      h += '</div>';
      if (tracks.length > 60) h += '<div class="sf-note">仅显示前 60 条轨迹，完整数据随清单 JSON 落盘。</div>';
    }
    if (tl.note) h += '<div class="sf-note">' + esc(tl.note) + '</div>';
    $('sf-panel-timeline').innerHTML = h;
  }

  function genPlan() {
    if (!ST.lossId) { alert('请先完成多次推演生成战损清单'); return; }
    var useLlm = $('sf-use-llm').checked;
    var tpl = $('sf-template').value;
    setNode('plan', 'running'); setNode('review', 'idle');
    $('sf-plan-hint').textContent = '正在生成（本地大模型只写叙述）…';
    post('/api/support/plan', { mission_id: ST.missionId, loss_id: ST.lossId,
                                 template: tpl, use_llm: useLlm,
                                 llm_params: collectLlmParams() }).then(function (r) {
      if (r.error) { setNode('plan', 'error'); alert(r.error); return; }
      $('sf-recon-detail').hidden = true;
      applyPlan(r.plan);
      var v = r.plan.validation || {};
      $('sf-check').innerHTML = v.passed
        ? '✓ 数字一致性复核通过：' + v.checked_numeric_slots + ' 个数值槽位来自清单/录入，叙述无编造数字'
        : '✗ 复核未通过：未替换槽位 ' + (v.unfilled_slots || []).length +
          ' / 叙述含数字 ' + (v.fabricated_numbers || []).length +
          ' / 关键数字缺失 ' + (v.absent_key_numbers || []).length;
      $('sf-plan-doc').querySelector('.sf-llm-note').innerHTML +=
        ' · 模型 ' + esc((r.plan.llm && r.plan.llm.model) || '本地 Qwen3.8-27B');
    });
  }

  // P0-8：正文数字标注溯源（点数字弹出来源卡片）
  function provValueMap() {
    var m = {};
    var slots = (ST.provision && ST.provision.slots) || {};
    Object.keys(slots).forEach(function (k) {
      var v = String(slots[k].value == null ? '' : slots[k].value).trim();
      if (!v) return;
      // 只标注纯数值/百分比类槽位；叙述型长文本不标注
      if (!/^[0-9]+(?:\.[0-9]+)?%?$/.test(v)) return;
      (m[v] = m[v] || []).push({ key: k, meta: slots[k] });
    });
    return m;
  }
  // 在已渲染的 DOM 文本节点上标注数字，避免字符串替换污染 HTML 属性
  function annotateNumbersIn(root) {
    var map = provValueMap();
    var keys = Object.keys(map).sort(function (a, b) { return b.length - a.length; });
    if (!keys.length) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    var nodes = [], n;
    while ((n = walker.nextNode())) {
      if (n.parentNode && n.parentNode.className && String(n.parentNode.className).indexOf('sf-prov-num') >= 0) continue;
      nodes.push(n);
    }
    nodes.forEach(function (node) {
      var txt = node.nodeValue;
      if (!txt || !/\d/.test(txt)) return;
      var ranges = [];
      keys.forEach(function (v) {
        var pos = 0, idx;
        var re = new RegExp(v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
        while ((idx = re.exec(txt)) !== null) {
          var s = idx.index, e = s + v.length;
          var overlap = ranges.some(function (r) { return s < r.e && e > r.s; });
          if (!overlap) ranges.push({ s: s, e: e, v: v, meta: map[v][0].meta, key: map[v][0].key });
          pos = e; re.lastIndex = e;
          if (pos >= txt.length) break;
        }
      });
      if (!ranges.length) return;
      ranges.sort(function (a, b) { return a.s - b.s; });
      var frag = document.createDocumentFragment(), last = 0;
      ranges.forEach(function (r) {
        if (r.s > last) frag.appendChild(document.createTextNode(txt.slice(last, r.s)));
        var span = document.createElement('span');
        span.className = 'sf-prov-num';
        span.setAttribute('data-prov', JSON.stringify({ label: r.meta.label, source_label: r.meta.source_label,
                                                        section: r.meta.section, value: r.meta.value, key: r.key }));
        span.textContent = r.v;
        frag.appendChild(span);
        last = r.e;
      });
      if (last < txt.length) frag.appendChild(document.createTextNode(txt.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
  }
  function annotateProv(text, prov) {
    if (!prov || !prov.slots) return '';
    var srcSeen = {}, labels = prov.source_labels || {};
    Object.keys(prov.slots).forEach(function (k) { srcSeen[prov.slots[k].source] = 1; });
    var srcs = Object.keys(srcSeen).map(function (s) { return labels[s] || s; });
    return '<div class="sf-prov-legend">数字来源：' + esc(srcs.join(' · ')) +
           '（共 ' + Object.keys(prov.slots).length + ' 个数值槽位）</div>';
  }

  function md(text) {
    var lines = text.split('\n'), out = '', i = 0;
    function inline(s) {
      return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    }
    while (i < lines.length) {
      var line = lines[i];
      if (/^\|/.test(line) && i + 1 < lines.length && /^\|[\s:\-|]+\|$/.test(lines[i + 1])) {
        var head = line.split('|').slice(1, -1).map(inline);
        i += 2;
        var body = [];
        while (i < lines.length && /^\|/.test(lines[i])) {
          body.push(lines[i].split('|').slice(1, -1).map(inline)); i++;
        }
        out += '<table><thead><tr>' + head.map(function (x) { return '<th>' + x + '</th>'; }).join('') +
               '</tr></thead><tbody>' + body.map(function (row) {
                 return '<tr>' + row.map(function (x) { return '<td>' + x + '</td>'; }).join('') + '</tr>';
               }).join('') + '</tbody></table>';
        continue;
      }
      if (!line.trim()) { out += '<div style="height:4px"></div>'; i++; continue; }
      out += '<p>' + inline(line) + '</p>'; i++;
    }
    return out;
  }

  function download(what) {
    var body = what === 'loss' ? { what: 'loss', loss_id: ST.lossId }
             : what === 'docx' ? { what: 'plan_docx', plan_id: ST.planId }
             : { what: 'plan', plan_id: ST.planId };
    post('/api/support/export', body).then(function (r) {
      if (r.error) { alert(r.error); return; }
      var blob;
      if (r.format === 'docx') {
        var bin = atob(r.content_b64), bytes = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
      } else {
        blob = new Blob([r.content], { type: r.format === 'csv' ? 'text/csv;charset=utf-8' : 'text/markdown;charset=utf-8' });
      }
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = r.filename;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
      setNode('export', 'done');
    });
  }

  // P0-8：溯源弹卡
  function showProvPop(el) {
    var pop = $('sf-prov-pop'); if (!pop) return;
    var raw = el.getAttribute('data-prov');
    try {
      var d = JSON.parse(raw);
      pop.innerHTML = '<div class="pp-h">数字溯源</div>' +
        '<div class="pp-row"><span>槽位</span><b>' + esc(d.label || d.key) + '</b></div>' +
        '<div class="pp-row"><span>数值</span><b>' + esc(d.value) + '</b></div>' +
        '<div class="pp-row"><span>来源</span><b>' + esc(d.source_label || d.source || '') + '</b></div>' +
        (d.section ? '<div class="pp-row"><span>章节</span><b>' + esc(d.section) + '</b></div>' : '') +
        '<button type="button" class="sf-btn pp-close">关闭</button>';
      var rect = el.getBoundingClientRect(), host = $('sf-col-plan').getBoundingClientRect();
      pop.style.left = Math.max(4, rect.left - host.left) + 'px';
      pop.style.top = Math.min(host.height - 150, rect.bottom - host.top + 4) + 'px';
      pop.hidden = false;
      var cb = pop.querySelector('.pp-close');
      if (cb) cb.addEventListener('click', function () { pop.hidden = true; });
    } catch (e) { pop.hidden = true; }
  }

  function refreshLlmBadge() {
    api('/api/llm').then(function (r) {
      var el = $('sf-llm-badge');
      var ok = r.available || r.running_real;
      el.textContent = 'LLM：' + (r.model || '—') + (ok ? ' · 就绪' : ' · 降级模板');
      el.className = 'sf-llm-badge' + (ok ? '' : ' off');
    });
  }

  /* ---------------- boot ---------------- */
  function boot() {
    api('/api/support/catalog').then(function (r) {
      if (r.error) { console.error('[support-flow] catalog 加载失败', r.error); return; }
      CAT = r;
      window.DASH_GLOS = r.metric_glossary || {};  // P1-7 指标口径字典供对账明细复用
      renderForm(CAT);
      renderLlmParams();          // P1-3
      renderPipeline(CAT.process_nodes);
      $('sf-template').innerHTML = (r.templates || []).map(function (t) {
        return '<option value="' + esc(t.id) + '">' + esc(t.name) + '</option>';
      }).join('');
      resetNodes();
      refreshLlmBadge();
      api('/api/support/mission').then(function (m) {
        if (m.mission) { ST.missionId = m.mission.mission_id; fillForm(m.mission); }
      });
      api('/api/support/loss').then(function (l) {
        if (l.loss) { ST.lossId = l.loss.loss_id; loadLoss(l.loss.loss_id); }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var btn = $('btn-support-flow');
    if (btn) btn.addEventListener('click', open);
    var cls = $('sf-close');
    if (cls) cls.addEventListener('click', close);
    var save = $('sf-save'); if (save) save.addEventListener('click', saveMission);
    var load = $('sf-load'); if (load) load.addEventListener('click', loadMission);
    var ded = $('sf-deduce'); if (ded) ded.addEventListener('click', deduce);
    var gp = $('sf-gen-plan'); if (gp) gp.addEventListener('click', genPlan);
    var el = $('sf-export-loss'); if (el) el.addEventListener('click', function () { download('loss'); });
    var ep = $('sf-export-plan'); if (ep) ep.addEventListener('click', function () { download('plan'); });
    var ed = $('sf-export-docx'); if (ed) ed.addEventListener('click', function () { download('docx'); });
    // P1 新增交互
    var adv = $('sf-adv-toggle'); if (adv) adv.addEventListener('click', function () {
      var b = $('sf-adv-body'); b.hidden = !b.hidden;
      adv.textContent = b.hidden ? '⚙ LLM 高级参数（27B CPU 调优）▾' : '⚙ LLM 高级参数（27B CPU 调优）▴';
    });
    var ne = $('sf-edit-narrative'); if (ne) ne.addEventListener('click', openNarrEditor);
    var tm = $('sf-template-map'); if (tm) tm.addEventListener('click', openTemplateMap);
    var ar = $('sf-archive'); if (ar) ar.addEventListener('click', function () { backToArchive(); openArchive(); });
    // P2 新增交互
    var dd = $('btn-data-dict'); if (dd) dd.addEventListener('click', openDataDict);
    var dd2 = $('sf-data-dict'); if (dd2) dd2.addEventListener('click', openDataDict);
    var ac = $('sf-arch-compare'); if (ac) ac.addEventListener('click', openCompare);
    var pc = $('sf-plan-compare'); if (pc) pc.addEventListener('click', openPlanCompare);
    var dv = $('sf-deploy-view'); if (dv) dv.addEventListener('click', openDeployView);
    Array.prototype.forEach.call(document.querySelectorAll('[data-recompute]'), function (b) {
      b.addEventListener('click', function () { recomputeStage(b.getAttribute('data-recompute')); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('.sf-modal-x'), function (x) {
      x.addEventListener('click', function () { $(x.getAttribute('data-modal')).hidden = true; });
    });
    Array.prototype.forEach.call(document.querySelectorAll('.sf-modal'), function (m) {
      m.addEventListener('click', function (e) { if (e.target === m) m.hidden = true; });
    });
    // 清单子 tab
    var tabs = $('sf-loss-tabs');
    if (tabs) tabs.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('.sftab') : null;
      if (t) { ST.lossTab = t.getAttribute('data-tab'); renderLossTab(); }
    });
    // 数字溯源点击
    var pd = $('sf-plan-doc');
    if (pd) pd.addEventListener('click', function (e) {
      var num = e.target.closest ? e.target.closest('.sf-prov-num') : null;
      if (num) showProvPop(num);
      else { var pop = $('sf-prov-pop'); if (pop) pop.hidden = true; }
    });
    // 双方实力交互（表单为动态渲染，用委托绑定）
    $('sf-form').addEventListener('click', function (e) {
      var tab = e.target.closest ? e.target.closest('.sf-side-tab') : null;
      if (tab) { switchForceSide(tab.getAttribute('data-side')); return; }
      if (e.target.id === 'sf-forces-load') { loadForcesDefault(); return; }
      if (e.target.id === 'sf-forces-clear') { clearForces(); return; }
    });
    $('sf-form').addEventListener('change', function (e) {
      if (e.target && e.target.id === 'sf-forces-enable') updateForcesSummary();
    });
    $('sf-form').addEventListener('input', function (e) {
      if (e.target && e.target.getAttribute && e.target.getAttribute('data-group') === 'forces') {
        updateForcesSummary();
      }
    });
  });

  window.SupportFlow = { open: open, close: close, boot: boot };
})();
