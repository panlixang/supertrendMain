# -*- coding: utf-8 -*-
"""算 43 线上当前配置下 SNDK 的盈亏/收益信息。

① 实时拉 43 的 pattern config，取 SNDK-USDT-SWAP 的「当前」出场/周期参数；
② 用本地 db 的 SNDK 1h/4h 数据，按 V3 口径(filter_v3=True) 生成信号；
③ 用①的配置跑回测，给出：
   - 复利收益%(1x, 初始1000U) 这一标准口径的 收益/回撤/胜率/PF/Calmar；
   - 贴近 43 实盘仓位的固定名义(60U×10x=600U/笔) 的 净U / 账户收益%。
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bt_pattern_page import build_signals
from sl2_tf_sweep import load_db

HOST = "http://43.108.10.84:5174"
SYM = "SNDK-USDT"
FEE = 0.05 / 100          # 单边 taker，与 bt_pattern_page / bt_sndk 一致
SL_FALLBACK = 2.0


def _get_cfg43():
    d = json.loads(urllib.request.urlopen(
        HOST + "/api/pattern/trade/config", timeout=15).read())
    for s in d.get("symbols") or []:
        if s["symbol"].startswith(SYM):
            return s
    raise SystemExit("43 上没找到 %s" % SYM)


def _ping():
    try:
        return json.loads(urllib.request.urlopen(
            HOST + "/api/pattern/trade/ping", timeout=12).read())
    except Exception:
        return {}


def backtest(sigs, highs, lows, closes, up_plot, dn_plot, flip_idx,
             tp1_pct, tp1_ratio, sl_mode, sl_pct, trail_with_st,
             move_sl_to_entry=True, reverse_close=False,
             notional=600.0, fee=FEE, init_cash=1000.0, diag=True):
    """固定名义 notional/笔，coins=notional/entry；返回复利口径的权益曲线与逐笔。"""
    tp1_ratio = tp1_ratio / 100.0
    cash = init_cash
    eq_curve = [cash]
    trades = []
    last_exit = -1
    for s in sigs:
        i, long = s["i"], s["dir"] > 0
        if i <= last_exit:
            continue
        entry = closes[i]
        coins = notional / entry
        if sl_mode == "st":
            stp0 = up_plot[i] if long else dn_plot[i]
            if stp0 is None or (long and stp0 >= entry) or (not long and stp0 <= entry):
                stp0 = entry * (1 - SL_FALLBACK / 100) if long else entry * (1 + SL_FALLBACK / 100)
        else:
            stp0 = entry * (1 - sl_pct / 100) if long else entry * (1 + sl_pct / 100)
        stop = stp0
        tp1p = entry * (1 + tp1_pct / 100) if long else entry * (1 - tp1_pct / 100)
        tp1 = False
        pnl = -entry * coins * fee
        reason, ex, ex_i, closed = "末根平仓", closes[-1], len(closes) - 1, False
        for j in range(i + 1, len(closes)):
            if not reverse_close:
                if trail_with_st:
                    nl = up_plot[j] if long else dn_plot[j]
                    if nl is not None:
                        if long and nl > stop:
                            stop = nl
                        elif (not long) and nl < stop:
                            stop = nl
                if long and lows[j] <= stop:
                    px, rest = stop, 1 - (tp1_ratio if tp1 else 0)
                    pnl += (px - entry) * coins * rest - px * coins * rest * fee
                    reason, ex, ex_i, closed = "止损", px, j, True
                    break
                if (not long) and highs[j] >= stop:
                    px, rest = stop, 1 - (tp1_ratio if tp1 else 0)
                    pnl += (entry - px) * coins * rest - px * coins * rest * fee
                    reason, ex, ex_i, closed = "止损", px, j, True
                    break
                if not tp1:
                    if long and highs[j] >= tp1p:
                        pnl += (tp1p - entry) * coins * tp1_ratio - tp1p * coins * tp1_ratio * fee
                        tp1, stop = True, (entry if move_sl_to_entry else stop)
                    elif (not long) and lows[j] <= tp1p:
                        pnl += (entry - tp1p) * coins * tp1_ratio - tp1p * coins * tp1_ratio * fee
                        tp1, stop = True, (entry if move_sl_to_entry else stop)
            if j in flip_idx:
                px = closes[j]
                rest = 1 - (tp1_ratio if tp1 else 0)
                pnl += ((px - entry) if long else (entry - px)) * coins * rest - px * coins * rest * fee
                reason, ex, ex_i, closed = "反向信号", px, j, True
                break
        if not closed:
            px = closes[-1]
            rest = 1 - (tp1_ratio if tp1 else 0)
            pnl += ((px - entry) if long else (entry - px)) * coins * rest - px * coins * rest * fee
        cash += pnl
        eq_curve.append(cash)
        trades.append({"pnl": pnl, "dir": s["dir"], "reason": reason, "tp1": tp1,
                       "entry": entry, "exit": ex, "stop0": stp0,
                       "ret_pct": pnl / notional * 100})
        last_exit = ex_i
        if diag and len(trades) <= 15:
            print(f"  [t{len(trades):2d}] {'多' if long else '空'} "
                  f"entry={entry:.2f} exit={ex:.2f} stop0={stp0:.2f} "
                  f"reason={reason} tp1={tp1} pnl={pnl:+.2f}U ret={pnl/notional*100:+.2f}%")
    return cash, eq_curve, trades


def metrics(cash0, eq_curve, trades, notional):
    equity = eq_curve[-1]
    ret = (equity / cash0 - 1) * 100
    peak = cash0
    max_dd = 0.0
    for e in eq_curve:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak * 100)
    wins = [t for t in trades if t["pnl"] > 0]
    loss = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = sum(-t["pnl"] for t in loss)
    pf = gw / gl if gl > 0 else float("inf")
    n = len(trades)
    return dict(
        net_u=equity - cash0, ret_pct=ret, dd=max_dd,
        pf=pf, n=n, wr=len(wins) / n * 100 if n else 0,
        cal=(ret / max_dd if max_dd > 0 else 0.0),
        avg_ret=sum(t["ret_pct"] for t in trades) / n if n else 0,
        acct_ret_pct=(equity - cash0) / cash0 * 100 if cash0 else 0,
    )


def main():
    cfg = _get_cfg43()
    p = dict(tp1_pct=cfg["tp1_pct"], tp1_ratio=cfg["tp1_ratio"],
             sl_mode=cfg["sl_mode"], sl_pct=cfg["sl_pct"],
             trail_with_st=cfg["trail_with_st"],
             move_sl_to_entry=cfg["move_sl_to_entry"],
             reverse_close=cfg["reverse_close"])
    margin = cfg["margin_usdt"]
    lev = cfg["leverage"]
    notional = margin * lev
    tfs = cfg.get("allow_tfs") or ["1h"]
    v3 = cfg.get("filter_v3")

    ping = _ping()
    acc = ping.get("equity")
    print("=" * 78)
    print(f"43 线上当前配置 · {cfg['symbol']} (enabled={cfg['enabled']})")
    print(f"  出场: tp1={p['tp1_pct']}/{p['tp1_ratio']}  exit_mode={cfg.get('exit_mode')}  "
          f"sl={p['sl_mode']}/{p['sl_pct']} 保本={p['move_sl_to_entry']} "
          f"ST跟踪={p['trail_with_st']} 反向平={p['reverse_close']}")
    print(f"  过滤: filter_v3={v3} ｜ 周期={tfs} ｜ 仓位: 固定 {margin}U × {lev}x = {notional}U/笔")
    if acc:
        print(f"  43 账户实时净值(上下文): {acc:.2f}U ｜ 单笔名义/净值 ≈ {notional/acc:.2f}x")
    print("=" * 78)

    base = load_db(SYM, "1h")
    h4 = load_db(SYM, "4h")
    if not base or not h4:
        print("本地 db 无 SNDK 数据，先跑 download_sndk.py")
        return
    print(f"SNDK 1h {len(base)} 根 "
          f"{dt.datetime.utcfromtimestamp(base[0]['ts']/1000):%Y-%m-%d} ~ "
          f"{dt.datetime.utcfromtimestamp(base[-1]['ts']/1000):%Y-%m-%d} UTC")

    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = build_signals(base, h4)
    all_s = sigs
    c_s = [s for s in sigs if s.get("v3_execute")]
    print(f"信号: 全部翻转 {len(all_s)} ｜ V3放行(C) {len(c_s)}")
    print(f"── 用 {('V3(C)' if v3 else '全部翻转')} 信号回测（43 当前配置）──")
    target = c_s if v3 else all_s

    # 固定名义 600U/笔，初始 1000U 账户
    cash, eq, trades = backtest(target, highs, lows, closes, up_plot, dn_plot,
                                flip_idx, init_cash=1000.0, notional=notional,
                                diag=True, **p)
    m = metrics(1000.0, eq, trades, notional)
    print()
    print(f"[复利 1x/1000U] 收益 {m['ret_pct']:+.2f}%  回撤 {m['dd']:.2f}%  "
          f"PF {m['pf']:.2f}  笔数 {m['n']}  胜率 {m['wr']:.1f}%  Calmar {m['cal']:.2f}")
    print(f"[固定名义 {notional}U/笔, 账户1000U] 净 {m['net_u']:+.2f}U  "
          f"账户收益 {m['acct_ret_pct']:+.2f}%  单笔均收益 {m['avg_ret']:+.2f}%")
    if acc:
        # 把单笔均收益映射到真实账户：每笔占 notional，对账户贡献 ≈ 均收益×(notional/acc)
        per_trade_acct = m['avg_ret'] * (notional / acc)
        print(f"[真实账户 {acc:.2f}U, {notional}U/笔] 每笔平均对账户贡献 "
              f"{per_trade_acct:+.2f}%（共 {m['n']} 笔，理论累计≈{per_trade_acct*m['n']:+.1f}%）")
    # 离场原因分布
    from collections import Counter
    c = Counter(t["reason"] for t in trades)
    print(f"  离场原因: " + " ".join(f"{k}×{v}" for k, v in c.most_common()))


if __name__ == "__main__":
    main()
