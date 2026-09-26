# -*- coding: utf-8 -*-
"""A/B/C 对比：V3 / V3+15m确认 / 仅15m确认（只读分析，不改交易逻辑）。

A = 现有 V3（Trend Score：成熟趋势 / 早期启动）
B = V3 + 15m Confirm
C = 仅 15m Confirm（不看 V3）

15m Momentum Confirm（打分制，合计100，>=60 放行）：
  ① 方向一致   25  多：close_15m > MA30_15m      空：< 
  ② 动量突破   30  mom5 = (close-close5)/ATR14_15m；多 > 0.5（敏感 0.3）
  ③ ATR扩张    20  ATR14_15m / ATR50_15m > 1
  ④ ST距离扩大 25  st_dist[j]-st_dist[j-20]；多 > 0，空 < 0

防未来函数：1h 信号在该小时 bar 收盘时触发，15m 只取该小时内最后一根已收盘
K（起始时间 = 1h bar 起始 + 45min），即决策时刻真实可见的信息。
"""
import sys, os, json, csv, calendar, datetime, bisect
BASE = r"d:\个人项目代码\supertrendMain\backend"
sys.path.insert(0, BASE)
from indicators import super_trend, ta_atr, ta_sma
import signal_v3

FEAT_CSV = os.path.join(BASE, "backtest", "st_signals_1h_features.csv")
TH_RED = 2.0

# ── 数据 ────────────────────────────────────────────────────────
b1 = json.load(open(os.path.join(BASE, "backtest", "btc_1h_full.json"), encoding="utf-8"))["base"]
h4 = json.load(open(os.path.join(BASE, "backtest", "btc_4h_full.json"), encoding="utf-8"))["h4"]
b15 = json.load(open(os.path.join(BASE, "backtest", "btc_15m_full.json"), encoding="utf-8"))["base"]

rows = list(csv.DictReader(open(FEAT_CSV, encoding="utf-8-sig")))
def msec(s): return calendar.timegm(datetime.datetime.strptime(s, "%Y/%m/%d %H:%M").timetuple())
def f(x):
    try: return float(x)
    except: return None

# ── 15m 序列（全量算一次）──────────────────────────────────────
o15 = [x["o"] for x in b15]; h15 = [x["h"] for x in b15]
l15 = [x["l"] for x in b15]; c15 = [x["c"] for x in b15]
ts15 = [x["ts"] for x in b15]
st15 = super_trend(o15, h15, l15, c15, periods=10, multiplier=3.0, change_atr=True)
atr14_15 = ta_atr(h15, l15, c15, 14)
atr50_15 = ta_atr(h15, l15, c15, 50)
ma30_15 = ta_sma(c15, 30)
sd15 = signal_v3._st_dist_series(st15["trend"], st15["up"], st15["dn"], c15, st15["atr"])

