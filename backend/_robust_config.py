import csv
import os
from collections import defaultdict

ROOT = r"e:/个人文档/supertrendMain-master"
FILES = {
    "ETH": "ETH_15m_2026_supertrend_signals.csv",
    "GOOGL": "GOOGL_15m_2026_supertrend_signals.csv",
    "AMD": "AMD_15m_2026_supertrend_signals.csv",
    "AVGO": "AVGO_15m_2026_supertrend_signals.csv",
    "MU": "MU_15m_2026_supertrend_signals.csv",
}


def load(name):
    out = []
    with open(os.path.join(ROOT, FILES[name]), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append({"pnl": float(r["pnl_pct"]) / 100,
                            "er": float(r["ER20"]), "atr": float(r["ATR14_ATR50"]),
                            "win": r["result"] == "胜", "m": r["signal_time_utc"][:7]})
            except (ValueError, TypeError, KeyError):
                pass
    return out


DATA = {k: load(k) for k in FILES}


def stats(sub):
    if not sub:
        return None
    eq, peak, mdd = 1.0, 1.0, 0.0
    p = []
    for r in sub:
        x = r["pnl"]
        p.append(x)
        eq *= (1 + x)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    pos, neg = sum(x for x in p if x > 0), -sum(x for x in p if x < 0)
    by = defaultdict(list)
    for r in sub:
        by[r["m"]].append(r["pnl"])
    return {"n": len(sub), "wr": sum(r["win"] for r in sub) / len(sub) * 100,
            "simple": sum(p) * 100, "comp": (eq - 1) * 100, "mdd": mdd * 100,
            "pf": pos / neg if neg else float("inf"),
            "posm": sum(sum(v) > 0 for v in by.values()), "nm": len(by)}


BANDS = {
    "A: .70-.80 + 1.00-1.10": lambda r: .70 <= r["atr"] < .80 or 1.00 <= r["atr"] < 1.10,
    "B: .70-.80 + 1.20-1.30": lambda r: .70 <= r["atr"] < .80 or 1.20 <= r["atr"] < 1.30,
    "C: .70-.80 + 1.30-1.50": lambda r: .70 <= r["atr"] < .80 or 1.30 <= r["atr"] < 1.50,
    "D: .70-.80 + >=1.50": lambda r: .70 <= r["atr"] < .80 or r["atr"] >= 1.50,
    "E: 1.00-1.20 单段": lambda r: 1.00 <= r["atr"] < 1.20,
    "F: .70-.80 + 1.00-1.50": lambda r: .70 <= r["atr"] < .80 or 1.00 <= r["atr"] < 1.50,
}
ER_RANGE = {
    "ER .10-.40": lambda r: .10 <= r["er"] < .40,
    "ER .05-.40": lambda r: .05 <= r["er"] < .40,
    "ER .00-.30": lambda r: .00 <= r["er"] < .30,
}

for er_name, er_fn in ER_RANGE.items():
    print(f"\n{'='*90}\n{er_name}，方向不选")
    for bname, bfn in BANDS.items():
        rows = []
        for sym, all_rows in DATA.items():
            s = stats([r for r in all_rows if er_fn(r) and bfn(r)])
            if s:
                rows.append((sym, s))
        pos = sum(1 for _, s in rows if s["simple"] > 0 and s["pf"] > 1)
        avg_pf = sum(s["pf"] for _, s in rows) / len(rows)
        avg_mdd = sum(s["mdd"] for _, s in rows) / len(rows)
        tot = sum(s["simple"] for _, s in rows)
        print(f"\n{bname:26s} 正收益品种 {pos}/{len(rows)} 合计Σ={tot:+7.1f}% "
              f"平均PF={avg_pf:4.2f} 平均MDD={avg_mdd:6.2f}%")
        print("   " + "  ".join(f"{sym}:{s['simple']:+.1f}%(PF{s['pf']:.2f},DD{s['mdd']:.1f},n{s['n']})"
                                for sym, s in rows))
