# 装备四率平台 · 启动与大屏/独立 rerun 命令手册

> 适用目录：`C:\Users\flyn4\Desktop\daqing`
> 运行环境：项目自带的 `.venv`（已 editable 安装 brigsim，rerun.exe 随 venv 可用）
> 约定：下文 `python` = `.venv/Scripts/python.exe`，所有命令在该 venv 下执行。

---

## 〇、前置：进入项目并激活 venv

打开 **PowerShell** 或 **Git Bash**，切到项目根目录：

```bash
cd /c/Users/flyn4/Desktop/daqing
```

下文命令里的 `python` 一律用项目 venv 的绝对路径，避免环境变量污染：

```bash
# 等价于把 .venv/Scripts 放进 PATH，下面统一用绝对写法
PY=./.venv/Scripts/python.exe
```

---

## 一、指挥大屏软件（Web 端）启动

### 1. 启动服务（前台，终端保持打开）

```bash
cd /c/Users/flyn4/Desktop/daqing
export PYTHONPATH= BASH_ENV=
./.venv/Scripts/python.exe -m brigsim.cli agent-serve --port 8848
```

看到 `指挥大屏已启动：http://127.0.0.1:8848` 即成功。
占用端口：**8848**（大屏）/ **9090**（内嵌 3D viewer）/ **9876**（gRPC）。

> 想在后台常驻：用工具「后台运行」启动同一条命令即可；或 `nohup ... &`。
> 改完源码后**重启服务即生效**（editable 安装，无需重装）。

### 2. 进入界面

1. 浏览器打开 `http://127.0.0.1:8848/`（自动跳登录页）
2. 登录：**用户名 `admin` ／ 密码 `123456`**
3. 模式选择页点卡片：
   - 「历史数据回放」→ 回放模式
   - 「实时仿真推演」→ 实时推演模式

或直接用固定 URL 跳过选择页：
- 回放：`http://127.0.0.1:8848/index.html?mode=replay`
- 推演：`http://127.0.0.1:8848/index.html?mode=live`

> ⚠️ 每次更新后请 **Ctrl+F5 强刷**，避免浏览器缓存旧 JS（当前版本 `main.js?v=c912`）。

---

## 二、独立 rerun 查看器（与大屏解耦，推荐用于评审/演示）

脚本：`tools/rerun_window.py`，三种子命令。**默认用浏览器端「网页查看器」**（`--viewer web`），因为它与本机大屏 dashboard 是同一套 WebGPU/WebGL 渲染、已验证可正常显示；而本机原生 rerun 窗口（GPU/wgpu 后端）在部分机器上会渲染成**黑屏**。

| 子命令 | 作用 |
|---|---|
| `replay <场景>` | 运行一次想定 → 生成/命中 rrd → rerun 查看器打开（可拖时间轴回放） |
| `live <场景>` | 实时推演：逐 tick 流式推给 rerun 查看器 |
| `open <rrd路径>` | 直接打开一个已有 rrd 文件 |

> 通用选项 `--viewer {web|native}`（默认 `web`）：
> - **web（默认）**：rerun 网页查看器，浏览器自动打开一个标签页显示 3D 战场，
>   终端会打印访问地址 `http://127.0.0.1:<web端口>?url=rerun+http://localhost:<gRPC端口>/proxy`，
>   没自动弹出也可手动粘贴。推演/回放结束后**关闭浏览器标签页，再回到终端按回车**关掉后台服务。
> - **native**：启动原生 GUI 窗口（仅在你机器上原生渲染正常时用；否则会黑屏）。
>
> 端口说明：
> - **回放 / 打开 rrd / live**：`rerun.exe` 默认会抢 **9876/9090**，与大屏冲突；脚本已自动探测**空闲端口**规避。gRPC 与 web 端口均走空闲端口，彻底避开大屏 9876/9090。
> - **实时推演（live）**：脚本显式以「服务器模式」拉起 `rerun.exe`（它自己托管 gRPC 服务器，未被本机禁用），SDK 用 `rr.connect_grpc(url="rerun+http://127.0.0.1:<空闲gRPC端口>/proxy")` 作为客户端连上去，不依赖被禁用的 `serve_grpc` / `spawn=True`（那套会因内嵌服务器被禁用而让窗口起不来）。
> - **大屏后台运行时也可同时开独立查看器，互不干扰。**

### 通用写法

```bash
cd /c/Users/flyn4/Desktop/daqing
./.venv/Scripts/python.exe tools/rerun_window.py <子命令> <参数>
```

---

### 2.1 回放（replay）—— 全部 7 个场景

预生成 rrd 已就绪（命中即秒开，无需重算）：

