# 通过 GitHub 部署到 fusion 工作站 · 抢修方案链路（v1）

> 目标环境：**fusion 工作站，ARM64（aarch64）、128GB 统一内存、无独立 CUDA GPU，已部署 qwen3.8-27B 本地大模型**。
> 事实基准：本文所有命令、路径、端口、配置项均来自当前仓库实际代码（2026-11-22 核实），不引用 PPT/报告口径。
> 交付物格式：仅 Markdown。

---

## 1. 本次新增链路总览

本次需求是："软件得到战损清单后（**仿真计算的** 与 **人工上传 Word 的** 两种来源），都能调用工作站上的大模型，按给定模板生成抢修方案。"

```
战损清单（两条来源，同构汇入）
  ├─ ① 仿真多次推演 → battle_loss.from_event_frames → loss(source=events/estimate)
  └─ ② 人工填报 Word → loss_import.parse_loss_docx → loss(source=manual_docx)
                                ↓ 同一 loss_id 读取（/api/support/loss?loss_id=）
                    repair_plan.generate（薄封装，复用 plan.generate）
                                ↓
        数字槽位：全部来自清单/录入（确定性，零编造）
        叙述槽位：本地 Qwen3.8-27B 按 repair_plan_v1.md 模板生成（失败自动降级）
                                ↓
        数字一致性校验 + 溯源 + 公文式 Word 导出
```

**关键事实**：
- 抢修模板 `templates/support/repair_plan_v1.md`，章节为：报损情况 → 抢修任务与决心 → 战损统计与抢修量测算 → 分级抢修处置（K/F/M）→ 力量编组 → 器材前送 → 拖救后送 → 防卫通信运输 → 实施步骤 → 附表。模板**只使用 `catalog.SLOT_DICT` 已定义槽位**，因此数字校验、溯源、docx 导出逻辑零改动。
- 大模型调用沿用现有 `agent/llm_client.py`（OpenAI 兼容 `/v1/chat/completions`），`llm_config.json` 默认已指向 `http://127.0.0.1:8000/v1`、模型 `Qwen3.8-27B`、timeout 180、mode auto。**换推理框架只改这个 JSON，不改代码**。
- 数字红线：叙述中一旦出现阿拉伯数字即判无效、用默认文字替换（plan.py `_NUM_RE`）；`loss_import` 不伪造数量/等级/部位，未填参战总数时 `roster_size=0` 并告警，不臆造战损率分母。

---

## 2. GitHub 同步（本机 → 远端 → 工作站）

远端：`origin https://github.com/Flyn4983/daqing.git`（当前仓库已配置）。

**本机（Windows，开发完成）：**
```bash
cd C:/Users/flyn4/Desktop/daqing
git add src/brigsim/support/loss_import.py src/brigsim/support/repair_plan.py \
        src/brigsim/support/api.py src/brigsim/support/battle_loss.py \
        src/brigsim/agent/server.py \
        templates/support/repair_plan_v1.md \
        src/brigsim/agent/web/index.html \
        src/brigsim/agent/web/js/support_flow.js \
        src/brigsim/agent/web/css/support_flow.css \
        tests/test_repair_flow.py tools/verify_repair_flow.py \
        pyproject.toml \
        "report/GitHub部署到fusion工作站_抢修方案链路_v1.md"
git commit -m "feat: 人工Word战损清单导入 + 抢修方案生成 + ARM部署链路"
git push origin main
```
> 注意：`.gitignore` 已排除 `outputs/`（战损清单/方案落盘）、`*.parquet`、`data/real/`（涉密真实数据）、`.venv/`。这些**不会被推送**——符合"涉密数据不进 GitHub"的既有约定。

**工作站（Ubuntu ARM64，首次）：**
```bash
sudo apt update && sudo apt install -y git python3-venv python3-pip
git clone https://github.com/Flyn4983/daqing.git
cd daqing

# 后续更新
git pull origin main
```

---

## 3. 工作站 Python 环境（ARM64 / 128G）

```bash
cd ~/daqing
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# 核心 + 保障链路 + 可视化 + Word 导入
pip install -e ".[combat,analysis,viz,report]"
# 若只需数据大屏 + 抢修链路（不跑 3D 回放），可去掉 viz：
# pip install -e ".[combat,analysis,report]"
```

