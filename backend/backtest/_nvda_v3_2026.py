# -*- coding: utf-8 -*-
"""NVDA-USDT-SWAP 1h 2026：V3 过滤信号 + 单档 1.5%/70% 出口（ST基线 & 3%硬止损）。
复用 MU 脚本口径，数据换成 NVDA 的 1h(+4h HTF)。
"""
import sys, calendar, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import signal_v3
from indicators import super_trend
from position import ExitRules
import backtest_engine as BE
from history import fetch_candles

SYM = "NVDA-USDT-SWAP"
print("拉取 %s 1h/4h ..." % SYM)

def to_dicts(candles):
    return [{"ts": b.ts, "o": b.o, "h": b.h, "l": b.l, "c": b.c,
             "vol": getattr(b, "vol", 0.0)} for b in candles]

base = to_dicts(fetch_candles("1h", limit=8000, symbol=SYM))
raw4 = to_dicts(fetch_candles("4h", limit=4200, symbol=SYM))
n = len(base)
print("1h K线 %d 根, 4h %d 根" % (n, len(raw4)))

o = [x["o"] for x in base]; h = [x["h"] for x in base]
l = [x["l"] for x in base]; c = [x["c"] for x in base]
st = super_trend(o, h, l, c, periods=10, multiplier=3.0, change_atr=True)
flip_idxs = sorted(f["i"] for f in st["flips"])

# ── V3 过滤 ──
sel = []
for fi in flip_idxs:
    if fi < 1:
        continue
    side = 1 if st["trend"][fi] == 1 else -1
    feats = signal_v3.features_from_candles(base, fi, side, candles_htf=raw4)
    if feats is None:
        continue
    if not signal_v3.v3_decide(side, feats)["execute"]:
        continue
    sel.append((fi, base[fi]["ts"], side))

T2026 = calendar.timegm((2026, 1, 1, 0, 0, 0))
sel26 = [t for t in sel if t[1] >= T2026 * 1000]
print("V3过滤后翻转: %d  其中2026入场: %d" % (len(sel), len(sel26)))

P = {"periods": 10, "multiplier": 3.0, "src": "hl2", "change_atr": True}

def run_engine(er):
    return BE.run_backtest(base, P, init_cash=10000.0, fee_rate=0.0005,
                           allow_short=True, leverage=1, sizing="equity",
                           exit_rules=er, full_trades=True)

def metrics(pairs, label):
    if not pairs:
        print("  [%s] 空集" % label); return
    pn = [x[1] for x in pairs]; N = len(pn)
    wins = [x for x in pn if x > 0]; gl = -sum(x for x in pn if x <= 0)
    eq = 1.0; pk = 1.0; mdd = 0.0
    for x in pn:
        eq *= (1 + x / 100); pk = max(pk, eq); mdd = max(mdd, pk - eq)
    pf = sum(wins) / gl if gl else float("inf")
    print("  [%s] 笔%3d 复利%7.1f%% 胜率%4.1f%% PF=%4.2f DD%5.1f%% 红字%d 均%5.2f%% R/DD%4.2f" % (
        label, N, (eq - 1) * 100, len(wins) / N * 100, pf, mdd / pk * 100,
        sum(1 for x in pn if x > 2.0), sum(pn) / N, (eq - 1) / (mdd / pk) if mdd > 0 else 0))

def report(name, er):
    res = run_engine(er)
    tmap = {t["entry_ts"]: t for t in res["trades_list"]}
    y26 = [(ts, tmap[ts]["pnl_pct"]) for _, ts, _ in sel26 if ts in tmap]
    print("\n== %s ==" % name); metrics(y26, "2026")

report("单档 1.5%/70% [ST基线 sl=st]", ExitRules(
    tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=2.0,
    move_sl_to_entry=True, trail_with_st=True, reverse_close=False))
report("单档 1.5%/70% [3%硬止损]", ExitRules(
    tp1_pct=1.5, tp1_ratio=70.0, sl_mode="pct", sl_pct=3.0,
    move_sl_to_entry=True, trail_with_st=True, reverse_close=False))
