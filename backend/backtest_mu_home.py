# -*- coding: utf-8 -*-
"""首页(V2 软分闸门) MU 配置回测：近 3 个月 vs 前 3 个月对比。

完全复用线上 /api/trade/symbols 的 MU 真实配置：
  - 入场：score_engine=v2, 阈值 40/40/40, use_dynamic_threshold=True
  - 出场：三档 TP(2.0/30, 2.5/40, 4.0/100 reverse_signal) + 保本 + 跟随 ST
  - ST 参数 periods=18, multiplier=3.0, src=hl2, EMA 快/慢 20/50
实时拉 OKX 收盘 K（confirm=1），与实盘只在收盘判信号一致。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from backtest_engine import run_backtest
from position_enhanced import EnhancedExitRules
from regime import TradeConfig

LIVE_URL = "http://43.108.10.84:5174/api/trade/symbols"
OKX = ["https://www.okx.com", "https://aws.okx.com"]
OKX_BAR = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
BIAS_TFS = ["15m", "1h", "4h", "1d"]
BARS = {"5m": 30000, "15m": 9000, "1h": 4500, "4h": 3000, "1d": 500}


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "supertrend-bt/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_candles(symbol, tf, limit):
    collected = {}
    bar = OKX_BAR[tf]
    after = None
    while len(collected) < limit:
        page_n = min(300, limit - len(collected))
        endpoint = "history-candles" if after else "candles"
        qs = f"instId={symbol}&bar={bar}&limit={page_n}"
        if after:
            qs += f"&after={after}"
        data = None
        for base in OKX:
            try:
                data = _get(f"{base}/api/v5/market/{endpoint}?{qs}")
                if data and data.get("code") == "0":
                    break
            except Exception:
                continue
        if not data or data.get("code") != "0" or not data.get("data"):
            break
        rows = data["data"]
        if not rows:
            break
        for row in rows:
            try:
                if len(row) > 8 and row[8] != "1":
                    continue
                ts = int(row[0])
                collected[ts] = {"ts": ts, "o": float(row[1]), "h": float(row[2]),
                                 "l": float(row[3]), "c": float(row[4]), "vol": float(row[5])}
            except (IndexError, ValueError):
                continue
        after = rows[-1][0]
        if len(rows) < page_n:
            break
        time.sleep(0.06)
    return [collected[k] for k in sorted(collected)][-limit:]


def ts_fmt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def trade_cfg(sym):
    return TradeConfig(
        enabled=True, leverage=sym["leverage"], amount_usdt=sym["margin_usdt"],
        er_hide_below=sym["er_hide_below"], er_weak_min=sym["er_weak_min"],
        er_min=sym["er_min"], er_trend=sym["er_trend"],
        quick_enabled=sym["quick_enabled"], allow_grades=list(sym["allow_grades"]),
        min_score=sym["min_score"], allow_tfs=list(sym["allow_tfs"]),
        cooldown_sec=sym["cooldown_sec"],
        atr_filter_enabled=sym["atr_filter_enabled"], atr_vol_min=sym["atr_vol_min"],
        range_filter_enabled=sym["range_filter_enabled"], range_size_max=sym["range_size_max"],
        range_touches_min=sym["range_touches_min"],
        mtf_filter_enabled=sym["mtf_filter_enabled"], mtf_consistency_min=sym["mtf_consistency_min"],
        mtf_flip_max=sym["mtf_flip_max"],
        adx_filter_enabled=sym["adx_filter_enabled"], adx_min=sym["adx_min"], adx_period=sym["adx_period"],
        use_scoring=sym.get("use_scoring", True),
        scoring_full_threshold=sym.get("scoring_full_threshold", 80.0),
        scoring_half_threshold=sym.get("scoring_half_threshold", 60.0),
        scoring_alert_threshold=sym.get("scoring_alert_threshold", 40.0),
        use_dynamic_threshold=sym.get("use_dynamic_threshold", True),
        score_engine=sym.get("score_engine", ""),
    )


def exit_rules(sym):
    r = sym["exit_rules"]
    return EnhancedExitRules(
        enabled=r.get("enabled", True),
        tp1_pct=r["tp1_pct"], tp1_ratio=r["tp1_ratio"],
        tp2_pct=r.get("tp2_pct", 2.0), tp2_ratio=r.get("tp2_ratio", 40.0),
        tp3_pct=r.get("tp3_pct", 3.5), tp3_ratio=r.get("tp3_ratio", 100.0),
        tp3_mode=r.get("tp3_mode", "pct"),
        move_sl_to_entry=r.get("move_sl_to_entry", True),
        sl_mode=r.get("sl_mode", "st"), sl_pct=r.get("sl_pct", 2.0),
        trail_with_st=r.get("trail_with_st", True),
        sl_buffer_atr=r.get("sl_buffer_atr", 0.5),
        sl_min_pct=r.get("sl_min_pct", 1.2),
        protect_profit_at=r.get("protect_profit_at", 1.5),
        protect_trail_pct=r.get("protect_trail_pct", 0.8),
        max_loss_enabled=r.get("max_loss_enabled", False),
        max_loss_pct=r.get("max_loss_pct", 10.0),
    )


def exit_rules_single(sym):
    """形态页风格：仅单档 tp1:1.5%/70%，其余仓位跟随 ST 止损（关闭二/三档）。"""
    r = dict(sym["exit_rules"])
    r["tp1_pct"] = 1.5
    r["tp1_ratio"] = 70.0
    r["tp2_pct"] = 999.0
    r["tp2_ratio"] = 0.0
    r["tp3_pct"] = 999.0
    r["tp3_ratio"] = 0.0
    r["tp3_mode"] = "pct"
    r["move_sl_to_entry"] = True
    r["sl_mode"] = "st"
    r["trail_with_st"] = True
    return EnhancedExitRules(
        enabled=r.get("enabled", True),
        tp1_pct=r["tp1_pct"], tp1_ratio=r["tp1_ratio"],
        tp2_pct=r["tp2_pct"], tp2_ratio=r["tp2_ratio"],
        tp3_pct=r["tp3_pct"], tp3_ratio=r["tp3_ratio"],
        tp3_mode=r.get("tp3_mode", "pct"),
        move_sl_to_entry=r.get("move_sl_to_entry", True),
        sl_mode=r.get("sl_mode", "st"), sl_pct=r.get("sl_pct", 2.0),
        trail_with_st=r.get("trail_with_st", True),
        sl_buffer_atr=r.get("sl_buffer_atr", 0.5),
        sl_min_pct=r.get("sl_min_pct", 1.2),
        protect_profit_at=r.get("protect_profit_at", 1.5),
        protect_trail_pct=r.get("protect_trail_pct", 0.8),
        max_loss_enabled=r.get("max_loss_enabled", False),
        max_loss_pct=r.get("max_loss_pct", 10.0),
    )


def get_symbol(name):
    d = _get(LIVE_URL)
    for s in d["symbols"]:
        if name in s["symbol"].upper():
            return s
    raise RuntimeError(f"{name} 不在线上配置")


def metrics(r):
    if "error" in r:
        return {"error": r["error"]}
    wr = r.get("win_rate") or 0.0
    aw = r.get("avg_win") or 0.0
    al = r.get("avg_loss") or 0.0
    exp = ((wr / 100) * aw - (1 - wr / 100) * abs(al)) if r.get("trades") else 0.0
    pnl = round((r.get("final", 100) - 100), 2)
    margin = r.get("init_cash", 100)
    return {
        "t": r.get("trades", 0), "pnl": pnl,
        "margin_roi": round(pnl / margin * 100, 1),
        "e": round(exp, 3), "pf": round(r.get("profit_factor") or 0, 2),
        "dd": round(r.get("max_dd_pct") or 0, 1), "wr": round(wr, 1),
        "hold": round(r.get("hold_pct") or 0, 1), "blocked": r.get("er_blocked") or 0,
        "tp1": r.get("tp1_count") or 0, "tp2": r.get("tp2_count") or 0,
        "tp3": r.get("tp3_count") or 0, "stops": r.get("stop_count") or 0,
        "reverses": r.get("reverse_count") or 0,
    }


def backtest_symbol(sym, out):
    gate_tf = sym["allow_tfs"][0]
    se_cur = sym.get("score_engine")
    print(f"\n##### {sym['symbol']} (gate_tf={gate_tf}) "
          f"leverage={sym['leverage']} margin={sym['margin_usdt']} #####", flush=True)
    print(f"  params={sym['params']}\n  当前 score_engine={se_cur!r} "
          f"thr={sym.get('scoring_full_threshold')}/{sym.get('scoring_half_threshold')} "
          f"dyn={sym.get('use_dynamic_threshold')}", flush=True)
    print("  拉取 K 线 ...", flush=True)
    tfs = list(BIAS_TFS)
    if gate_tf not in tfs:
        tfs = [gate_tf] + tfs
    cbtf = {}
    for tf in tfs:
        cbtf[tf] = fetch_candles(sym["symbol"], tf, BARS.get(tf, 4500))
    candles_full = cbtf[gate_tf]
    if len(candles_full) < 100:
        print(f"  !! {sym['symbol']} K 线不足 ({len(candles_full)})", flush=True)
        out[sym["symbol"]] = {"error": "no candles"}
        return
    print(f"  {gate_tf} K 范围: {ts_fmt(candles_full[0]['ts'])} ~ "
          f"{ts_fmt(candles_full[-1]['ts'])} (n={len(candles_full)})", flush=True)

    PREV_LO = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
    PREV_HI = int(datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)
    REC_LO = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    REC_HI = int(datetime(2026, 9, 27, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)

    ex = exit_rules_single(sym) if os.environ.get("BT_SINGLE") else exit_rules(sym)
    WINDOWS = [("前3月 04-01~06-30", PREV_LO, PREV_HI),
               ("近3月 07-01~09-27", REC_LO, REC_HI)]

    def slice_arr(arr, lo, hi):
        return [c for c in arr if lo <= c["ts"] <= hi]

    def run_one(se_force, v3_mode=False):
        s = dict(sym)
        if se_force is not None:
            s["score_engine"] = se_force
        if v3_mode:
            s = dict(s)
            s["params"] = {"periods": 10, "multiplier": 3.0}
        cfg = trade_cfg(s)
        res = {}
        for label, lo, hi in WINDOWS:
            seg = slice_arr(candles_full, lo, hi)
            cbtf_seg = {tf: slice_arr(arr, lo, hi) for tf, arr in cbtf.items() if arr}
            if len(seg) < 100:
                print(f"    [{label}] K 不足 ({len(seg)})", flush=True)
                continue
            r = run_backtest(
                seg, s["params"], init_cash=100.0, fee_rate=0.0005, allow_short=True,
                exit_rules=ex, sizing="fixed", margin_usdt=sym["margin_usdt"],
                leverage=sym["leverage"],
                live_gate=(None if v3_mode else cfg), gate_tf=gate_tf,
                candles_by_tf=cbtf_seg, v3_filter=v3_mode,
            )
            m = metrics(r)
            res[label] = m
            if "error" in m:
                print(f"    [{label}] error: {m['error']}", flush=True)
                continue
            tag = "V3挡" if v3_mode else "ER挡"
            print(f"    [{label}] 收益={m['pnl']}% PF={m['pf']} 成交={m['t']} "
                  f"胜率={m['wr']}% 回撤={m['dd']}% ({tag}={m['blocked']})", flush=True)
        return res

    is_v3 = bool(os.environ.get("BT_V3"))
    if is_v3:
        print("  -- 形态页口径 (ST10/3 + V3 + 单档) --", flush=True)
        v3 = run_one(None, v3_mode=True)
        out[sym["symbol"]] = {"v3": v3}
    else:
        print("  -- V2 口径 (quality_filter_v2 连续软分, 当前 MU 线上) --", flush=True)
        v2 = run_one(None)
        print("  -- V1 口径 (trend_follow_v1 阶梯引擎, 空串默认回退) --", flush=True)
        v1 = run_one("")
        out[sym["symbol"]] = {"v2": v2, "v1": v1}


def main():
    t0 = time.time()
    names = sys.argv[1:] or ["MU-USDT-SWAP", "SPCX-USDT-SWAP", "SNDK-USDT-SWAP"]
    out = {}
    for nm in names:
        try:
            sym = get_symbol(nm)
        except Exception as e:
            print(f"!! {nm} 获取失败: {e}", flush=True)
            continue
        backtest_symbol(sym, out)
    print(f"\n总耗时 {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
