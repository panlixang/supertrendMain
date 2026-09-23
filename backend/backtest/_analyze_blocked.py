import json, sys

def load(sm):
    return json.load(open(f"backtest/_score_signal_list_2026_sm{sm:g}.json", encoding="utf-8"))

def outcome_of(r):
    """优先用已算好的 tp1_hit；否则按 reason 兜底分类。"""
    if r.get("tp1_hit"):
        return "TP1"
    s = (r.get("reason") or "").replace("若成交·", "").replace("确认·", "")
    if "止盈" in s:
        return "TP1"
    if "保本" in s:
        return "BE"
    if "止损" in s:
        return "SL"
    if "timeout" in s or "超时" in s or "区间结束" in s:
        return "TO"
    return (r.get("outcome") or s or "-")

sm = float(sys.argv[1]) if len(sys.argv) > 1 else 80.0
d = load(sm)
rows = d["rows"]

print(f"=== FULL 109 @ score_min={sm:g} ===")
print(f"{'#':>3} {'date':>10} {'dir':>4} {'score_detail':>20} {'entry':>9} {'tp1':>9} {'sl':>9} {'exit':>9} {'filt':>4} {'fate':>16} {'pnlU':>8}")
for r in rows:
    sd = f"{r['score']:.0f}={r['m1']:g}+{r['m2']:g}+{r['m3']:g}+{r['m4']:g}{r['pen']:+g}"
    ex = f"{r['exit']:.1f}" if r["exit"] is not None else "-"
    pu = f"{r['pnl_u']:+.2f}" if r["pnl_u"] is not None else "-"
    o = outcome_of(r)
    fate = f"拦截→若{o}" if r["blocked"] else o
    print(f"{r['idx']:>3} {r['date']:>10} {r['type']:>4} {sd:>20} {r['entry']:>9.1f} {r['tp1']:>9.1f} {r['sl']:>9.1f} {ex:>9} {r['filt']:>4} {fate:>16} {pu:>8}")
print()

blk = [r for r in rows if r["blocked"]]
nb = [r for r in blk if r["pnl_u"] is not None]
tp1 = sum(1 for r in nb if r.get("tp1_hit"))
be = sum(1 for r in nb if outcome_of(r) == "BE")
sl = sum(1 for r in nb if outcome_of(r) == "SL")
to = sum(1 for r in nb if outcome_of(r) == "TO")
win = sum(1 for r in nb if r["pnl_u"] > 0)
lose = sum(1 for r in nb if r["pnl_u"] < 0)
su = sum(r["pnl_u"] for r in nb)
passed = [r for r in rows if not r["blocked"]]
pw = sum(1 for r in passed if r["pnl_u"] is not None and r["pnl_u"] > 0)
pl = sum(1 for r in passed if r["pnl_u"] is not None and r["pnl_u"] < 0)
psu = sum(r["pnl_u"] for r in passed if r["pnl_u"] is not None)
print(f"=== score_min={sm:g} | total={len(rows)} pass={len(passed)} block={len(blk)} ===")
print(f"PASSED real: win={pw} lose={pl} sumU={psu:+.2f} (TP1命中={sum(1 for r in passed if r.get('tp1_hit'))})")
print(f"BLOCKED counterfactual: win={win} lose={lose} TP1={tp1} BE={be} SL={sl} TO={to} sumU={su:+.2f}")
print()
print(f"{'#':>3} {'date':>10} {'dir':>4} {'score_detail':>20} {'entry':>9} {'tp1':>9} {'sl':>9} {'exit':>9} {'fate':>16} {'pnlU':>8}")
for r in blk:
    sd = f"{r['score']:.0f}={r['m1']:g}+{r['m2']:g}+{r['m3']:g}+{r['m4']:g}{r['pen']:+g}"
    ex = f"{r['exit']:.1f}" if r["exit"] is not None else "-"
    pu = f"{r['pnl_u']:+.2f}" if r["pnl_u"] is not None else "-"
    print(f"{r['idx']:>3} {r['date']:>10} {r['type']:>4} {sd:>20} {r['entry']:>9.1f} {r['tp1']:>9.1f} {r['sl']:>9.1f} {ex:>9} {outcome_of(r):>16} {pu:>8}")

if sm != 80.0:
    d80 = load(80.0)
    b80 = {r["idx"] for r in d80["rows"] if r["blocked"]}
    band = [r for r in passed if r["idx"] in b80]
    bsu = sum(r["pnl_u"] for r in band if r["pnl_u"] is not None)
    bwin = sum(1 for r in band if r["pnl_u"] is not None and r["pnl_u"] > 0)
    blow = sum(1 for r in band if r["pnl_u"] is not None and r["pnl_u"] < 0)
    print()
    print(f">> BAND (blocked@80 but pass@{sm:g}): {len(band)} trades, win={bwin} lose={blow} sumU={bsu:+.2f} (TP1命中={sum(1 for r in band if r.get('tp1_hit'))})")
    for r in band:
        sd = f"{r['score']:.0f}={r['m1']:g}+{r['m2']:g}+{r['m3']:g}+{r['m4']:g}{r['pen']:+g}"
        pu = f"{r['pnl_u']:+.2f}" if r["pnl_u"] is not None else "-"
        print(f"   #{r['idx']} {r['date']} {r['type']} {sd} -> {outcome_of(r)} {pu}")
