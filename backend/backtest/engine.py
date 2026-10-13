# -*- coding: utf-8 -*-
"""通用首页策略回测引擎 —— 完全复用线上纯函数，参数来自线上真实配置。

兼容两种配置结构：
  - INTC 式：{symbol, symbol_cfg:{...}, params, exit_rules}
  - AMD 式 ：平铺品种字段 + {params, exit_rules}

信号链路（与 feed.py 一致）：
    super_trend(1h, params) -> st_signals 找收盘根翻转
    -> strategy.evaluate 补 MTF Bias
    -> integration.enhanced_signal_handler (regime_enhanced.evaluate_enhanced) 闸门
    -> 开/平/止盈/止损 用 position_enhanced 纯函数

已知近似（不影响与实盘一致性，因逻辑/参数相同）：
    1) 开仓价用信号根收盘价（实盘限价单，略有偏差）
    2) 止损/止盈用当根 high/low 判定触及（实盘逐秒 tick 检查）
    3) MTF Bias 仅用 15m/1h/4h（本地 db 缺 5m/30m/1d/1w/1M；线上用全 8 周期）

对外接口：
    run_backtest(db_symbol, config_path, out_dir, out_name, tf, limit)
        —— 读配置、回测、写报告（供单品种脚本调用）
    run_core(db_symbol, sc, pr, er, tf, limit, candles)
        —— 纯回测核心，无 IO，返回指标 dict（供参数寻优循环调用）
"""
import sqlite3, json, os, sys, dataclasses, copy
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

from indicators import super_trend, st_signals
from state import Params, SymbolTradeConfig


class _ParamsProxy:
    """让 Params 对象同时支持属性访问与 dict 式 .get。

    enhanced_signal_handler 内部（regime_scoring._mtf_soft_part）会把 p 当
    dict 用 p.get("periods"/"multiplier"/...) 取 ST 参数；而其它路径又用
    p.xxx 属性访问。Params 只有属性无 .get，故在此包一层兼容代理。
    """
    def __init__(self, o):
        self._o = o
    def get(self, k, d=None):
        return getattr(self._o, k, d)
    def __getattr__(self, k):
        return getattr(self._o, k)


def _as_p(p):
    return p if isinstance(p, dict) else _ParamsProxy(p)
import strategy
from integration import enhanced_signal_handler
from position_enhanced import (EnhancedExitRules, EnhancedPosition,
                               check_enhanced, apply_tp_enhanced,
                               enhanced_initial_stop)


def _extract(config):
    """从配置里取出 (symbol_cfg_dict, params_dict, exit_rules_dict)，兼容平铺/包裹两种结构。"""
    SC_FIELDS = {f.name for f in dataclasses.fields(SymbolTradeConfig)}
    sc_src = config.get("symbol_cfg", config)
    sc = {}
    for k in SC_FIELDS:
        if k in config:
            sc[k] = config[k]
        elif k in sc_src:
            sc[k] = sc_src[k]
    return sc, config["params"], config["exit_rules"]


def _load_tf(db_symbol, t):
    con = sqlite3.connect(os.path.join(BACKEND, "candle_data.db"))
    rs = con.execute(
        "SELECT ts,o,h,l,c,vol FROM candles WHERE symbol=? AND tf=? ORDER BY ts",
        (db_symbol, t)).fetchall()
    con.close()
    return [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "vol": r[5]} for r in rs]


