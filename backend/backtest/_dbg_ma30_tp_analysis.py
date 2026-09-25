# -*- coding: utf-8 -*-
"""分析：大赢 TP（盈亏>1.5 / >2）与 MA30 四个字段的关系。

字段定义（见 build_signal_features_1h.py）：
  1h_MA30斜率 = 过去10根 MA30 累计变化%
  1h_MA30距离 = (信号价 - MA30) / MA30 * 100   （4h 同理）
"""
import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)
pd.set_option("display.unicode.east_asian_width", True)

SRC = "st_signals_1h_features.csv"
MA30 = ["1h_MA30斜率", "4h_MA30斜率", "1h_MA30距离", "4h_MA30距离"]

df = pd.read_csv(SRC)
for c in MA30 + ["盈亏", "信号", "4h方向"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")

print("=" * 90)
print(f"总行数: {len(df)}")
print("\n[止盈止损 分布]\n", df["止盈止损"].value_counts(dropna=False))
print("\n[信号方向 分布]\n", df["信号"].value_counts(dropna=False))
print("\n[盈亏 描述]\n", df["盈亏"].describe())

# ── 方向归一化：正数 = 顺着信号方向 ──
df["顺势_1h斜率"] = df["1h_MA30斜率"] * df["信号"]
df["顺势_4h斜率"] = df["4h_MA30斜率"] * df["信号"]
df["顺势_1h距离"] = df["1h_MA30距离"] * df["信号"]
df["顺势_4h距离"] = df["4h_MA30距离"] * df["信号"]
NORM = ["顺势_1h斜率", "顺势_4h斜率", "顺势_1h距离", "顺势_4h距离"]

tp = df[df["止盈止损"] == "TP"].copy()
g15 = tp[tp["盈亏"] > 1.5]
g20 = tp[tp["盈亏"] > 2.0]
print("=" * 90)
print(f"TP 总数: {len(tp)} | TP&盈亏>1.5: {len(g15)} | TP&盈亏>2: {len(g20)}")
print(f"占全部信号比: TP>1.5 = {len(g15)/len(df)*100:.1f}% | TP>2 = {len(g20)/len(df)*100:.1f}%")


def stats(frame, cols):
    out = {}
    for c in cols:
        s = frame[c].dropna()
        if len(s) == 0:
            out[c] = (0, float("nan"), float("nan"), float("nan"), float("nan"))
            continue
        out[c] = (len(s), s.mean(), s.median(), s.quantile(.25), s.quantile(.75))
    return pd.DataFrame(out, index=["n", "均值", "中位数", "Q25", "Q75"]).T


print("\n" + "=" * 90)
print("【一】原始四字段：基准 vs 大赢组")
for name, frame in [("全部信号", df), ("全部TP", tp), ("TP>1.5", g15), ("TP>2", g20)]:
    print(f"\n--- {name} (n={len(frame)}) ---")
    print(stats(frame, MA30).round(4))

print("\n" + "=" * 90)
print("【二】方向归一化后（正数=顺势）：基准 vs 大赢组")
for name, frame in [("全部信号", df), ("全部TP", tp), ("TP>1.5", g15), ("TP>2", g20)]:
    print(f"\n--- {name} (n={len(frame)}) ---")
    print(stats(frame, NORM).round(4))

print("\n" + "=" * 90)
print("【三】相关性（Pearson / Spearman，对全部信号）")
for c in MA30 + NORM:
    p = df[["盈亏", c]].dropna()
    if len(p) > 3:
        print(f"  {c:<12} pearson={p['盈亏'].corr(p[c]):+.4f}  spearman={p['盈亏'].corr(p[c], method='spearman'):+.4f}  n={len(p)}")

print("\n" + "=" * 90)
print("【四】分桶：每字段按分位分 5 桶，看桶内表现")
allg = ["全部TP占比", "TP>1.5占比", "TP>2占比", "平均盈亏", "盈亏中位数"]
rows = []
for c in NORM:
    d = df.dropna(subset=[c]).copy()
    try:
        d["bucket"] = pd.qcut(d[c], 5, duplicates="drop")
    except Exception:
        continue
    b = d.groupby("bucket", observed=True)
    t = pd.DataFrame({
        "n": b.size(),
        "全部TP占比": (b.apply(lambda x: (x["止盈止损"] == "TP").mean() * 100, include_groups=False)),
        "TP>1.5占比": (b.apply(lambda x: ((x["止盈止损"] == "TP") & (x["盈亏"] > 1.5)).mean() * 100, include_groups=False)),
        "TP>2占比": (b.apply(lambda x: ((x["止盈止损"] == "TP") & (x["盈亏"] > 2)).mean() * 100, include_groups=False)),
        "平均盈亏": b["盈亏"].mean(),
        "盈亏中位数": b["盈亏"].median(),
    })
    t.index = [f"{c}#{i+1}" for i in range(len(t))]
    rows.append(t.round(3))
print(pd.concat(rows))

print("\n" + "=" * 90)
print("【五】关键组合的筛选效果")
combos = {
    "基准(全部)": None,
    "4h斜率顺势(>0)": df["顺势_4h斜率"] > 0,
    "4h斜率顺势>0.5": df["顺势_4h斜率"] > 0.5,
    "4h斜率逆势(<0)": df["顺势_4h斜率"] < 0,
    "1h斜率顺势(>0)": df["顺势_1h斜率"] > 0,
    "4h距离顺势(>0)": df["顺势_4h距离"] > 0,
    "4h距离逆势(<0)": df["顺势_4h距离"] < 0,
    "斜率+距离双顺势": (df["顺势_4h斜率"] > 0) & (df["顺势_4h距离"] > 0),
    "4h顺势 & 1h顺势": (df["顺势_4h斜率"] > 0) & (df["顺势_1h斜率"] > 0),
    "|4h距离|大(>1%)": df["顺势_4h距离"].abs() > 1,
    "|4h距离|小(<0.3%)": df["顺势_4h距离"].abs() < 0.3,
}
res = []
for name, mask in combos.items():
    d = df if mask is None else df[mask]
    if len(d) == 0:
        continue
    res.append({
        "组合": name, "n": len(d),
        "TP率%": round((d["止盈止损"] == "TP").mean() * 100, 1),
        "TP>1.5%": round(((d["止盈止损"] == "TP") & (d["盈亏"] > 1.5)).mean() * 100, 1),
        "TP>2%": round(((d["止盈止损"] == "TP") & (d["盈亏"] > 2)).mean() * 100, 1),
        "平均盈亏": round(d["盈亏"].mean(), 4),
        "平均盈利单": round(d[d["盈亏"] > 0]["盈亏"].mean(), 4),
    })
print(pd.DataFrame(res).to_string(index=False))

print("\n" + "=" * 90)
print("【六】大赢组实例明细（TP>2，按盈亏降序前25）")
cols = ["时间", "信号", "止盈止损", "盈亏"] + MA30 + NORM
print(g20.sort_values("盈亏", ascending=False)[cols].head(25).round(3).to_string(index=False))
