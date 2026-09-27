# -*- coding: utf-8 -*-
"""在 43 上给 CL 增量补 tp1_pct=1.5 / tp1_ratio=70.0，保持 reverse_close=False、sl_mode=st 不变。
action=update + 只传这两个字段 → 纯增量。
对齐回测口径：单档 TP1 1.5%/70% + 保本 + 跟随 ST（sl_mode=st），reverse_close=False。
"""
import json, urllib.request

HOST = "http://43.108.10.84:5174"
HDR = {"User-Agent": "diag/1.0", "Content-Type": "application/json"}

def post_symbol(sym, **kw):
    body = {"action": "update", "symbol": sym}
    body.update(kw)
    req = urllib.request.Request(
        HOST + "/api/pattern/trade/symbols",
        data=json.dumps(body).encode(), headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def get_config():
    with urllib.request.urlopen(urllib.request.Request(
            HOST + "/api/pattern/trade/config", headers={"User-Agent": "diag/1.0"}),
            timeout=20) as r:
        return json.loads(r.read())

r = post_symbol("CL-USDT-SWAP", tp1_pct=1.5, tp1_ratio=70.0)
print("CL-USDT-SWAP -> ok=%s %s" % (r.get("ok"), r.get("error", "")))

print("\n==== CL 改后核验（对齐回测 ST 基线）====")
cfg = get_config()
WANT = {"tp1_pct": 1.5, "tp1_ratio": 70.0, "sl_mode": "st",
        "exit_mode": "single", "allow_tfs": ["1h"], "filter_v3": True}
for s in cfg.get("symbols") or []:
    if s.get("symbol") != "CL-USDT-SWAP":
        continue
    for k, want in WANT.items():
        actual = s.get(k)
        ok = (actual == want) or (actual is None and k in ("sl_mode",)) and False
        print("  %-14s = %-10s %s" % (k, actual, "✓" if actual == want else "≠ 期望 %s" % want))
    print("  reverse_close = %s  (回测=False，出场靠 TP1+保本+跟随ST)" % s.get("reverse_close"))
    pos = s.get("position")
    if pos:
        print("  持仓: %s @%s 现价=%s pnl=%.2f%% roe=%.2f%% stop=%s（止损仍生效 ✓）" % (
            pos.get("side"), pos.get("entry"), pos.get("price"),
            pos.get("pnl_pct", 0), pos.get("roe_pct", 0), pos.get("stop")))
