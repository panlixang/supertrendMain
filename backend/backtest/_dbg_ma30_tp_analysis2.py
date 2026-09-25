# -*- coding: utf-8 -*-
"""第二轮：大赢 TP(>1.5 / >2) 与 MA30 四字段的关系 — 含分位边界、十分位、显著性。"""
import pandas as pd

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 60)

SRC = "st_signals_1h_features.csv"
MA30 = ["1h_MA30斜率", "4h_MA30斜率", "1h_MA30距离", "4h_MA30距离"]

df = pd.read_csv(SRC)
for c in MA30 + ["盈亏", "信号", "4h方向"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")

# 顺势归一化：正数=与信号同向
df["s_1h斜率"] = df["1h_MA30斜率"] * df["信号"]
df["s_4h斜率"] = df["4h_MA30斜率"] * df["信号"]
df["s_1h距离"] = df["1h_MA30距离"] * df["信号"]
df["s_4h距离"] = df["4h_MA30距离"] * df["信号"]
df["a_1h距离"] = df["1h_MA30距离"].abs()
df["a_4h距离"] = df["4h_MA30距离"].abs()
NORM = ["s_1h斜率", "s_4h斜率", "s_1h距离", "s_4h距离"]

df["big15"] = ((df["止盈止损"] == "TP") & (df["盈亏"] > 1.5)).astype(int)
df["big20"] = ((df["止盈止损"] == "TP") & (df["盈亏"] > 2.0)).astype(int)

b = df["big20"].mean() * 100
print("=" * 100)
print(f"全部 n={len(df)} | TP=289 | TP>1.5=155 | TP>2=137 | 基准 TP>2 命中率 = {b:.1f}%")

# ───────── 1. 均值对比（含 lift） ─────────
print("\n" + "=" * 100)
print("【1】字段均值对比：大赢组 vs 其余组（含差值/lift）")
rows = []
for c in NORM:
    big = df[df.big20 == 1][c].dropna()
    rest = df[df.big20 == 0][c].dropna()
    big15 = df[df.big15 == 1][c].dropna()
    rows.append({
        "字段": c,
        "均值_TP>2": round(big.mean(), 3),
        "均值_其余": round(rest.mean(), 3),
        "差值": round(big.mean() - rest.mean(), 3),
        "中位_TP>2": round(big.median(), 3),
        "中位_其余": round(rest.median(), 3),
        "均值_TP>1.5": round(big15.mean(), 3),
    })
print(pd.DataFrame(rows).to_string(index=False))

# ───────── 2. 十分位：每字段按十分位看 TP>2 命中率 ─────────
print("\n" + "=" * 100)
print("【2】十分位分组（1=最负/最小, 10=最正/最大）：TP>2 命中率 %")
for c in NORM + ["a_1h距离", "a_4h距离"]:
    d = df.dropna(subset=[c]).copy()
    try:
        d["dec"] = pd.qcut(d[c], 10, labels=False, duplicates="drop") + 1
    except Exception:
        continue
    g = d.groupby("dec").agg(
        n=("big20", "size"),
        TP率=("止盈止损", lambda s: round((s == "TP").mean() * 100, 1)),
        TP15率=("big15", lambda s: round(s.mean() * 100, 1)),
        TP2率=("big20", lambda s: round(s.mean() * 100, 1)),
        均值盈亏=("盈亏", lambda s: round(s.mean(), 3)),
        均值盈利单=("盈亏", lambda s: round(s[s > 0].mean(), 3) if (s > 0).any() else float("nan")),
    )
    lo = d.groupby("dec")[c].min().round(2)
    hi = d.groupby("dec")[c].max().round(2)
    g.insert(0, "区间下限", lo)
    g.insert(1, "区间上限", hi)
    print(f"\n--- {c} ---")
    print(g.to_string())

# ───────── 3. 关键组合筛选 ─────────
print("\n" + "=" * 100)
print("【3】候选过滤条件下的大赢捕获率（基准 TP>2 = 17.0%）")
p80_4hs = df["s_4h斜率"].quantile(.80)
p60_4hs = df["s_4h斜率"].quantile(.60)
p20_4hs = df["s_4h斜率"].quantile(.20)
combos = {
    "基准(无过滤)": pd.Series(True, index=df.index),
    "4h斜率顺势>0": df["s_4h斜率"] > 0,
    "4h斜率顺势 Q4段(60~80分位)": (df["s_4h斜率"] >= p60_4hs) & (df["s_4h斜率"] <= p80_4hs),
    "4h斜率顺势 前20%(>80分位)": df["s_4h斜率"] > p80_4hs,
    "4h斜率逆势 后20%": df["s_4h斜率"] < p20_4hs,
    "|4h距离|<0.3%": df["a_4h距离"] < 0.3,
    "|4h距离|<0.5%": df["a_4h距离"] < 0.5,
    "|4h距离|>1%": df["a_4h距离"] > 1,
    "|1h距离|<0.3%": df["a_1h距离"] < 0.3,
    "|1h距离|>1%": df["a_1h距离"] > 1,
    "Q4段斜率 & |4h距离|<0.5": ((df["s_4h斜率"] >= p60_4hs) & (df["s_4h斜率"] <= p80_4hs)) & (df["a_4h距离"] < 0.5),
    "4h斜率顺势>0 & |4h距离|<0.5": (df["s_4h斜率"] > 0) & (df["a_4h距离"] < 0.5),
    "4h斜率顺势>0 & |1h距离|<0.5": (df["s_4h斜率"] > 0) & (df["a_1h距离"] < 0.5),
}
print(f"(参考分位: s_4h斜率 Q20={p20_4hs:.2f} Q60={p60_4hs:.2f} Q80={p80_4hs:.2f})")
res = []
for name, m in combos.items():
    m = m.fillna(False).astype(bool)
    d = df[m]
    if len(d) < 20:
        continue
    res.append({
        "过滤条件": name, "n": len(d),
        "TP率%": round((d["止盈止损"] == "TP").mean() * 100, 1),
        "TP>1.5%": round(d.big15.mean() * 100, 1),
        "TP>2%": round(d.big20.mean() * 100, 1),
        "lift_TP>2": round(d.big20.mean() * 100 / b, 2),
        "均盈亏": round(d["盈亏"].mean(), 3),
        "盈亏和": round(d["盈亏"].sum(), 1),
        "期望/笔": round(d["盈亏"].sum() / len(d), 3),
    })
print(pd.DataFrame(res).to_string(index=False))

# ───────── 4. AUC / 显著性 ─────────
print("\n" + "=" * 100)
print("【4】区分能力 AUC（越大越强，0.5=无区分）与 Mann-Whitney p 值")
try:
    from scipy.stats import mannwhitneyu
except ImportError:
    mannwhitneyu = None

for c in NORM + ["a_1h距离", "a_4h距离"]:
    d = df.dropna(subset=[c])
    pos, neg = d[d.big20 == 1][c], d[d.big20 == 0][c]
    if len(pos) < 5:
        continue
    if mannwhitneyu:
        u, p = mannwhitneyu(pos, neg, alternative="two-sided")
        auc = u / (len(pos) * len(neg))
        print(f"  {c:<10} AUC={auc:.4f}  p={p:.4f}")
    else:
        auc = ((pos.values[:, None] > neg.values[None, :]).mean())
        print(f"  {c:<10} AUC={auc:.4f}")
