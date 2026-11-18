@echo off
chcp 65001 >nul
title 智演先知·保障先行 - 装备四率智能分析平台
echo ============================================================
echo   装备"四率"智能分析与决策支持平台  启动脚本
echo   软件与本地大模型同机部署，走 127.0.0.1 回环，零外网
echo ============================================================
echo.

cd /d "%~dp0"

echo [1/3] 离线自检...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\selftest_offline.py
) else (
  echo   [警告] 未找到 .venv，尝试系统 Python
  python tools\selftest_offline.py
)
echo.

echo [2/3] 本地大模型连接方式（配置见 llm_config.json）：
echo   auto  = 探测到模型则真实调用，否则自动降级模板（演示推荐）
echo   real  = 强制真实调用；mock = 强制模板
echo.

echo [3/3] 启动指挥大屏  http://127.0.0.1:8848/login.html
echo   3D 态势回放随服务一同挂载（同源代理，零外网）
echo   （按 Ctrl+C 停止服务）
echo.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m brigsim.cli agent-serve --host 127.0.0.1 --port 8848
) else (
  python -m brigsim.cli agent-serve --host 127.0.0.1 --port 8848
)
pause