```bash
# 合成旅 · 标准攻坚（基线示例）
./.venv/Scripts/python.exe tools/rerun_window.py replay example_brigade

# 重型旅 · 峡谷晴日攻坚
./.venv/Scripts/python.exe tools/rerun_window.py replay brigade_canyon_clear

# 重型旅 · 平原浓雾突击
./.venv/Scripts/python.exe tools/rerun_window.py replay brigade_plain_fog

# 重型旅 · 丘陵筑垒防御
./.venv/Scripts/python.exe tools/rerun_window.py replay brigade_rolling_fortified

# 中型旅合成营
./.venv/Scripts/python.exe tools/rerun_window.py replay medium_brigade

# 连级合成战斗（company）
./.venv/Scripts/python.exe tools/rerun_window.py replay example_company

# 合成营战术演练（battalion）
./.venv/Scripts/python.exe tools/rerun_window.py replay example_battalion
```

可选参数：
- `--rebuild`：忽略预生成 rrd，强制重新构建（重新跑一次仿真再开窗）
- `--seed N`：指定随机种子（默认 `20261013`）
- `--no-models`：用简单图元代替 GLB 模型，更轻量

```bash
# 例：强制重建 example_brigade 的回放
./.venv/Scripts/python.exe tools/rerun_window.py replay example_brigade --rebuild
```

---

### 2.2 实时推演（live）—— 全部 7 个场景

逐 tick 流式推演，独立 rerun 窗口实时刷新（地形/红蓝单位/射界扇面/作用范围圈/气象）：

```bash
# 合成旅 · 标准攻坚
./.venv/Scripts/python.exe tools/rerun_window.py live example_brigade

# 重型旅 · 峡谷晴日攻坚
./.venv/Scripts/python.exe tools/rerun_window.py live brigade_canyon_clear

# 重型旅 · 平原浓雾突击
./.venv/Scripts/python.exe tools/rerun_window.py live brigade_plain_fog

# 重型旅 · 丘陵筑垒防御
./.venv/Scripts/python.exe tools/rerun_window.py live brigade_rolling_fortified

# 中型旅合成营
./.venv/Scripts/python.exe tools/rerun_window.py live medium_brigade

# 连级合成战斗
./.venv/Scripts/python.exe tools/rerun_window.py live example_company

# 合成营战术演练
./.venv/Scripts/python.exe tools/rerun_window.py live example_battalion
```

可选参数：
- `--tick-pause 0.12`：每个 tick 暂停秒数（默认 0.12，便于肉眼观看；设 `0` 全速）
- `--max-ticks N`：限制推演 tick 数（默认 0 = 全部），快速预览用
- `--seed N`：随机种子（默认 `20261014`）

```bash
# 例：全速、只看前 20 个 tick 的实时推演预览
./.venv/Scripts/python.exe tools/rerun_window.py live example_brigade --tick-pause 0 --max-ticks 20
```

> 推演结束后窗口保持打开，按 **Ctrl+C** 或回车关闭。

---

### 2.3 直接打开已有 rrd（open）

```bash
./.venv/Scripts/python.exe tools/rerun_window.py open outputs/dash/replay_example_brigade.rrd
```

也可打开自己用 `--out` 生成的独立 rrd：

```bash
./.venv/Scripts/python.exe tools/rerun_window.py replay example_company --out outputs/standalone/my_run.rrd
./.venv/Scripts/python.exe tools/rerun_window.py open outputs/standalone/my_run.rrd
```

---

## 三、两种可视化方式对比与建议

| 用途 | 推荐方式 | 命令 |
|---|---|---|
| 汇报/评审，需可拖拽时间轴、定格讲解 | **独立 rerun 窗口（replay）** | `replay <场景>` |
| 展示“实时生成、逐帧刷新”的动态过程 | **独立 rerun 窗口（live）** | `live <场景>` |
| 大屏一体化操作（2D 图表 + 内嵌 3D） | **Web 大屏** | 浏览器开 `agent-serve` 地址 |

- 独立 rerun 窗口与大屏软件**完全解耦**：只复用仿真库与绘制函数，不改动 `agent/web` 任何代码。
- 大屏后台常驻时，仍可并行开任意多个独立 rerun 窗口（端口自动错开）。

---

## 四、常见问题

1. **独立窗口没弹出来 / 报 `os error 10048`（端口被占用）**
   → 旧版脚本写死端口导致；当前已改为自动空闲端口，重跑上面的命令即可。
2. **原生 rerun 窗口是黑屏**
   → 本机原生查看器的 GPU(wgpu/Vulkan) 后端在该机器上渲染 3D 失败。**改用默认 `web` 模式**（浏览器网页查看器，与大屏 dashboard 同套 WebGPU 渲染、已验证正常）。若仍要原生窗口，可试 `--viewer native`，但黑屏即代表该机器原生后端不可用。
3. **rerun 提示“Rerun is disabled”**
   → 这是 SDK 内嵌 `serve_grpc` 被本机禁用所致；脚本已改用「查看器托管 gRPC + SDK 客户端连接」规避，无需理会该提示。
4. **网页查看器没自动弹出**
   → 复制终端里打印的 `http://127.0.0.1:<web端口>?url=rerun+http://localhost:<gRPC端口>/proxy` 到浏览器打开即可。
5. **想换端口**：脚本自动分配空闲 gRPC/web 端口，无需手动指定。
6. **大屏刷新后界面没变**：`Ctrl+F5` 强刷；确认服务已重启（editable 安装改源码要重启才生效）。