def confirm15(secs_from_epoch, side, mom_thr=0.5, win=8):
    """15m Momentum Confirm（窗口版）。

    j = 决策时刻（1h 收盘）最后一根已收盘 15m K；确认看的是 j 之前的
    `win` 根 15m K（默认 8，即最近 ~2h 的启动窗口），不再只看单根快照。
    返回 (是否放行, 分数, 明细)。
    """
    j = bisect.bisect_right(ts15, secs_from_epoch * 1000 + 45 * 60 * 1000) - 1
    if j is None or j < max(win, 50):
        return False, -1, ["无15m数据"]
    m30 = ma30_15[j]; a14 = atr14_15[j]; a50 = atr50_15[j]
    if not (m30 and a14 and a50):
        return False, -1, ["指标未就绪"]

    lo = j - win + 1
    got = []
    # ① 方向一致 25：窗口内多数 bar 已站上/跌破 MA30
    if side > 0:
        align = sum(1 for k in range(lo, j + 1) if c15[k] > ma30_15[k])
    else:
        align = sum(1 for k in range(lo, j + 1) if c15[k] < ma30_15[k])
    if align >= (win // 2 + 1):           # 多数（>50%）对齐
        got.append(("方向一致", 25))
    # ② 动量突破 30：窗口净动量（close[j]-close[j-win]）/ ATR14
    mom = (c15[j] - c15[j - win]) / a14
    ok2 = (mom > mom_thr) if side > 0 else (mom < -mom_thr)
    if ok2: got.append(("动量突破", 30))
    # ③ ATR扩张 20：ATR14 / ATR50 > 1
    if (a14 / a50) > 1:
        got.append(("ATR扩张", 20))
    # ④ ST距离扩大 25：窗口内 ST 距离净变化
    d = sd15[j] - sd15[j - win]
    ok4 = (d > 0) if side > 0 else (d < 0)
    if ok4: got.append(("ST距离扩大", 25))

    score = sum(w for _, w in got)
    return score >= 60, score, [n for n, _ in got]


# ── 逐笔决策 ────────────────────────────────────────────────────
# V3：按 ts 定位 1h bar 索引
b1_sec = {int(x["ts"] / 1000): i for i, x in enumerate(b1)}
recs = []
nodata = 0
for r in rows:
    secs = msec(r["时间"]); side = int(r["信号"]); pnl = f(r["盈亏"]) or 0.0
    i = b1_sec.get(secs)
    v3_ok = False; v3_path = "?"
    if i is not None:
        ft = signal_v3.features_from_candles(b1, i, side, h4)
        if ft:
            d = signal_v3.v3_decide(side, ft)
            v3_ok, v3_path = d["execute"], d["path"]
    c_ok, c_score, c_hit = confirm15(secs, side)
    if c_score == -1:
        nodata += 1
    recs.append(dict(secs=secs, side=side, pnl=pnl, res=r["止盈止损"],
                     v3=v3_ok, v3_path=v3_path, c15=c_ok, c_score=c_score, c_hit=c_hit))

print(f"样本 {len(recs)} 笔，15m 数据缺失 {nodata} 笔\n")


# ── 指标 ────────────────────────────────────────────────────────
def metrics(sel, label):
    if not sel:
        print(f"[{label}] 空集"); return
    p = [x["pnl"] for x in sel]; n = len(p)
    tp = sum(1 for x in sel if x["res"] == "TP")
    gp = sum(x for x in p if x > 0); gl = -sum(x for x in p if x < 0)
    eq = 1.0; pk = 1.0; mdd = 0.0
    for x in p:
        eq *= (1 + x / 100); pk = max(pk, eq); mdd = max(mdd, pk - eq)
    red = sum(1 for x in p if x > TH_RED)
    pf = gp / gl if gl else float("inf")
    cum = eq - 1                  # 复利真实累计收益（1x 满仓）
    ddp = mdd / pk * 100         # 标准最大回撤 = (峰-谷)/峰
    print(f"[{label:14s}] 交易{n:4d}  累计(复利){cum*100:8.2f}%  算术和{sum(p):8.2f}%  "
          f"胜率{tp/n*100:5.1f}%  PF={pf:5.2f}  DD(标准){ddp:6.1f}%  红字{red:3d}  "
          f"均盈{sum(p)/n:+6.3f}%")
    return dict(n=n, tot=cum * 100, pf=pf, dd=ddp, red=red, wr=tp / n * 100)

ALL = recs
A = [x for x in recs if x["v3"]]
C = [x for x in recs if x["c15"]]
B = [x for x in recs if x["v3"] and x["c15"]]

print("═" * 96)
print("【主对比】")
print("═" * 96)
metrics(ALL, "全量 808")
metrics(A, "A: V3")
metrics(B, "B: V3+15m确认")
metrics(C, "C: 仅15m确认")

print("\n" + "═" * 96)
print("【相对全量 808 的删减】（重点：红字删了多少 vs 交易删了多少）")
print("═" * 96)
base_red = sum(1 for x in ALL if x["pnl"] > TH_RED)
for name, S in (("A: V3", A), ("B: V3+15m", B), ("C: 仅15m", C)):
    red = sum(1 for x in S if x["pnl"] > TH_RED)
    n0, n1 = len(ALL), len(S)
    print(f"{name:12s} 交易 {n0:3d}→{n1:3d} (-{n0-n1:3d}, {-(n0-n1)/n0*100:5.1f}%)   "
          f"红字 {base_red:3d}→{red:3d} (-{base_red-red:3d}, {-(base_red-red)/base_red*100:5.1f}%)")

print("\n" + "═" * 96)
print("【B vs A：15m 叠加在 V3 之上的边际贡献】—— 判断是不是重复过滤的关键")
print("═" * 96)
only_A = [x for x in A if not x["c15"]]          # V3 放行但被 15m 砍掉的那批
print(f"V3 放行 {len(A)} 笔，其中 15m 否掉 {len(only_A)} 笔")
if only_A:
    p = [x["pnl"] for x in only_A]
    gp = sum(x for x in p if x > 0); gl = -sum(x for x in p if x < 0)
    red = sum(1 for x in p if x > TH_RED)
    avgA = sum(x["pnl"] for x in A) / len(A)
    print(f"  被砍这批：累计{sum(p):8.2f}%  均盈{sum(p)/len(p):+6.3f}%  "
          f"PF={gp/gl if gl else 0:5.2f}  红字{red}  (V3整体均盈 {avgA:+.3f}%)")
    print(f"  → 若均盈明显<V3整体且PF<1：15m 砍掉了真垃圾，是增量信息")
    print(f"  → 若均盈≈V3整体：15m 只是随机重复过滤，1h 已包含该信息")

print("\n【V3 与 15m 是否互相重复（列联表）】")
both = len([x for x in recs if x["v3"] and x["c15"]])
v3o = len([x for x in recs if x["v3"] and not x["c15"]])
c15o = len([x for x in recs if not x["v3"] and x["c15"]])
neither = len([x for x in recs if not x["v3"] and not x["c15"]])
print(f"  V3通过&15m通过 {both:4d} | V3通过&15m拒绝 {v3o:4d}")
print(f"  V3拒绝&15m通过 {c15o:4d} | 两者都拒绝     {neither:4d}")
for nm, sel in (("V3通过&15m拒绝", [x for x in recs if x["v3"] and not x["c15"]]),
                ("V3拒绝&15m通过", [x for x in recs if not x["v3"] and x["c15"]])):
    p = [x["pnl"] for x in sel]
    gp = sum(x for x in p if x > 0); gl = -sum(x for x in p if x < 0)
    print(f"  {nm}: {len(sel)} 笔 累计{sum(p):8.2f}% 均盈{sum(p)/len(p):+6.3f}% "
          f"PF={gp/gl if gl else 0:5.2f} 红字{sum(1 for x in p if x>TH_RED)}")

print("\n【15m 各分项命中率 & V3 路径分布】")
from collections import Counter
print("  V3路径:", dict(Counter(x["v3_path"] for x in recs)))
print("  15m分数分布:", dict(sorted(Counter(x["c_score"] for x in recs).items())))
hit = Counter()
for x in recs:
    for h in x["c_hit"]: hit[h] += 1
print("  15m分项命中:", dict(hit))

print("\n【窗口长度 × 动量阈值 敏感性】  (win=8 为本轮默认)")
for win in (4, 6, 8):
    for thr in (0.3, 0.5):
        Cc = [x for x in recs if confirm15(x["secs"], x["side"], thr, win)[0]]
        Bb = [x for x in recs if x["v3"] and confirm15(x["secs"], x["side"], thr, win)[0]]
        metrics(Cc, f"C win={win} thr={thr}")
        metrics(Bb, f"B win={win} thr={thr}")
