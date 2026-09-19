# -*- coding: utf-8 -*-
"""弱档 profile(quick 快出)多品种泛化验证。

问题：弱档(弱ER信号走 quick 出场)只在 BTC 上有效，还是普遍适用？

方法(与 _btc_weak_profile.py 同口径，扩展到全部可用品种)：
  对每个品种，用其线上配置(引擎/阈值/ER参数/标准出场)跑回测，唯一变量是
  弱ER段(ER∈[er_weak_min,er_min))的出场规则：
    - 弱档关(OFF)：exit_rules_quick = 标准规则 → 弱ER走标准三级止盈
    - 弱档开(ON) ：exit_rules_quick = quick 规则(tp1 一次性平光+关价格止损)
                     对 tp1∈[0.8,1.0,1.2,1.5] 做网格
  入场闸门两变体完全一致，差异纯来自弱ER段出场。

判定某品种是否有弱ER带：直接看 OFF 跑出的 profile=="quick" 成交数(n_quick)。
若 n_quick==0，弱档对其无作用 → 标 N/A。
弱档适用：ON 整体 pnl% 优于 OFF 且 ON 弱ER段 E% 不低于 OFF。

输出：_weak_profile_matrix.json + 控制台矩阵 + 弱档适用率。
"""
from __future__ import annotations
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get  # noqa: E402
from position_enhanced import EnhancedExitRules  # noqa: E402

LIVE_URLS = ["http://43.108.10.84:5174/api/trade/symbols",
             "http://47.84.106.154:5174/api/trade/symbols"]
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
CACHE = {"BTC": "BTC.json", "ETH": "ETH.json", "MU": "MU.json",
         "SPCX": "SPCX.json", "SNDK": "SNDK.json", "NVDA": "NVDA.json",
         "SKHYNIX": "SKHYNIX.json", "CL": "cl_half_cache.json"}
TF = "1h"
QUICK_TP1 = [0.8, 1.0, 1.2, 1.5]   # 0.8=生产默认；其余为寻优网格


def make_quick(tp1_pct):
    """镜像 state.py 的 exit_rules_quick（关价格止损，tp1 一次性平光）。"""
    return EnhancedExitRules(
        enabled=False,
        tp1_pct=tp1_pct, tp1_ratio=100.0,
        tp2_pct=999.0, tp2_ratio=0.0,
        tp3_pct=999.0, tp3_ratio=0.0, tp3_mode="pct",
        move_sl_to_entry=False, trail_with_st=False,
        sl_mode="pct", sl_pct=1.0, sl_buffer_atr=0.3, sl_min_pct=1.0,
        protect_profit_at=999.0, protect_trail_pct=0.0,
        max_loss_enabled=False, max_loss_pct=8.0,
    )


def trades(r):
    return r.get("trades_list") or r.get("trade_list") or []


def metrics(tl):
    if not tl:
        return {"t": 0, "pnl": 0.0, "e": 0.0, "wr": 0.0, "pf": 0.0}
    n = len(tl)
    wins = [t["pnl_pct"] for t in tl if t["pnl_pct"] > 0]
    loss = [t["pnl_pct"] for t in tl if t["pnl_pct"] <= 0]
    wr = len(wins) / n * 100
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(loss) / len(loss) if loss else 0.0
    e = wr / 100 * aw - (1 - wr / 100) * abs(al)
    loss_abs = abs(sum(loss))
    gain = sum(wins)
    pf = gain / loss_abs if loss_abs > 0 else (gain if gain else 0.0)
    return {"t": n, "pnl": round(sum(t["pnl_pct"] for t in tl), 2),
            "e": round(e, 3), "wr": round(wr, 1), "pf": round(pf, 2)}


def run_sym(sym, cbtf, cfg, normal_rules, quick_rules):
    gate_tf = sym["allow_tfs"][0]
    candles = cbtf.get(gate_tf)
    if not candles or len(candles) < 300:
        return None
    common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                  sizing="fixed", margin_usdt=sym["margin_usdt"],
                  leverage=sym["leverage"], gate_tf=gate_tf,
                  candles_by_tf=cbtf, exit_rules=normal_rules,
                  exit_rules_quick=quick_rules, live_gate=cfg)
    return run_backtest(candles, sym["params"], **common)


def summarize(r):
    if r is None or "error" in r:
        return None
    tl = trades(r)
    m = metrics(tl)
    quick = [t for t in tl if t.get("profile") == "quick"]
    return {"overall": m, "quick_seg": metrics(quick), "n_quick": len(quick)}


