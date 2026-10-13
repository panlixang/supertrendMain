"""
INTC 回测 —— 首页（监控页）线上逻辑复现版

数据落 backend/backtest/ ：
  - intc_usdt_swap_1h.json        原始 1h K 线
  - intc_backtest_report.json     回测报告（含 IS/OOS 拆分）

逻辑严格复用线上代码（保证与 43/47 页面一致）：
  - 信号：indicators.super_trend 的翻转点（buy/sell）
  - 闸门：strategy.mtf_bias（首页 MTF Bias 过滤，不含 V3 / signal_v3）
  - 出场：position_enhanced 的三级止盈 / 智能止损 / 跟踪（sl_mode=st 跟随超趋线）

INTC 不在 pattern_trade.json 里，线上 cfg_for 会返回 enabled=False（永不下单）。
因此这里用线上【全局默认交易参数】（pattern_trade.json 的 cfg 块）作为 INTC 模板，
全部集中在 CONFIG，方便你直接改。

注意（为透明起见）：
  首页的完整闸门还含 ER 评分引擎 / ADX / 区间 / Shadow / 冷却 等，那些依赖
  settings.json 且非策略核心。本脚本只复现最核心的两道：SuperTrend 翻转 + MTF
  Bias 方向过滤 + 增强出场。如需逐样照搬可再补。
"""

from __future__ import annotations
import os
import sys
import json
import bisect
import logging

# backend/ 在 backtest/ 的上一级，加进 path 才能 import 线上模块
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

import indicators          # super_trend, bias
import strategy           # mtf_bias
import position           # hit_stop
from position_enhanced import (
    EnhancedExitRules, enhanced_initial_stop, check_enhanced,
    apply_tp_enhanced, trail_enhanced,
)
import history            # fetch_candles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bt_intc")

# ───────────────────────────── CONFIG ─────────────────────────────
SYMBOL = "INTC-USDT-SWAP"
TF = "1h"
N_BARS = 3000                  # 拉取上限（OKX 翻页到头会自动停）

# 线上 SuperTrend 参数（state.Params 默认，所有品种共用）
ST = {"periods": 15, "multiplier": 9.1, "src": "hl2", "change_atr": True}
# 线上 MTF Bias 参数
BIAS = {"fast_len": 20, "slow_len": 50, "ma_type": "EMA"}
# 合并给线上函数用的参数字典
P = {**ST, **BIAS}

# 线上【全局默认交易参数】—— pattern_trade.json 的 cfg 块（INTC 无单独配置，用默认）
EXIT_CFG = {
    "tp1_pct": 1.5, "tp1_ratio": 70.0,
    "tp2_pct": 2.0, "tp2_ratio": 40.0,
    "tp3_pct": 3.5, "tp3_ratio": 100.0, "tp3_mode": "pct",
    "sl_pct": 2.0, "sl_mode": "st",
    "sl_buffer_atr": 0.5, "sl_min_pct": 1.2,
    "move_sl_to_entry": True, "trail_with_st": True,
    "protect_profit_at": 1.5, "protect_trail_pct": 0.8,
    "max_loss_enabled": False, "max_loss_pct": 10.0,
    "enabled": True,
}
LEVERAGE = 10
MARGIN_USDT = 50.0            # 仅用于把 % 折算成 USDT 展示（不影响 % 指标）

# 首页 MTF Bias 闸门
GATE_ALLOW_MIXED = True       # mixed 时按 bias score 正负放行（对齐首页 grade B/C 仍可交易）
COOLDOWN_BARS = 1             # 止损/止盈平仓后，间隔几根 1h 才允许再开（翻转同根棒除外）
IS_RATIO = 0.7                # IS/OOS 按时间切分比例

OUT_DIR = HERE
RAW_FILE = os.path.join(OUT_DIR, "intc_usdt_swap_1h.json")
REPORT_FILE = os.path.join(OUT_DIR, "intc_backtest_report.json")
# ───────────────────────────────────────────────────────────────────


class BP:
    """精简持仓，喂给 position_enhanced 的纯函数。"""
    def __init__(self, side: int, entry: float, qty: float, stop: float, lev: int):
        self.side = side                 # +1 多 / -1 空
        self.long = side == 1
        self.entry = entry
        self.qty = qty                  # 当前剩余仓位比例（1.0=满仓）
        self.init_qty = qty
        self.stop = stop
        self.leverage = lev
        self.tp1_done = False
        self.tp2_done = False
        self.tp3_done = False
        self.breakeven = False
        self.max_unrealized_pct = 0.0
        self.realized = 0.0             # 累计已实现（price-% 口径，按平仓比例加权）

    def pnl_pct(self, price: float) -> float:
        if self.long:
            return (price - self.entry) / self.entry * 100.0
        return (self.entry - price) / self.entry * 100.0

    def coins(self, qty: float) -> float:
        return qty

    def log(self, *a, **k):
        # position_enhanced 里会调用 pos.log(...) 记录动作，回测里静默
        pass


