# fusion 工作站部署交接 Runbook（供工作站端 CodeBuddy 执行）

> **读者**：工作站端 AI 编程助手（CodeBuddy）。本文是**执行手册**，不是概述文档。请按第 0 节判定前置条件后，从第 2 节顺序执行，每步都有验收标准。
> **背景事实基准**：所有路径、命令、端口、字段均来自仓库实际代码（本机 2026-11-22 核实），不引用 PPT/报告口径。
> **一句话目标**：在 fusion 工作站把 daqing 软件跑起来，并确保"仿真计算的战损清单"和"人工上传 Word 战损清单"两种来源，都能调用工作站本地 qwen3.8-27B 大模型，按 `repair_plan_v1.md` 模板生成抢修方案。

---

## 0. 执行前必读：角色与红线

### 0.1 本机 vs 工作站的分工

| 环节 | 在哪做 | 谁做 |
|---|---|---|
| 代码开发、测试、commit | 本机 Windows（已完成） | 本机助手 |
| `git push` 到 GitHub | 本机（**本次执行**） | 本机助手 |
| `git pull`、装环境、配模型、启服务、验收 | **fusion 工作站** | **你（工作站 CodeBuddy）** |

你拿到的仓库是**已推送的最新 `main`**（含抢修方案链路）。不要重新开发功能，只做部署、配置、验收。

### 0.2 红线：绝对不可做的事（违反会破坏已冻结的权威结果）

1. **不要修改权威数字文件**：`src/brigsim/agent/web/data/` 下的 `verification.json` / `pareto.json` / `surrogate.json` / `timeseries.json` / `distribution.json` / `sensitivity.json` / `manifest.json`。这些是比赛冻结数字（R²=0.705、加速≈38万倍、Pareto 74 方案、成本 0.458→0.165、能见度 ST=0.758），任何改动都会导致报告/软件/图表不一致。
2. **不要重跑仿真、不要重算代理模型、不要重建 rrd** 去"更新"这些数字。部署只需启动现成服务。
3. **不要改仿真内核 / experiment / surrogate 代码**。抢修链路是独立的 `support/` 包，与内核解耦，你只需配置和调用。
4. **不要 `git add -A` / `git commit` 无关文件**：仓库工作区有大量历史中间产物（`_*.txt`、`report/_*/`、截图 png 等），且 `.gitignore` 已排除涉密数据（`data/real/`、`outputs/`、`*.parquet`）。若非用户明确要求，工作站端**无需任何 commit**——只 pull、配置、启动、验收。
5. **不要把涉密/真实战损数据 push 到 GitHub**（`.gitignore` 已排除，保持即可）。

### 0.3 前置条件检查（第一条命令）

```bash
# 在工作站终端执行；任一 FAIL 先解决再继续
echo "arch: $(uname -m)"                       # 期望 aarch64
free -h | head -2                              # 期望约 125G 可用
python3 --version                              # 期望 3.10+
git --version
curl -s http://127.0.0.1:8000/v1/models | head -c 300   # 期望返回模型ID JSON（模型服务已启动）
echo; echo "--- 模型端口探测 ---"
ss -ltnp 2>/dev/null | grep -E ':8000|:8080|:11434|:1234' || echo "未见常见LLM端口，请确认模型服务实际端口"
```
> 若第 5 条无返回，说明大模型服务未启动——先按工作站既定方式启动 qwen3.8-27B 的 OpenAI 兼容服务，再继续。软件**不强依赖**模型在线（`mode=auto` 会自动降级为模板叙述），但要验收"真实调用"必须模型在线。

---

## 1. 背景：本次交付了什么（帮你理解你要部署的东西）

本机已完成并推送的功能链路：

```
战损清单（两条来源，同构汇入同一个 loss_id）
  ├─ ① 仿真多次推演 → 逐件战损清单（source=events/estimate）
  └─ ② 人工填报 Word → loss_import 解析（source=manual_docx）
                          ↓
              repair_plan.generate（复用 plan.generate）
                          ↓
        数字槽位：全部来自清单/录入，确定性、零编造
        叙述槽位：qwen3.8-27B 按 repair_plan_v1.md 模板生成（失败自动降级）
                          ↓
        数字一致性校验 + 溯源 + 公文式 Word 导出
```

