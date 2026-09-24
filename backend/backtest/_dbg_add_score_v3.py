# -*- coding: utf-8 -*-
"""ST Score V3: 9个子项共100分, 写入 st_signals_1h.csv (808笔)"""
import csv

SRC = r"d:\个人项目代码\supertrendMain\backend\backtest\st_signals_1h.csv"


def f(x):
    try:
        return float(x)
    except Exception:
        return float("nan")


# A. 趋势环境 30
def s_adx(v):                       # 15
    if v < 15: return 0
    if v < 20: return 5
    if v <= 25: return 10
    return 15


def s_er(v):                        # 15
    if v < 0.15: return 0
    if v <= 0.3: return 8
    return 15


# B. 突破质量 25
def s_stdist(d):                    # 10 (按|距离|, 太远=追涨)
    a = abs(d)
    if a <= 1: return 10
    if a <= 2: return 7
    return 2


def s_brk(bd):                      # 15 = 突破10 + 距离5
    base = 10 if abs(bd) > 0.05 else 0
    m = abs(bd)
    if m <= 1: dist = 5
    elif m <= 1.5: dist = 2
    else: dist = 0
    return base + dist


# C. 动量确认 20
def s_vol(v):                       # 10
    if v < 0.8: return 0
    if v <= 1.5: return 5
    return 10


def s_body(v):                      # 10
    if v < 0.2: return 0
    if v <= 0.8: return 5
    if v <= 1.5: return 10
    return 5


# D. 风险过滤 15
def s_atr(v):                       # 10
    if v < 0.3: return 0
    if v <= 1.5: return 10
    if v <= 2.5: return 7
    return 3


def s_sq(v):                        # 5
    return 5 if v == 1 else 0


# E. 高级过滤 10
def s_htf(align):                   # 10
    return 10 if align == 1 else 0


def grade(sc):
    if sc >= 75: return "S"
    if sc >= 60: return "A"
    if sc >= 45: return "B"
    return "C"


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
    out_cols = list(rows[0].keys()) + [
        "sc_adx", "sc_er", "sc_stdist", "sc_brk", "sc_vol", "sc_body",
        "sc_atr", "sc_sq", "sc_htf", "score", "grade"]
    new = []
    for r in rows:
        adx = f(r["ADX14"]); er = f(r["ER20"]); sd = f(r["st_distance_atr"])
        bd = f(r["break_distance_atr"]); vr = f(r["vol_ratio"]); bo = f(r["body_atr"])
        atr = f(r["ATR_pct"]); sq = int(f(r["squeeze"])) if r["squeeze"] not in ("", "nan") else 0
        al = int(float(r["align"])) if r["align"] not in ("", "nan") else -1
        a = s_adx(adx); e = s_er(er); st = s_stdist(sd); b = s_brk(bd)
        vo = s_vol(vr); by = s_body(bo); at = s_atr(atr); sqs = s_sq(sq); h = s_htf(al)
        tot = a + e + st + b + vo + by + at + sqs + h
        row = dict(r)
        row.update({"sc_adx": a, "sc_er": e, "sc_stdist": st, "sc_brk": b,
                    "sc_vol": vo, "sc_body": by, "sc_atr": at, "sc_sq": sqs,
                    "sc_htf": h, "score": tot, "grade": grade(tot)})
        new.append(row)
    with open(SRC, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=out_cols)
        w.writeheader()
        w.writerows(new)
    # 统计
    from collections import Counter
    gc = Counter(x["grade"] for x in new)
    scs = [x["score"] for x in new]
    print(f"已写入 {len(new)} 笔, 总分区间 {min(scs)}~{max(scs)} 均值 {sum(scs)/len(scs):.1f}")
    print("等级分布:", dict(sorted(gc.items())))
    print("S级(>=75) 笔数:", gc["S"], " A级(60-75):", gc["A"],
          " B级(45-60):", gc["B"], " C级(<45):", gc["C"])
    print("\n样例明细(前3笔):")
    for x in new[:3]:
        print(f"  {x['time']} sig{x['signal']} score={x['score']}({x['grade']}) "
              f"= ADX{x['sc_adx']}+ER{x['sc_er']}+STdist{x['sc_stdist']}+Brk{x['sc_brk']}"
              f"+Vol{x['sc_vol']}+Body{x['sc_body']}+ATR{x['sc_atr']}+Sq{x['sc_sq']}+4h{x['sc_htf']}")


if __name__ == "__main__":
    main()