def resample(candles_1h: list, bar_ms: int) -> list:
    buckets: dict = {}
    for c in candles_1h:
        b = c["ts"] // bar_ms
        if b not in buckets:
            buckets[b] = {"ts": b * bar_ms, "o": c["o"], "h": c["h"],
                          "l": c["l"], "c": c["c"], "vol": c["vol"]}
        else:
            e = buckets[b]
            e["h"] = max(e["h"], c["h"]); e["l"] = min(e["l"], c["l"])
            e["c"] = c["c"]; e["vol"] += c["vol"]
    return [buckets[k] for k in sorted(buckets)]


def build_rules() -> EnhancedExitRules:
    return EnhancedExitRules(
        tp1_pct=EXIT_CFG["tp1_pct"], tp1_ratio=EXIT_CFG["tp1_ratio"],
        tp2_pct=EXIT_CFG["tp2_pct"], tp2_ratio=EXIT_CFG["tp2_ratio"],
        tp3_pct=EXIT_CFG["tp3_pct"], tp3_ratio=EXIT_CFG["tp3_ratio"],
        tp3_mode=EXIT_CFG["tp3_mode"],
        sl_buffer_atr=EXIT_CFG["sl_buffer_atr"], sl_min_pct=EXIT_CFG["sl_min_pct"],
        sl_pct=EXIT_CFG["sl_pct"], sl_mode=EXIT_CFG["sl_mode"],
        move_sl_to_entry=EXIT_CFG["move_sl_to_entry"], trail_with_st=EXIT_CFG["trail_with_st"],
        protect_profit_at=EXIT_CFG["protect_profit_at"], protect_trail_pct=EXIT_CFG["protect_trail_pct"],
        max_loss_enabled=EXIT_CFG["max_loss_enabled"], max_loss_pct=EXIT_CFG["max_loss_pct"],
        enabled=EXIT_CFG["enabled"],
    )


def bias_at(i: int, one_h: list, four: list, one_d: list) -> tuple:
    """首页 MTF Bias：用 1h/4h/1d 截至第 i 根 1h 棒的收盘算 verdict/score。"""
    ts_i = one_h[i]["ts"]
    idx4 = bisect.bisect_right([c["ts"] for c in four], ts_i) - 1
    idx1 = bisect.bisect_right([c["ts"] for c in one_d], ts_i) - 1
    cbtf = {"1h": one_h[:i + 1], "4h": four[:idx4 + 1], "1d": one_d[:idx1 + 1]}
    b = strategy.mtf_bias(cbtf, P)
    return b.get("verdict", "mixed"), b.get("score", 0.0)


def gate_pass(sig_type: str, verdict: str, score: float) -> bool:
    if sig_type == "buy":
        if verdict == "bull":
            return True
        if GATE_ALLOW_MIXED and verdict == "mixed":
            return score > 0
        return False
    else:  # sell
        if verdict == "bear":
            return True
        if GATE_ALLOW_MIXED and verdict == "mixed":
            return score < 0
        return False


