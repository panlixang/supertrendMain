# -*- coding: utf-8 -*-
"""INTC 首页策略回测 —— 完全复用线上纯函数，参数来自 intc_config.json（线上真实配置）。

信号链路（与 feed.py 一致）：
    super_trend(1h, params) -> st_signals 找收盘根翻转
    -> strategy.evaluate 补 MTF Bias
    -> integration.enhanced_signal_handler (regime_enhanced.evaluate_enhanced) 闸门
    -> 开/平/止盈/止损 用 position_enhanced 纯函数

数据源：backend/candle_data.db 的 INTC-USDT 1h K线（本地已存，2026 全年 5325 根）

已知近似（不影响与实盘的一致性，因逻辑/参数相同）：
    1) 开仓价用信号根收盘价（实盘限价单，略有偏差）
    2) 止损/止盈用当根 high/low 判定触及（实盘逐秒 tick 检查）
    3) MTF Bias 仅用 15m/1h/4h（本地 db 缺 5m/30m/1d/1w/1M；线上用全 8 周期）
"""
import sqlite3, json, os, sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)   # 让 backend 内模块（indicators/state/...）可被 import

from indicators import super_trend, st_signals
from state import Params, SymbolTradeConfig
import strategy
from integration import enhanced_signal_handler
from position_enhanced import (EnhancedExitRules, EnhancedPosition,
                               check_enhanced, apply_tp_enhanced,
                               enhanced_initial_stop)

with open(os.path.join(HERE, "intc_config.json"), encoding="utf-8") as f:
    C = json.load(f)
sc, pr, er = C["symbol_cfg"], C["params"], C["exit_rules"]

cfg = SymbolTradeConfig(symbol=C["symbol"], **sc)
params = Params(**pr)
exit_rules = EnhancedExitRules(**er)

margin_usdt = float(cfg.margin_usdt)
leverage = int(cfg.leverage)
notional = margin_usdt * leverage
# 首页是单笔固定 notional 滚动（同时最多 1 笔持仓），以 notional 作资金曲线基准，
# 收益率% = 相对单笔名义本金，与实盘/寻优口径一致
INIT = notional
tf = "1h"


def load_tf(t):
    con = sqlite3.connect(os.path.join(ROOT, "candle_data.db"))
    rs = con.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol='INTC-USDT' AND tf=? ORDER BY ts",
        (t,)).fetchall()
    con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rs]


klines = load_tf(tf)
tf15 = load_tf("15m")
tf4 = load_tf("4h")
# 其它周期本地 db 无数据，留空（MTF Bias 仅 15m/1h/4h 近似）
all_candles = {"15m": tf15, "1h": klines, "4h": tf4}

N = len(klines)
print(f"[数据] INTC-USDT {tf} K线 {N} 根, 时间 {klines[0]['ts']} ~ {klines[-1]['ts']}")
print(f"[参数] ST {params.periods}x{params.multiplier} src={params.src} change_atr={params.change_atr} | "
      f"MTF {params.fast_len}/{params.slow_len} {params.ma_type}")
print(f"[交易] margin={margin_usdt}U leverage={leverage}x notional={notional}U allow_tfs={cfg.allow_tfs}")
print(f"[出场] tp1={exit_rules.tp1_pct}%/{exit_rules.tp1_ratio}% sl={exit_rules.sl_mode} {exit_rules.sl_pct}% "
      f"trail={exit_rules.trail_with_st} tp2/tp3={'关' if exit_rules.tp2_pct>=100 else '开'}")


def make_pos(sig):
    side = "long" if sig["type"] == "buy" else "short"
    entry = float(sig["price"])
    qty = notional / entry
    atr = sig.get("atr")
    stop = enhanced_initial_stop(sig, exit_rules, atr, leverage)
    return EnhancedPosition(
        symbol="INTC-USDT", side=side, tf=tf, entry=entry, qty=qty, init_qty=qty,
        stop=stop, leverage=leverage, entry_ts=sig["ts"], order_id="",
        ct_val=1.0, profile="normal")


pos = None
last_order_ts = {}
realized = 0.0
trades = []          # 每笔平仓: entry_ts,exit_ts,side,pnl,reason
equity_curve = []    # 逐根净值


