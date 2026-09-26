"""形态识别页（/pattern）BTC 1h 策略回测 —— 与页面实盘逻辑逐条对齐。

信号（router.get_pattern / pattern_trade）：
    1h 原始 SuperTrend（ATR 周期 10 / factor 3.0 / change_atr=True）的 trend 翻转。

过滤（pattern_trade.PatternConfig 默认值）：
    block_4h=True     4h 形态方向明确反向才拦（无趋势 dir=0 / 数据不足 一律放行）
    4 条过滤规则（趋势形态识别.md，品种独立开关，默认全关=不过滤）：
        ① 连续翻转过滤  bars_since_last_flip<20 拦截
        ② 波动异常过滤  ATR_percent>0.8 拦截
        ③ 箱体错误位置  多 Pos<0.3 / 空 Pos>0.7 拦截
        ④ 极端K过滤     candle_range_ATR>3 拦截

出场（position.ExitRules 默认档，页面面板可配）：
    TP1 触及 +1.5% 平 70% 并把止损移到开仓价（保本）；
    剩余 30% 跟随 SuperTrend 轨道跟踪（sl_mode="st"），或遇下一个反向翻转收盘平掉；
    轨道无效时按固定 2% 兜底止损。

成本：每笔名义 10,000 USDT（无复利）、OKX taker 单边 0.05%。

用法：
    python3 bt_pattern_page.py                    # 默认 BTC-USDT，近 6 个月 + 2026H1 + 2025全年
    python3 bt_pattern_page.py ETH-USDT --window 6m
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import os
import sys
from collections import Counter
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import history
from indicators import super_trend, ta_sma
from pattern_recog import recognize as recognize_pattern
from signal_v3 import v3_decide, base_features, SCORE_MATURE, SCORE_EARLY

SYM_DEFAULT = "BTC-USDT"
BASE_TF, H4_TF = "1h", "4h"

# ── 与 pattern_trade.PatternConfig / position.ExitRules 默认值一致 ──
ST_PERIODS, ST_MULT = 10, 3.0
TP1_PCT, TP1_RATIO = 1.5, 0.70
SL_PCT_FALLBACK = 2.0
FEE = 0.05 / 100                 # 单边 taker
NOTIONAL = 10_000.0

# 4 条过滤规则（趋势形态识别.md）：品种独立开关；此处逐条独立评估，看各规则拦截量。

def _cache_path(sym: str) -> str:
    """缓存放系统临时目录，避免几 MB 的行情缓存落进仓库。"""
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"bt_pattern_{sym}.json")


# ── 数据 ────────────────────────────────────────────────────────
def load(sym: str, use_cache: bool = True):
    CACHE = _cache_path(sym)
    if use_cache and os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE))
            if c.get("sym") == sym and c.get("base") and c.get("h4"):
                ts = dt.datetime.fromtimestamp(c["fetched"] / 1000).strftime("%m-%d %H:%M")
                print(f"（命中缓存 {CACHE}，抓取于 {ts}；加 --refresh 重新拉取）")
                return c["base"], c["h4"]
        except Exception:
            pass
    print(f"拉取 {sym} 1h 历史（约 1.7 年）…")
    base = history.fetch_candles(BASE_TF, limit=15600, symbol=sym)
    print(f"拉取 {sym} 4h 历史…")
    h4 = history.fetch_candles(H4_TF, limit=4200, symbol=sym)
    if not base or not h4:
        print("抓取失败（网络/代理不可达 OKX）")
        return None, None
    rows_b = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in base]
    rows_h = [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "vol": c.vol} for c in h4]
    json.dump({"sym": sym, "fetched": int(dt.datetime.now().timestamp() * 1000),
               "base": rows_b, "h4": rows_h}, open(CACHE, "w"))
    print(f"抓取完成：1h {len(rows_b)} 根（{fmt(rows_b[0]['ts'])} ~ {fmt(rows_b[-1]['ts'])}）"
          f"，4h {len(rows_h)} 根")
    return rows_b, rows_h


# ── 信号 ────────────────────────────────────────────────────────
def build_signals(base, h4):
    """用 V3 口径重构信号生成和过滤逻辑"""
    opens = [c["o"] for c in base]
    highs = [c["h"] for c in base]
    lows = [c["l"] for c in base]
    closes = [c["c"] for c in base]
    vols = [c["vol"] for c in base]
    tss = [c["ts"] for c in base]

    st = super_trend(opens, highs, lows, closes, periods=ST_PERIODS,
                     multiplier=ST_MULT, change_atr=True)

    # 4h 形态方向
    pat = recognize_pattern([{"ts": c["ts"], "o": c["o"], "h": c["h"],
                              "l": c["l"], "c": c["c"]} for c in h4])["pattern"]
    pts = [p["ts"] for p in pat]
    pmap = {p["ts"]: p for p in pat}

    def dir_at(ts: int) -> Optional[int]:
        idx = bisect.bisect_right(pts, ts) - 1
        return pmap[pts[idx]].get("dir") if idx >= 0 else None

    # 计算 V3 所需的基础指标
    from signal_v3 import features_from_candles

    # 准备 4h K 线供 features_from_candles 使用
    h4_candles = [{"ts": c["ts"], "o": c["o"], "h": c["h"],
                   "l": c["l"], "c": c["c"], "vol": c.get("vol", 0)} for c in h4]

    sigs = []
    for f in st["flips"]:
        i = f["i"]
        if i >= len(base) or i < 50:  # V3 需要至少 50 根历史
            continue
        sd = 1 if f["type"] == "buy" else -1
        pdir = dir_at(tss[i])

        # 使用 V3 特征提取
        feats = features_from_candles(base, i, sd, h4_candles,
                                     st_periods=ST_PERIODS, st_mult=ST_MULT)

        if not feats:
            continue

        # V3 决策
        v3_result = v3_decide(sd, feats)

        sigs.append({
            "i": i, "ts": tss[i], "type": f["type"], "dir": sd,
            "price": closes[i], "pdir": pdir,
            "pass_4h": (pdir != -sd),          # 4h 趋势对齐
            "feat": feats,                      # V3 特征
            "v3_score": v3_result["score"],     # V3 打分
            "v3_path": v3_result["path"],       # 通过路径
            "v3_execute": v3_result["execute"], # 是否放行
            "v3_fused": v3_result["fused"],     # 是否熔断
            # 兼容旧的 pass_filter 字段（供回测统计用）
            "pass_filter": v3_result["execute"],
        })
    return sigs, opens, highs, lows, closes, st["up_plot"], st["dn_plot"], \
        {f["i"] for f in st["flips"] if f["i"] < len(base)}


def print_funnel(sigs):
    """过滤漏斗：V3 口径（趋势打分闸门）"""
    total = len(sigs)
    a4h = [s for s in sigs if s["pass_4h"]]

    # V3 路径统计
    from signal_v3 import PATH_MATURE, PATH_EARLY, PATH_FUSE, PATH_NONE

    mature = [s for s in a4h if s.get("v3_path") == PATH_MATURE]
    early = [s for s in a4h if s.get("v3_path") == PATH_EARLY]
    fused = [s for s in a4h if s.get("v3_fused")]
    failed = [s for s in a4h if s.get("v3_path") == PATH_NONE]

    final = [s for s in sigs if s["pass_4h"] and s.get("v3_execute")]

    print("  ── V3 过滤漏斗（趋势打分闸门）──")
    print(f"     ① SuperTrend 翻转信号总数        : {total}")
    print(f"     ② 过 4h 趋势对齐闸门            : {len(a4h)}  "
          f"(拦截 {total - len(a4h)} = 4h 反向)")
    print(f"     ③ V3 路径1 成熟趋势（score=100） : {len(mature)}")
    print(f"     ④ V3 路径2 早期启动（score=80）  : {len(early)}")
    print(f"     ⑤ V3 震荡熔断（极端震荡拦截）    : {len(fused)}")
    print(f"     ⑥ V3 未通过任何路径              : {len(failed)}")
    print(f"     ⑦ 最终放行开仓                  : {len(final)}  "
          f"(总拦截 {total - len(final)})")
    print(f"     放行率: {len(final)/total*100:.1f}%")


# ── 回测 ────────────────────────────────────────────────────────
def backtest(sigs, highs, lows, closes, up_plot, dn_plot, flip_idx,
             reverse_close: bool = False):
    """出场对齐 position.ExitRules 默认档：TP1 1.5% 平 70% + 保本 + 跟随 ST 轨道。

    reverse_close=True（对应 ExitRules.reverse_close）：TP1 / 保本 / ST 跟踪 /
    2% 硬止损全部失效，只在下一个反向翻转收盘平掉全部仓位。
    """
    trades = []
    for s in sigs:
        i, long = s["i"], s["dir"] > 0
        entry = closes[i]
        stp0 = up_plot[i] if long else dn_plot[i]
        if stp0 is None or (long and stp0 >= entry) or (not long and stp0 <= entry):
            stp0 = entry * (1 - SL_PCT_FALLBACK / 100) if long else entry * (1 + SL_PCT_FALLBACK / 100)
        stop = stp0
        tp1p = entry * (1 + TP1_PCT / 100) if long else entry * (1 - TP1_PCT / 100)
        tp1 = False
        coins = NOTIONAL / entry
        pnl = -entry * coins * FEE
        fee = entry * coins * FEE
        reason, ex, ex_i = "末根平仓", closes[-1], len(closes) - 1
        closed = False

        for j in range(i + 1, len(closes)):
            if long and not reverse_close:
                nl = up_plot[j]
                if nl is not None and nl > stop:
                    stop = nl
                if lows[j] <= stop:                      # 先判止损（保守）
                    px = stop
                    rest = 1 - (TP1_RATIO if tp1 else 0)
                    pnl += (px - entry) * coins * rest - px * coins * rest * FEE
                    fee += px * coins * rest * FEE
                    reason, ex, ex_i, closed = "止损", px, j, True
                    break
                if not tp1 and highs[j] >= tp1p:
                    pnl += (tp1p - entry) * coins * TP1_RATIO - tp1p * coins * TP1_RATIO * FEE
                    fee += tp1p * coins * TP1_RATIO * FEE
                    tp1, stop = True, entry              # 保本
            elif not long and not reverse_close:
                nl = dn_plot[j]
                if nl is not None and nl < stop:
                    stop = nl
                if highs[j] >= stop:
                    px = stop
                    rest = 1 - (TP1_RATIO if tp1 else 0)
                    pnl += (entry - px) * coins * rest - px * coins * rest * FEE
                    fee += px * coins * rest * FEE
                    reason, ex, ex_i, closed = "止损", px, j, True
                    break
                if not tp1 and lows[j] <= tp1p:
                    pnl += (entry - tp1p) * coins * TP1_RATIO - tp1p * coins * TP1_RATIO * FEE
                    fee += tp1p * coins * TP1_RATIO * FEE
                    tp1, stop = True, entry
            if j in flip_idx:                            # 下一个翻转必为反向 → 平剩余
                px = closes[j]
                rest = 1 - (TP1_RATIO if tp1 else 0)
                # 空头盈亏方向相反（此处原本无条件 (px-entry)，空头反向平仓会算反号）
                pnl += ((px - entry) if long else (entry - px)) * coins * rest \
                    - px * coins * rest * FEE
                fee += px * coins * rest * FEE
                reason, ex, ex_i, closed = "反向信号", px, j, True
                break
        if not closed:
            px = closes[-1]
            rest = 1 - (TP1_RATIO if tp1 else 0)
            pnl += ((px - entry) if long else (entry - px)) * coins * rest \
                - px * coins * rest * FEE
            fee += px * coins * rest * FEE
        trades.append({"ts": s["ts"], "dir": s["dir"], "entry": entry, "exit": ex,
                       "i": s["i"], "j": ex_i, "pnl": pnl, "fee": fee,
                       "gross": pnl + fee, "tp1": tp1, "reason": reason,
                       "ret_pct": pnl / NOTIONAL * 100})
    return trades


def metrics(tr):
    n = len(tr)
    wins = sum(1 for t in tr if t["pnl"] > 0)
    tot = sum(t["pnl"] for t in tr)
    eq = peak = max_dd = 0.0
    streak = worst_streak = 0
    for t in tr:
        eq += t["pnl"]
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / NOTIONAL * 100)
        streak = 0 if t["pnl"] > 0 else streak + 1
        worst_streak = max(worst_streak, streak)
    return dict(n=n, wins=wins, wr=wins / n * 100 if n else 0, tot=tot,
                ret=tot / NOTIONAL * 100, avg=tot / n / NOTIONAL * 100 if n else 0,
                worst=min((t["ret_pct"] for t in tr), default=0),
                max_dd=max_dd, streak=worst_streak)


STRATS = [
    ("A", "A) 无过滤（全部翻转）", lambda s: True),
    ("B", "B) 仅 4h 形态过滤", lambda s: s["pass_4h"]),
    ("C", "C) 页面默认(4h+趋势过滤)", lambda s: s["pass_4h"] and s["pass_trend"]),
    ("D", "D) 页面默认 + 只做多", lambda s: s["pass_4h"] and s["pass_trend"] and s["dir"] > 0),
]


def run(base, h4, start, end, label):
    sigs, opens, highs, lows, closes, up_plot, dn_plot, flip_idx = build_signals(base, h4)
    win = [s for s in sigs if start <= s["ts"] < end]
    print(f"\n===== {label} =====")
    print(f"窗口信号数={len(win)}  4h方向分布: " + ", ".join(
        f"{k}={v}" for k, v in sorted(Counter(
            (s["pdir"] if s["pdir"] is not None else "None") for s in win).items(),
            key=lambda x: str(x[0]))))

    res = {k: backtest([s for s in win if ok(s)], highs, lows, closes,
                       up_plot, dn_plot, flip_idx) for k, _, ok in STRATS}
    m = {k: metrics(v) for k, v in res.items()}

    hdr = (f"{'策略':<28}{'笔数':>6}{'胜率':>8}{'收益USDT':>11}{'收益率%':>9}"
           f"{'均笔%':>8}{'最大单笔亏%':>12}{'最大回撤%':>10}{'最长连亏':>9}")
    print(hdr)
    print("-" * len(hdr))
    for k, lab, _ in STRATS:
        x = m[k]
        print(f"{lab:<28}{x['n']:>6}{x['wr']:>7.1f}%{x['tot']:>11.0f}{x['ret']:>9.2f}"
              f"{x['avg']:>8.3f}{x['worst']:>12.2f}{x['max_dd']:>10.2f}{x['streak']:>9}")

    print("  放行/拦截（信号总数=%d）：" % len(win) + "  ".join(
        f"{k}={sum(1 for s in win if ok(s))}" for k, _, ok in STRATS))
    print_funnel(win)
    rc = Counter(t["reason"] for t in res["C"])
    print("  C 出场原因：" + "  ".join(f"{k}={v}" for k, v in rc.most_common()))
    diagnose(res["C"], "C) 页面默认")
    if res["D"]:
        d = metrics(res["D"])
        print(f"  仅多头对照：D) {d['n']} 笔 胜率 {d['wr']:.1f}%  "
              f"净 {d['ret']:+.2f}% 最大回撤 {d['max_dd']:.2f}% 最长连亏 {d['streak']}")
    return m


def diagnose(tr, label):
    """把「策略本身有没有钱赚」和「手续费吃掉多少」拆开看。"""
    if not tr:
        return
    n = len(tr)
    fee = sum(t["fee"] for t in tr)
    gross = sum(t["gross"] for t in tr)
    net = sum(t["pnl"] for t in tr)
    tp1_n = sum(1 for t in tr if t["tp1"])
    hold = sum(t["j"] - t["i"] for t in tr) / n
    lg = [t for t in tr if t["dir"] > 0]
    sh = [t for t in tr if t["dir"] < 0]
    print(f"  ── {label} 成本/结构诊断（C 策略，名义 {NOTIONAL:,.0f} U/笔）──")
    print(f"     毛利 {gross:>8.0f} U（{gross/NOTIONAL*100:+.2f}%）  "
          f"手续费 {fee:>7.0f} U  净利 {net:>8.0f} U（{net/NOTIONAL*100:+.2f}%）")
    print(f"     手续费占名义 {fee/NOTIONAL/n*100:.3f}%/笔，"
          f"{'手续费吃掉了全部毛利' if gross > 0 and net < 0 else ('毛利本身为负' if gross < 0 else '毛利为正')}")
    print(f"     TP1(+{TP1_PCT}%)命中 {tp1_n}/{n}（{tp1_n/n*100:.0f}%）  平均持仓 {hold:.1f} 根≈{hold:.1f}h")
    print(f"     多头 {len(lg)} 笔 {sum(t['pnl'] for t in lg)/NOTIONAL*100:+.2f}%   "
          f"空头 {len(sh)} 笔 {sum(t['pnl'] for t in sh)/NOTIONAL*100:+.2f}%")
    # 零费 + maker 费率下的理论上限，判断是不是纯粹被成本拖死
    print(f"     若零手续费：{gross/NOTIONAL*100:+.2f}%；若 maker 单边 0.02%："
          f"{(gross - fee*0.4)/NOTIONAL*100:+.2f}%")


def fmt(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


WINDOWS = [
    ("近 6 个月（形态页可见窗口）", lambda now: (now - dt.timedelta(days=182), now)),
    ("2026 H1", lambda now: (dt.datetime(2026, 1, 1), dt.datetime(2026, 7, 1))),
    ("2025 全年", lambda now: (dt.datetime(2025, 1, 1), dt.datetime(2026, 1, 1))),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default=SYM_DEFAULT)
    ap.add_argument("--window", choices=["6m", "2026h1", "2025", "all"], default="all")
    ap.add_argument("--refresh", action="store_true", help="忽略本地缓存重新拉取")
    a = ap.parse_args()

    base, h4 = load(a.symbol, use_cache=not a.refresh)
    if not base:
        return
    tss = [c["ts"] for c in base]
    print(f"1h 数据：{fmt(tss[0])} ~ {fmt(tss[-1])}（{len(base)} 根）")

    now = dt.datetime.utcfromtimestamp(tss[-1] / 1000) + dt.timedelta(hours=1)
    pick = {"6m": WINDOWS[:1], "2026h1": WINDOWS[1:2], "2025": WINDOWS[2:3], "all": WINDOWS}[a.window]
    for label, f in pick:
        s, e = f(now)
        run(base, h4, int(s.replace(tzinfo=dt.timezone.utc).timestamp() * 1000),
            int(e.replace(tzinfo=dt.timezone.utc).timestamp() * 1000), label)


if __name__ == "__main__":
    main()