def run_core(db_symbol, sc, pr, er, tf="1h", limit=0, candles=None):
    """纯回测核心：接受已解析的 sc/pr/er（dict），返回指标 dict，不写任何文件。

    candles: 可选预加载的 {tf: [...]}（寻优时复用，避免重复读 db）。
    若 None 则从本地 db 加载 1h/15m/4h。
    """
    cfg = SymbolTradeConfig(**sc)
    params = Params(**pr)
    exit_rules = EnhancedExitRules(**er)

    margin_usdt = float(cfg.margin_usdt)
    leverage = int(cfg.leverage)
    notional = margin_usdt * leverage
    INIT = notional

    if candles is None:
        tf15 = _load_tf(db_symbol, "15m")
        klines = _load_tf(db_symbol, tf)
        tf4 = _load_tf(db_symbol, "4h")
        candles = {"15m": tf15, "1h": klines, "4h": tf4}
    klines = candles[tf]
    all_candles = candles

    N = len(klines)

    # 整段一次性预计算 SuperTrend 与其信号（O(N)，替代原逐根 O(N^2) 重算）
    # 关键等价：原逐根回测时每根的 window 末尾就是该信号根本身，故 st_signals
    # 里的 chg->pnl_pct、bars_since 恒为 0；预计算后强制归零以完全等价。
    st_full = super_trend([c["o"] for c in klines], [c["h"] for c in klines],
                          [c["l"] for c in klines], [c["c"] for c in klines],
                          periods=params.periods, multiplier=params.multiplier,
                          src=params.src, change_atr=params.change_atr)
    all_sigs = st_signals(klines, st_full, tf)
    for _s in all_sigs:
        _s["pnl_pct"] = 0.0
        _s["bars_since"] = 0
    sig_by_ts = {_s["ts"]: _s for _s in all_sigs}

    def make_pos(sig):
        side = "long" if sig["type"] == "buy" else "short"
        entry = float(sig["price"])
        qty = notional / entry
        atr = sig.get("atr")
        stop = enhanced_initial_stop(sig, exit_rules, atr, leverage)
        return EnhancedPosition(
            symbol=db_symbol, side=side, tf=tf, entry=entry, qty=qty, init_qty=qty,
            stop=stop, leverage=leverage, entry_ts=sig["ts"], order_id="",
            ct_val=1.0, profile="normal")

    pos = None
    last_order_ts = {}
    realized = 0.0
    trades = []
    equity_curve = []

    def close_full(price, reason):
        nonlocal pos, realized
        if pos is None or pos.qty <= 0:
            return
        pnl = (price - pos.entry) * pos.qty if pos.long else (pos.entry - price) * pos.qty
        realized += pnl
        trades.append({"entry_ts": pos.entry_ts, "exit_ts": None, "side": pos.side,
                       "pnl": pnl, "reason": reason, "entry": pos.entry, "exit": price})
        pos = None

    end = N if limit == 0 else min(N, limit)
    for i in range(end):
        cur = klines[i]
        window = klines[:i + 1]
        if len(window) < params.periods + 2:
            equity_curve.append(INIT + realized)
            continue

        if pos is not None and pos.qty > 0:
            long = pos.long
            stop = pos.stop
            pos.update_max_unrealized(cur["h"] if long else cur["l"])
            mu = pos.max_unrealized_pct

            stop_eval = cur["l"] if long else cur["h"]
            act = check_enhanced(pos, stop_eval, exit_rules, mu)
            if act and act["action"] in ("stop", "max_stop"):
                px = stop if act["action"] == "stop" else stop_eval
                close_full(px, "止损" if act["action"] == "stop" else "极端止损")
            else:
                tp_eval = cur["h"] if long else cur["l"]
                act2 = check_enhanced(pos, tp_eval, exit_rules, mu)
                if act2 and act2["action"] in ("tp1", "tp2", "tp3"):
                    stage = {"tp1": 1, "tp2": 2, "tp3": 3}[act2["action"]]
                    pct = {"tp1": exit_rules.tp1_pct, "tp2": exit_rules.tp2_pct,
                           "tp3": exit_rules.tp3_pct}[act2["action"]]
                    fill = pos.entry * (1 + pct / 100) if long else pos.entry * (1 - pct / 100)
                    cq = min(pos.qty * act2["ratio"] / 100, pos.qty)
                    pnl = ((fill - pos.entry) * cq if long else (pos.entry - fill) * cq)
                    realized += pnl
                    trades.append({"entry_ts": pos.entry_ts, "exit_ts": cur["ts"], "side": pos.side,
                                   "pnl": pnl, "reason": act2["action"], "entry": pos.entry, "exit": fill})
                    apply_tp_enhanced(pos, fill, cq, exit_rules, stage)
                    if pos.qty <= 0:
                        pos = None

        sig = sig_by_ts.get(cur["ts"])
        if sig is not None:
            full = strategy.evaluate(all_candles, vars(params), sig)
            gate = enhanced_signal_handler(full, window, cfg,
                                          candles_by_tf=all_candles, p=_as_p(params))
            if gate.get("trade"):
                last = last_order_ts.get(tf, 0)
                if (cur["ts"] - last) / 1000 >= cfg.cooldown_sec:
                    if pos is not None and pos.qty > 0:
                        want_long = sig["type"] == "buy"
                        if (pos.long and not want_long) or (not pos.long and want_long):
                            close_full(cur["c"], "反向信号平仓")
                    if pos is None or pos.qty <= 0:
                        pos = make_pos(sig)
                        last_order_ts[tf] = cur["ts"]

        floating = 0.0
        if pos is not None and pos.qty > 0:
            floating = ((cur["c"] - pos.entry) * pos.qty if pos.long
                        else (pos.entry - cur["c"]) * pos.qty)
        equity_curve.append(INIT + realized + floating)

    if pos is not None and pos.qty > 0:
        close_full(klines[end - 1]["c"], "回测结束平仓")

    def stats(sub):
        if not sub:
            return None
        p = sum(t["pnl"] for t in sub if t["pnl"] > 0)
        l = -sum(t["pnl"] for t in sub if t["pnl"] < 0)
        pf = (p / l) if l > 0 else float("inf")
        win = sum(1 for t in sub if t["pnl"] > 0)
        return {"n": len(sub), "pnl": sum(t["pnl"] for t in sub),
                "ret_pct": sum(t["pnl"] for t in sub) / INIT * 100,
                "pf": pf, "win": win, "winrate": win / len(sub) * 100}

    ordered = sorted([t for t in trades if t.get("entry_ts")], key=lambda t: t["entry_ts"])
    k = int(len(ordered) * 0.7)
    is_t = ordered[:k]
    oos_t = ordered[k:]
    all_s, is_s, oos_s = stats(trades), stats(is_t), stats(oos_t)

    peak = INIT
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "db_symbol": db_symbol, "tf": tf, "n": N,
        "params": params, "exit_rules": exit_rules, "cfg": cfg, "INIT": INIT,
        "klines": klines, "trades": trades, "equity_curve": equity_curve,
        "all": all_s, "is": is_s, "oos": oos_s, "max_dd": max_dd * 100,
    }


