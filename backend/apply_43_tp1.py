# -*- coding: utf-8 -*-
"""在 43 上给 NVDA/SKHYNIX/TSLA 增量补 tp1_pct=1.5 / tp1_ratio=70.0。
action=update + 只传这两个字段 → 纯增量，不覆盖 sl/pct/exit_mode 等已有配置。
CL 不在此列（它有持仓且要走"反转平仓"，另行确认后再改）。
"""
import json, urllib.request

HOST = "http://43.108.10.84:5174"
HDR = {"User-Agent": "diag/1.0", "Content-Type": "application/json"}

def post_symbol(sym, **kw):
    body = {"action": "update", "symbol": sym}
    body.update(kw)
    req = urllib.request.Request(
        HOST + "/api/pattern/trade/symbols",
        data=json.dumps(body).encode(),
        headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def get_config():
    with urllib.request.urlopen(urllib.request.Request(
            HOST + "/api/pattern/trade/config", headers={"User-Agent": "diag/1.0"}),
            timeout=20) as r:
        return json.loads(r.read())

TARGETS = ("NVDA-USDT-SWAP", "SKHYNIX-USDT-SWAP", "TSLA-USDT-SWAP")
for s in TARGETS:
    try:
        r = post_symbol(s, tp1_pct=1.5, tp1_ratio=70.0)
        print("%-20s -> ok=%s %s" % (s, r.get("ok"), r.get("error", "")))
    except Exception as e:
        print("%-20s -> ERROR %r" % (s, e))

print("\n==== 改后验证 ====")
cfg = get_config()
print("全局默认 tp1=%.1f/%.1f sl=%s/%.1f exit=%s" % (
    (cfg["cfg"]).get("tp1_pct", 0), (cfg["cfg"]).get("tp1_ratio", 0),
    (cfg["cfg"]).get("sl_mode"), (cfg["cfg"]).get("sl_pct", 0), (cfg["cfg"]).get("exit_mode")))
for s in cfg.get("symbols") or []:
    pos = s.get("position")
    print("%-20s tp1=%s/%s  sl=%s/%s  exit=%s  tfs=%s  v3=%s%s" % (
        s.get("symbol"), s.get("tp1_pct"), s.get("tp1_ratio"),
        s.get("sl_mode"), s.get("sl_pct"), s.get("exit_mode"),
        ",".join(s.get("allow_tfs") or []), s.get("filter_v3"),
        ("  [持仓 %s @%s stop=%s]" % (pos.get("side"), pos.get("entry"), pos.get("stop"))) if pos else ""))