**ARM64 装包提示**：
- `rasterio`、`numpy`、`pandas`、`scipy` 在 aarch64 上均有预编译 wheel，`pip install` 直接可用；若某包无 wheel 触发源码编译，先 `sudo apt install -y build-essential gfortran libgdal-dev`。
- **无需 CUDA / warp**：抢修链路与数据大屏不依赖 GPU；`warp` 是可选 GPU 批量后端，ARM 工作站不装即可。
- **python-docx**：由 `report` 组安装（`python-docx>=1.1`），仅用于"下载上报模板 / 解析上传 Word"。若漏装，导入时会给出明确安装提示，仿真链路不受影响。

---

## 4. 本地大模型接入（qwen3.8-27B）

### 4.1 启动模型服务（以实际推理框架为准，选其一）

软件只认 OpenAI 兼容 `/v1`，**不绑定具体框架**。以下为常见示例（按你在工作站实际部署的框架选用）：

```bash
# 示例 A：vLLM（若已装）
python -m vllm.entrypoints.openai.api_server \
  --model /path/to/qwen3.8-27b --host 127.0.0.1 --port 8000

# 示例 B：llama.cpp server（ARM CPU / 统一内存常用）
llama-server -m /path/to/qwen3.8-27b.Q4_K_M.gguf --host 127.0.0.1 --port 8080 -c 8192
```

### 4.2 配置软件指向模型

编辑仓库根 `llm_config.json`（字段均为实际代码读取项）：

```json
{
  "base_url": "http://127.0.0.1:8000/v1",
  "model": "<你的实际模型ID，以 /v1/models 返回为准>",
  "api_key": "EMPTY",
  "timeout": 180,
  "mode": "auto"
}
```
- 若用 llama.cpp 的 8080 端口，把 `base_url` 改为 `http://127.0.0.1:8080/v1`。
- `model` 必须与模型服务 `/v1/models` 返回的 ID 一致（qwen3.8-27B 的正式 ID 以部署实际为准，源码默认值 `Qwen3.8-27B` 仅占位）。
- `mode=auto`：探测到模型用真实生成，未就绪自动降级模板叙述，**现场演示不翻车**；正式使用建议确认模型在线后设为 `real`。
- 环境变量优先级高于 JSON：`BRIGSIM_LLM_BASE_URL` / `BRIGSIM_LLM_MODEL` / `BRIGSIM_LLM_MODE` / `BRIGSIM_LLM_TIMEOUT`。

### 4.3 ARM/128G 性能适配（已实现，非文档承诺）
- 方案叙述拆为 4 段、每段 ≤900 tokens 分别请求，单段失败只降级该段（源码 `plan.py::_LLM_GROUPS`、`generate_narratives`）。
- timeout 默认 180 秒，适配 27B 在 ARM CPU 上单请求 20~60s 的量级。
- 大模型只写叙述，**不产生任何数字**——即使模型输出异常，也不影响战损清单与权威四率。

---

## 5. 启动软件

```bash
cd ~/daqing
source .venv/bin/activate

# 完整启动（数据大屏 + 3D 回放）
python -m brigsim.cli agent-serve --host 0.0.0.0 --port 8848

# 若无 3D rrd / 不需要 3D（抢修链路更轻）：
python -m brigsim.cli agent-serve --host 0.0.0.0 --port 8848 --no-replay
```
- 打开 `http://<工作站IP>:8848/index.html`，点头栏 **「保障全过程」** 进入抽屉。
- `--no-replay` 实测参数来自 `cli.py::agent_serve_cmd`（`--no-replay` / `--replay` / `--delay` / `--host` / `--port`）。
- 零外网：3D 回放走同源反代，LLM 走 127.0.0.1 回环，拔网线仍可演示。

---

## 6. 两种战损来源 → 抢修方案（操作步骤）

### 6.1 来源①：仿真计算的战损清单
1. 抽屉左栏录入任务与资源 → 「保存录入并校验」。
2. 中栏设置推演次数 → 「▶ 开始多次推演」→ 生成逐件战损清单。
3. 右栏模板下拉选 `repair_plan_v1.md`，点 **「⚒ 生成抢修方案」**。
   - 或点「生成保障方案」用原十章模板（两者并存，互不影响）。

