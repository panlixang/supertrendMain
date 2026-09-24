# -*- coding: utf-8 -*-
"""ST Score V4: 新权重(9子项/100分) 写入 st_signals_1h.csv
① 入场位置35: ST距离20 + 突破距离15
② K线质量20: body10 + wick10
③ 趋势稳定25: ER10 + ADX5 + Flip10
④ 环境20: 4H10 + ATR5 + vol5
阈值由助手按V3逻辑推广设定(已在输出标注)
"""
import csv
from collections import Counter

SRC = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_1h.csv"
V3_COLS = ["sc_adx", "sc_er", "sc_stdist", "sc_brk", "sc_vol", "sc_body",
           "sc_atr", "sc_sq", "sc_htf", "score", "grade"]


def f(x):
    try:
        return float(x)
    except Exception:
        return float("nan")


# ① 入场位置 35
def sc_stdist(d):                       # 20  (|距离|越大=追涨越低)
    a = abs(d)
    return 20 if a <= 1 else (14 if a <= 2 else 4)


def sc_brk(bd):                         # 15  (突破距离, 刚突破最好)
    m = abs(bd)
    return 15 if m <= 1 else (7 if m <= 1.5 else 0)


# ② K线质量 20
def sc_body(v):                         # 10
    if v < 0.2: return 0
    if v <= 0.8: return 5
    if v <= 1.5: return 10
    return 5


def sc_wick(wr):                        # 10  (wick占比越小=越坚决)
    if wr < 0.3: return 10
    if wr < 0.6: return 7
    if wr < 0.8: return 4
    return 0


# ③ 趋势稳定 25
def sc_er(v):                           # 10
    if v < 0.15: return 0
    if v <= 0.3: return 5
    return 10


def sc_adx(v):                          # 5
    if v < 15: return 0
    if v < 20: return 1
    if v <= 25: return 3
    return 5


def sc_flip(bars):                      # 10  (距上次翻转越久=越稳)
    if bars >= 20: return 10
    if bars >= 10: return 7
    if bars >= 5: return 4
    return 0


# ④ 环境 20
def sc_4h(align):                       # 10
    return 10 if align == 1 else 0


def sc_atr(v):                          # 5
    if v < 0.3: return 0
    if v <= 1.5: return 5
    if v <= 2.5: return 3
    return 2


def sc_vol(v):                          # 5
    if v < 0.8: return 0
    if v <= 1.5: return 3
    return 5


def grade(sc):
    if sc >= 80: return "S"
    if sc >= 65: return "A"
    if sc >= 50: return "B"
    return "C"


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    base_cols = [k for k in rows[0].keys() if k not in V3_COLS]
    out_cols = base_cols + ["wick_ratio", "sc_stdist", "sc_brk", "sc_body",
                             "sc_wick", "sc_er", "sc_adx", "sc_flip", "sc_4h",
                             "sc_atr", "sc_vol", "score", "grade"]
    new = []
    for r in rows:
        sd = f(r["st_distance_atr"]); bd = f(r["break_distance_atr"])
        bo = f(r["body_atr"]); ra = f(r["range_atr"])
        er = f(r["ER20"]); adx = f(r["ADX14"]); bs = f(r["bars_since_flip"])
        al = int(float(r["align"])) if r["align"] not in ("", "nan") else -1
        atr = f(r["ATR_pct"]); vr = f(r["vol_ratio"])
        wr = 1 - bo / ra if ra > 0 else 0.5
        a = sc_stdist(sd); b = sc_brk(bd); by = sc_body(bo); wk = sc_wick(wr)
        e = sc_er(er); ax = sc_adx(adx); fl = sc_flip(bs); h = sc_4h(al)
        at = sc_atr(atr); vo = sc_vol(vr)
        tot = a + b + by + wk + e + ax + fl + h + at + vo
        row = {k: r[k] for k in base_cols}
        row.update({"wick_ratio": round(wr, 3), "sc_stdist": a, "sc_brk": b,
                    "sc_body": by, "sc_wick": wk, "sc_er": e, "sc_adx": ax,
                    "sc_flip": fl, "sc_4h": h, "sc_atr": at, "sc_vol": vo,
                    "score": tot, "grade": grade(tot)})
        new.append(row)
    with open(SRC, "w", newline="", encoding="utf-8-sig") as fp:
        csv.DictWriter(fp, fieldnames=out_cols).writeheader()
        csv.DictWriter(fp, fieldnames=out_cols).writerows(new)
    gc = Counter(x["grade"] for x in new)
    scs = [x["score"] for x in new]
    print(f"V4 已写入 {len(new)} 笔, 总分 {min(scs)}~{max(scs)} 均值 {sum(scs)/len(scs):.1f}")
    print("等级分布:", dict(sorted(gc.items())))
    print("S(>=80):", gc["S"], " A(65-80):", gc["A"], " B(50-65):", gc["B"], " C(<50):", gc["C"])
    print("\n样例明细(前3笔):")
    for x in new[:3]:
        print(f"  {x['time']} sig{x['signal']} score={x['score']}({x['grade']}) "
              f"= STdist{x['sc_stdist']}+Brk{x['sc_brk']}+Body{x['sc_body']}+Wick{x['sc_wick']}"
              f"+ER{x['sc_er']}+ADX{x['sc_adx']}+Flip{x['sc_flip']}+4h{x['sc_4h']}"
              f"+ATR{x['sc_atr']}+Vol{x['sc_vol']}  (wick_ratio={x['wick_ratio']})")


if __name__ == "__main__":
    main()
