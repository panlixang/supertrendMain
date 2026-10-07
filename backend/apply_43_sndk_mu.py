# -*- coding: utf-8 -*-
"""热更新 43：把已在 43 的 SNDK / MU 刷成各自回测寻优最优出场参数。

机制：POST /api/pattern/trade/symbols action=update —— 增量改，只覆盖传的字段。
出场参数取自回测寻优最优档：
  SNDK: tp1=5.0/ratio=50 / sl=pct 3.0 / trail=False / v3=True / single / 1h
  MU  : tp1=1.0/ratio=30 / sl=st(兜底2%) / trail=True / v3=True / single / 1h
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
    "SNDK-USDT-SWAP": dict(
        enabled=True, margin_usdt=10.0, leverage=3, sizing_mode="fixed",
        allow_tfs=["1h"],
        tp1_pct=5.0, tp1_ratio=50.0,
        exit_mode="single",
        sl_pct=3.0, sl_mode="pct",
        move_sl_to_entry=True, trail_with_st=False,
        reverse_close=False, filter_v3=True,
    ),
    "MU-USDT-SWAP": dict(
        enabled=True, margin_usdt=10.0, leverage=3, sizing_mode="fixed",
        allow_tfs=["1h"],
        tp1_pct=1.0, tp1_ratio=30.0,
        exit_mode="single",
        sl_pct=2.0, sl_mode="st",          # sl=st 时 sl_pct 即兜底百分比
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
        print("%-18s en=%s %s" % (s["symbol"], s["enabled"], row(s)))
