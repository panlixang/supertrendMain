"""临时：拆解 ⑥ 加权打分的逐项计算过程（取 2026 极值样本）。"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pattern_trade as PT  # noqa: E402

CSV = "_raw_full.csv"


def feat_of(r):
    d = 1.0 if r["direction"] == "buy" else -1.0
    g = lambda k: float(r[k])
    return {
        "bars_since_last_flip": g("bars_since_last_flip"),
        "range_position": g("range_position"),
        "candle_range_ATR": g("candle_range_ATR"),
        "ATR_percent": g("ATR_percent"),
        "distance_to_range_high_ATR": g("distance_to_range_high_ATR"),
        "distance_to_range_low_ATR": g("distance_to_range_low_ATR"),
        "upper_wick_ratio": g("upper_wick_ratio"),
        "lower_wick_ratio": g("lower_wick_ratio"),
        "mom12_ATR": g("mom12_ATR"),
        "volume_ratio": g("volume_ratio"),
        "ADX14": g("ADX14"),
    }, (1 if d > 0 else -1)


def show(r, sd):
    f, sdn = feat_of(r)
    sc = PT.signal_score(f, sdn)
    print(f"\n--- {r['time']} {r['direction']}  实际结果={r['result']} "
          f"profit_U={r['profit_U']}  → score={sc:.3f} "
          f"{'【拦截】' if sc > PT.SCORE_CUT_DEFAULT else '【放行】'}")
    print(f"    {'指标':<22}{'原始值x':>10}{'阈值thr':>9}{'坡度s':>8}"
          f"{'惩罚b':>8}{'权重w':>8}{'贡献w*b':>9}")
    tot = 0.0
    for m in PT.SCORE_MODEL:
        x = m["get"](f, sdn)
        b = 1.0 / (1.0 + pow(2.718281828459045, -((x - m["thr"]) / m["s"])))
        c = m["w"] * b
        tot += c
        print(f"    {m['name']:<22}{x:>10.3f}{m['thr']:>9.3f}{m['s']:>8.3f}"
              f"{b:>8.3f}{m['w']:>8.3f}{c:>9.4f}")
    print(f"    {'合计 score':<22}{'':>35}{tot:>9.4f}   "
          f"(阈值 {PT.SCORE_CUT_DEFAULT})")


def main():
    rows = []
    with open(CSV, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if r.get("profit_U") in ("", None) or r.get("result") not in ("win", "loss"):
                continue
            if r["time"][:4] != "2026":
                continue
            f, sd = feat_of(r)
            r["_sc"] = PT.signal_score(f, sd)
            rows.append(r)
    rows.sort(key=lambda r: r["_sc"])
    print(f"2026 样本 {len(rows)} 笔，score 范围 "
          f"{rows[0]['_sc']:.3f} ~ {rows[-1]['_sc']:.3f}")
    print(f"权重合计 = {sum(m['w'] for m in PT.SCORE_MODEL):.3f}（Σw=1，故 score∈[0,1]）")
    show(rows[0], 1)      # 最干净的一笔
    show(rows[-1], 1)     # 最垃圾的一笔


if __name__ == "__main__":
    main()
