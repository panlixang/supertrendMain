import csv
from collections import defaultdict

rows = list(csv.DictReader(open("backtest/_raw109.csv", encoding="utf-8-sig")))
done = [r for r in rows if r["pnl_pct"] != ""]


def f(x):
    try:
        return float(x)
    except Exception:
        return None


win = [r for r in done if f(r["pnl_pct"]) > 0]
lose = [r for r in done if f(r["pnl_pct"]) <= 0]
print("总信号 %d | 有出场 %d | 止盈 %d / 止损 %d | 胜率 %.1f%%" %
      (len(rows), len(done), len(win), len(lose), len(win) / len(done) * 100))
print("净值合计 %+.2f%%  均值 %+.2f%%" %
      (sum(f(r["pnl_pct"]) for r in done), sum(f(r["pnl_pct"]) for r in done) / len(done)))
print()


def grp(keyfn, label):
    d = defaultdict(lambda: [0, 0])
    for r in done:
        k = keyfn(r)
        d[k][0] += 1
        d[k][1] += (1 if f(r["pnl_pct"]) > 0 else 0)
    print("--- 按 %s 胜率 ---" % label)
    for k in sorted(d):
        t, w = d[k]
        print("  %-10s n=%3d 胜=%3d 胜率 %5.1f%%" % (k, t, w, w / t * 100))
    print()


grp(lambda r: r["h4"], "4H趋势")
grp(lambda r: ("ADX>25" if f(r["adx"]) > 25 else ("ADX<20" if f(r["adx"]) < 20 else "20~25")), "ADX")
grp(lambda r: ("过度突破" if r["brk_label"] == "过度" else "正常突破"), "突破幅度")
grp(lambda r: ("顺势" if (r["type"] == "buy" and f(r["mom12"]) > 0) or
               (r["type"] == "sell" and f(r["mom12"]) < 0) else "逆势/弱"), "Mom12方向")
grp(lambda r: ("贴结构<1ATR" if f(r["struct_atr"]) < 1 else
               ("追涨>3ATR" if f(r["struct_atr"]) > 3 else "1~3ATR")), "距结构")

wmae = sum(f(r["mae"]) for r in win) / len(win)
wmfe = sum(f(r["mfe"]) for r in win) / len(win)
lmae = sum(f(r["mae"]) for r in lose) / len(lose)
lmfe = sum(f(r["mfe"]) for r in lose) / len(lose)
print("--- 平均 MAE/MFE ---")
print("  胜者: MAE=%.2f%% MFE=%.2f%% (曾盈利 %.2f%%)" % (wmae, wmfe, wmfe))
print("  负者: MAE=%.2f%% MFE=%.2f%% (曾盈利 %.2f%%)" % (lmae, lmfe, lmfe))
rev = [r for r in lose if f(r["mfe"]) > 1.0]
print("  负者中 MFE 曾>1%% 却反转止损: %d 笔（假突破/过早反向）" % len(rev))
