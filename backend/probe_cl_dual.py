# -*- coding: utf-8 -*-
"""查 43 与 47 两个服务器上 CL-USDT-SWAP 的当前持仓 + 最近成交，确认是否反向。"""
import urllib.request, json

BASES = {
    "43": "http://43.108.10.84:5174",
    "47": "http://47.84.106.154:5174",
}


def get(base, ep, timeout=15):
    try:
        return json.loads(urllib.request.urlopen(base + ep, timeout=timeout).read())
    except Exception as e:
        return {"__err__": repr(e)}


for tag, base in BASES.items():
    print(f"\n########## {tag} ({base}) CL ##########")
    # 当前持仓（先打原始，避免格式差异崩）
    p = get(base, "/api/trade/positions")
    print("  /api/trade/positions 原始:", json.dumps(p, ensure_ascii=False)[:800])

    # 完整 symbol 配置（拿 ST 参数）
    sy = get(base, "/api/trade/symbols")
    for s in (sy.get("symbols") or []):
        if "CL" in str(s.get("symbol", "")):
            print(f"  symbol={s.get('symbol')} enabled={s.get('enabled')} "
                  f"params={s.get('params')} allow_grades={s.get('allow_grades')} "
                  f"min_score={s.get('min_score')} filter_v3={s.get('filter_v3')} "
                  f"position={s.get('position')}")

    # 最近成交
    h = get(base, "/api/trade/history?sym=CL-USDT-SWAP&recent=6")
    if "__err__" in h:
        print("  history 失败:", h["__err__"])
        continue
    print(f"  total_trades={h.get('total_trades')}")
    for t in (h.get("recent") or []):
        print("   ", t.get("logged_at"), t.get("side"), "entry=", t.get("entry"),
              "exit=", t.get("price"), "pnl%=", t.get("pnl_pct"),
              "reason=", t.get("close_reason"))