def run_backtest(db_symbol, config_path, out_dir, out_name="backtest_1h.txt", tf="1h", limit=0):
    with open(config_path, encoding="utf-8") as f:
        C = json.load(f)
    sc, pr, er = _extract(C)
    r = run_core(db_symbol, sc, pr, er, tf=tf, limit=limit)

    params, exit_rules, cfg = r["params"], r["exit_rules"], r["cfg"]
    klines, N = r["klines"], r["n"]
    all_s, is_s, oos_s, max_dd = r["all"], r["is"], r["oos"], r["max_dd"]

    print(f"[数据] {db_symbol} {tf} K线 {N} 根, 时间 {klines[0]['ts']} ~ {klines[-1]['ts']}")
    print(f"[参数] ST {params.periods}x{params.multiplier} src={params.src} change_atr={params.change_atr} | "
          f"MTF {params.fast_len}/{params.slow_len} {params.ma_type}")
    print(f"[交易] margin={float(cfg.margin_usdt)}U leverage={int(cfg.leverage)}x "
          f"notional={float(cfg.margin_usdt)*int(cfg.leverage)}U allow_tfs={cfg.allow_tfs}")
    print(f"[出场] tp1={exit_rules.tp1_pct}%/{exit_rules.tp1_ratio}% sl={exit_rules.sl_mode} {exit_rules.sl_pct}% "
          f"trail={exit_rules.trail_with_st} tp2/tp3={'关' if exit_rules.tp2_pct>=100 else '开'}")

    def make_pos_report():
        pass

    lines = []
    A = lines.append
    A("=" * 64)
    A(f"{db_symbol} 首页策略回测（线上真实参数）")
    A(f"K线: {tf}  {N} 根  时间窗 {klines[0]['ts']} ~ {klines[-1]['ts']}")
    A(f"参数: ST {params.periods}x{params.multiplier} {params.src} chg_atr={params.change_atr} | "
      f"MTF {params.fast_len}/{params.slow_len} {params.ma_type}")
    A(f"交易: {float(cfg.margin_usdt)}U x{int(cfg.leverage)} = {float(cfg.margin_usdt)*int(cfg.leverage)}U  "
      f"入场=信号收盘  冷却={cfg.cooldown_sec}s")
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
    A(f"最大回撤: {max_dd:.2f}%   末净值: {r['equity_curve'][-1]:.2f}U")
    A("=" * 64)
    A("逐笔明细（前20笔）:")
    for t in r["trades"][:20]:
        A(f"  {t['side']:5s} entry={t['entry']:.3f} exit={t['exit']:.3f} "
          f"pnl={t['pnl']:+.2f}U  {t['reason']}")
    if len(r["trades"]) > 20:
        A(f"  ... 共 {len(r['trades'])} 笔")

    out = "\n".join(lines)
    print(out)
    report = {
        "symbol": db_symbol, "tf": tf, "n": N,
        "params": {"st": f"{params.periods}x{params.multiplier}", "src": params.src,
                   "mtf": f"{params.fast_len}/{params.slow_len}{params.ma_type}"},
        "all": {"n": all_s["n"], "ret_pct": all_s["ret_pct"], "pf": all_s["pf"],
                "winrate": all_s["winrate"], "pnl": all_s["pnl"]} if all_s else None,
        "is": {"n": is_s["n"], "ret_pct": is_s["ret_pct"], "pf": is_s["pf"],
               "winrate": is_s["winrate"]} if is_s else None,
        "oos": {"n": oos_s["n"], "ret_pct": oos_s["ret_pct"], "pf": oos_s["pf"],
                "winrate": oos_s["winrate"]} if oos_s else None,
        "max_dd_pct": max_dd,
        "trades": r["trades"],
    }
    with open(os.path.join(out_dir, out_name), "w", encoding="utf-8") as f:
        f.write(out + "\n")
    with open(os.path.join(out_dir, out_name.replace(".txt", "_report.json")), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 结果已写入 {out_dir}/{out_name}")
