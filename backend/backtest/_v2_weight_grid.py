# -*- coding: utf-8 -*-
"""V2 权重网格搜索（train 03-06 / oos 07-09 拆分）。

做法：
- monkey-patch regime_scoring._parts_v2：用全局 _W（各因子乘子）把原始分项
  重新加权并归一化到 0-100（与 regime_scoring 现有 _V2_FACTOR_WEIGHTS 同一口径）。
- 对每个权重组合(combo)：在 TRAIN 窗扫阈值选 t*（最大化 train E，需 train 样本>=8），
  再在 OOS 窗以 t* 评估 Expectancy（阈值选择不触碰 OOS，避免前视）。
- 每个品种取 OOS E 最高的 combo 为“该品种最优权重”；
  综合最优权重 = 在 V2 三品种(MU/ETH/SPCX)上平均 OOS E 最高的 combo。
- 最后对综合最优权重跑稳定带阈值扫描，给出可上线阈值 + V1/V2 对比。

输出 _v2_weight_grid.json（含完整网格 + 推荐），并渐进写入防中断丢失。
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import regime_scoring
from _v2_threshold_scan import (fetch_symbols, slice_cbtf, run, trade_cfg,
                                exit_rules, DATA)

FACTOR_MAX = {"signal_quality": 30.0, "er_momentum": 25.0, "volatility": 15.0,
              "mtf_alignment": 20.0, "breakout_boost": 20.0}

_ORIG = regime_scoring._parts_v2
_W = None


def _patched(sig, candles, cfg, candles_by_tf=None, p=None):
    bd, reasons, total, detail = _ORIG(sig, candles, cfg, candles_by_tf, p)
    if _W is None:
        return bd, reasons, total, detail
    weighted = 0.0
    cap = 0.0
    for f, nat in FACTOR_MAX.items():
        w = _W.get(f, 1.0)
        weighted += bd.get(f, 0.0) * w
        cap += nat * w
    pen = bd.get("penalties", 0.0)
    new_total = max(0.0, min(100.0, (weighted + pen) / cap * 100.0)) if cap > 0 else 0.0
    return bd, reasons, round(new_total, 3), detail


regime_scoring._parts_v2 = _patched

# 网格：主要在 ER↑ / SQ↓ / 4H↑ 三轴搜索；ATR、Breakout 按消融结论固定在低值
ER_LEVELS = [1.0, 1.6, 2.2]
SQ_LEVELS = [0.3, 0.5, 0.7, 1.0]
H4_LEVELS = [1.0, 1.25]
ATR_FIXED = 1.0
BRK_FIXED = 0.75

COMBOS = []
for er in ER_LEVELS:
    for sq in SQ_LEVELS:
        for h4 in H4_LEVELS:
            COMBOS.append({
                "signal_quality": sq, "er_momentum": er,
                "volatility": ATR_FIXED, "mtf_alignment": h4,
                "breakout_boost": BRK_FIXED,
            })

TRAIN_THR = list(range(28, 66, 4))   # 28..64
V2_SYMS = ["MU", "ETH", "SPCX"]        # V2 闸门品种，用于综合最优
GRID_SYMS = V2_SYMS + ["SNDK", "BTC"]

TRAIN_LO = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_HI = int(datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
OOS_LO = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
OOS_HI = int(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)


def metrics(r):
    if "error" in r:
        return {"t": 0, "pnl": 0.0, "e": 0.0, "pf": 0.0, "dd": 0.0, "wr": 0.0, "error": r["error"]}
    wr = r.get("win_rate") or 0.0
    aw = r.get("avg_win") or 0.0
    al = r.get("avg_loss") or 0.0
    exp = ((wr / 100.0) * aw - (1 - wr / 100.0) * abs(al)) if r.get("trades") else 0.0
    return {"t": r.get("trades", 0), "pnl": round((r.get("final", 100) - 100), 2),
            "e": round(exp, 3), "pf": round(r.get("profit_factor") or 0.0, 2),
            "dd": round(r.get("max_dd_pct", 0), 1), "wr": round(wr, 1)}


def mk_cfg(s, thr):
    return replace(trade_cfg(s), score_engine="v2",
                   scoring_full_threshold=float(thr),
                   scoring_half_threshold=float(thr),
                   scoring_alert_threshold=float(thr))


def max_contiguous(vals, step=4):
    vals = sorted(vals)
    best = cur = []
    for v in vals:
        if not cur or v - cur[-1] <= step:
            cur.append(v)
        else:
            if len(cur) > len(best):
                best = cur
            cur = [v]
    if len(cur) > len(best):
        best = cur
    return best


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): x for x in syms}
    out = {"grid": COMBOS, "per_symbol": {}, "per_symbol_best": {},
           "combined": None, "final_scan": {}, "v1_oos": {}}

    data = {}
    for name in GRID_SYMS:
        if name not in sym_map:
            print(f"!! {name} 不在配置", flush=True)
            continue
        cache = os.path.join(DATA, f"{name}.json")
        if not os.path.exists(cache):
            print(f"!! {name} 缓存缺失", flush=True)
            continue
        cbtf = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                if isinstance(v, list) and v}
        data[name] = (sym_map[name], cbtf)

    def dump():
        with open(os.path.join(HERE, "_v2_weight_grid.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

    for name in data:
        s, cbtf = data[name]
        ex = exit_rules(s)
        cbtf_train = slice_cbtf(cbtf, TRAIN_LO, TRAIN_HI)
        cbtf_oos = slice_cbtf(cbtf, OOS_LO, OOS_HI)
        v1_oos = metrics(run(s, cbtf_oos, replace(trade_cfg(s), score_engine=""), ex))
        out["v1_oos"][name] = v1_oos
        sym_grid = []
        for combo in COMBOS:
            global _W
            _W = combo
            train_rows = []
            for t in TRAIN_THR:
                m = metrics(run(s, cbtf_train, mk_cfg(s, t), ex))
                train_rows.append({"t": t, **m})
            elig = [r for r in train_rows if r["t"] >= 8]
            best = max(elig, key=lambda r: r["e"]) if elig else max(train_rows, key=lambda r: r["e"])
            t_star = best["t"]
            oos = metrics(run(s, cbtf_oos, mk_cfg(s, t_star), ex))
            sym_grid.append({
                "combo": combo, "t_star": t_star,
                "train_e": best["e"], "train_t": best["t"],
                "oos_e": oos["e"], "oos_t": oos["t"], "oos_pf": oos["pf"],
            })
            print(f"  {name} ER{combo['er_momentum']:.2f} SQ{combo['signal_quality']:.2f} "
                  f"4H{combo['mtf_alignment']:.2f} -> t*={t_star} TR.E={best['e']:.2f} "
                  f"OOS.E={oos['e']:.2f} OOS.T={oos['t']}", flush=True)
        out["per_symbol"][name] = sym_grid
        dump()
        print(f"  >> {name} 完成", flush=True)

    # 每个品种最优
    for name in out["per_symbol"]:
        rows = out["per_symbol"][name]
        elig = [r for r in rows if r["oos_t"] >= 4 and r["train_e"] > 0]
        pool = elig if elig else rows
        out["per_symbol_best"][name] = max(pool, key=lambda r: r["oos_e"])

    # 综合最优（V2 三品种平均 OOS E）
    scored = []
    for i, combo in enumerate(COMBOS):
        oos_list = []
        ok = True
        for name in V2_SYMS:
            rows = out["per_symbol"].get(name, [])
            if not rows:
                ok = False
                break
            r = rows[i]
            if not (r["oos_t"] >= 4 and r["train_e"] > 0):
                ok = False
                break
            oos_list.append(r["oos_e"])
        if ok:
            scored.append((sum(oos_list) / len(oos_list), combo, oos_list))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        mean_e, best_combo, oos_list = scored[0]
        out["combined"] = {
            "combo": best_combo, "mean_oos_e": round(mean_e, 3),
            "oos_per_sym": {n: out["per_symbol"][n][COMBOS.index(best_combo)]["oos_e"]
                            for n in V2_SYMS},
        }
    else:
        out["combined"] = None

    # 综合最优权重的稳定带阈值确认
    if out["combined"]:
        _W = out["combined"]["combo"]
        for name in V2_SYMS:
            if name not in data:
                continue
            s, cbtf = data[name]
            ex = exit_rules(s)
            cbtf_train = slice_cbtf(cbtf, TRAIN_LO, TRAIN_HI)
            cbtf_oos = slice_cbtf(cbtf, OOS_LO, OOS_HI)
            scan = []
            for t in range(28, 84, 4):
                mtr = metrics(run(s, cbtf_train, mk_cfg(s, t), ex))
                moo = metrics(run(s, cbtf_oos, mk_cfg(s, t), ex))
                scan.append({"t": t, "train": mtr, "oos": moo})
            stable = [r for r in scan if r["train"]["t"] >= 8 and r["train"]["e"] > 0
                      and r["train"]["pf"] > 1 and r["oos"]["t"] >= 4
                      and r["oos"]["e"] > 0 and r["oos"]["pf"] > 1]
            if stable:
                seg = max_contiguous([r["t"] for r in stable])
                t_star = seg[len(seg) // 2]
            else:
                cand = [r for r in scan if r["oos"]["e"] > 0 and r["train"]["t"] >= 8]
                t_star = max(cand, key=lambda r: r["train"]["e"])["t"] if cand else 44
            v1_full = metrics(run(s, cbtf, replace(trade_cfg(s), score_engine=""), ex))
            v2_full = metrics(run(s, cbtf, mk_cfg(s, t_star), ex))
            v2_oos = metrics(run(s, cbtf_oos, mk_cfg(s, t_star), ex))
            out["final_scan"][name] = {
                "t_star": t_star, "scan": scan,
                "compare": {"V1_full": v1_full, "V2_full": v2_full, "V2_oos": v2_oos},
            }
            print(f"  FINAL {name}: t*={t_star} V2_oos.E={v2_oos['e']} "
                  f"vs V1_oos.E={out['v1_oos'][name]['e']}", flush=True)
        _W = None

    dump()
    print(f"\nWrote _v2_weight_grid.json | 耗时 {time.time() - t0:.0f}s", flush=True)
    if out["combined"]:
        c = out["combined"]["combo"]
        print("综合最优权重(乘子):", c, "mean OOS E=", out["combined"]["mean_oos_e"])


if __name__ == "__main__":
    main()
