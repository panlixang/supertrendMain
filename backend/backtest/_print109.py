import json, sys

SM = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
d = json.load(open(f"backtest/_score_signal_list_2026_sm{SM:g}.json", encoding="utf-8"))
rows = d["rows"]


def classify(reason):
    s = reason or ""
    if "tp1" in s or "止盈" in s:
        return "TP1"
    if "保本" in s:
        return "BE"
    if "止损" in s:
        return "SL"
    if "超时" in s or "区间结束" in s or "timeout" in s:
        return "TO"
    return s or "—"


def fate_of(r):
    raw = (r["reason"] or "").replace("若成交·", "").replace("确认·", "")
    o = classify(raw)
    tag = f"TP1·{o}" if r.get("tp1_hit") else o
    if r["blocked"]:
        return f"拦截→若{tag}"
    if r["filt"] == "确认":
        return f"确认·{tag}"
    return tag


lines = []
hdr = (f"{'#':>3} {'日期':>10} {'方向':>4} {'评分明细':>20} {'入场':>10} "
       f"{'TP1':>10} {'SL':>10} {'出场':>10} {'归宿':>16} {'盈亏U':>9}")
lines.append(hdr)
lines.append("-" * len(hdr))
for r in rows:
    sc = f"{r['score']:.0f}={r['m1']:g}+{r['m2']:g}+{r['m3']:g}+{r['m4']:g}{r['pen']:+g}"
    ex = f"{r['exit']:.1f}" if r["exit"] is not None else "—"
    pu = f"{r['pnl_u']:+.2f}" if r["pnl_u"] is not None else "—"
    lines.append(f"{r['idx']:>3} {r['date']:>10} {r['type']:>4} {sc:>20} "
                 f"{r['entry']:>10.1f} {r['tp1']:>10.1f} {r['sl']:>10.1f} {ex:>10} "
                 f"{fate_of(r):>16} {pu:>9}")

tp1 = sum(1 for r in rows if r.get("tp1_hit"))
passed = [r for r in rows if not r["blocked"]]
blk = [r for r in rows if r["blocked"]]
cf = [r for r in blk if r["pnl_u"] is not None]
btp1 = sum(1 for r in blk if r.get("tp1_hit"))
bsl = sum(1 for r in cf if classify((r["reason"] or "").replace("若成交·", "")) == "SL")
bbe = sum(1 for r in cf if classify((r["reason"] or "").replace("若成交·", "")) == "BE")
bto = sum(1 for r in cf if classify((r["reason"] or "").replace("若成交·", "")) == "TO")
bw = sum(1 for r in cf if r["pnl_u"] > 0)
bl = sum(1 for r in cf if r["pnl_u"] < 0)
bsu = sum(r["pnl_u"] for r in cf)
lines.append("")
lines.append(f"全样本 TP1 命中 = {tp1} / {len(rows)}")
lines.append(f"放行 {len(passed)} 笔 | 拦截 {len(blk)} 笔（若成交）：TP1={btp1} SL={bsl} BE={bbe} TO={bto} | 盈利{bw}/亏损{bl} 合计{bsu:+.2f}U")

out = f"backtest/_109_sm{SM:g}.txt"
with open(out, "w", encoding="utf-8") as fp:
    fp.write("\n".join(lines) + "\n")
print("wrote", out)
