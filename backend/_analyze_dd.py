"""在已跑完的寻优缓存里，按回撤最小重排，筛 OOS 为正的低回撤备选。"""
import json, os, sys, glob

MIN = 12
HERE = os.path.dirname(os.path.abspath(__file__))

def load(sym):
    f = os.path.join(HERE, f"_opt_combo_{sym}.jsonl")
    rows = []
    for line in open(f, encoding="utf-8"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        r = o.get("r", {})
        if "err" in r or "all" not in r:
            continue
        rows.append(o)
    return rows

def rank(sym):
    rows = load(sym)
    cand = []
    for o in rows:
        r = o["r"]
        ai, ao, aa = r["is_"], r["oos"], r["all"]
        if ai["n"] < MIN or ao["n"] < MIN:
            continue
        if ai["tot"] <= 0 or ao["tot"] <= 0:
            continue
        # 用全样本回撤为主排序键，IS/OOS 回撤任一过大也标红
        dd = max(aa["mdd"], ai["mdd"], ao["mdd"])
        cand.append((dd, o, r))
    cand.sort(key=lambda x: x[0])
    print("=" * 118)
    print(f"{sym} · 低回撤备选（要求 IS/OOS 笔数≥{MIN} 且 IS/OOS Σ净% 均>0，按 max(IS,OOS,全样本)回撤升序）")
    print("=" * 118)
    print(f"{'#':>3} {'ST':>9}{'阈值':>5}{'dyn':>4} {'出场(tp1/sl/trail/src)':>28}"
          f"{'IS_Σ%':>8}{'IS回撤':>8}{'OOS_Σ%':>9}{'OOS回撤':>9}{'全样本%':>9}{'全回撤':>8}")
    print("-" * 118)
    for i, (dd, o, r) in enumerate(cand[:15], 1):
        ai, ao, aa = r["is_"], r["oos"], r["all"]
        ex = o.get("ex") or [None, None, None]
        src = o.get("src", "hl2")
        exs = f"{ex[0]}/{ex[1]}/{'T' if ex[2] else 'F'}/{src}"
        print(f"{i:>3} {tuple(o['st'])[0]}×{tuple(o['st'])[1]:<5}{o['gate']:>5}"
              f"{'Y' if o['dyn'] else 'N':>4} {exs:>28}"
              f"{ai['tot']:>+8.2f}{ai['mdd']:>8.2f}{ao['tot']:>+9.2f}{ao['mdd']:>9.2f}"
              f"{aa['tot']:>+9.2f}{aa['mdd']:>8.2f}")
    print(f"  (共 {len(cand)} 组满足 IS/OOS 均正；以上为回撤最小的 15 组)")

if __name__ == "__main__":
    for s in (sys.argv[1:] if len(sys.argv) > 1 else ["INTC", "AMD"]):
        rank(s)
