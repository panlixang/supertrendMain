# -*- coding: utf-8 -*-
"""历史版 Shadow 重放（不需要等实时样本积累）。

对两台服务器的全部线上品种，用本地 _live_data 历史 K 线，在每个 ST 翻转信号上
按 shadow.py 的同一口径重放双引擎评分：

    main   = 当前线上主引擎（43 全部 v1；47 的 NVDA/MU = v2）
    shadow = 与主引擎相反的另一引擎（与 _enable_shadow.py 同一规则）

每个信号输出与实时 shadow jsonl 同构的一行 + 未来收益回填：
    future_return_pct = (信号后第 horizon 根已收盘 1h close - 信号价) / 信号价 × 100
（与 _shadow_backfill.py 同口径，不模拟止盈止损，用于判断被过滤集合的赢亏方向）

落盘 backend/backtest/_shadow_replay/<SYM>.jsonl，并直接打印 A/B/C/D 分桶
（判读：C_主拒副买 future 均值 <0 → 影子引擎过滤有效；>0 → 影子在杀赢家）。

用法:
  python _shadow_replay.py                 # 全品种（43 + 47）
  python _shadow_replay.py --symbols CL    # 只看某品种（子串）
  python _shadow_replay.py --horizon 48    # 未来 48 根 1h
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import run_backtest  # noqa: E402  (确保环境可跑)
from indicators import super_trend, st_signals  # noqa: E402
from regime_scoring import score_signal  # noqa: E402
from _live_cfg_backtest import trade_cfg, _get  # noqa: E402

HOSTS = ["http://43.108.10.84:5174", "http://47.84.106.154:5174"]
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_data")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shadow_replay")
TF = "1h"
TFS = ["15m", "1h", "4h", "1d"]
HORIZON = 24

# 品种简称 → 本地数据文件（CL 用 half 缓存；其余用同名文件）
SYM_FILE = {
    "BTC": "BTC.json", "ETH": "ETH.json", "SPCX": "SPCX.json",
    "SNDK": "SNDK.json", "MU": "MU.json", "NVDA": "NVDA.json",
    "SKHYNIX": "SKHYNIX.json", "CL": "cl_half_cache.json",
}
TRADE = {"trade_full", "trade_half"}


def opposite(main: str) -> str:
    m = (main or "").strip().lower()
    if not m or m in ("v1", "trend_follow_v1"):
        return "v2"
    if m in ("v2", "quality_filter_v2"):
        return "v1"
    return None


def engine_name(e: str) -> str:
    aliases = {"v1": "trend_follow_v1", "v2": "quality_filter_v2"}
    return aliases.get((e or "").strip().lower(), e)


def cut_to(candles: list[dict], ts: int) -> list[dict]:
    return candles[:bisect.bisect_right([c["ts"] for c in candles], ts)]


def bucket_of(r: dict) -> str:
    m_trade, s_trade = r["main_action"] in TRADE, r["shadow_action"] in TRADE
    if m_trade and s_trade:
        return "A_一致买"
    if m_trade and not s_trade:
        return "B_主买副拒"
    if not m_trade and s_trade:
        return "C_主拒副买"
    return "D_一致不买"


def load_cbtf(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {TF: data}
    return {k: v for k, v in data.items() if isinstance(v, list) and k in TFS and v}


def replay_symbol(host: str, sym: dict, horizon: int):
    name = sym["symbol"].split("-")[0]
    fname = SYM_FILE.get(name)
    path = os.path.join(DATA_DIR, fname) if fname else None
    if not path or not os.path.exists(path):
        print(f"  [{host}] {sym['symbol']}: 无本地数据 {fname}，跳过", flush=True)
        return
    cbtf = load_cbtf(path)
    candles = cbtf.get(TF)
    if not candles or len(candles) < 300:
        print(f"  [{host}] {sym['symbol']}: 1h 数据不足，跳过", flush=True)
        return

    p = sym["params"]
    main_engine = sym.get("score_engine", "") or "v1"
    sh_eng = opposite(main_engine)
    if sh_eng is None:
        print(f"  [{host}] {sym['symbol']}: 未知主引擎 {main_engine!r}，跳过", flush=True)
        return
    cfg_main = trade_cfg(sym)
    cfg_sh = replace(cfg_main, score_engine=sh_eng, score_v2=False)

    st = super_trend([c["o"] for c in candles], [c["h"] for c in candles],
                     [c["l"] for c in candles], [c["c"] for c in candles],
                     periods=p.get("periods", 15), multiplier=p.get("multiplier", 9.1),
                     src=p.get("src", "hl2"), change_atr=p.get("change_atr", True))
    signals = st_signals(candles, st, TF)
    ts2idx = {c["ts"]: i for i, c in enumerate(candles)}
    ts_list = [c["ts"] for c in candles]

    import strategy as strategy_mod
    rows = []
    for s in signals:
        idx = ts2idx.get(s["ts"])
        if idx is None or idx < 40:
            continue
        c_i = candles[:idx + 1]
        cbtf_i = {t: cut_to(arr, s["ts"]) for t, arr in cbtf.items()}
        full = strategy_mod.evaluate(cbtf_i, p, s)
        a = score_signal(full, c_i, cfg_main, cbtf_i, p)
        b = score_signal(full, c_i, cfg_sh, cbtf_i, p)
        price = s.get("price")
        i_fut = bisect.bisect_right(ts_list, s["ts"])
        future = None
        if i_fut < len(candles) and price:
            future = round(
                (candles[min(i_fut + horizon, len(candles) - 1)]["c"] - price)
                / price * 100, 3)
        rows.append({
            "symbol": sym["symbol"],
            "ts": s["ts"], "tf": TF, "side": s.get("type"), "price": price,
            "main_engine": engine_name(main_engine),
            "main_score": a.get("total_score"), "main_action": a.get("action"),
            "main_reasons": a.get("reasons", []),
            "shadow_engine": engine_name(sh_eng),
            "shadow_score": b.get("total_score"), "shadow_action": b.get("action"),
            "shadow_reasons": b.get("reasons", []),
            "future_return_pct": future, "horizon": horizon,
        })

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"shadow_{name}.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    from collections import OrderedDict
    buckets = OrderedDict()
    for r in rows:
        b = bucket_of(r)
        d = buckets.setdefault(b, {"n": 0, "futs": [], "sides": set()})
        d["n"] += 1
        d["sides"].add(r["side"])
        if r["future_return_pct"] is not None:
            d["futs"].append(r["future_return_pct"])
    print(f"== {sym['symbol']}  [{host}]  {main_engine or '(v1)'}主 + {sh_eng}影子  "
          f"信号 {len(rows)} 个 → {out_path}", flush=True)
    for b in ["A_一致买", "B_主买副拒", "C_主拒副买", "D_一致不买"]:
        if b not in buckets:
            continue
        d = buckets[b]
        line = f"   {b:<9} n={d['n']:>4}  sides={'/'.join(sorted(d['sides']))}"
        if d["futs"]:
            avg = sum(d["futs"]) / len(d["futs"])
            pos = sum(1 for x in d["futs"] if x > 0)
            line += (f"  future avg={avg:+.3f}%  win={pos}/{len(d['futs'])}"
                     f" ({pos/len(d['futs'])*100:.0f}%)")
        print(line, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="只看某品种（子串匹配，如 CL）")
    ap.add_argument("--horizon", type=int, default=HORIZON, help="未来 N 根 1h")
    args = ap.parse_args()
    kw = args.symbols.upper()
    for host in HOSTS:
        print(f"\n######## {host} ########", flush=True)
        try:
            syms = _get(host + "/api/trade/symbols")["symbols"]
        except Exception as e:
            print(f"  GET 失败: {e}", flush=True)
            continue
        for sym in syms:
            if kw and kw not in sym["symbol"].upper():
                continue
            t0 = time.time()
            try:
                replay_symbol(host, sym, args.horizon)
            except Exception as e:
                import traceback
                print(f"  [{host}] {sym['symbol']} 重放失败: {e}", flush=True)
                traceback.print_exc()
            print(f"   (耗时 {time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
