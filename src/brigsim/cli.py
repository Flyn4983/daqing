"""L4 命令行入口：brigsim run <scenario> --runs N。"""

from __future__ import annotations

from pathlib import Path

import typer

from .data.provider import YamlFileProvider
from .experiment.metrics import summarize
from .experiment.runner import run_replications

app = typer.Typer(add_completion=False, help="合成旅作战任务仿真")


@app.callback()
def _main() -> None:
    """brigsim 命令行工具。"""


@app.command()
def run(
    scenario: str = typer.Argument(..., help="想定名称或 YAML 路径"),
    runs: int = typer.Option(100, "--runs", "-n", help="蒙特卡洛复制次数"),
    seed: int = typer.Option(20261013, "--seed", help="基础随机种子"),
    out: str | None = typer.Option(None, "--out", help="事件日志输出目录（Parquet）"),
) -> None:
    """加载想定，批量模拟，打印三大指标汇总。"""
    sc = YamlFileProvider().load_scenario(scenario)
    typer.echo(f"想定: {sc.name}    复制次数: {runs}")

    rows, events = run_replications(sc, n_runs=runs, base_seed=seed)
    summary = summarize(rows)
    typer.echo("\n指标（均值 ± 95%CI 半宽）：")
    for name, r in summary.iterrows():
        typer.echo(f"  {name:>15}: {r['mean']:.3f} ± {r['ci95']:.3f}")

    if out:
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        snapshots, engagements = events
        snapshots.to_parquet(out_dir / "snapshots.parquet", index=False)
        engagements.to_parquet(out_dir / "engagements.parquet", index=False)
        typer.echo(f"\n事件日志已写出: {out_dir}")


if __name__ == "__main__":
    app()
