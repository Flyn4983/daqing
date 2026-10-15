"""C7-a2 末段卡点诊断（一次性，粗标定辅助）。

三档 v4 候选 success 全 0、全部停在 phase=2：红方冲不进 [9000,5000] 500m。
本脚本逐 tick 记录红方矛头进度、阶段切换、死亡时刻，终局盘点目标区蓝方火力，
定位是"时间不够 / 被纵深火力钉住 / 领头单位恰好死在末段"中的哪一种。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from brigsim.combat.mesa_kernel import MesaKernel  # noqa: E402
from brigsim.data.provider import YamlFileProvider  # noqa: E402

OBJECTIVE = np.array([9000.0, 5000.0])
RED, BLUE = 0, 1


def run_diag(label: str, sc, seed: int = 100) -> None:
    k = MesaKernel(sc, seed=seed)
    s = k.state
    roles = np.asarray(s.role, dtype=object)
    mover_mask0 = (s.side == RED) & np.isin(roles, ["assault", "recon", "engineer"])
    lead_hist: list[tuple[int, float, int, int]] = []
    death_tick = {int(i): None for i in np.where(mover_mask0)[0]}
    alive_prev = s.alive.copy()
    last_phase = 0
    phase_enter = {0: 0}
    while not k.finished:
        k.step()
        t = int(s.tick)
        for i in death_tick:
            if alive_prev[i] and not s.alive[i]:
                death_tick[i] = t
        alive_prev = s.alive.copy()
        m = s.alive & mover_mask0
        if m.any():
            lead_x = float(s.x[m].max())
            d = np.hypot(s.x[m] - OBJECTIVE[0], s.y[m] - OBJECTIVE[1])
            n700 = int((d <= 700).sum())
            n1000 = int((d <= 1000).sum())
            lead_hist.append((t, lead_x, n700, n1000))
        if int(s.phase) != last_phase:
            last_phase = int(s.phase)
            phase_enter[last_phase] = t

    print(f"\n{'='*70}\n{label}  success={k.mission_success()}  终局 tick={s.tick}")
    print(f"阶段进入时刻: {phase_enter}")
    print("矛头进度（每 6 tick）: tick  lead_x  n<=700m  n<=1000m")
    for row in lead_hist[::6]:
        print(f"  t={row[0]:3d}  x={row[1]:7.0f}  {row[2]:3d}  {row[3]:3d}")
    if lead_hist:
        print(f"  t={lead_hist[-1][0]:3d}  x={lead_hist[-1][1]:7.0f}  "
              f"{lead_hist[-1][2]:3d}  {lead_hist[-1][3]:3d}  (终局)")

    # 终局目标区蓝方火力盘点
    blue = s.alive & (s.side == BLUE)
    db = np.hypot(s.x[blue] - OBJECTIVE[0], s.y[blue] - OBJECTIVE[1])
    bidx = np.where(blue)[0]
    order = np.argsort(db)
    print("\n终局蓝方存活单位（按距目标，前 20）:")
    print("  uid            role          fire      rng    dug   x     y    dist")
    for j in order[:20]:
        i = int(bidx[j])
        print(f"  {str(s.unit_ids[i]):14s} {str(s.role[i]):13s} "
              f"{str(s.fire_mode[i]):9s} {s.weapon_range[i]:6.0f} "
              f"{str(bool(s.dug_in[i])):5s} {s.x[i]:6.0f} {s.y[i]:6.0f} {db[j]:6.0f}")

    # 红方突击单位结局
    print("\n红方直接机动单位结局:")
    mi = np.where(mover_mask0)[0]
    rows = []
    for i in mi:
        d = np.hypot(s.x[i] - OBJECTIVE[0], s.y[i] - OBJECTIVE[1]) if s.alive[i] else None
        rows.append((str(s.unit_ids[i]), str(s.role[i]), bool(s.alive[i]),
                     death_tick[int(i)], d,
                     bool(s.under_repair[i]) if s.alive[i] else None,
                     int(s.damage_grade[i]) if s.alive[i] else None,
                     float(s.x[i]) if s.alive[i] else None))
    alive_rows = [r for r in rows if r[2]]
    dead_rows = [r for r in rows if not r[2]]
    print(f"  存活 {len(alive_rows)} / 阵亡 {len(dead_rows)}（共 {len(rows)}）")
    for r in sorted(alive_rows, key=lambda z: z[4])[:10]:
        print(f"    活 {r[0]:14s} {r[1]:9s} d={r[4]:6.0f} repair={r[5]} grade={r[6]} x={r[7]:.0f}")
    # 阵亡者死亡时刻分布
    dticks = sorted(t for t in (r[3] for r in dead_rows) if t is not None)
    if dticks:
        bins = [0, 24, 48, 60, 72, 100]
        hist = [sum(1 for t in dticks if a < t <= b) for a, b in zip(bins, bins[1:])]
        print(f"  机动单位阵亡时刻分布 (0-24/25-48/49-60/61-72/>72): {hist}")
        print(f"  最后 8 个阵亡时刻: {dticks[-8:]}")


def run_fire_diag(label: str, sc, seed: int = 100) -> None:
    k = MesaKernel(sc, seed=seed)
    s0 = k.state
    init_role = {str(s0.unit_ids[i]): str(s0.role[i]) for i in range(s0.n)}
    init_side = {str(s0.unit_ids[i]): int(s0.side[i]) for i in range(s0.n)}
    x0_arr = s0.x.copy()
    ammo0 = s0.ammo.copy()
    init_x = {str(s0.unit_ids[i]): float(x0_arr[i]) for i in range(s0.n)}
    while not k.finished:
        k.step()
    s = k.state
    snaps, eng = k.log.to_frames()
    eff = k.log.effect_frame()
    air = k.log.air_frame()

    print(f"\n{'#'*70}\n# {label} — 火力账")
    if eng.empty:
        print("无交战记录")
        return
    eng["tgt_side"] = eng["target"].map(init_side)
    eng["tgt_role"] = eng["target"].map(init_role)
    eng["tgt_x0"] = eng["target"].map(init_x)
    kills = eng[eng["killed"]]
    print("\n各方被击毁数（按武器来源）:")
    for side, name in [(BLUE, "蓝"), (RED, "红")]:
        sub = kills[kills["tgt_side"] == side]
        print(f"  {name}方阵亡 {len(sub)}: " +
              ", ".join(f"{w}={n}" for w, n in
                        sub.groupby("weapon").size().sort_values(ascending=False).items()))

    # 蓝方掘壕防御地带（初始 x 5800-7600 的直瞄单位）
    belt = eng[(eng["tgt_side"] == BLUE)
               & eng["tgt_x0"].between(5800, 7600)
               & eng["tgt_role"].isin(["assault", "atgm_team"])]
    belt_killed = belt[belt["killed"]]
    n_belt = len({u for u in init_role if init_side[u] == BLUE
                  and 5800 <= init_x[u] <= 7600 and init_role[u] in ("assault", "atgm_team")})
    print(f"\n蓝方防御地带直瞄单位初始 {n_belt}，被击毁 {belt_killed['target'].nunique()}")
    if not belt_killed.empty:
        print("  击毁武器: " + ", ".join(f"{w}={n}" for w, n in
              belt_killed.groupby("weapon").size().items()))
        print(f"  击毁 tick: min={belt_killed['tick'].min()} "
              f"中位={int(belt_killed['tick'].median())} max={belt_killed['tick'].max()}")

    # 红方间瞄/导弹火力输出
    launches = eff[eff["kind"] == "fire_launch"]
    print(f"\nfire_launch 次数: 红={int((launches['side']==RED).sum())} "
          f"蓝={int((launches['side']==BLUE).sum())}")
    impacts = eff[eff["kind"] == "fire_impact"]
    if not impacts.empty:
        ns = (impacts[impacts["side"] == RED]["detail"]
              .str.extract(r"n=(\d+)")[0].dropna().astype(int))
        if len(ns):
            print(f"红方弹着覆盖人数: mean={ns.mean():.2f} max={ns.max()} "
                  f"n=0 占比={float((ns == 0).mean()):.2f} 共{len(ns)}发")
    # 红方炮兵/导弹终局弹药（含阵亡者）
    fs0 = (s0.side == RED) & np.isin(s0.fire_mode, ["indirect", "blos"])
    for i in np.where(fs0)[0]:
        print(f"  红 {s0.unit_ids[i]:10s} {str(s0.role[i]):10s} x0={x0_arr[i]:6.0f} "
              f"alive={bool(s.alive[i])} ammo={s.ammo[i]:4.0f}/{ammo0[i]:.0f} "
              f"rng={s.weapon_range[i]:.0f} splash={s.splash[i]:.0f} pk={s.pk[i]:.2f}")
    # 蓝方全部直瞄突击单位盘点
    ba0 = (s0.side == BLUE) & np.isin(s0.role, ["assault", "atgm_team"])
    idxs = np.where(ba0)[0]
    killed_n = int((~s.alive[idxs]).sum())
    print(f"\n蓝方直瞄突击单位初始 {len(idxs)}，阵亡 {killed_n}，存活 {len(idxs)-killed_n}")
    for i in idxs:
        if s.alive[i]:
            print(f"  活 {s0.unit_ids[i]:10s} {str(s0.role[i]):10s} x0={x0_arr[i]:6.0f} "
                  f"x={s.x[i]:6.0f} dug={bool(s.dug_in[i])} rng={s.weapon_range[i]:.0f}")
    # 空中架次与拦截
    if not air.empty:
        last = air.sort_values("tick").groupby("asset_id").tail(1)
        print("\n空中架次终局: " + ", ".join(
            f"{r['kind']}:{r['state']}" for _, r in last.iterrows()))
    n_intercept = int((eff["kind"] == "intercept").sum()) if not eff.empty else 0
    print(f"拦截事件总数: {n_intercept}")


if __name__ == "__main__":
    base = YamlFileProvider().load_scenario("example_brigade")
    run_diag("baseline（v3 默认）", copy.deepcopy(base))
    run_fire_diag("baseline（v3 默认）", copy.deepcopy(base))
