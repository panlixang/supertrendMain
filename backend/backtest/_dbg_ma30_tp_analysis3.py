# -*- coding: utf-8 -*-
"""第三轮：稳健性检验 — 时间前后半样本 / 信号方向与4h方向一致性。"""
import pandas as pd

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 60)

df = pd.read_csv("st_signals_1h_features.csv")
for c in ["盈亏", "信号", "4h方向"] + ["1h_MA30斜率", "4h_MA30斜率", "1h_MA30距离", "4h_MA30距离"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")

df["s_4h斜率"] = df["4h_MA30斜率"] * df["信号"]
df["s_4h距离"] = df["4h_MA30距离"] * df["信号"]
df["a_4h距离"] = df["4h_MA30距离"].abs()
df["a_1h距离"] = df["1h_MA30距离"].abs()
df["big15"] = ((df["止盈止损"] == "TP") & (df["盈亏"] > 1.5)).astype(int)
df["big20"] = ((df["止盈止损"] == "TP") & (df["盈亏"] > 2.0)).astype(int)

print("=" * 100)
print("【A】信号方向 vs 4h方向 是否一致")
same = (df["信号"] == df["4h方向"]).sum()
print(f"  一致 {same}/{len(df)} = {same/len(df)*100:.1f}%")

# 按时间排序切半
df["ts"] = pd.to_datetime(df["时间"], errors="coerce")
df = df.sort_values("ts")
half = len(df) // 2
segs = {"全样本": df, "前半段": df.iloc[:half], "后半段": df.iloc[half:]}

# 阈值用全样本固定（避免分段内再调参）
LO, HI = 0.04, 0.71
rules = {
    "基准(无过滤)": None,
    f"4h斜率顺势 [{LO},{HI}]": (df["s_4h斜率"] >= LO) & (df["s_4h斜率"] <= HI),
    "4h斜率顺势 >0.71(过强)": df["s_4h斜率"] > 0.71,
    f"[{LO},{HI}] & |4h距离|<0.5": (df["s_4h斜率"] >= LO) & (df["s_4h斜率"] <= HI) & (df["a_4h距离"] < 0.5),
    "|4h距离|<0.3%": df["a_4h距离"] < 0.3,
    "排除 4h斜率>0.71": ~(df["s_4h斜率"] > 0.71),
}

print("\n" + "=" * 100)
print("【B】前后半样本稳健性（阈值固定自全样本）")
rows = []
for sname, seg in segs.items():
    for rname, m in rules.items():
        d = seg if m is None else seg[m.reindex(seg.index).fillna(False).astype(bool)]
        if len(d) < 15:
            continue
        rows.append({
            "样本": sname, "规则": rname, "n": len(d),
            "TP率%": round((d["止盈止损"] == "TP").mean() * 100, 1),
            "TP>1.5%": round(d.big15.mean() * 100, 1),
            "TP>2%": round(d.big20.mean() * 100, 1),
            "期望/笔": round(d["盈亏"].mean(), 3),
            "盈亏和": round(d["盈亏"].sum(), 1),
        })
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 100)
print("【C】4h斜率分箱（固定边界）在前后半样本的表现")
bins = [(-99, -2.0), (-2.0, -0.5), (-0.5, 0.0), (0.0, 0.3), (0.3, 0.71), (0.71, 99)]
rows = []
for sname, seg in segs.items():
    for lo, hi in bins:
        d = seg[(seg["s_4h斜率"] >= lo) & (seg["s_4h斜率"] < hi)]
        if len(d) < 10:
            continue
        rows.append({
            "样本": sname, "4h斜率区间": f"[{lo},{hi})", "n": len(d),
            "TP率%": round((d["止盈止损"] == "TP").mean() * 100, 1),
            "TP>2%": round(d.big20.mean() * 100, 1),
            "期望/笔": round(d["盈亏"].mean(), 3),
        })
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 100)
print("【D】大赢组(TP>2) vs 其余：4h斜率分布占比（%）")
d = df.dropna(subset=["s_4h斜率"])
for lo, hi in bins:
    m = (d["s_4h斜率"] >= lo) & (d["s_4h斜率"] < hi)
    big2 = d[d.big20 == 1]
    mb = (big2["s_4h斜率"] >= lo) & (big2["s_4h斜率"] < hi)
    print(f"  [{lo:>5},{hi:>4})  大赢组占比={mb.mean()*100:5.1f}%   其余占比={m.mean()*100:5.1f}%   差={mb.mean()*100-m.mean()*100:+5.1f}pt")
