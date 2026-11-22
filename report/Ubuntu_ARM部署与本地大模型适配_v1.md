# Ubuntu ARM 工作站部署与本地大模型（qwen3.8-27B）适配说明 v1

> 适用对象：`daqing`（合成旅装备"四率"智能分析与决策支持平台）新增的"作战保障全过程"功能。
> 目标环境：**Ubuntu / ARM 架构 / 128GB 统一内存 / qwen3.8-27B 本地部署**。
> 前提约束：**不改仿真内核、不改整体架构**；新增能力全部位于 `src/brigsim/support/` 与前端 `js/support_flow.js`。

---

## 1. 目标环境与设计取舍

| 维度 | Windows 开发机（现状） | Ubuntu ARM 目标机 | 适配策略 |
|---|---|---|---|
| CPU 架构 | x86_64 | ARM64 (aarch64) | 纯 Python + 标准库优先；numpy/pandas 用官方 aarch64 wheel |
| GPU | RTX A6000 96GB（可选） | 无独立 CUDA GPU | 推演走 MesaKernel(CPU)；Warp 批量后端不可用（可选依赖，不装） |
| 内存 | — | 128GB 统一内存 | 27B 模型 Q4 量化约 16~20GB，余量充足；推演 MC 结果落盘而非常驻 |
| 大模型 | Qwen3-32B（vLLM :8000） | qwen3.8-27B（OpenAI 兼容接口） | 配置驱动，只改 `llm_config.json` 的 `base_url`/`model` |
| 推理时延 | GPU 毫秒~秒级 | CPU 单请求 20~60 秒 | 方案叙述拆 4 段、每段 ≤900 tokens；超时 180s；失败逐段降级 |

**关键红线**：油料、日常业务、通用消耗品只在**方案层**建模，不进仿真内核，因此四率权威数字（R²=0.705 / 加速比≈38 万倍 / Pareto 74 / 成本 0.165 等）在 ARM 上完全不变。

---

## 2. 软件部署步骤

### 2.1 系统与 Python

```bash
# Ubuntu 22.04/24.04 ARM64
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
python3 --version   # 建议 3.10 ~ 3.12
```

### 2.2 安装依赖

```bash
cd /opt/daqing
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt          # 若仓库提供
# 或按最小依赖安装（推演+Web 所需）：
pip install numpy pandas pyyaml pydantic rerun==0.39.0
```

> **ARM 注意**：
> - numpy/pandas/pydantic/rerun 均有 aarch64 wheel，无需源码编译。
> - `warp-lang` 依赖 CUDA，**ARM 上不要安装**；推演默认使用 MesaKernel(CPU)，不受影响。
> - rerun Web Viewer 走大屏同源反代，无需外网，断网可用。

### 2.3 启动大屏

```bash
cd /opt/daqing
.venv/bin/python -m brigsim.cli agent-serve --host 127.0.0.1 --port 8848
```

浏览器打开 `http://<工作站IP>:8848/index.html`（本机可用 127.0.0.1）。
顶栏新增 **「保障全过程」** 按钮，点击进入全屏三栏视图。

---

## 3. 本地大模型服务（qwen3.8-27B）

软件只依赖 **OpenAI 兼容 `/v1/chat/completions`**，不绑定具体推理框架。三选一：

### 方案 A：Ollama（ARM 支持最省心，推荐）

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.8:27b                 # 以实际模型 tag 为准
OLLAMA_HOST=127.0.0.1:11434 ollama serve &
```

配置：`base_url = http://127.0.0.1:11434/v1`，`model = qwen3.8:27b`。

### 方案 B：llama.cpp server（纯 CPU，可控量化）

```bash
# 用 aarch64 预编译或源码编译 llama.cpp（启用 NEON）
./server -m /models/qwen3.8-27b-Q4_K_M.gguf \
         --host 127.0.0.1 --port 8080 -c 8192 -t 32
```

配置：`base_url = http://127.0.0.1:8080/v1`，`model = qwen3.8-27b-Q4_K_M`。

### 方案 C：vLLM（若后续接入 GPU）

```bash
python -m vllm.entrypoints.openai.api_server \
  --model /models/qwen3.8-27b --host 127.0.0.1 --port 8000
```

配置：`base_url = http://127.0.0.1:8000/v1`，`model = /models/qwen3.8-27b`。

---

## 4. 软件侧配置

编辑项目根 `llm_config.json`：

```json
{
  "base_url": "http://127.0.0.1:11434/v1",
  "model": "qwen3.8:27b",
  "api_key": "EMPTY",
  "timeout": 180,
  "mode": "auto",
  "temperature": 0.3,
  "max_tokens": 1024
}
```