### 6.2 来源②：人工上传 Word 战损清单
1. 中栏「战损清单来源」行点 **「↓ 上报模板」**，下载 `战损清单上报模板.docx`。
2. 基层单位按表头填写真实战损（一行一装；战损等级 K/F/M；删除示例行），可填参战总数。
3. 点 **「↑ 上传Word清单」** 选择填好的 docx → 系统解析并在中栏显示来源标签「人工填报 Word」与清单表格。
4. 右栏点 **「⚒ 生成抢修方案」**，方案头标注「来源：人工填报 Word 战损清单」。

### 6.3 表头容错与等级口径
- 表头支持常见别名：`装备名称/车型/装备型号`、`战损等级/损伤程度/毁伤等级`、`数量/台数`、`损伤部位/损坏部件`、`处置/处置方式` 等。
- 等级映射：`战毁/报废/摧毁→K`、`火力受损/重损/需后送→F`、`机动受损/轻损/可修/现地抢修→M`。
- 器材部位映射：`行走/履带→行走总成`、`动力/发动机→动力总成`、`火控/瞄准→火控系统`、`武器/火炮→武器系统`。
- 战损率只计 K 级（不可恢复），M/F 计入战斗损伤率但不计战损率——与仿真口径一致。
- 未填参战总数时 `roster_size=0`，方案只呈现绝对数并告警，不臆造战损率分母。

---

## 7. API 端点（供二次开发/联调）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/support/loss/template` | 下载基层填报 Word 模板（base64） |
| POST | `/api/support/loss/import` | 上传 Word（`content_b64`），返回 `loss_id` + 解析摘要 + warnings |
| POST | `/api/support/repair-plan` | 抢修方案生成（`mission_id`/`loss_id`/`use_llm`/`llm_params`） |
| GET/POST | `/api/support/loss`、`/api/support/plan` | 读取清单/方案（沿用既有） |

---

## 8. 验证（本次实测，均通过）

- **单测** `tests/test_repair_flow.py`：19 passed（模板有效性、别名表头、K/M/F 映射、示例行排除、数字不伪造、两种来源同构生成、数字校验、权威文件只读）。
- **support 全套**：88 passed（flow/force_config/P0/P1/P2/Pareto/repair）；**C7 可视化回归**：42 passed。
- **全量回归**：305 passed / 3 skipped。另有 4 项 GUI 测试因 8849 服务未启动报连接拒绝（该端口按既有要求保持关闭），临时启动后复跑 4 passed，与本次改动无关。
- **浏览器端到端** `tools/verify_repair_flow.py`：模板下载、Word 导入 3 条、来源标签、抢修方案生成、数字校验通过、0 控制台错误，VERDICT=PASS。
- **数字红线**：抢修生成前后 `pareto.json` / `verification.json` 的 md5 不变（单测 `test_no_authoritative_file_mutation`）。

> 工作站部署后自检：启动后用浏览器跑一遍第 6 节流程；若 top 栏「LLM 状态」显示"可用"且方案叙述非模板默认文字，说明真实调用了 qwen3.8-27B。

---

## 9. 已知边界（以事实为准）

1. **模型 ID 需按实际填写**：`llm_config.json` 的 `Qwen3.8-27B` 是占位默认，必须以工作站 `/v1/models` 返回为准；否则 `mode=real` 会报模型不存在，`auto` 会降级模板文字。
2. **人工清单无仿真元数据**：来源②没有推演次数/置信区间/内核对账，方案对应位置显示 0 或"—"，不伪造；这是人工填报的固有信息缺失，不是缺陷。
3. **roster_size 依赖填报**：来源②的战损率分母完全取决于填报的参战总数；不填则不出战损率。
4. **python-docx 为可选依赖**：漏装只影响 Word 导入/导出，不影响仿真与计算清单链路。
5. **仓库不携带涉密数据**：`.gitignore` 排除真实战损数据与 outputs，工作站需按实际补齐真实清单（走来源②上传即可）。
