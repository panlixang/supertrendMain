# -*- coding: utf-8 -*-
"""V1 vs V2 逐笔交易对照 + V2 Score 分桶。

同一份线上真实配置（43）下，分别用 engine=''（V1）与 engine='v2'（V2）各跑一遍回测，
monkey-patch regime_scoring.score_signal 逐笔抓 V1/V2 的 total_score 与分项明细
（signal_quality / er_momentum / volatility / mtf_alignment / breakout_boost / penalties），
再按 (entry_ts, 方向) 把两遍交易对齐，得到：

  表一（每个品种）：
    V1 独有（V1 交易、V2 跳过）/ V1+V2 共同 / V2 独有（V2 交易、V1 跳过）
    → Trades / PnL(U) / E(每笔%) / PF / WR

  表二（每个品种，仅 V2 实际成交）：
    V2 Score 分桶 0-20 / 20-30 / ... / 80+
    → Trades / PnL(U) / E(每笔%) / PF / WR
    （动量突破旁路的成交无 score，单独计数、不进分桶）

数据：复用 _live_data/{SYM}.json 缓存。输出 _v1_v2_trades.json（含逐笔明细）。
"""
from __future__ import annotations
import json
import os
import sys
import time
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

_CAPT = []
_CAPT_N = 0
_SCORE_ORIG = RS.score_signal


def _patch_score():
    global _CAPT_N
    def _p(sig, candles, cfg, candles_by_tf=None, p=None):
        global _CAPT_N
        _CAPT_N += 1
        res = _SCORE_ORIG(sig, candles, cfg, candles_by_tf, p)
        eng = getattr(cfg, "score_engine", "") or "v1"
        # score_signal 直接返回 total_score 与 breakdown（score_detail 是
        # evaluate_enhanced 包装层才加的，这里取不到）
        _CAPT.append({
            "engine": eng,
            "ts": sig.get("ts"),
            "dir": "LONG" if sig.get("type") == "buy" else "SHORT",
            "score": res.get("total_score"),
            "bd": res.get("breakdown", {}),
        })
        return res
    RS.score_signal = _p


def _reset_capt():
    global _CAPT, _CAPT_N
    _CAPT = []
    _CAPT_N = 0


def _norm_side(side):
    return "L" if str(side).upper().startswith("L") else "S"


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


def stats(trades):
    n = len(trades)
    if n == 0:
        return dict(trades=0, pnl=0.0, e=0.0, pf=None, wr=0.0)
    pnl = sum(t["pnl_u"] for t in trades)
    wins = [t for t in trades if t["pnl_u"] > 0]
    losses = [t for t in trades if t["pnl_u"] <= 0]
    wr = len(wins) / n * 100
    e = sum(t["pnl_pct"] for t in trades) / n
    g = sum(t["pnl_u"] for t in wins)
    l = abs(sum(t["pnl_u"] for t in losses))
    pf = round(g / l, 2) if l > 1e-9 else None  # 无亏损→∞，记为 None
    return dict(trades=n, pnl=round(pnl, 2), e=round(e, 3),
                pf=pf, wr=round(wr, 1))