def run():
    # 1) 取数
    logger.info(f"拉取 {SYMBOL} {TF} 历史（上限 {N_BARS} 根）…")
    cs = history.fetch_candles(TF, N_BARS, SYMBOL)
    if not cs:
        logger.error("取数为空，退出")
        return
    candles = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol,
                "confirm": c.confirm} for c in cs]
    if candles and not candles[-1]["confirm"]:
        candles = candles[:-1]
    logger.info(f"取得 {len(candles)} 根，区间 {candles[0]['ts']} ~ {candles[-1]['ts']}")

    with open(RAW_FILE, "w") as f:
        json.dump(candles, f, ensure_ascii=False, indent=2)
    logger.info(f"原始 K 线已存 {RAW_FILE}")

    # 2) SuperTrend（线上同款）
    opens = [c["o"] for c in candles]
    highs = [c["h"] for c in candles]
    lows  = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]
    st = indicators.super_trend(opens, highs, lows, closes,
                                periods=ST["periods"], multiplier=ST["multiplier"],
                                src=ST["src"], change_atr=ST["change_atr"])
    atr = st["atr"]
    flip_at = {}
    for f in st["flips"]:
        flip_at.setdefault(f["i"], []).append(f["type"])

    # 3) 高周期（MTF Bias）
    four_h = resample(candles, 4 * 3600 * 1000)
    one_d  = resample(candles, 24 * 3600 * 1000)
    logger.info(f"高周期：4h {len(four_h)} 根 / 1d {len(one_d)} 根")

    rules = build_rules()
    lev = LEVERAGE

    # 4) 事件循环
    pos = None
    last_exit_i = -999
    trades = []
    equity_val = 0.0
    peak = 0.0
    max_dd = 0.0

    def realize(delta: float):
        nonlocal equity_val, peak, max_dd
        equity_val += delta
        if equity_val > peak:
            peak = equity_val
        dd = peak - equity_val
        if dd > max_dd:
            max_dd = dd

    def close_pos(price, reason, i):
        nonlocal pos, last_exit_i
        if pos is None:
            return
        prev = pos.realized
        # 平仓剩余比例
        pnl_full = pos.pnl_pct(price)
        pos.realized += pnl_full * pos.qty
        delta = pos.realized - prev
        realize(delta)
        trades.append({
            "side": "long" if pos.long else "short",
            "entry": round(pos.entry, 4), "exit": round(price, 4), "exit_i": i,
            "pnl_pct": round(pos.realized, 4),
            "reason": reason,
        })
        pos = None
        last_exit_i = i

    for i in range(len(candles)):
        c = candles[i]
        h, l, cl = c["h"], c["l"], c["c"]
        up_line = st["up"][i]
        dn_line = st["dn"][i]
        a = atr[i] or 0.0

        # 4a. 持仓中：本棒高低点先判止损/止盈（用 PRE-棒 stop）
        if pos is not None:
            stopped = False
            if pos.long and l <= pos.stop:
                close_pos(pos.stop, "stop", i); stopped = True
            elif (not pos.long) and h >= pos.stop:
                close_pos(pos.stop, "stop", i); stopped = True

            if not stopped and pos is not None:
                fav = h if pos.long else l
                # 三级止盈按 tp1→tp2→tp3（用本棒有利极端价判断）
                if not pos.tp1_done:
                    lvl = pos.entry * (1 + rules.tp1_pct / 100) if pos.long \
                        else pos.entry * (1 - rules.tp1_pct / 100)
                    if (pos.long and fav >= lvl) or (not pos.long and fav <= lvl):
                        q = pos.qty * rules.tp1_ratio / 100
                        prev = pos.realized
                        apply_tp_enhanced(pos, lvl, q, rules, 1)
                        realize(pos.realized - prev)
                if pos.tp1_done and not pos.tp2_done:
                    lvl = pos.entry * (1 + rules.tp2_pct / 100) if pos.long \
                        else pos.entry * (1 - rules.tp2_pct / 100)
                    if (pos.long and fav >= lvl) or (not pos.long and fav <= lvl):
                        q = pos.qty * rules.tp2_ratio / 100
                        prev = pos.realized
                        apply_tp_enhanced(pos, lvl, q, rules, 2)
                        realize(pos.realized - prev)
                if pos.tp2_done and not pos.tp3_done and rules.tp3_mode == "pct":
                    lvl = pos.entry * (1 + rules.tp3_pct / 100) if pos.long \
                        else pos.entry * (1 - rules.tp3_pct / 100)
                    if (pos.long and fav >= lvl) or (not pos.long and fav <= lvl):
                        prev = pos.realized
                        apply_tp_enhanced(pos, lvl, pos.qty, rules, 3)
                        realize(pos.realized - prev)
                if pos.qty <= 1e-12:
                    close_pos(cl, "tp_done", i)

            # 跟踪止损（保本后跟随超趋线）+ 更新最高盈利
            if pos is not None:
                line = up_line if pos.long else dn_line
                if line is not None:
                    trail_enhanced(pos, line, rules, cl)
                    fu = pos.pnl_pct(h) if pos.long else pos.pnl_pct(l)
                    if fu > pos.max_unrealized_pct:
                        pos.max_unrealized_pct = fu

        # 4b. 翻转信号（本棒收盘）
        for sig_type in flip_at.get(i, []):
            sig_dir = 1 if sig_type == "buy" else -1
            if pos is not None and pos.side != sig_dir:
                close_pos(cl, "reverse", i)          # 翻转平仓（同根棒允许）
            if pos is None:
                verdict, score = bias_at(i, candles, four_h, one_d)
                if gate_pass(sig_type, verdict, score) and (i > last_exit_i + COOLDOWN_BARS):
                    line = up_line if sig_type == "buy" else dn_line
                    sig = {"price": cl, "type": sig_type, "line": line}
                    stop = enhanced_initial_stop(sig, rules, a, lev)
                    pos = BP(sig_dir, cl, 1.0, stop, lev)
                    logger.info(f"开仓 {sig_type} @ {cl:.4f} stop={stop:.4f} bias={verdict}({score})")

    if pos is not None:
        close_pos(candles[-1]["c"], "eod", len(candles) - 1)

    # 5) 指标
    report = summarize(trades, equity_val, max_dd)
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"报告已存 {REPORT_FILE}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def summarize(trades, total_ret, max_dd):
    n = len(trades)
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / n * 100 if n else 0.0
    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0

    split = int(n * IS_RATIO)
    def block(ts):
        if not ts:
            return {"trades": 0}
        w = [t for t in ts if t["pnl_pct"] > 0]
        return {
            "trades": len(ts),
            "win_rate": round(len(w) / len(ts) * 100, 2),
            "total_pct": round(sum(t["pnl_pct"] for t in ts), 2),
        }

    notional = MARGIN_USDT * LEVERAGE
    return {
        "symbol": SYMBOL, "tf": TF,
        "params": {"st": ST, "bias": BIAS, "exit": EXIT_CFG, "leverage": LEVERAGE},
        "trades": n,
        "win_rate": round(win_rate, 2),
        "total_return_pct": round(total_ret, 2),
        "total_return_usdt": round(notional * total_ret / 100, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "is": block(trades[:split]),
        "oos": block(trades[split:]),
        "trades_detail": trades,
    }


if __name__ == "__main__":
    run()