| 字段 | ARM 建议 | 说明 |
|---|---|---|
| `base_url` | 与所选框架一致 | **必须**以 `/v1` 结尾；只走回环，零外网 |
| `model` | 实际模型 ID/tag | 必须与服务 `/v1/models` 返回的 ID 完全一致 |
| `timeout` | **180**（默认已从 60 提升） | 27B CPU 单段最长可能 60s；留足余量 |
| `mode` | `auto`（演示）/ `real`（验收） | auto：模型未就绪自动降级模板叙述；real：强制真实调用，失败即报错 |
| `max_tokens` | 1024 | 方案每段 ≤900 tokens，1024 足够 |

环境变量可覆盖配置（便于现场临时切换）：
`BRIGSIM_LLM_BASE_URL` / `BRIGSIM_LLM_MODEL` / `BRIGSIM_LLM_TIMEOUT` / `BRIGSIM_LLM_MODE`。

验证模型连通：
```bash
curl http://127.0.0.1:11434/v1/models
curl http://127.0.0.1:8848/api/llm      # 应返回 available=true、model=qwen3.8…
```

---

## 5. 性能适配要点

### 5.1 多次推演（CPU）

- 单次 MesaKernel 推演耗时与 `duration_ticks × 单位数` 近似线性。ARM 上建议演示用 **2~8 次复制 × 60~96 ticks**；正式评估再提高到 12~30 次。
- 推演在**后台线程**执行，前端每 1.2 秒轮询 `/api/support/deduce?job_id=…`，不阻塞大屏。
- 战损清单落盘 `outputs/support/loss/`，含逐件 JSON + UTF-8-BOM CSV；最多保留 8 个并发任务。

### 5.2 大模型叙述（27B CPU）

| 机制 | 实现 | 收益 |
|---|---|---|
| 分段生成 | `support/plan.py` 的 `_LLM_GROUPS` 把 31 个叙述槽位拆成 4 段 | 单次上下文短，27B CPU 可在可接受时间内返回 |
| 数字隔离 | 数值槽位由清单/录入确定性填充，LLM **禁止输出数字**；响应含数字即判无效 | 杜绝模型编造战损数字，保证清单=方案=报告 |
| 逐段降级 | 任一段失败只回退该段为模板默认文字 | 模型抖动不会导致方案整体失败 |
| 连通性预检 | `is_available()` 探测 `/models`，失败直接降级 | 模型服务未启动时大屏仍可完整演示 |

### 5.3 内存预算（128GB 统一内存）

| 项目 | 估算 |
|---|---|
| qwen3.8-27B Q4_K_M | 16~20 GB |
| KV cache（8K 上下文） | 2~4 GB |
| Python + pandas + rerun WASM viewer | 2~4 GB |
| MC 推演中间结果 | < 2 GB（2~30 次复制） |
| **合计** | **约 25~30 GB，128GB 余量充足** |

### 5.4 可选加速

- 若后续接入 NVIDIA GPU，仅需把框架换成 vLLM 并改 `base_url`，代码零改动。
- ARM 上可用 `-t N` 限制 llama.cpp 线程数（建议留 2~4 核给 Web 服务），避免 CPU 争抢导致推演轮询卡顿。

---

## 6. 验收清单（在 ARM 工作站上逐项确认）

1. `curl /api/support/catalog` 返回 8 节点流程、26 类装备、6 类弹药、4 类油料、5 类保障车辆、7 类器材、5 类日常业务要素。
2. 顶栏点「保障全过程」→ 抽屉展开，8 节点流程条与三栏布局无重叠、无横向滚动。
3. 左栏录入装备/弹药/油料/车辆/器材/日常业务数量 → 点「保存录入并校验」→ 节点 ①② 变绿。
4. 中栏设推演次数 → 点「开始多次推演」→ 进度条推进 → 生成逐件战损清单；**对账提示为绿色**（清单派生战损率与内核四率差异 ≤ 1e-6）。
5. 右栏选模板、生成方案 → 数字一致性复核为绿（43 个数值槽位全部来自清单/录入）；叙述落款显示真实模型 `qwen3.8-27B`。
6. 「↓清单」导出 CSV、「↓方案」导出 Markdown，数字与界面一致。
7. 断网状态下全部功能可用（大模型走 127.0.0.1 回环，无外网依赖）。

---

## 7. 已知限制与待确认

- **模型 ID**：`qwen3.8-27B` 按用户给定名称配置；实际部署时请以 `/v1/models` 返回的 ID 为准修改 `llm_config.json`，否则 auto 模式会降级为模板叙述（不影响推演与清单）。
- **油料/日常业务不进内核**：只在方案层测算，不改变四率权威数字；若未来需要油料消耗影响机动，属于内核改造，须另行立项并重新标定全部权威数字。
- **模板替换**：正式制式模板放入 `templates/support/`，用 `{{slot_id}}` 标记槽位（字典见 `/api/support/catalog` 的 `slot_dict`）；未知槽位会在保存时报错，避免静默漏填。
- **清单口径**：战损率只认 K 级（与内核 `1-alive_n/n` 等价），M/F 单列"战斗损伤率"；蓝方战损属战果，单独记录但不计入我方战损率分母。