def main():
    t0 = time.time()
    syms = fetch_symbols()
    sym_map = {s["symbol"].replace("-USDT-SWAP", "").replace("-USDT", ""): s
               for s in syms}
    out = {"per_trade": {}, "table1": {}, "table2": {}, "meta": {}}

    for name in SYMS:
        if name not in sym_map:
            print(f"!! {name} 不在线上配置，跳过", flush=True)
            continue
        s = sym_map[name]
        p = s["params"]
        v1cfg = trade_cfg(s)                       # engine='' → v1
        v2cfg = replace(trade_cfg(s), score_engine="v2")
        ex = exit_rules(s)
        sl_pct = float(s.get("exit_rules", {}).get("sl_pct", 2.0)) or 2.0

        cache_path = os.path.join(DATA, f"{name}.json")
        if not os.path.exists(cache_path):
            print(f"!! {name} 缓存缺失 {cache_path}", flush=True)
            continue
        with open(cache_path, encoding="utf-8") as f:
            cbtf = {k: v for k, v in json.load(f).items() if isinstance(v, list) and v}
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
              f"V1 engine={v1cfg.score_engine} | V2 engine={v2cfg.score_engine} "
              f"sl_pct={sl_pct} F/H/A={v2cfg.scoring_full_threshold}/"
              f"{v2cfg.scoring_half_threshold}/{v2cfg.scoring_alert_threshold} ===",
              flush=True)

        # ---- V1 跑一遍，抓分 ----
        _reset_capt(); _patch_score()
        r1 = run_backtest(candles, p, live_gate=v1cfg, **common)
        v1_trades = {(t["entry_ts"], _norm_side(t["side"])): t
                     for t in r1.get("trade_list", [])}
        v1_scores = {(c["ts"], _norm_side(c["dir"])): c
                     for c in _CAPT if c["engine"] == "v1"}
        print(f"  [dbg] V1 fired={_CAPT_N} len(_CAPT)={len(_CAPT)} "
              f"v1_scores={len(v1_scores)} r1.trades={r1.get('trades')}",
              flush=True)

        # ---- V2 跑一遍，抓分 ----
        _reset_capt()
        r2 = run_backtest(candles, p, live_gate=v2cfg, **common)
        v2_trades = {(t["entry_ts"], _norm_side(t["side"])): t
                     for t in r2.get("trade_list", [])}
        v2_scores = {(c["ts"], _norm_side(c["dir"])): c
                     for c in _CAPT if c["engine"] == "v2"}
        print(f"  [dbg] V2 fired={_CAPT_N} len(_CAPT)={len(_CAPT)} "
              f"v2_scores={len(v2_scores)} r2.trades={r2.get('trades')}",
              flush=True)

        keys = set(v1_scores) | set(v2_scores)
        per_trade = []
        v1_only, common_tr, v2_only = [], [], []
        bypass = 0  # V2 成交但无 score（动量突破旁路）
        for k in sorted(keys, key=lambda x: x[0]):
            ts, sd = k
            v1sc = v1_scores.get(k)
            v2sc = v2_scores.get(k)
            v1_buy = k in v1_trades
            v2_buy = k in v2_trades
            tr = v1_trades.get(k) or v2_trades.get(k)
            pnl = tr["pnl"] if tr else 0.0
            pnl_pct = tr["pnl_pct"] if tr else 0.0
            v1_score = v1sc["score"] if v1sc else None
            v2_score = v2sc["score"] if v2sc else None
            bd = v2sc["bd"] if v2sc else {}
            rec = {
                "symbol": name,
                "time": ts_fmt(ts),
                "direction": "LONG" if sd == "L" else "SHORT",
                "v1_score": v1_score,
                "v2_score": v2_score,
                "atr_score": bd.get("volatility"),
                "4h_score": bd.get("mtf_alignment"),
                "er_score": bd.get("er_momentum"),
                "breakout_score": bd.get("breakout_boost"),
                "penalty": bd.get("penalties"),
                "v1_action": "BUY" if v1_buy else "SKIP",
                "v2_action": "BUY" if v2_buy else "SKIP",
                "entry": tr["entry"] if tr else None,
                "exit": tr["exit"] if tr else None,
                "pnl_u": round(pnl, 2),
                "pnl_r": round(pnl_pct / sl_pct, 2),
                "pnl_pct": pnl_pct,
                "result": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT"),
            }
            per_trade.append(rec)
            if v1_buy and not v2_buy:
                v1_only.append(rec)
            elif v1_buy and v2_buy:
                common_tr.append(rec)
            elif (not v1_buy) and v2_buy:
                v2_only.append(rec)
            if v2_buy and v2_score is None:
                bypass += 1

        out["per_trade"][name] = per_trade
        out["table1"][name] = {
            "V1_only": stats(v1_only),
            "common": stats(common_tr),
            "V2_only": stats(v2_only),
        }
        # ---- 表二：V2 成交的 score 分桶 ----
        bins = [(0, 20), (20, 30), (30, 40), (40, 45), (45, 50),
                (50, 55), (55, 60), (60, 70), (70, 80), (80, 10 ** 9)]
        v2_recs = [r for r in per_trade if r["v2_action"] == "BUY"]
        buckets = []
        for lo, hi in bins:
            label = f"{lo}-{hi if hi < 10 ** 9 else '+'}"
            grp = [r for r in v2_recs
                   if r["v2_score"] is not None and lo <= r["v2_score"] < hi]
            st = stats(grp)
            st["bin"] = label
            buckets.append(st)
        out["table2"][name] = buckets
        out["meta"][name] = {
            "v1_trades": len(v1_trades), "v2_trades": len(v2_trades),
            "v2_bypass_no_score": bypass,
        }

        # ---- 打印表一 ----
        print(f"\n— {name} 表一：V1 vs V2 逐笔分类（Trades/PnL/E%/PF/WR）—", flush=True)
        for label, st in [("V1 独有(被V2过滤)", out["table1"][name]["V1_only"]),
                          ("V1+V2 共同", out["table1"][name]["common"]),
                          ("V2 独有(新增)", out["table1"][name]["V2_only"])]:
            pf = "∞" if st["pf"] is None else f"{st['pf']:.2f}"
            print(f"  {label:18s}: T={st['trades']:>3}  PnL={st['pnl']:>8.2f}U  "
                  f"E={st['e']:>6.3f}%  PF={pf:>5}  WR={st['wr']:>5.1f}%",
                  flush=True)
        # ---- 打印表二 ----
        print(f"\n— {name} 表二：V2 Score 分桶（仅 V2 成交，旁路 {bypass} 笔无分不计入）—",
              flush=True)
        for st in buckets:
            if st["trades"] == 0:
                continue
            pf = "∞" if st["pf"] is None else f"{st['pf']:.2f}"
            print(f"  {st['bin']:>7s}: T={st['trades']:>3}  PnL={st['pnl']:>8.2f}U  "
                  f"E={st['e']:>6.3f}%  PF={pf:>5}  WR={st['wr']:>5.1f}%",
                  flush=True)
        # ---- 逐笔样例（前 6 行） ----
        print(f"\n— {name} 逐笔样例（前 6 行；完整见 _v1_v2_trades.json）—", flush=True)
        for r in per_trade[:6]:
            print(f"  {r['time']} {r['direction']:5s} V1={str(r['v1_score']):>5} "
                  f"V2={str(r['v2_score']):>5} ATR={str(r['atr_score']):>4} "
                  f"4H={str(r['4h_score']):>4} ER={str(r['er_score']):>4} "
                  f"BO={str(r['breakout_score']):>4} Pen={str(r['penalty']):>4} "
                  f"{r['v1_action']:>4}→{r['v2_action']:>4} "
                  f"{r['pnl_r']:>+5}R {r['result']}", flush=True)

    RS.score_signal = _SCORE_ORIG
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_v1_v2_trades.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path} | 总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
