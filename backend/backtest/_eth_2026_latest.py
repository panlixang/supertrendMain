# -*- coding: utf-8 -*-
"""ETH 2026 回测：最新策略 = V3过滤 + 单档(ExitRules) TP1 1.5%/70% + 3%真实硬止损 + 保本 + ST跟踪。
1h 来自 candle_data.db(ETH-USDT)，4h 来自 _live_data/ETH.json（V3 需要高周期特征）。
统计窗口 2026-01-01 ~ 4h 数据末端（避免 slope_htf=None 造成的偏差）。
对照：①无V3 ②V3+ST止损(非硬止损)。
"""
import sqlite3, json, sys, bisect, datetime
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
import signal_v3
from indicators import super_trend, ta_sma, ta_adx
from position import ExitRules
import backtest_engine as BE

# ── 数据 ──────────────────────────────────────────────
con = sqlite3.connect(BASE + r"\candle_data.db")
rows = con.execute("SELECT ts,o,h,l,c,vol FROM candles WHERE symbol='ETH-USDT' AND tf='1h' ORDER BY ts").fetchall()
con.close()
base = [{"ts": int(r[0]), "o": float(r[1]), "h": float(r[2]),
         "l": float(r[3]), "c": float(r[4]), "vol": float(r[5] or 0)} for r in rows]
j = json.load(open(BASE + r"\backtest\_live_data\ETH.json", encoding="utf-8"))
htf = sorted([dict(x) for x in j["4h"]], key=lambda x: x["ts"])

c = [b["c"] for b in base]; o = [b["o"] for b in base]
h = [b["h"] for b in base]; l = [b["l"] for b in base]; n = len(c)
print("ETH 1h %d 根  %s ~ %s" % (n,
      datetime.datetime.fromtimestamp(base[0]["ts"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d"),
      datetime.datetime.fromtimestamp(base[-1]["ts"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")))
print("ETH 4h %d 根  ~ %s" % (len(htf),
      datetime.datetime.fromtimestamp(htf[-1]["ts"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")))

# ── ST / 特征序列（形态页口径 ATR10 × 3.0）────────────
st = super_trend(o, h, l, c, periods=10, multiplier=3.0, change_atr=True)
flips = sorted(f["i"] for f in st["flips"]); ftype = {f["i"]: f["type"] for f in st["flips"]}
atr = st["atr"]; ma = ta_sma(c, 30)
atr_pct = [(atr[k] / c[k] * 100 if (atr[k] and c[k]) else 0.0) for k in range(n)]
er20 = signal_v3._er20_series(c)
st_dist = signal_v3._st_dist_series(st["trend"], st["up"], st["dn"], c, atr)
adx = ta_adx(h, l, c, 14)
c4 = [x["c"] for x in htf]; ts4 = [x["ts"] for x in htf]; ma4 = ta_sma(c4, 30)

def feats(i, side):
    """同 signal_v3.features_from_candles 的口径，但序列只算一次。"""
    b = signal_v3.base_features(c, atr, atr_pct, er20, st_dist, flips, ma, i, side)
    if not b: return None
    b["dist_base_ma"] = ((c[i] - ma[i]) / ma[i] * 100.0) if ma[i] else None
    a0, a1 = max(0, i - 20), i - 1
    b["adx_chg20"] = (adx[a1] or 0) - (adx[a0] or 0)
    b["er_chg20"] = er20[a1] - er20[a0]
    win = range(max(0, i - 100), i)
    med_list = sorted(atr_pct[k] for k in win if atr_pct[k] > 0)
    med = med_list[len(med_list) // 2] if med_list else 0.0
    b["vol100"] = sum(1 for k in win if med > 0 and atr_pct[k] > 1.5 * med)
    b["slope_htf"] = None; b["dist_htf_ma"] = None
    j4 = bisect.bisect_right(ts4, base[i]["ts"]) - 1
    if j4 >= 10 and ma4[j4] and ma4[j4 - 10]:
        b["slope_htf"] = (ma4[j4] - ma4[j4 - 10]) / ma4[j4 - 10] * 100.0
        b["dist_htf_ma"] = (c[i] - ma4[j4]) / ma4[j4] * 100.0
    return b

# ── V3 过滤 ───────────────────────────────────────────
sel_v3, sel_all = [], []
for i in flips:
    sd = 1 if ftype[i] == "buy" else -1
    sel_all.append((i, base[i]["ts"], sd))
    f = feats(i, sd)
    if f is None: continue
    if not signal_v3.v3_decide(sd, f)["execute"]: continue
    sel_v3.append((i, base[i]["ts"], sd))

T2026 = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
T_END = htf[-1]["ts"]          # 4h 末端，保证 slope_htf 有效
print("\n总翻转 %d 个 | V3 放行 %d 个 | 2026 窗口内: 全量 %d / V3 %d" % (
    len(sel_all), len(sel_v3),
    sum(1 for _, ts, _ in sel_all if T2026 <= ts < T_END),
    sum(1 for _, ts, _ in sel_v3 if T2026 <= ts < T_END)))

# ── 回测 ──────────────────────────────────────────────
P = {"periods": 10, "multiplier": 3.0, "src": "hl2", "change_atr": True}
def run(er):
    return BE.run_backtest(base, P, init_cash=10000.0, fee_rate=0.0005,
                           allow_short=True, leverage=1, sizing="equity",
                           exit_rules=er, full_trades=True)
def metrics(pairs, label):
    if not pairs: print("  [%s] 空集" % label); return
    pn = [x[1] for x in pairs]; k = len(pn)
    wins = [x for x in pn if x > 0]; gl = -sum(x for x in pn if x <= 0)
    eq = 1.0; pk = 1.0; mdd = 0.0
    for x in pn:
        eq *= (1 + x / 100); pk = max(pk, eq); mdd = max(mdd, pk - eq)
    pf = sum(wins) / gl if gl else float("inf")
    print("  [%-6s] 笔%3d 复利%7.1f%% 胜率%4.1f%% PF=%4.2f DD%5.1f%% 红字%d 均%5.2f%% R/DD%4.2f" % (
        label, k, (eq - 1) * 100, len(wins) / k * 100, pf, mdd / pk * 100,
        sum(1 for x in pn if x > 2.0), sum(pn) / k, (eq - 1) / (mdd / pk) if mdd > 0 else 0))
def report(name, er, iset):
    res = run(er); tmap = {t["entry_ts"]: t for t in res["trades_list"]}
    y = [(ts, tmap[ts]["pnl_pct"]) for _, ts, _ in iset
         if T2026 <= ts < T_END and ts in tmap]
    print("\n== %s ==" % name); metrics(y, "2026")

# 最新档：单档 1.5%/70% + 3% 真实硬止损
LATEST = ExitRules(tp1_pct=1.5, tp1_ratio=70.0, sl_mode="pct", sl_pct=3.0,
                   move_sl_to_entry=True, trail_with_st=True, reverse_close=False)
report("★最新策略 V3 + 单档1.5%/70% + 3%硬止损", LATEST, sel_v3)
report("  对照A 无V3 + 最新出场", LATEST, sel_all)
report("  对照B V3 + ST止损(非硬)",
       ExitRules(tp1_pct=1.5, tp1_ratio=70.0, sl_mode="st", sl_pct=3.0,
                 move_sl_to_entry=True, trail_with_st=True, reverse_close=False), sel_v3)