**抢修模板章节**（`templates/support/repair_plan_v1.md`）：报损情况 → 抢修任务与决心 → 战损统计与抢修量测算 → 分级抢修处置（K 战毁 / F 火力受损 / M 机动受损）→ 力量编组 → 器材前送 → 拖救后送 → 防卫通信运输 → 实施步骤 → 附表。

**关键设计**：模板只用 `catalog.SLOT_DICT` 已定义槽位，因此数字校验、溯源、docx 导出逻辑零改动；叙述中一旦出现阿拉伯数字即判无效、用默认文字替换（LLM 只写文字，不产生数字）。

---

## 2. 部署步骤（顺序执行，每步带验收）

### 步骤 1：拉取代码

```bash
cd ~
# 首次
git clone https://github.com/Flyn4983/daqing.git
cd daqing
# 已 clone 过
# cd ~/daqing && git pull origin main

git log --oneline -3    # 验收：首行应为 de006fa 或更新（含"人工Word战损清单导入"+P3真实约束录入）
git status -sb          # 验收：应为 main...origin/main，无 ahead（若有 ahead，说明本地有未推送改动，先与本机确认）
```

**验收标准**：`git log` 能看到 `de006fa`（其下依次为 `1db66b8` P3 真实约束、`e55ea15` 抢修方案链路）；工作区干净（未跟踪文件忽略即可）。

### 步骤 2：创建 Python 环境

```bash
cd ~/daqing
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# 数据大屏 + 保障链路 + Word 导入（ARM64 全功能，不含 3D 回放也可先这样装）
pip install -e ".[combat,analysis,report]"
# 需要 3D 回放再加 viz：pip install -e ".[combat,analysis,report,viz]"
```

**ARM64 装包提示**：
- `numpy/pandas/scipy/rasterio` 在 aarch64 有预编译 wheel；若某包触发源码编译，先 `sudo apt install -y build-essential gfortran libgdal-dev`。
- **不装 warp / 不需要 CUDA**：抢修链路与大屏不依赖 GPU（`warp` 是可选项）。
- `report` 组提供 `python-docx>=1.1`，仅用于"下载上报模板 / 解析上传 Word"；漏装只影响 Word 导入，仿真链路不受影响。

**验收标准**：`python -c "import docx, flask, numpy; print('deps OK')"` 无报错。

> 注：本项目用 Python 标准库 `http.server` 起大屏（见 `agent/server.py::AgentWebServer`），不强制 Flask；若 `import flask` 失败不影响启动，以步骤 4 的启动成功为准。

### 步骤 3：配置本地大模型（关键）

软件只认 OpenAI 兼容 `/v1` 接口，不绑定框架。编辑仓库根 `llm_config.json`：

```json
{
  "base_url": "http://127.0.0.1:8000/v1",
  "model": "<填 /v1/models 返回的真实模型ID>",
  "api_key": "EMPTY",
  "timeout": 180,
  "mode": "auto",
  "temperature": 0.3,
  "max_tokens": 1024
}
```

- `base_url`：按模型服务实际端口改。常见：vLLM `8000`、llama.cpp server `8080`、Ollama `11434`、LM Studio `1234`。
- `model`：**必须**与 `curl http://127.0.0.1:<端口>/v1/models` 返回的 `id` 完全一致。仓库默认 `Qwen3.8-27B` 只是占位符——不改成真实 ID 会导致 `mode=real` 报"模型不存在"、`auto` 静默降级。这是现场最常见的坑。
- `mode`：`auto`=探测到模型用真实生成、否则降级模板（推荐，演示不翻车）；确认模型在线后可用 `real` 强制真实调用。
- 环境变量可覆盖 JSON：`BRIGSIM_LLM_BASE_URL` / `BRIGSIM_LLM_MODEL` / `BRIGSIM_LLM_MODE` / `BRIGSIM_LLM_TIMEOUT`。

**验收标准**：
```bash
python -c "from brigsim.agent.llm_client import llm_client; print(llm_client)"
# 或直接看模型探测（auto 模式启动后，接口 /api/support/catalog 无 LLM 字段，
# 以步骤 6 的"真实调用判定"为准）
```
> 自检要点：记住你填的 `model` 真实 ID，步骤 6 会用它判定是否真实调用了大模型。