def close_full(price, reason):
    """当前持仓全平，记账并报交易。"""
    global pos, realized
    if pos is None or pos.qty <= 0:
        return
    pnl = (price - pos.entry) * pos.qty if pos.long else (pos.entry - price) * pos.qty
    realized += pnl
    trades.append({"entry_ts": pos.entry_ts, "exit_ts": None, "side": pos.side,
                   "pnl": pnl, "reason": reason, "entry": pos.entry, "exit": price})
    pos = None


LIMIT = int(os.environ.get("LIMIT", "0")) or 0
end = N if LIMIT == 0 else min(N, LIMIT)
for i in range(end):
    cur = klines[i]
    window = klines[:i + 1]
    if len(window) < params.periods + 2:
        equity_curve.append(INIT + realized)
        continue

    # ── 1) 持仓检查（用当根 high/low 模拟 tick 触及）──
    if pos is not None and pos.qty > 0:
        long = pos.long
        stop = pos.stop
        tp1_price = (pos.entry * (1 + exit_rules.tp1_pct / 100) if long
                     else pos.entry * (1 - exit_rules.tp1_pct / 100))
        # 跟踪最高未实现盈利（用于盈利保护）
        pos.update_max_unrealized(cur["h"] if long else cur["l"])
        # 候选触发价位
        cands = []
        if long and cur["l"] <= stop:
            cands.append((stop, "stop"))
        if (not long) and cur["h"] >= stop:
            cands.append((stop, "stop"))
        if long and cur["h"] >= tp1_price:
            cands.append((tp1_price, "tp1"))
        if (not long) and cur["l"] <= tp1_price:
            cands.append((tp1_price, "tp1"))
        cands.sort(key=lambda x: 0 if x[1] == "stop" else 1)   # 止损优先
        for price, kind in cands:
            act = check_enhanced(pos, price, exit_rules, pos.max_unrealized_pct)
            if not act:
                continue
            if act["action"] == "stop":
                close_full(stop, "止损")
            elif act["action"] == "tp1":
                cq = min(pos.qty * exit_rules.tp1_ratio / 100, pos.qty)
                pnl = ((price - pos.entry) * cq if long else (pos.entry - price) * cq)
                realized += pnl
                trades.append({"entry_ts": pos.entry_ts, "exit_ts": cur["ts"], "side": pos.side,
                               "pnl": pnl, "reason": "tp1", "entry": pos.entry, "exit": price})
                apply_tp_enhanced(pos, price, cq, exit_rules, 1)
                if pos.qty <= 0:
                    pos = None
            break

    # ── 2) 信号 ──
    st = super_trend([c["o"] for c in window], [c["h"] for c in window],
                     [c["l"] for c in window], [c["c"] for c in window],
                     periods=params.periods, multiplier=params.multiplier,
                     src=params.src, change_atr=params.change_atr)
    if st.get("flips"):
        sigs = st_signals(window, st, tf)
        if sigs and sigs[-1]["ts"] == cur["ts"]:
            sig = sigs[-1]
            full = strategy.evaluate(all_candles, vars(params), sig)
            gate = enhanced_signal_handler(full, window, cfg,
                                          candles_by_tf=all_candles, p=params)
            if gate.get("trade"):
                last = last_order_ts.get(tf, 0)
                if (cur["ts"] - last) / 1000 >= cfg.cooldown_sec:
                    # 同周期反向 -> 平剩余（反转）
                    if pos is not None and pos.qty > 0:
                        want_long = sig["type"] == "buy"
                        if (pos.long and not want_long) or (not pos.long and want_long):
                            close_full(cur["c"], "反向信号平仓")
                    # 无同向持仓 -> 开新仓
                    if pos is None or pos.qty <= 0:
                        pos = make_pos(sig)
                        last_order_ts[tf] = cur["ts"]

    # ── 3) 净值记录 ──
    floating = 0.0
    if pos is not None and pos.qty > 0:
        floating = ((cur["c"] - pos.entry) * pos.qty if pos.long
                    else (pos.entry - cur["c"]) * pos.qty)
    equity_curve.append(INIT + realized + floating)


