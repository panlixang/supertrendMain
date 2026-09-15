# -*- coding: utf-8 -*-
"""V2 内部消融：逐项关闭 V2 的 6 个评分模块，定位 V2 相对 V1 的核心增量。

实验（均基于 score_engine='v2' 真实 V2 配置）：
  ① V2 Full        基准
  ② V2 - ATR       volatility(0-15) 置 0
  ③ V2 - 4H        mtf_alignment(0-20) 置 0
  ④ V2 - ER        er_momentum(0-25) 置 0
  ⑤ V2 - Breakout  breakout_boost(0-20) 置 0
  ⑥ V2 - Penalty   penalties(-20~0) 置 0
  ⑦ V2 - Dynamic   use_dynamic_threshold=False（固定阈值，保留连续评分）

机制：monkeypatch regime_scoring 的 6 个分项函数（会贯穿
  integration → regime_enhanced → regime.evaluate → regime_scoring
  整条链路生效），其余 V2 旁路（动量突破/假突破/自适应阈值）在 7 个实验中
保持恒定，不混入对比。

数据：复用 _live_data/{SYM}.json 缓存（5 品种同窗口，跨实验可比）。
配置：fetch 线上 /api/trade/symbols（V2 实盘口径）。

输出每个品种：PnL(U) / Trades / Expectancy(%) / PF / DD(%) / WR(%)
写 _ablation_v2.json。
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402
from _live_cfg_backtest import exit_rules, trade_cfg, _get, ts_fmt  # noqa: E402
import regime_scoring as RS  # noqa: E402

SYMS = ["MU", "ETH", "SPCX", "SNDK", "BTC"]
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
LIVE = [
    "http://43.108.10.84:5174/api/trade/symbols",
    "http://47.84.106.154:5174/api/trade/symbols",
]

# 已知 V2完整 基准（来自 _ablation_v1_v2.json），用于校验数据/配置一致性
KNOWN_V2_FULL = {
    "MU": (29.58, 97), "ETH": (29.3, 68), "SPCX": (53.74, 59),
    "SNDK": (27.96, 51), "BTC": (23.83, 34),
}


def fetch_symbols():
    syms = None
    last_err = None
    for url in LIVE:
        try:
            d = _get(url)
            if d and d.get("symbols"):
                syms = d["symbols"]
                print(f"[cfg] 取自 {url} → {len(syms)} 个品种: "
                      f"{[s['symbol'] for s in syms]}", flush=True)
                return syms
        except Exception as e:  # noqa: BLE001
            last_err = e
    if syms is None:
        raise RuntimeError(f"无法获取线上配置: {last_err}")


def summarize(r: dict) -> dict:
    tl = r.get("trade_list", [])
    wr = r["win_rate"]
    aw, al = r.get("avg_win"), r.get("avg_loss")
    if tl and aw is not None and al is not None and wr is not None:
        exp = (wr / 100.0) * aw - (1 - wr / 100.0) * abs(al)
    else:
        exp = 0.0
    pf = r.get("profit_factor")
    return {
        "pnl_u": round(r["final"] - 100.0, 2),
        "trades": r["trades"],
        "exp_pct": round(exp, 3),
        "pf": round(pf, 2) if isinstance(pf, (int, float)) else 0.0,
        "dd": r["max_dd_pct"],
        "wr": wr if wr is not None else 0.0,
    }


# ---- monkeypatch 基础设施：直接改 regime_scoring 模块级函数 ----
_ORIG = {
    "atr": RS._atr_soft_part,
    "mtf": RS._mtf_soft_part,
    "er": RS._er_part,
    "breakout": RS._breakout_part,
    "penalty": RS._penalty_part,
}


def _restore():
    RS._atr_soft_part = _ORIG["atr"]
    RS._mtf_soft_part = _ORIG["mtf"]
    RS._er_part = _ORIG["er"]
    RS._breakout_part = _ORIG["breakout"]
    RS._penalty_part = _ORIG["penalty"]


def _patch(name: str):
    _restore()
    if name == "atr":
        RS._atr_soft_part = lambda candles: (0.0, {"note": "ablated"})
    elif name == "mtf":
        RS._mtf_soft_part = lambda sig, cbtf, p, cfg=None: (0.0, {"note": "ablated"})
    elif name == "er":
        RS._er_part = lambda sig, candles, cfg: (0, ["ablated ER"])
    elif name == "breakout":
        RS._breakout_part = lambda sig, candles, cfg, qf: (0, ["ablated breakout"])
    elif name == "penalty":
        RS._penalty_part = lambda sig, candles, cfg: (0, ["ablated penalty"])


EXPERIMENTS = [
    ("V2 Full", None, None),
    ("V2 - ATR", "atr", None),
    ("V2 - 4H", "mtf", None),
    ("V2 - ER", "er", None),
    ("V2 - Breakout", "breakout", None),
    ("V2 - Penalty", "penalty", None),
    ("V2 - Dynamic", None, "no_dynamic"),
]


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): s
               for s in syms}
    out = {}

    for name in SYMS:
        if name not in sym_map:
            print(f"!! {name} 不在线上配置，跳过", flush=True)
            continue
        s = sym_map[name]
        p = s["params"]
        # 43 线上当前 score_engine=''（即 v1）。V2 与 V1 的唯一区别是引擎切换
        # （resolve_engine: ''→v1, 'v2'→_parts_v2，含 ATR/4H/ER/Breakout/Penalty/Dynamic）。
        # 其余按品种配置（ST参数/ER/出场/阈值）= 真实线上口径，保证 V2 Full 可比。
        base = replace(trade_cfg(s), score_engine="v2")
        ex = exit_rules(s)

        cache_path = os.path.join(DATA, f"{name}.json")
        if not os.path.exists(cache_path):
            print(f"!! {name} 缓存缺失 {cache_path}", flush=True)
            continue
        with open(cache_path, encoding="utf-8") as f:
            cbtf = {k: v for k, v in json.load(f).items()
                    if isinstance(v, list) and v}
        gate_tf = s["allow_tfs"][0]
        candles = cbtf.get(gate_tf)
        if not candles or len(candles) < 300:
            print(f"!! {name} 数据不足 ({gate_tf} n={len(candles or [])})", flush=True)
            continue

        common = dict(init_cash=100.0, fee_rate=0.0005, allow_short=True,
                      exit_rules=ex, sizing="fixed",
                      margin_usdt=s["margin_usdt"], leverage=s["leverage"],
                      gate_tf=gate_tf, candles_by_tf=cbtf)
        print(f"\n=== {name} {gate_tf} 数据 "
              f"[{ts_fmt(candles[0]['ts'])}~{ts_fmt(candles[-1]['ts'])}] "
              f"engine={base.score_engine} scoring={base.use_scoring} "
              f"dyn={base.use_dynamic_threshold} er={base.er_min}/{base.er_trend} "
              f"F/H/A={base.scoring_full_threshold}/{base.scoring_half_threshold}/"
              f"{base.scoring_alert_threshold} "
              f"atrF={base.atr_filter_enabled} rangeF={base.range_filter_enabled} "
              f"mtfF={base.mtf_filter_enabled} adxF={base.adx_filter_enabled} ===",
              flush=True)

        out[name] = {}
        for label, pname, mode in EXPERIMENTS:
            _restore()
            cfg = base
            if mode == "no_dynamic":
                cfg = replace(base, use_dynamic_threshold=False)
            if pname:
                _patch(pname)
            try:
                r = run_backtest(candles, p, live_gate=cfg, **common)
            finally:
                _restore()
            if "error" in r:
                out[name][label] = {"error": r["error"]}
                print(f"  {label:14s}: ERROR {r['error']}", flush=True)
                continue
            info = summarize(r)
            out[name][label] = info
            kr = KNOWN_V2_FULL.get(name)
            mark = ""
            if label == "V2 Full" and kr:
                ok = (abs(info["pnl_u"] - kr[0]) < 3 and abs(info["trades"] - kr[1]) < 8)
                mark = "  [校验" + ("OK" if ok else f"偏差! 期望~{kr[0]}U/{kr[1]}笔") + "]"
            print(f"  {label:14s}: PnL={info['pnl_u']:>7.2f}U Trades={info['trades']:>3} "
                  f"E={info['exp_pct']:>5.2f}% PF={info['pf']:>4.2f} "
                  f"DD={info['dd']:>4.1f}% WR={info['wr']:>5.1f}%{mark}", flush=True)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ablation_v2.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
