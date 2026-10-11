# -*- coding: utf-8 -*-
"""热更新 43：把 SKHYNIX 刷成「trail=True + ST 止损」的正确配置。

机制：POST /api/pattern/trade/symbols action=update —— 增量改，只覆盖传的字段。
依据（本次 V3 1h 回测 + 之前 check_43_symbols 通用推荐）：
  SKHYNIX 不能用 SNDK 那套「不跟踪 + pct 硬止损」(Calmar 0.04)；
  正确打开法是 trail_with_st=True + sl_mode=st（ST 轨道止损，pct 仅作兜底）。
  故本次只修正出场：sl pct->st、保 trail=True，其余保持线上当前值。
"""
import json
import urllib.request

HOST = "http://43.108.10.84:5174"
HDR = {"User-Agent": "apply/1.0", "Content-Type": "application/json"}


def _http(method, path, body=None):
    req = urllib.request.Request(
        HOST + path,
        data=(json.dumps(body).encode() if body is not None else None),
        headers=HDR, method=method)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def get_config():
    return _http("GET", "/api/pattern/trade/config")


SPEC = {
    "SKHYNIX-USDT-SWAP": dict(
        enabled=True,
        allow_tfs=["1h"],
        tp1_pct=1.5, tp1_ratio=70.0,
        exit_mode="single",
        sl_pct=2.0, sl_mode="st",          # ← 关键修正：pct 硬止损改 ST 轨道止损
        move_sl_to_entry=True, trail_with_st=True,
        reverse_close=False, filter_v3=True,
    ),
}


def row(s):
    return (f"tp1={s.get('tp1_pct')}/{s.get('tp1_ratio')} sl={s.get('sl_mode')}/"
            f"{s.get('sl_pct')} exit={s.get('exit_mode')} v3={s.get('filter_v3')} "
            f"trail={s.get('trail_with_st')} tfs={','.join(s.get('allow_tfs') or [])}")


cfg = get_config()
have = {s["symbol"]: s for s in (cfg.get("symbols") or [])}
print("43 现有品种:", sorted(have))

for sym, kw in SPEC.items():
    exists = sym in have
    action = "update" if exists else "add"
    if exists:
        print(f"\n>>> {sym} 已存在，改前: {row(have[sym])}")
    else:
        print(f"\n>>> {sym} 不存在，新增")
    body = {"action": action, "symbol": sym, **kw}
    print(f"    POST {action} 目标: tp1={kw['tp1_pct']}/{kw['tp1_ratio']} "
          f"sl={kw['sl_mode']}/{kw['sl_pct']} exit={kw['exit_mode']} "
          f"v3={kw['filter_v3']} trail={kw['trail_with_st']}")
    try:
        r = _http("POST", "/api/pattern/trade/symbols", body)
        print("    ok=%s error=%s" % (r.get("ok"), r.get("error", "")))
    except Exception as e:
        print("    ERROR", repr(e))

print("\n==== 改后验证 ====")
cfg = get_config()
for s in cfg.get("symbols") or []:
    if s["symbol"] in SPEC:
        print("%-20s en=%s %s" % (s["symbol"], s["enabled"], row(s)))