# 收尾：未平持仓按最后收盘价清掉
if pos is not None and pos.qty > 0:
    close_full(klines[end - 1]["c"], "回测结束平仓")


def stats(sub):
    if not sub:
        return None
    p = sum(t["pnl"] for t in sub if t["pnl"] > 0)
    l = -sum(t["pnl"] for t in sub if t["pnl"] < 0)
    pf = (p / l) if l > 0 else float("inf")
    win = sum(1 for t in sub if t["pnl"] > 0)
    return {
        "n": len(sub),
        "pnl": sum(t["pnl"] for t in sub),
        "ret_pct": sum(t["pnl"] for t in sub) / INIT * 100,
        "pf": pf,
        "win": win,
        "winrate": win / len(sub) * 100,
    }


# 按 entry_ts 排序切 IS/OOS（70/30）
ordered = sorted([t for t in trades if t.get("entry_ts")], key=lambda t: t["entry_ts"])
k = int(len(ordered) * 0.7)
is_t = ordered[:k]
oos_t = ordered[k:]

all_s = stats(trades)
is_s = stats(is_t)
oos_s = stats(oos_t)

# 最大回撤
peak = INIT
max_dd = 0.0
for eq in equity_curve:
    if eq > peak:
        peak = eq
    dd = (peak - eq) / peak if peak > 0 else 0
    if dd > max_dd:
        max_dd = dd

lines = []
A = lines.append
A("=" * 64)
A("INTC-USDT 首页策略回测（线上真实参数）")
A(f"K线: {tf}  {N} 根  时间窗 {klines[0]['ts']} ~ {klines[-1]['ts']}")
A(f"参数: ST {params.periods}x{params.multiplier} {params.src} chg_atr={params.change_atr} | "
  f"MTF {params.fast_len}/{params.slow_len} {params.ma_type}")
A(f"交易: {margin_usdt}U x{leverage} = {notional}U  入场=信号收盘  冷却={cfg.cooldown_sec}s")
A(f"出场: tp1={exit_rules.tp1_pct}%/{exit_rules.tp1_ratio}% 保本  "
  f"sl={exit_rules.sl_mode} {exit_rules.sl_pct}%  trail={exit_rules.trail_with_st}")
A(f"闸门: use_scoring={cfg.use_scoring} full/half/alert="
  f"{cfg.scoring_full_threshold}/{cfg.scoring_half_threshold}/{cfg.scoring_alert_threshold}")
A("-" * 64)
A(f"全样本: 笔数={all_s['n'] if all_s else 0}  收益={all_s['ret_pct']:+.2f}%  "
  f"PF={all_s['pf']:.2f} 胜率={all_s['winrate']:.1f}%  盈亏={all_s['pnl']:+.2f}U" if all_s else "全样本: 无交易")
A(f"IS(前70%): 笔数={is_s['n'] if is_s else 0}  收益={is_s['ret_pct']:+.2f}%  "
  f"PF={is_s['pf']:.2f} 胜率={is_s['winrate']:.1f}%" if is_s else "IS: 无交易")
A(f"OOS(后30%): 笔数={oos_s['n'] if oos_s else 0}  收益={oos_s['ret_pct']:+.2f}%  "
  f"PF={oos_s['pf']:.2f} 胜率={oos_s['winrate']:.1f}%" if oos_s else "OOS: 无交易")
A("-" * 64)
A(f"最大回撤: {max_dd*100:.2f}%   末净值: {equity_curve[-1]:.2f}U")
A("=" * 64)
A("逐笔明细（前20笔）:")
for t in trades[:20]:
    A(f"  {t['side']:5s} entry={t['entry']:.3f} exit={t['exit']:.3f} "
      f"pnl={t['pnl']:+.2f}U  {t['reason']}")
if len(trades) > 20:
    A(f"  ... 共 {len(trades)} 笔")

out = "\n".join(lines)
print(out)
with open(os.path.join(HERE, "intc_backtest_1h.txt"), "w", encoding="utf-8") as f:
    f.write(out + "\n")
print(f"\n[完成] 结果已写入 backend/backtest/intc_backtest_1h.txt")
