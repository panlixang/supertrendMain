"""把低回撤参数写回 43 服务器首页 /api/trade/symbols（增量，仅改 ST 与出场）。"""
import json, urllib.request

HOST = "http://43.108.10.84:5174"

# 与 _dump_backup 当前线上对比，只改最小集：
#   INTC: periods 18->15, tp1_pct 3.0->2.0 (其余不变)
#   AMD : periods 15->12, trail_with_st False->True (其余不变)
PAYLOADS = [
    {
        "symbol": "INTC-USDT-SWAP",
        "periods": 15,
        "multiplier": 3.0,
        "exit_rules": {"tp1_pct": 2.0, "tp1_ratio": 50.0},
    },
    {
        "symbol": "AMD-USDT-SWAP",
        "periods": 12,
        "multiplier": 4.0,
        "exit_rules": {"trail_with_st": True},
    },
]

def post(payload):
    req = urllib.request.Request(
        HOST + "/api/trade/symbols",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return r
    except Exception as e:
        return {"__err__": str(e)}

print("── 写回前确认目标 ──")
for p in PAYLOADS:
    print(f"  {p['symbol']}: periods={p['periods']} mult={p['multiplier']} exit={p['exit_rules']}")

print("\n── POST 写回 ──")
for p in PAYLOADS:
    r = post(p)
    syms = r.get("symbols") or []
    hit = [s for s in syms if s["symbol"] == p["symbol"]]
    if hit:
        s = hit[0]
        pp = s.get("params") or {}
        er = s.get("exit_rules") or {}
        print(f"  [{p['symbol']}] ok={r.get('ok')} -> ST={pp.get('periods')}×{pp.get('multiplier')} "
              f"tp1={er.get('tp1_pct')}/{er.get('tp1_ratio')} sl={er.get('sl_mode')}/{er.get('sl_pct')} "
              f"trail={er.get('trail_with_st')}")
    else:
        print(f"  [{p['symbol']}] 响应: {r}")
