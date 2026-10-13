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


@app.command()
def replay(
    scenario: str = typer.Argument(..., help="想定名称或 YAML 路径"),
    seed: int = typer.Option(20261013, "--seed", help="随机种子（指定某次复制）"),
    rrd: str = typer.Option("outputs/replay.rrd", "--rrd", help="录制文件输出路径"),
    show: bool = typer.Option(True, "--show/--no-show", help="立即弹出 rerun 窗口"),
) -> None:
    """运行一次想定并生成 3D 态势回放与指标曲线。"""
    from .experiment.runner import run_once
    from .viz.rerun_view import visualize

    sc = YamlFileProvider().load_scenario(scenario)
    from .combat.mesa_kernel import MesaKernel

    _, kernel = run_once(sc, seed=seed, kernel_cls=MesaKernel)
    snapshots, engagements = kernel.log.to_frames()

    import sys

    rrd_path = Path(rrd)
    rrd_path.parent.mkdir(parents=True, exist_ok=True)
    # rerun 0.39 的 pip 包不支持 SDK 内 spawn，统一录制后调用同环境 rerun.exe 打开
    visualize(
        snapshots,
        engagements,
        scenario=sc,
        terrain=kernel.terrain,
        rrd_path=rrd_path,
        spawn=False,
    )
    if show:
        viewer = Path(sys.executable).parent / "rerun.exe"
        if viewer.exists():
            import subprocess

            subprocess.Popen([str(viewer), str(rrd_path)])  # noqa: S603
            typer.echo(f"回放录制完成: {rrd_path}，已打开 viewer")
        else:
            typer.echo(f"回放录制完成: {rrd_path}（未找到 rerun.exe，请手动打开）")
    else:
        typer.echo(f"回放录制完成: {rrd_path}")


@app.command()
def sensitivity(
    scenario: str = typer.Argument(..., help="想定名称或 YAML 路径"),
    trajectories: int = typer.Option(12, "--trajectories", "-t", help="Morris 轨道数"),
    reps: int = typer.Option(12, "--reps-per-point", help="每个设计点的蒙特卡洛复制数"),
    seed: int = typer.Option(20261013, "--seed", help="基础随机种子"),
    out: str | None = typer.Option(None, "--out", help="结果 CSV 输出目录"),
) -> None:
    """Morris 一次一列法灵敏度扫描，输出各参数对指标的 μ* 排序。"""
    from .experiment.sensitivity import run_sensitivity

    sc = YamlFileProvider().load_scenario(scenario)
    typer.echo(
        f"想定: {sc.name}    轨道: {trajectories}    每点复制: {reps}"
        f"（共 {trajectories * 6} 个设计点 × {reps} 次模拟）"
    )
    df = run_sensitivity(sc, trajectories=trajectories, reps_per_point=reps, base_seed=seed)
    for metric, grp in df.groupby("metric", sort=False):
        typer.echo(f"\n{metric}（按 μ* 排序，μ* 越大越敏感，σ 大表示非线性/交互）：")
        for _, r in grp.iterrows():
            typer.echo(f"  {r['param']:>13}: μ*={r['mu_star']:.3f}  σ={r['sigma']:.3f}")

    if out:
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "sensitivity_morris.csv"
        df.to_csv(path, index=False)
        typer.echo(f"\n结果已写出: {path}")


if __name__ == "__main__":
    app()
