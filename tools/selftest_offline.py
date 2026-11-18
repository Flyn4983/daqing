# -*- coding: utf-8 -*-
"""离线自检：迁移到新电脑后，一条命令确认软件可演示。

检查项：Python/依赖、6 个数据 JSON、LLM 配置、本地模型连通性（含降级判定）。
不修改任何文件；只读检查。退出码 0=可演示，1=有阻断项。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OK = "[OK]"
WARN = "[WARN]"
FAIL = "[FAIL]"
blockers = 0


def check(name: str, cond: bool, detail: str = "", hard: bool = True) -> None:
    global blockers
    tag = OK if cond else (FAIL if hard else WARN)
    print(f"  {tag} {name}" + (f" — {detail}" if detail else ""))
    if not cond and hard:
        blockers += 1


def main() -> int:
    print("=" * 60)
    print("  装备四率平台 · 离线自检")
    print("=" * 60)

    print("\n[1] Python 与核心依赖")
    check("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    for mod in ("numpy", "pandas", "joblib", "yaml"):
        try:
            __import__(mod)
            check(f"依赖 {mod}", True)
        except ImportError as e:
            check(f"依赖 {mod}", False, str(e))

    print("\n[2] 前端数据文件（6 个 JSON）")
    data_dir = ROOT / "src" / "brigsim" / "agent" / "web" / "data"
    for f in ("timeseries", "distribution", "sensitivity", "surrogate", "pareto", "manifest"):
        p = data_dir / f"{f}.json"
        check(f"{f}.json", p.is_file(), f"{p.stat().st_size}B" if p.is_file() else "缺失")

    print("\n[3] 本地大模型配置")
    cfg_path = ROOT / "llm_config.json"
    check("llm_config.json", cfg_path.is_file(), "缺失则用内置默认")
    try:
        from brigsim.agent.llm_client import LocalLLMClient, LLMConfig
        cfg = LLMConfig.load()
        print(f"  {OK} 连接配置: mode={cfg.mode}  base_url={cfg.base_url}  model={cfg.model}")
        client = LocalLLMClient(cfg)
        avail = client.is_available()
        if cfg.mode == "mock":
            print(f"  {WARN} 当前为 mock 模式（模板兜底，演示数字仍为真实计算值）")
        elif avail:
            print(f"  {OK} 本地大模型已连通（真实调用模式可用）")
        else:
            if cfg.mode == "real":
                check("本地大模型连通", False, client.status().get("last_error", cfg.base_url))
            else:
                print(f"  {WARN} 本地大模型未连通，auto 模式将自动降级模板（仍可演示）")
    except Exception as e:  # noqa: BLE001
        check("LLM 适配层可加载", False, str(e))

    print("\n" + "=" * 60)
    if blockers == 0:
        print("  自检通过：软件可离线演示。运行 start_dashboard.bat 启动。")
    else:
        print(f"  自检发现 {blockers} 项阻断问题，请先修复。")
    print("=" * 60)
    return 0 if blockers == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
