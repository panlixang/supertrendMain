# -*- coding: utf-8 -*-
"""从增量 JSONL 汇总 Top30 成 _opt_v1_{name}_result.txt（避免与主脚本重定向文件冲突）。"""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
for name in ("INTC", "AMD"):
    p = os.path.join(HERE, f"_opt_v1_{name}.jsonl")
    if not os.path.exists(p):
        continue
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda o: o["s"]["tot"], reverse=True)
    out = os.path.join(HERE, f"_opt_v1_{name}_result.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# {name} V1 联合寻优 Top30（共 {len(rows)} 组合）\n")
        for o in rows[:30]:
            s, st = o["s"], o["st"]
            f.write(f"{st[0]}×{st[1]} tp1={o['tp1']} sl={o['sl']} tr={o['tr']} gate={o['gate']} "
                    f"n={s.get('n',0)} wr={s.get('wr',0):.1f} pf={s.get('pf',0):.2f} "
                    f"payoff={s.get('payoff',0):.2f} tot={s.get('tot',0):+.2f} "
                    f"mdd={s.get('mdd_u',0):.2f} blocked={s.get('blocked',0)} "
                    f"{s.get('start','')}~{s.get('end','')}\n")
    print(f"{name}: {len(rows)} 组 -> {out}", flush=True)