def main():
    t0 = time.time()
    sym_map: dict[str, dict] = {}
    for url in LIVE_URLS:
        try:
            d = _get(url)
            for x in d.get("symbols", []):
                sym_map.setdefault(
                    x["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""), x)
        except Exception:
            continue
    if not sym_map:
        print("无法获取线上配置"); return

    rows = []
    for name, fname in CACHE.items():
        if name not in sym_map:
            print(f"{name}: 无线上配置，跳过"); continue
        sym = sym_map[name]
        cache = os.path.join(DATA, fname)
        if not os.path.exists(cache):
            print(f"{name}: 无缓存 {fname}，跳过"); continue
        cbtf = {k: v for k, v in json.load(open(cache, encoding="utf-8")).items()
                if isinstance(v, list) and v and "ts" in (v[0] or {})}
        cfg = trade_cfg(sym)
        normal = exit_rules(sym)
        off = summarize(run_sym(sym, cbtf, cfg, normal, normal))
        if off is None:
            print(f"{name}: 回测失败"); continue
        eng = sym.get("score_engine") or "v1"
        nq = off["n_quick"]
        print(f"\n--- {name} [引擎={eng}] 弱ER成交={nq} ---")
        print(f"  OFF(弱ER走标准): 整体 pnl%={off['overall']['pnl']} "
              f"E%={off['overall']['e']} n={off['overall']['t']} | "
              f"弱ER段 E%={off['quick_seg']['e']} n={nq}")

        if nq == 0:
            print("  -> N/A：本窗口无弱ER成交，弱档无作用")
            rows.append({"name": name, "engine": eng, "has_weak": False,
                         "off": off, "on_grid": [],
                         "verdict": "N/A(无弱ER成交)"})
            continue

        grid = []
        for tp1 in QUICK_TP1:
            qt = make_quick(tp1)
            s = summarize(run_sym(sym, cbtf, cfg, normal, qt))
            if s is None:
                continue
            grid.append({"tp1": tp1, **s})
            print(f"  ON tp1={tp1:g}: 整体 pnl%={s['overall']['pnl']} "
                  f"E%={s['overall']['e']} n={s['overall']['t']} | "
                  f"弱ER段 E%={s['quick_seg']['e']} n={s['quick_seg']['t']}")

        on_best = max(grid, key=lambda g: g["overall"]["pnl"])
        d_overall = on_best["overall"]["pnl"] - off["overall"]["pnl"]
        d_quick = on_best["quick_seg"]["e"] - off["quick_seg"]["e"]
        on_def = next((g for g in grid if g["tp1"] == 0.8), None)
        verdict = ("建议开弱档" if (on_best["overall"]["pnl"] > off["overall"]["pnl"]
                                    and on_best["quick_seg"]["e"] >= off["quick_seg"]["e"])
                   else "不建议开弱档")
        print(f"  -> {verdict} (ON最优tp1={on_best['tp1']:g} 整体Δ={d_overall:+.2f} "
              f"弱ER段EΔ={d_quick:+.3f})")
        rows.append({"name": name, "engine": eng, "has_weak": True,
                     "off": off, "on_grid": grid, "on_best": on_best,
                     "on_default": on_def, "verdict": verdict})

    # 矩阵
    print("\n=== 弱档 Profile 多品种矩阵 ===")
    print(f"  {'品种':<9s}{'引擎':>5s}{'弱ER':>5s}{'OFF_pnl':>9s}"
          f"{'ON@0.8':>8s}{'ONbest':>8s}{'Δbest':>7s}{'弱ER_E_OFF':>11s}"
          f"{'弱ER_E_ON':>10s}{'选择':>10s}")
    for r in rows:
        if not r["has_weak"]:
            print(f"  {r['name']:<9s}{r['engine']:>5s}{'-':>5s}"
                  f"{r['off']['overall']['pnl']:>9.2f}{'':>8s}{'':>8s}{'':>7s}"
                  f"{r['off']['quick_seg']['e']:>11.3f}{'':>10s}{'N/A':>10s}")
            continue
        ob = r["on_best"]
        od = r["on_default"]
        de = ob["overall"]["pnl"] - r["off"]["overall"]["pnl"]
        print(f"  {r['name']:<9s}{r['engine']:>5s}{r['off']['n_quick']:>5d}"
              f"{r['off']['overall']['pnl']:>9.2f}"
              f"{(od['overall']['pnl'] if od else 0):>8.2f}"
              f"{ob['overall']['pnl']:>8.2f}{de:>+7.2f}"
              f"{r['off']['quick_seg']['e']:>11.3f}{ob['quick_seg']['e']:>10.3f}"
              f"{r['verdict']:>10s}")

    hw = [r for r in rows if r["has_weak"]]
    yes = [r for r in hw if r["verdict"] == "建议开弱档"]
    print(f"\n有弱ER带的品种 = {len(hw)}")
    print(f"弱档适用率(建议开 / 有弱ER带) = {len(yes)}/{len(hw)} "
          f"= {len(yes)/len(hw)*100:.0f}%" if hw else "无")

    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "method": "同闸门仅切换弱ER段出场, ON=tp1网格",
               "rows": rows,
               "summary": {"has_weak": len(hw), "yes": len(yes),
                           "rate": round(len(yes)/len(hw), 3) if hw else 0}},
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "_weak_profile_matrix.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2, default=str)
    print(f"\n耗时 {time.time()-t0:.0f}s | Wrote _weak_profile_matrix.json")


if __name__ == "__main__":
    main()
