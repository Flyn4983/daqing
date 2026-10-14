"""L4 命令行入口：brigsim run <scenario> --runs N。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
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
    kernel: str = typer.Option("mesa", "--kernel", help="mesa（智能体）或 warp（向量化）"),
    device: str = typer.Option(
        "cuda:0", "--device", help="warp 内核设备：cuda:0（默认，不可用自动回退）或 cpu"
    ),
    batch: bool = typer.Option(False, "--batch", help="warp env 维批量复制（M4 切片 2）"),
    batch_size: int = typer.Option(
        512, "--batch-size", help="批量模式每批环境数（实测 E=2048 <120MB）"
    ),
    out: str | None = typer.Option(None, "--out", help="事件日志输出目录（Parquet）"),
) -> None:
    """加载想定，批量模拟，打印三大指标汇总。"""
    sc = YamlFileProvider().load_scenario(scenario)
    if kernel == "mesa":
        from .combat.mesa_kernel import MesaKernel

        kernel_cls = MesaKernel
        batch_device = None
    elif kernel == "warp":
        from .combat.warp_kernel import WarpKernel

        kernel_cls = lambda scenario, seed=0: WarpKernel(scenario, seed, device=device)  # noqa: E731
        batch_device = device if batch else None
    else:
        raise typer.BadParameter(f"未知内核 {kernel}（可选 mesa/warp）")
    mode = "warp-batch" if batch else kernel
    typer.echo(f"想定: {sc.name}    内核: {mode}    复制次数: {runs}")

    rows, events = run_replications(
        sc, n_runs=runs, base_seed=seed, kernel_cls=kernel_cls,
        batch_device=batch_device, batch_size=batch_size,
    )
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
        supply=kernel.log.supply_frame(),
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
    method: str = typer.Option("morris", "--method", help="morris（筛选）或 sobol（方差分解）"),
    trajectories: int = typer.Option(12, "--trajectories", "-t", help="Morris 轨道数"),
    sobol_n: int = typer.Option(16, "--sobol-n", help="Sobol 基准样本数 N（设计点 = N×(D+2)）"),
    reps: int = typer.Option(12, "--reps-per-point", help="每个设计点的蒙特卡洛复制数"),
    seed: int = typer.Option(20261013, "--seed", help="基础随机种子"),
    kernel: str = typer.Option("mesa", "--kernel", help="mesa（智能体）或 warp（向量化）"),
    device: str = typer.Option("cuda:0", "--device", help="warp 内核设备：cuda:0 或 cpu"),
    batch: bool = typer.Option(
        False, "--batch", help="warp 设计点×复制 env 维全批量（需 --kernel warp）"
    ),
    batch_size: int = typer.Option(
        512, "--batch-size", help="每批环境数（实测 E=2048 <120MB）"
    ),
    out: str | None = typer.Option(None, "--out", help="结果 CSV 输出目录"),
) -> None:
    """灵敏度扫描：Morris（μ* 排序）或 Sobol（S1/ST 方差贡献占比）。"""
    from .experiment.sensitivity import PROBLEM, run_sensitivity, run_sobol

    sc = YamlFileProvider().load_scenario(scenario)
    if kernel == "mesa":
        from .combat.mesa_kernel import MesaKernel

        kernel_cls = MesaKernel
        batch_device = None
    elif kernel == "warp":
        from .combat.warp_kernel import WarpKernel

        kernel_cls = lambda scenario, seed=0: WarpKernel(scenario, seed, device=device)  # noqa: E731
        batch_device = device if batch else None
    else:
        raise typer.BadParameter(f"未知内核 {kernel}（可选 mesa/warp）")
    d = PROBLEM["num_vars"]
    if method == "morris":
        typer.echo(
            f"想定: {sc.name}  方法: Morris    轨道: {trajectories}    每点复制: {reps}"
            f"（共 {trajectories * (d + 1)} 个设计点 × {reps} 次模拟）"
        )
        df = run_sensitivity(
            scenario=sc, trajectories=trajectories, reps_per_point=reps,
            base_seed=seed, kernel_cls=kernel_cls,
            batch_device=batch_device, batch_size=batch_size,
        )
        for metric, grp in df.groupby("metric", sort=False):
            typer.echo(f"\n{metric}（按 μ* 排序，μ* 越大越敏感，σ 大表示非线性/交互）：")
            for _, r in grp.iterrows():
                typer.echo(f"  {r['param']:>13}: μ*={r['mu_star']:.3f}  σ={r['sigma']:.3f}")
        csv_name = "sensitivity_morris.csv"
    elif method == "sobol":
        n_points = sobol_n * (d + 2)
        typer.echo(
            f"想定: {sc.name}  方法: Sobol    N: {sobol_n}    每点复制: {reps}"
            f"（共 {n_points} 个设计点 × {reps} 次模拟）"
        )
        df = run_sobol(
            scenario=sc, n_base=sobol_n, reps_per_point=reps,
            base_seed=seed, kernel_cls=kernel_cls,
            batch_device=batch_device, batch_size=batch_size,
        )
        for metric, grp in df.groupby("metric", sort=False):
            typer.echo(f"\n{metric}（按 ST 总效应排序；ST=含交互总贡献，S1=一阶主效应）：")
            for _, r in grp.iterrows():
                typer.echo(f"  {r['param']:>13}: S1={r['S1']:+.3f}  ST={r['ST']:.3f}")
        csv_name = "sensitivity_sobol.csv"
    else:
        raise typer.BadParameter(f"未知方法 {method}（可选 morris/sobol）")

    if out:
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / csv_name
        df.to_csv(path, index=False)
        typer.echo(f"\n结果已写出: {path}")


@app.command("surrogate-gen")
def surrogate_gen(
    scenario: str = typer.Argument(..., help="想定名称或 YAML 路径（代理模型的标定基线）"),
    points: int = typer.Option(512, "--points", help="训练设计点数（LHS 抽样）"),
    holdout: int = typer.Option(96, "--holdout", help="留出测试点数（均匀随机）"),
    reps: int = typer.Option(8, "--reps", help="训练点每点蒙特卡洛复制数（CRN）"),
    reps_holdout: int = typer.Option(12, "--reps-holdout", help="留出点复制数（标签更稳）"),
    seed: int = typer.Option(20261014, "--seed", help="基础随机种子"),
    kernel: str = typer.Option("warp", "--kernel", help="warp（GPU 批量，推荐）或 mesa"),
    device: str = typer.Option("cuda:0", "--device", help="warp 设备：cuda:0 或 cpu"),
    batch_size: int = typer.Option(512, "--batch-size", help="每批环境数"),
    out: str = typer.Option(..., "--out", help="数据集 Parquet 输出路径"),
) -> None:
    """C3：在保障旋钮×情景因子空间批量推演，生成代理模型训练数据集。"""
    from .surrogate.dataset import build_dataset

    sc = YamlFileProvider().load_scenario(scenario)
    dev = None if kernel == "mesa" else device
    typer.echo(
        f"想定: {sc.name}  特征空间: 保障旋钮6 + 情景因子4"
        f"  训练: {points}点×{reps}复制  留出: {holdout}点×{reps_holdout}复制"
    )
    df = build_dataset(
        sc, n_train=points, n_holdout=holdout, reps_train=reps, reps_holdout=reps_holdout,
        base_seed=seed, device=dev, batch_size=batch_size, echo=True,
    )
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    typer.echo(f"\n数据集已写出: {out_path}（{len(df)} 行 × {df.shape[1]} 列）")


@app.command("surrogate-train")
def surrogate_train(
    data: str = typer.Argument(..., help="surrogate-gen 产出的 Parquet"),
    model_out: str = typer.Option(..., "--model-out", help="模型 joblib 输出路径"),
    scenario: str = typer.Option("", "--scenario", help="基线想定名（记入模型元数据）"),
) -> None:
    """C3：训练轻量 MLP 四率代理模型，报告留出集精度与'秒级预测'加速比。"""
    from .surrogate.design import FEATURE_NAMES, TARGETS
    from .surrogate.model import SurrogateModel, TrainMetadata

    df = pd.read_parquet(data)
    train = df[df["split"] == "train"]
    hold = df[df["split"] == "holdout"]
    typer.echo(f"训练点 {len(train)} / 留出点 {len(hold)}")

    model = SurrogateModel()
    model.fit(
        train[FEATURE_NAMES],
        train[TARGETS],
        metadata=TrainMetadata(
            scenario=scenario or Path(data).stem,
            n_train=len(train),
            n_holdout=len(hold),
            reps_train=int(train['n_reps'].iloc[0]),
            reps_holdout=int(hold['n_reps'].iloc[0]),
            train_seconds=0.0,
        ),
    )
    report = model.evaluate(hold[TARGETS + [f"{t}_std" for t in TARGETS]], model.predict(hold[FEATURE_NAMES]))
    typer.echo("\n留出集精度（R² 越接近 1 越好；MAE 为绝对误差；噪声地板=标签蒙特卡洛波动）：")
    for _, r in report.iterrows():
        typer.echo(
            f"  {r['target']:>22}: R²={r['r2']:+.3f}  MAE={r['mae']:.3f}  "
            f"RMSE={r['rmse']:.3f}  噪声≈{r['label_noise_mean']:.3f}"
        )
    typer.echo(f"  {'平均':>22}: R²={report['r2'].mean():+.3f}")

    pred_seconds, n_pred = model.predict_benchmark(10000)
    typer.echo(f"\n推理速度: {n_pred} 个保障方案预测耗时 {pred_seconds*1000:.1f} ms"
               f"（约 {pred_seconds/n_pred*1000:.3f} ms/方案）")

    out_path = Path(model_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out_path))
    typer.echo(f"模型已保存: {out_path}")


@app.command("optimize")
def optimize_cmd(
    model: str = typer.Argument(..., help="代理模型 joblib（surrogate-train 产出）"),
    scenario: str = typer.Option("example_brigade", "--scenario", help="基线想定（取任务时长；--validate 时回仿真）"),
    pop: int = typer.Option(80, "--pop", help="NSGA-II 种群规模"),
    gens: int = typer.Option(60, "--gens", help="进化代数"),
    min_readiness: float = typer.Option(0.0, "--min-readiness", help="红方完好率保底约束（0=不启用）"),
    validate: bool = typer.Option(False, "--validate", help="对代表方案回蒙特卡洛仿真复验"),
    reps: int = typer.Option(16, "--reps", help="复验复制次数"),
    kernel: str = typer.Option("warp", "--kernel", help="复验内核 warp/mesa"),
    device: str = typer.Option("cuda:0", "--device", help="warp 设备"),
    out: str | None = typer.Option(None, "--out", help="Pareto 方案表 CSV 输出路径"),
) -> None:
    """C4：代理驱动 NSGA-II 多目标保障决策优化（成功率↑ 红战损↓ 资源成本↓）。"""
    from .optimize.nsga import evaluate_baseline, optimize_support
    from .optimize.problem import ScenarioContext
    from .optimize.validation import representative_plans, validate_plans
    from .surrogate.model import SurrogateModel

    sc = YamlFileProvider().load_scenario(scenario)
    surrogate = SurrogateModel.load(model)
    ctx = ScenarioContext.baseline()

    pareto = optimize_support(
        surrogate, duration_ticks=sc.mission.duration_ticks, ctx=ctx,
        pop_size=pop, n_gen=gens, min_readiness_red=min_readiness,
    )
    base = evaluate_baseline(surrogate, sc.mission.duration_ticks, ctx)
    knee = pareto[pareto["rank_tag"] == "knee"].iloc[0]

    typer.echo(f"NSGA-II 完成：{pop} 种群 × {gens} 代 → {len(pareto)} 个 Pareto 非支配方案")
    typer.echo("\n基线方案（v3 标定）与 knee 推荐方案对比（代理预测）：")
    typer.echo(
        f"  {'':>16}{'抢修组':>6}{'备件':>5}{'周期':>5}{'批量':>5}"
        f"{'成功率':>8}{'红战损':>8}{'红完好':>8}{'成本':>7}"
    )
    for label, r in (("基线", base), ("knee推荐", knee)):
        typer.echo(
            f"  {label:>16}{int(r['red_repair_teams']):>6}{int(r['red_spare_stock']):>5}"
            f"{int(r['resupply_interval_ticks']):>5}{int(r['resupply_qty']):>5}"
            f"{r['success']:>8.3f}{r['attrition_red']:>8.3f}"
            f"{r['readiness_red']:>8.3f}{r['cost']:>7.3f}"
        )

    if validate:
        dev = None if kernel == "mesa" else device
        reps_df = representative_plans(pareto)
        cmp_df = validate_plans(sc, reps_df, ctx=ctx, reps=reps, device=dev)
        typer.echo(f"\n代表方案仿真复验（{reps} 次蒙特卡洛；代理 vs 仿真均值）：")
        for plan in reps_df["rank_tag"].replace("", "pareto"):
            sub = cmp_df[cmp_df["plan"] == plan]
            if sub.empty:
                continue
            mae = sub["abs_error"].mean()
            typer.echo(f"  [{plan}] 九指标平均绝对误差 {mae:.3f}")
            for _, r in sub.iterrows():
                if r["metric"] in ("success", "attrition_red", "readiness_red"):
                    typer.echo(
                        f"    {r['metric']:>16}: 代理 {r['surrogate']:.3f}"
                        f" / 仿真 {r['simulation']:.3f} ± {r['sim_std']:.3f}"
                    )
        if out:
            cmp_path = Path(out).with_name(Path(out).stem + "_validation.csv")
            cmp_df.to_csv(cmp_path, index=False)
            typer.echo(f"复验明细已写出: {cmp_path}")

    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pareto.to_csv(out_path, index=False)
        typer.echo(f"\nPareto 方案表已写出: {out_path}")


if __name__ == "__main__":
    app()