### 步骤 4：启动软件

```bash
cd ~/daqing
source .venv/bin/activate

# 推荐：轻量化，不依赖 3D rrd（抢修链路够用）
python -m brigsim.cli agent-serve --host 0.0.0.0 --port 8848 --no-replay

# 若需要完整 3D 回放（有 rrd 数据时）：
# python -m brigsim.cli agent-serve --host 0.0.0.0 --port 8848
```

可用参数（`cli.py::agent_serve_cmd`）：`--host` / `--port` / `--no-replay` / `--replay <rrd路径>` / `--delay`（模拟流式等待）。

**验收标准**：
```bash
curl -s -o /dev/null -w "index: %{http_code}\n" http://127.0.0.1:8848/index.html           # 200
curl -s -o /dev/null -w "catalog: %{http_code}\n" http://127.0.0.1:8848/api/support/catalog  # 200
curl -s -o /dev/null -w "loss/template: %{http_code}\n" http://127.0.0.1:8848/api/support/loss/template  # 200
```
浏览器打开 `http://<工作站IP>:8848/index.html`，点头栏「保障全过程」进入抽屉。
> 零外网：大屏 3D 走同源反代、LLM 走 127.0.0.1 回环，拔网线仍可演示。

### 步骤 5：跑通两种战损来源 → 抢修方案（功能验收）

**来源① 仿真计算**：
1. 抽屉左栏录入任务与资源 → 「保存录入并校验」。
2. 中栏设推演次数 → 「▶ 开始多次推演」→ 生成逐件战损清单。
3. 右栏模板下拉选 `repair_plan_v1.md` → 「⚒ 生成抢修方案」。

**来源② 人工 Word**：
1. 中栏「↓ 上报模板」下载 `战损清单上报模板.docx`。
2. 基层按表头填写（一行一装；等级 K/F/M；删示例行），可填参战总数。
3. 「↑ 上传Word清单」选填好的 docx → 中栏出现来源标签「人工填报 Word」与清单表格。
4. 右栏「⚒ 生成抢修方案」，方案头标注「来源：人工填报 Word 战损清单」。

**表头容错**：装备名称/车型/装备型号、战损等级/损伤程度/毁伤等级、数量/台数、损伤部位/损坏部件、处置/处置方式等别名均可识别。
**等级口径**：战毁/报废/摧毁→K；火力受损/重损→F；机动受损/轻损/可修/现地抢修→M。战损率只计 K 级，与仿真口径一致；未填参战总数时不出战损率、不臆造分母。

**验收标准**：两种来源都能生成方案，且方案正文的数字与清单一致、无未替换的 `{{槽位}}`、无 LLM 编造的阿拉伯数字。

### 步骤 6：判定大模型是否真实调用（重要）

```bash
# 看服务日志：mode=real 时，生成方案期间会有 /v1/chat/completions 请求记录
# 方法 A：把 mode 设为 real，若方案叙述明显是自然语言段落（非模板默认文字）→ 真实调用成功
# 方法 B：临时停掉模型服务再生成一次 → 若仍能出方案且叙述变模板默认 → 说明 auto 降级生效（这也验证了解耦设计）
```
**判定要点**：叙述槽位（如"接报来源与时机""抢修决心"等段落）应是与本次战损情况相关的自然语言；数字槽位（战损数量、等级统计、编组数量等）始终精确来自清单。若发现叙述里出现阿拉伯数字，属异常——数字校验逻辑会拦截替换，请记录并反馈。

---

## 3. API 端点速查（联调/排错用）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/support/catalog` | 目录/模板/口径/槽位字典（含可用模板列表） |
| GET | `/api/support/loss/template` | 下载基层填报 Word 模板（base64） |
| POST | `/api/support/loss/import` | 上传 Word（`content_b64`），返回 `loss_id`+解析摘要+warnings |
| POST | `/api/support/repair-plan` | 抢修方案生成（`mission_id`/`loss_id`/`use_llm`/`llm_params`） |
| GET/POST | `/api/support/loss`、`/api/support/plan` | 读取清单/方案（既有） |
| GET | `/api/support/pareto/recommend` | 成本约束 Pareto 选优（只读 74 方案；GUI 显式录入约束时自动按资源上限硬筛选） |
| GET/POST | `/api/support/constraints/check` | P3 真实业务约束校核（三源/硬阻断软告警；纯派生不进内核） |
| GET | `/api/support/pareto/region` | P4 二维可行域投影数据（74 方案原维度 + GUI 约束可行性；只读派生，前端画 SVG） |

