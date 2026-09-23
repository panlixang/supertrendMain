import csv
from collections import defaultdict

rows = list(csv.DictReader(open("backtest/_raw_full.csv", encoding="utf-8-sig")))
done = [r for r in rows if r["result"] != "—"]
win = [r for r in done if r["result"] == "win"]
lose = [r for r in done if r["result"] == "loss"]


def f(x):
    try:
        return float(x)
    except Exception:
        return None


print("总信号 %d | win %d / loss %d | 胜率 %.1f%% | 净 %.2fU" %
      (len(rows), len(win), len(lose), len(win) / len(done) * 100,
       sum(f(r["profit_U"]) for r in done)))

print("\n--- 按月 ---")
m = defaultdict(lambda: [0, 0, 0.0])
for r in done:
    mo = r["time"][:7]
    m[mo][0] += 1
    m[mo][1] += (1 if r["result"] == "win" else 0)
    m[mo][2] += f(r["profit_U"])
for mo in sorted(m):
    t, w, u = m[mo]
    print("  %s  n=%3d 胜=%3d 胜率 %4.1f%% 净 %+7.1fU" % (mo, t, w, w / t * 100, u))

print("\n--- win vs loss 均值 ---")
for col in ["range_position", "mom12_ATR", "volume_ratio", "ADX14", "body_ATR",
            "future_max_profit", "future_max_drawdown", "bars_since_last_flip",
            "current_bar_contribution", "distance_to_range_high_ATR",
            "distance_to_range_low_ATR"]:
    wa = sum(f(r[col]) for r in win) / len(win)
    la = sum(f(r[col]) for r in lose) / len(lose)
    print("  %-24s win %7.3f   loss %7.3f" % (col, wa, la))
