"""ETH/BTC 弱档（quick profile）出场规则开/关的回测对比。

数据：_live_data/{BTC,ETH}.json（从线上缓存拉取，1h 4500 根 + bias 周期）。
口径 A：原回测口径 —— score_only 模式不传 exit_rules_quick → 弱档单用 _OFF 裸奔。
口径 B：实盘同口径 —— 传 exit_rules_quick（0.8% 全平 / 无价格止损 / 8% 极端保护）
         → 弱档单用线上 quick 规则出场。
口径 C：彻底关弱档 —— 拦掉 edge/range 非 tradable 信号，只下标准档。
口径 D：弱档改用正常档规则 —— exit_rules_quick = 正常档 exit_rules（三级止盈+价格止损）。
输出：整体指标 + 弱档单独立盈亏统计。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest
from position_enhanced import EnhancedExitRules
from _btc_score_opt import score_only_cfg
from _live_cfg_backtest import exit_rules, ts_fmt

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")

# 寻优 best（1h）：ETH 7×4.0 score≥35；BTC 11×4.0 score≥50
CASES = {
    "ETH": {"params": {"periods": 7, "multiplier": 4.0}, "thr": 35.0},
    "BTC": {"params": {"periods": 11, "multiplier": 4.0}, "thr": 50.0},
}
GATE_TF = "1h"
ER_MIN, ER_WEAK_MIN = 0.15, 0.12


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _quick_rules(sym: dict) -> EnhancedExitRules:
    q = sym.get("exit_rules_quick") or {}
    valid = {f.name for f in EnhancedExitRules.__dataclass_fields__.values()}
    return EnhancedExitRules(**{k: v for k, v in q.items() if k in valid})


def agg_quick(trade_list: list[dict]) -> dict:
    """按 profile 聚合弱档单与标准档单。"""
    quick = [t for t in trade_list if t.get("profile") == "quick"]
    normal = [t for t in trade_list if t.get("profile") != "quick"]
    out = {}
    for name, group in (("quick", quick), ("normal", normal)):
        n = len(group)
        if not n:
            out[name] = {"trades": 0}
            continue
        wins = [t for t in group if t["pnl"] > 0]
        pnl = sum(t["pnl"] for t in group)
        gross = sum(t["pnl"] for t in group if t["pnl"] > 0)
        loss = abs(sum(t["pnl"] for t in group if t["pnl"] < 0))
        out[name] = {
            "trades": n,
            "pnl_u": round(pnl, 2),
            "win_rate": round(len(wins) / n * 100, 1),
            "avg_pnl": round(pnl / n, 2),
            "pf": round(gross / loss, 2) if loss else None,
        }
    return out


def run_case(tag: str, sym: dict, candles: list, cbtf: dict,
             mode: str) -> dict:
    """mode: A=原口径(弱档裸奔)  B=实盘同口径(弱档带规则)
             C=彻底关弱档(拦edge/range)  D=弱档用正常档规则(三级止盈+价格止损)
             E=er_min降到0.12(弱档并入正常档,全用正常档规则)"""
    p = CASES[tag]["params"]
    thr = CASES[tag]["thr"]
    rules = exit_rules(sym)
    cfg = score_only_cfg(GATE_TF)
    kwargs = dict(
        init_cash=100.0, fee_rate=0.0005, allow_short=True,
        exit_rules=rules,
        sizing="fixed", margin_usdt=sym["margin_usdt"], leverage=sym["leverage"],
        live_gate=cfg, gate_tf=GATE_TF, candles_by_tf=cbtf,
        score_only_gate=True, min_total_score=thr,
    )
    if mode in ("B", "D"):
        kwargs["er_min"] = ER_MIN
        kwargs["er_weak_min"] = ER_WEAK_MIN
        if mode == "B":
            kwargs["exit_rules_quick"] = _quick_rules(sym)
        else:  # D：弱档用正常档规则
            kwargs["exit_rules_quick"] = rules
    if mode == "C":
        cfg.quick_enabled = False
        kwargs["block_untradable"] = True
    if mode == "E":
        # 弱档并入正常档：er_min 降到 0.12，[0.12,0.15) 原弱档信号变 normal。
        # 注意 er_momentum 打分从 8 涨到 15~20，部分原被分数线拦掉的会进来。
        cfg.er_min = 0.12
        cfg.er_weak_min = 0.12
    r = run_backtest(candles, p, **kwargs)
    if "error" in r:
        return {"error": r["error"]}
    agg = agg_quick(r["trade_list"])
    return {
        "pnl_u": round(r["final"] - r["init_cash"], 2),
        "max_dd_pct": r["max_dd_pct"],
        "trades": r["trades"],
        "quick_trades": r["quick_trades"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "start": ts_fmt(r["start_ts"]), "end": ts_fmt(r["end_ts"]),
        "agg": agg,
    }


def main():
    live = _get(LIVE_URL)
    syms = {s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): s
            for s in live["symbols"]}
    print("== 对比基线（寻优 best）==")
    print(f"  ETH: 7×4.0 score≥35 | BTC: 11×4.0 score≥50 | tf=1h 4500根\n")

    results = {}
    for tag in ("ETH", "BTC"):
        data = json.load(open(os.path.join(DATA_DIR, f"{tag}.json"), encoding="utf-8"))
        candles = data[GATE_TF]
        cbtf = {tf: data[tf] for tf in ("15m", "1h", "4h", "1d") if data.get(tf)}
        sym = syms[tag]
        print(f"=== {tag} ===", flush=True)
        a = run_case(tag, sym, candles, cbtf, "A")
        b = run_case(tag, sym, candles, cbtf, "B")
        c = run_case(tag, sym, candles, cbtf, "C")
        d = run_case(tag, sym, candles, cbtf, "D")
        e = run_case(tag, sym, candles, cbtf, "E")
        results[tag] = {
            "A_原口径_弱档裸奔": a,
            "B_实盘同口径_带弱档规则": b,
            "C_彻底关弱档": c,
            "D_弱档用正常档规则": d,
            "E_弱档并入正常档_er_min=0.12": e,
        }

        def line(name, r):
            if "error" in r:
                print(f"  {name}: ERROR {r['error']}", flush=True)
                return
            agg = r["agg"]
            q = agg.get("quick", {})
            print(f"  {name}:", flush=True)
            print(f"    pnl={r['pnl_u']}U  trades={r['trades']} "
                  f"(weak={r['quick_trades']})  wr={r['win_rate']}%  "
                  f"PF={r['profit_factor']}  dd={r['max_dd_pct']}%", flush=True)
            print(f"    weak单: {q.get('trades', 0)}笔  pnl={q.get('pnl_u', 0)}U  "
                  f"wr={q.get('win_rate', 0)}%  avg={q.get('avg_pnl', 0)}U  "
                  f"PF={q.get('pf')}", flush=True)
        line("A_原口径(弱档裸奔)", a)
        line("B_实盘同口径(带弱档规则)", b)
        line("C_彻底关弱档(拦edge/range)", c)
        line("D_弱档用正常档规则(三级止盈+价格止损)", d)
        line("E_弱档并入正常档(er_min=0.12)", e)
        print(flush=True)

    with open(os.path.join(os.path.dirname(__file__), "_compare_quick.json"),
              "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("Wrote _compare_quick.json")


if __name__ == "__main__":
    main()