---

## 4. 排错决策树

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `git pull` 报 CONNECT/SSL 错误 | 工作站网络/代理 | 确认 `git config --get http.proxy`；公司网需配代理或走 SSH |
| `pip install` 某包源码编译失败 | 缺编译工具 | `sudo apt install -y build-essential gfortran libgdal-dev` 后重试 |
| 启动报端口占用 | 8848 被占 | `ss -ltnp \| grep 8848` 找进程；或换 `--port 8849` |
| 方案叙述是模板默认文字 | 模型 ID 填错 / 服务未起 / mode=auto 降级 | 核对 `/v1/models` 真实 ID；确认端口；临时设 `mode=real` 看报错 |
| Word 导入报"缺少 python-docx" | 漏装 report 组 | `pip install -e ".[report]"` |
| 上传 Word 解析行数为 0 | 表头/示例行问题 | 确认删除了"（示例）"行、表头在第一行；参考 5.2 别名 |
| 页面 3D 区空白 | `--no-replay` 或无障碍 | 正常（轻量化模式）；不影响抢修链路 |
| 数字与清单对不上 | 疑似改了权威文件 | 立即停止，检查红线 §0.2，`git status` 看是否有 data/*.json 改动 |

---

## 5. 本机已完成事项（你 pull 后应已包含）

| commit | 内容 |
|---|---|
| `1db66b8` | P3 真实业务约束 GUI 录入与校核（三源约束/硬阻断软告警/选优硬筛选；权威数字不变） |
| `31de992` | 新增工作站 CodeBuddy 交接 Runbook |
| `e55ea15` | 人工 Word 战损清单导入 + 抢修方案生成 + GitHub→ARM 部署链路 |
| `0056d21` | 成本约束 Pareto 选优（P2 边界补齐） |
| `7517c2f` | 作战保障全过程 P0-P2 + 双方实力 GUI + ARM 适配 |
| `70466b8`~`a1eff1e` | C7 可视化全工况真实地形/装备 + 本地大模型串联 + 文档软件对齐（共 8 个） |

本机验证记录（供参考，工作站需自行复验）：单测 `test_repair_flow` 19 passed；support 全套 88 passed；C7 回归 42 passed；全量 305 passed / 3 skipped；浏览器端到端 0 控制台错误；权威数字文件 md5 前后不变。

---

## 6. 验收清单（全部打勾即部署完成）

- [ ] `git log` 看到 `e55ea15`，工作区无意外改动
- [ ] venv 建好，`import docx` 成功
- [ ] `llm_config.json` 的 `model` 已改为 `/v1/models` 真实 ID
- [ ] `curl index/catalog/loss/template` 均 200
- [ ] 浏览器「保障全过程」抽屉可打开
- [ ] 仿真清单 → 抢修方案 生成成功、数字一致
- [ ] 人工 Word 清单 → 抢修方案 生成成功、来源标注正确
- [ ] 判定大模型真实调用成功（叙述为相关自然语言、无编造数字）
- [ ] 权威数字文件 `git status` 无改动（红线 §0.2）
- [ ] 拔网线/离线仍可演示（可选加测）

---

## 7. 需要反馈给本机的情况

若遇到以下任一情况，请记录现象与日志原文反馈，不要自行改动权威数字或内核代码：
1. 模型 `/v1/models` 返回的真实 ID（本机需据此更新占位默认值）。
2. 工作站实际 Python 版本、pip 装包报错。
3. 两种来源任一种生成失败的具体错误。
4. 任何涉及 `data/*.json` 权威数字变动的迹象。

---

*文档版本 v1 · 2026-11-22 · 与部署细节文档 `report/GitHub部署到fusion工作站_抢修方案链路_v1.md` 配套：那篇是"事实说明"，本篇是"AI 执行手册"。*
